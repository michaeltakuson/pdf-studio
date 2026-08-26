import { Viewer } from './viewer.js';
import { ToolController } from './tools.js';
import * as model from './model.js';
import { renderPage } from './render.js';
import {
  renderContextBar, renderProps, renderComments, renderThumbs, renderOutline,
  renderSettings, renderTakeoff, keepingFocus, typeLabel,
} from './panels.js';
import * as measure from './measure.js';
import { remember, getPref, setPref } from './defaults.js';
import { confirmDialog, formDialog, openMenu } from './dialogs.js';

const $ = (sel) => document.querySelector(sel);

const stage = $('#stage');
const viewer = new Viewer($('#pages'), stage);
const tools = new ToolController(viewer);

const state = {
  toc: [],
  filters: { query: '', checked: 'all', author: 'all', state: 'all', type: 'all', sort: 'page' },
  takeoffSubject: '',
  searchHits: [],
  searchIndex: -1,
  saving: false,
};

// ---------------------------------------------------------------- toast

let toastTimer;
function toast(message, kind = '') {
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, 3200);
}

function status(message) {
  $('#statusLeft').textContent = message;
}

// ---------------------------------------------------------------- opening

async function openFile(file) {
  status('読み込み中…');
  const form = new FormData();
  form.append('file', file);
  const response = await fetch('/api/open', { method: 'POST', body: form });
  if (!response.ok) {
    toast(`開けませんでした: ${(await response.json()).detail || response.status}`, 'error');
    status('準備完了');
    return;
  }
  const data = await response.json();
  state.toc = data.toc || [];
  model.loadDocument(data);
  $('#docName').textContent = data.name;
  // The name is hidden on narrow windows, so keep it reachable.
  $('#docName').title = data.name;
  document.title = `${data.name} — PDF Studio`;
  $('#emptyState').classList.add('hidden');
  await viewer.load(`/api/doc/${data.id}/file`);
  renderOutline($('#panelOutline'), state.toc, (page) => viewer.scrollToPage(page));
  renderThumbs($('#panelThumbs'), viewer, viewer.currentPage, (page) => viewer.scrollToPage(page));
  refreshAll();
  toast(`${data.name} を開きました`);
}

$('#fileInput').addEventListener('change', (e) => {
  if (e.target.files[0]) openFile(e.target.files[0]);
  e.target.value = '';
});
for (const id of ['#btnOpen', '#btnOpen2']) {
  $(id).addEventListener('click', () => $('#fileInput').click());
}

stage.addEventListener('dragover', (e) => { e.preventDefault(); stage.classList.add('dragover'); });
stage.addEventListener('dragleave', () => stage.classList.remove('dragover'));
stage.addEventListener('drop', (e) => {
  e.preventDefault();
  stage.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file && file.type === 'application/pdf') openFile(file);
});

// ---------------------------------------------------------------- rendering

function refreshOverlays() {
  for (const view of viewer.pageViews) {
    renderPage(view, model.onPage(view.index), model.store.selection);
  }
}

// The context bar is rebuilt only when what it is editing changes. Rebuilding
// it on every value change would replace the control mid-gesture, so dragging
// the opacity slider would stop after the first pixel.
let contextSignature = null;

function refreshPanels() {
  const selection = model.store.selection.map(model.byId).filter(Boolean);
  const signature = `${tools.tool}|${selection.map((a) => a.id).join(',')}`;
  if (signature !== contextSignature) {
    contextSignature = signature;
    renderContextBar($('#contextbar'), {
      tool: tools.tool,
      selection,
      onChange: (patch, gesture) => applyStylePatch(patch, selection, gesture),
      onCommit: () => model.endMerge(),
      onExtra: (patch) => {
        if (selection.length) model.updateAnnots(selection.map((a) => a.id), patch);
      },
    });
  }
  keepingFocus($('#panelProps'), () => renderProps($('#panelProps'), {
    selection,
    // One merge token per field, so a run of typing is one undo step but
    // switching fields starts a new one.
    onPatch: (patch, field) => {
      model.updateAnnots(
        selection.map((a) => a.id), patch,
        { merge: field ? `prop:${selection.map((a) => a.id).join()}:${field}` : null },
      );
      if (field) endMergeWhenIdle();
    },
  }));
  keepingFocus($('#panelComments'), () => renderComments($('#panelComments'), {
    annots: model.store.annots,
    selection,
    filters: state.filters,
    onFilter: (patch) => { Object.assign(state.filters, patch); refreshPanels(); },
    onSelect: (annot) => {
      model.select([annot.id]);
      viewer.scrollToPage(annot.page, annot.rect[1]);
    },
    onPatch: (id, patch) => { model.updateAnnots([id], patch); scheduleAutosave(); },
    onReply: (id, text) => {
      const annot = model.byId(id);
      if (!annot) return;
      model.updateAnnots([id], {
        replies: [...(annot.replies || []), {
          id: model.uid(),
          author: getPref('author') || '',
          contents: text,
          created: new Date().toISOString(),
        }],
      });
      scheduleAutosave();
      toast('返信を追加しました');
    },
    onBulk: (action, items) => {
      if (action === 'select') {
        model.select(items.map((a) => a.id));
        if (items.length) viewer.scrollToPage(items[0].page, items[0].rect[1]);
      }
    },
  }));
  keepingFocus($('#panelTakeoff'), () => renderTakeoff($('#panelTakeoff'), {
    rows: measure.summarise(model.store.annots),
    scale: measure.getScale(),
    calibrated: measure.isCalibrated(),
    unitLabels: measure.UNIT_LABELS,
    subject: state.takeoffSubject,
    onCalibrate: startCalibration,
    onUnit: (unit) => { measure.setScale({ unit }); refreshPanels(); },
    onSubject: (value) => { state.takeoffSubject = value; tools.subject = value; },
    onExportCsv: exportTakeoff,
    onLegend: placeLegend,
  }));
  keepingFocus($('#panelSettings'), () => renderSettings($('#panelSettings'), {
    getPref, setPref,
    onChange: () => { document.body.dataset.theme = getPref('theme'); },
  }));
  $('#btnUndo').disabled = !model.history.canUndo;
  $('#btnRedo').disabled = !model.history.canRedo;
  $('#btnUndo').title = model.history.canUndo
    ? '元に戻す (Ctrl+Z)'
    : 'この操作は元に戻せません。ページ操作や墨消しなどの前には、自動でスナップショットが保存されます';
  $('#statusSave').textContent = model.store.dirty ? '未保存の変更あり' : '保存済み';
  if (model.store.docId) {
    const selected = selection.length ? ` · ${selection.length} 件を選択` : '';
    status(`${model.store.pages.length} ページ · 注釈 ${model.store.annots.length} 件${selected}`);
  }
}

function refreshAll() {
  refreshOverlays();
  refreshPanels();
}

// A burst of typing is one undo step; a pause starts the next. Ending it on
// blur would not work, because re-rendering the panel blurs the field on every
// keystroke — each character would become its own step.
let mergeIdleTimer;
function endMergeWhenIdle(delay = 1200) {
  clearTimeout(mergeIdleTimer);
  mergeIdleTimer = setTimeout(() => model.endMerge(), delay);
}

function applyStylePatch(patch, selection, gesture) {
  if (selection.length) {
    model.updateAnnots(
      selection.map((a) => a.id), { style: patch },
      { merge: gesture ? `style:${selection.map((a) => a.id).join()}:${gesture}` : null },
    );
  } else {
    remember(tools.tool, patch);
    refreshPanels();
  }
}

model.subscribe((reason) => {
  if (reason === 'selection') { refreshOverlays(); refreshPanels(); return; }
  refreshAll();
});

viewer.addEventListener('zoom', refreshOverlays);
viewer.addEventListener('page', (e) => {
  $('#statusPage').textContent = `${e.detail.page + 1} / ${viewer.pageViews.length}`;
  for (const [index, node] of $('#panelThumbs').querySelectorAll('.thumb').entries()) {
    node.classList.toggle('current', index === e.detail.page);
  }
});

tools.addEventListener('edited', () => { refreshAll(); scheduleAutosave(); });
tools.addEventListener('tool', () => refreshPanels());

// ---------------------------------------------------------------- toolbar

for (const button of document.querySelectorAll('.tool')) {
  button.addEventListener('click', () => selectTool(button.dataset.tool));
}

function selectTool(tool) {
  tools.setTool(tool);
  for (const button of document.querySelectorAll('.tool')) {
    button.classList.toggle('active', button.dataset.tool === tool);
  }
}
selectTool('select');

/**
 * Keep the zoom control showing the real zoom.
 *
 * The presets do not cover every level the +/- buttons and the fit modes
 * produce, and assigning an unlisted value to a <select> blanks it out. So an
 * entry for the current level is kept alongside the presets.
 */
function syncZoomSelect() {
  const select = $('#zoomSelect');
  if (viewer.zoomMode.startsWith('fit')) {
    select.value = viewer.zoomMode;
    return;
  }
  const exact = [...select.options].find((o) => Number(o.value) === viewer.scale);
  if (exact) {
    select.value = exact.value;
    return;
  }
  let custom = select.querySelector('option[data-custom]');
  if (!custom) {
    custom = document.createElement('option');
    custom.dataset.custom = 'true';
  }
  custom.value = String(viewer.scale);
  custom.textContent = `${Math.round(viewer.scale * 100)}%`;
  // Slot it among the presets rather than after them, so the list stays in
  // ascending order and 125% does not appear below 400%.
  const next = [...select.options].find(
    (o) => !o.dataset.custom && Number(o.value) > viewer.scale,
  );
  select.insertBefore(custom, next ?? null);
  select.value = custom.value;
}

for (const button of document.querySelectorAll('[data-zoom]')) {
  button.addEventListener('click', () => viewer.nudgeZoom(button.dataset.zoom === 'in' ? 1 : -1));
}
$('#zoomSelect').addEventListener('change', (e) => viewer.setZoom(e.target.value));
viewer.addEventListener('zoom', syncZoomSelect);

$('#btnUndo').addEventListener('click', () => model.undo());
$('#btnRedo').addEventListener('click', () => model.redo());

$('#btnTheme').addEventListener('click', () => {
  const next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
  document.body.dataset.theme = next;
  setPref('theme', next);
});
document.body.dataset.theme = getPref('theme') || 'dark';

for (const tabs of document.querySelectorAll('.side-tabs')) {
  tabs.addEventListener('click', (e) => {
    const tab = e.target.closest('.side-tab');
    if (!tab) return;
    const side = tabs.closest('.side');
    for (const other of tabs.querySelectorAll('.side-tab')) other.classList.toggle('active', other === tab);
    for (const panel of side.querySelectorAll('.panel')) {
      panel.classList.toggle('active', panel.id.toLowerCase().endsWith(tab.dataset.panel));
    }
  });
}

// ---------------------------------------------------------------- saving

let autosaveTimer;
let lastSaveMs = 0;

/**
 * Autosave, paced by how long saving actually takes.
 *
 * Every save rewrites the whole annotation set, so a document with hundreds of
 * marks takes seconds. Saving again 4 seconds later would leave it writing
 * almost continuously, so heavy documents get a longer gap.
 */
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  const delay = Math.max(4000, Math.min(30000, lastSaveMs * 3));
  autosaveTimer = setTimeout(() => save({ quiet: true }), delay);
}

async function save({ quiet = false } = {}) {
  if (!model.store.docId || !model.store.dirty) return;
  if (state.saving) {
    // A save is already in flight. Dropping this one would leave the newest
    // edits unsaved until something else happened to trigger another.
    scheduleAutosave();
    return;
  }
  state.saving = true;
  $('#statusSave').textContent = '保存中…';
  const started = performance.now();
  // Snapshot what is being saved: edits made while the request is in flight
  // must not be marked clean.
  const savingCount = model.store.annots.length;
  const payload = JSON.stringify({ annots: model.store.annots });
  try {
    const response = await fetch(`/api/doc/${model.store.docId}/annots`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
    });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    lastSaveMs = performance.now() - started;
    if (model.store.annots.length === savingCount) model.markClean();
    else scheduleAutosave();  // changed mid-save; write again shortly
    if (!quiet) toast(`保存しました（注釈 ${result.written} 件をPDFに書き込み）`);
  } catch (err) {
    toast(`保存に失敗しました: ${err.message}`, 'error');
  } finally {
    state.saving = false;
    refreshPanels();
  }
}

$('#btnSave').addEventListener('click', () => save());
$('#btnDownload').addEventListener('click', async () => {
  if (!model.store.docId) return;
  await save({ quiet: true });
  window.location.href = `/api/doc/${model.store.docId}/download`;
});

window.addEventListener('beforeunload', (e) => {
  if (model.store.dirty) { e.preventDefault(); e.returnValue = ''; }
});

// ---------------------------------------------------------------- menus

async function exportAs(fmt) {
  if (!model.store.docId) return;
  await save({ quiet: true });
  const response = await fetch(`/api/doc/${model.store.docId}/export/${fmt}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ annots: model.store.annots, colourTags: COLOUR_TAGS }),
  });
  if (!response.ok) { toast('書き出しに失敗しました', 'error'); return; }
  downloadResponse(await response.blob(), response, `export.${fmt}`);
}

// Colour as meaning: the source material's point is that a colour convention
// only pays off if it survives extraction, so these tags ride along to Markdown.
const COLOUR_TAGS = {
  '#ffe14d': '重要',
  '#3fb950': '実行項目',
  '#e0403a': '要検討',
  '#2f6df6': 'メモ',
};

$('#btnMore').addEventListener('click', (e) => {
  const has = model.store.annots.length;
  openMenu(e.currentTarget, [
    { label: `注釈一覧をPDFで書き出す（${has} 件）`, disabled: !has, action: () => exportAs('summary') },
    { label: 'XFDFで書き出す（注釈だけ・軽量）', disabled: !has, action: () => exportAs('xfdf') },
    { label: 'CSVで書き出す', disabled: !has, action: () => exportAs('csv') },
    { label: 'Markdownで書き出す（色をタグとして保持）', disabled: !has, action: () => exportAs('markdown') },
    '-',
    { label: 'XFDFを読み込む', disabled: !model.store.docId, action: () => $('#xfdfInput').click() },
    '-',
    { label: '注釈をフラット化する（元に戻せません）', disabled: !has, action: flattenAnnots },
    { label: '注釈をすべて削除する', disabled: !has, action: clearAnnots },
    { note: 'フラット化・全削除の前に、自動でバックアップを取ります。' },
  ]);
});

async function flattenAnnots() {
  const ok = await confirmDialog({
    title: '注釈をフラット化しますか',
    intro: `${model.store.annots.length} 件の注釈をページの中身として焼き付けます。相手の環境で確実に同じ表示になり、読み上げにも乗ります。`,
    warning: 'フラット化した注釈は、二度と選択・移動・削除できません。実行前の状態は data/work 内にスナップショットとして自動保存されます。',
    confirmLabel: 'フラット化する',
    danger: true,
  });
  if (!ok) return;
  const result = await structural('/flatten', {}, { label: 'フラット化' });
  if (result) toast(`フラット化しました（バックアップ: ${result.backup}）`);
}

async function clearAnnots() {
  const ok = await confirmDialog({
    title: '注釈をすべて削除しますか',
    intro: `${model.store.annots.length} 件の注釈を取り除きます。ページの中身はそのまま残ります。`,
    warning: '実行前の状態は data/work 内にスナップショットとして自動保存されます。',
    confirmLabel: 'すべて削除する',
    danger: true,
  });
  if (!ok) return;
  const result = await structural('/clear-annots', {}, { label: '削除' });
  if (result) toast(`${result.removed} 件の注釈を削除しました（バックアップ: ${result.backup}）`);
}

// ---------------------------------------------------------------- document menu

/** Show the page is working. Long server work would otherwise look like a hang. */
function busy(message) {
  $('#busyText').textContent = message;
  $('#busy').hidden = false;
  return () => { $('#busy').hidden = true; };
}

/** Every layer ②/③ call reloads the file, so they share one code path. */
async function structural(url, payload, { method = 'POST', label } = {}) {
  if (!model.store.docId) return null;
  const done = busy(`${label || '処理'}しています…`);
  try {
    const response = await fetch(`/api/doc/${model.store.docId}${url}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ annots: model.store.annots, ...payload }),
    });
    if (!response.ok) {
      let detail = response.status;
      try { detail = (await response.json()).detail; } catch { /* not json */ }
      toast(`${label || '操作'}に失敗しました: ${detail}`, 'error');
      return null;
    }
    const result = await response.json();
    state.toc = result.toc || [];
    model.loadDocument(result);
    await viewer.load(`/api/doc/${model.store.docId}/file?t=${Date.now()}`);
    renderOutline($('#panelOutline'), state.toc, (page) => viewer.scrollToPage(page));
    renderThumbs($('#panelThumbs'), viewer, viewer.currentPage, (page) => viewer.scrollToPage(page));
    refreshAll();
    return result;
  } catch (err) {
    toast(`${label || '操作'}に失敗しました: ${err.message}`, 'error');
    return null;
  } finally {
    done();
  }
}

function currentPages() {
  return [viewer.currentPage];
}

$('#btnDoc').addEventListener('click', (e) => {
  const open = !!model.store.docId;
  const page = viewer.currentPage + 1;
  const redactions = model.store.annots.filter((a) => a.type === 'redact').length;
  openMenu(e.currentTarget, [
    { heading: 'ページ' },
    { label: `${page} ページ目を右に90°回転`, disabled: !open,
      action: () => structural('/pages/rotate', { pages: currentPages(), degrees: 90 }, { label: '回転' }) },
    { label: `${page} ページ目を削除`, disabled: !open, action: deletePage },
    { label: `${page} ページ目を複製`, disabled: !open,
      action: () => structural('/pages/duplicate', { pages: currentPages() }, { label: '複製' }) },
    { label: '白紙ページを後ろに挿入', disabled: !open,
      action: () => structural('/pages/blank', { at: viewer.currentPage + 1 }, { label: '挿入' }) },
    { label: '別のPDFを結合', disabled: !open, action: () => $('#mergeInput').click() },
    { label: `${page} ページ目を抽出して保存`, disabled: !open, action: extractPage },
    '-',
    { heading: '墨消し' },
    { label: `墨消しを適用する（${redactions} 箇所・元に戻せません）`,
      disabled: !redactions, action: applyRedactions },
    { label: '検索して墨消しを指定', disabled: !open, action: redactBySearch },
    { label: '非表示情報を削除（メタデータ・埋め込み等）', disabled: !open, action: scrubDocument },
    '-',
    { heading: 'ページ全体への追記' },
    { label: '透かしを入れる', disabled: !open, action: addWatermark },
    { label: 'ヘッダー・フッターを入れる', disabled: !open, action: addHeaderFooter },
    { label: 'ベイツ番号を振る', disabled: !open, action: addBates },
    '-',
    { heading: '本文・OCR' },
    { label: 'OCR（スキャンPDFを検索可能に）', disabled: !open, action: runOcr },
    { label: '本文を検索して置換', disabled: !open, action: searchReplaceText },
    '-',
    { heading: 'フォーム' },
    { label: 'フォーム欄を確認・入力', disabled: !open, action: showFields },
    { label: '罫線からフォーム欄を自動作成', disabled: !open, action: detectFields },
    { label: 'フォームデータを書き出す（FDF）', disabled: !open, action: () => exportFields('fdf') },
    { label: 'フォームデータを書き出す（CSV）', disabled: !open, action: () => exportFields('csv') },
    { label: 'フォームデータを読み込む', disabled: !open, action: () => $('#fdfInput').click() },
    { label: '複数の回答を集計する', disabled: !open, action: () => $('#collateInput').click() },
    '-',
    { heading: '文書を比べる' },
    { label: '別の版と比較（差分を雲形注釈に）', disabled: !open, action: () => startCompare('diff') },
    { label: '別の版と重ね合わせる（色分けPDF）', disabled: !open, action: () => startCompare('overlay') },
    '-',
    { heading: '署名' },
    { label: '署名を置く（手書き・タイプ・画像）', disabled: !open, action: addSignature },
    { label: '署名欄を作る（相手に署名してもらう）', disabled: !open, action: addSignatureField },
    { label: '署名の状態を確認', disabled: !open, action: showSignatureState },
    '-',
    { heading: 'アクセシビリティ' },
    { label: 'アクセシビリティを点検する', disabled: !open, action: runAccessibilityAudit },
    { label: '読み上げ順序を確認する', disabled: !open, action: showReadingOrder },
    '-',
    { heading: 'ファイル' },
    { label: 'パスワードで保護して書き出す', disabled: !open, action: protectDocument },
    { label: 'ファイルを最適化する', disabled: !open, action: optimiseDocument },
  ]);
});

// ---------------------------------------------------------------- signatures

async function addSignature() {
  const signatures = await (await fetch(`/api/doc/${model.store.docId}/signatures`)).json();
  const values = await formDialog({
    title: '署名を置く',
    intro: `${viewer.currentPage + 1} ページ目の右下に置きます。`,
    warning: signatures.note,
    fields: [
      { key: 'kind', label: '種類', type: 'select',
        options: { typed: 'タイプ（フォントで生成）', drawn: '手書き風' } },
      { key: 'name', label: '氏名', value: getPref('author') || '' },
      { key: 'size', label: '文字サイズ', type: 'number', value: 20, min: 8, max: 48 },
      { key: 'colour', label: '色', type: 'colour', value: '#12305e' },
      { key: 'block', label: '日時などを添える', type: 'checkbox', value: true },
      { key: 'reason', label: '理由', placeholder: '例: 内容確認のため' },
    ],
    confirmLabel: '置く',
  });
  if (!values || !values.name.trim()) return;

  const page = model.store.pages[viewer.currentPage];
  const rect = [page.width - 250, page.height - 150, page.width - 60, page.height - 110];
  const payload = { ...values, page: viewer.currentPage, rect };
  if (values.kind === 'drawn') {
    // A quick script-like path so a typed name can still look handwritten.
    payload.strokes = [{ pts: handwritingPath(values.name, rect) }];
    payload.width = 1.6;
  }
  const result = await structural('/sign', payload, { label: '署名' });
  if (result) toast(`署名を置きました（バックアップ: ${result.backup}）`);
}

/** A wavy baseline through the signature box — a stand-in for a real hand. */
function handwritingPath(name, rect) {
  const [x0, y0, x1, y1] = rect;
  const midY = (y0 + y1) / 2;
  const points = [];
  const steps = Math.max(40, name.length * 12);
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    points.push([
      x0 + t * (x1 - x0),
      midY + Math.sin(t * Math.PI * name.length) * (y1 - y0) * 0.28
        + Math.sin(t * Math.PI * 2) * (y1 - y0) * 0.12,
    ]);
  }
  return points;
}

async function addSignatureField() {
  const values = await formDialog({
    title: '署名欄を作る',
    intro: '空の署名欄を置きます。この文書を受け取った人が、対応するビューアで署名できます。',
    fields: [{ key: 'name', label: '欄の名前', value: '承認者' }],
    confirmLabel: '作る',
  });
  if (!values) return;
  const page = model.store.pages[viewer.currentPage];
  const rect = [page.width - 250, page.height - 150, page.width - 60, page.height - 110];
  const result = await structural('/sign', {
    kind: 'field', name: values.name, page: viewer.currentPage, rect,
  }, { label: '署名欄' });
  if (result) toast('署名欄を作りました');
}

async function showSignatureState() {
  const signatures = await (await fetch(`/api/doc/${model.store.docId}/signatures`)).json();
  const lines = signatures.fields.length
    ? signatures.fields
      .map((f) => `・${f.name || '(無名)'} — ${f.page + 1}ページ・${f.signed ? '署名済み' : '未署名'}`)
      .join('\n')
    : '署名欄はありません。';
  await confirmDialog({
    title: '署名の状態',
    intro: lines,
    warning: signatures.note,
    confirmLabel: '閉じる',
  });
}

// ---------------------------------------------------------------- accessibility

const SEVERITY = { high: '重要', medium: '中', low: '軽微' };

async function runAccessibilityAudit() {
  const report = await (await fetch(`/api/doc/${model.store.docId}/accessibility`)).json();
  if (report.passed) {
    await confirmDialog({
      title: 'アクセシビリティ点検',
      intro: '問題は見つかりませんでした。タグ・言語・代替テキストがそろっています。',
      confirmLabel: '閉じる',
    });
    return;
  }
  const body = report.issues
    .map((i) => `【${SEVERITY[i.severity] || i.severity}】${i.title}\n  ${i.detail}\n  → ${i.fix}`)
    .join('\n\n');
  const needsTags = report.issues.some((i) => i.id === 'tags' || i.id === 'lang');
  const ok = await confirmDialog({
    title: `アクセシビリティの問題が ${report.issues.length} 件`,
    intro: body,
    warning: needsTags
      ? 'タグと言語は「自動でタグを付ける」で一度に直せます。文字サイズから見出しを推定するので、結果は確認してください。'
      : undefined,
    confirmLabel: needsTags ? '自動でタグを付ける' : '閉じる',
  });
  if (!ok || !needsTags) return;

  const result = await structural('/accessibility/autotag', { language: 'ja-JP' },
    { label: 'タグ付け' });
  if (!result) return;
  const remaining = result.audit?.issues?.length || 0;
  toast(remaining
    ? `${result.elements} 要素にタグを付けました。残りの指摘は ${remaining} 件です`
    : `${result.elements} 要素にタグを付けました（見出し ${result.headings} 個）`);
}

async function showReadingOrder() {
  const data = await (await fetch(
    `/api/doc/${model.store.docId}/accessibility/order/${viewer.currentPage}`,
  )).json();
  if (!data.blocks.length) {
    toast('このページにはテキストがありません。OCRが必要かもしれません', 'warn');
    return;
  }
  // Draw the order on the page rather than only listing it: seeing the path is
  // how you notice that a sidebar gets read in the middle of a paragraph.
  const view = viewer.pageViews[viewer.currentPage];
  const layer = view.draw;
  for (const node of layer.querySelectorAll('.order-mark')) node.remove();
  const ns = 'http://www.w3.org/2000/svg';
  const path = document.createElementNS(ns, 'polyline');
  path.setAttribute('class', 'order-mark');
  path.setAttribute('points', data.blocks
    .map((b) => `${(b.rect[0] + b.rect[2]) / 2},${(b.rect[1] + b.rect[3]) / 2}`).join(' '));
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', '#2f6df6');
  path.setAttribute('stroke-width', '1.5');
  path.setAttribute('stroke-dasharray', '5 3');
  layer.append(path);
  for (const block of data.blocks) {
    const badge = document.createElementNS(ns, 'g');
    badge.setAttribute('class', 'order-mark');
    const cx = (block.rect[0] + block.rect[2]) / 2;
    const cy = (block.rect[1] + block.rect[3]) / 2;
    const circle = document.createElementNS(ns, 'circle');
    circle.setAttribute('cx', cx); circle.setAttribute('cy', cy);
    circle.setAttribute('r', '9'); circle.setAttribute('fill', '#2f6df6');
    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', cx); text.setAttribute('y', cy);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dominant-baseline', 'central');
    text.setAttribute('fill', '#fff');
    text.setAttribute('font-size', '10');
    text.textContent = String(block.order);
    badge.append(circle, text);
    layer.append(badge);
  }
  toast(`読み上げ順序を ${data.blocks.length} ブロック分表示しました（Escで消えます）`);
}

// ---------------------------------------------------------------- forms

async function showFields() {
  const data = await (await fetch(`/api/doc/${model.store.docId}/fields`)).json();
  if (data.hasXfa) {
    await confirmDialog({
      title: 'XFAフォームです',
      intro: 'この文書はAdobe独自のXFA形式のフォームを含んでいます。XFAはPDF 2.0で廃止されており、Acrobat以外のビューアでは開けません。このアプリも対応していません。',
      warning: '「フォームが開けない」という現象の主な原因がこれです。作成側にAcroForm形式での再出力を依頼してください。',
      confirmLabel: '閉じる',
    });
    return;
  }
  if (!data.fields.length) {
    toast('この文書にはフォーム欄がありません。「罫線からフォーム欄を自動作成」を試せます', 'warn');
    return;
  }
  const fillable = data.fields.filter((f) => !['button', 'signature'].includes(f.type));
  const values = await formDialog({
    title: 'フォーム入力',
    intro: `${data.fields.length} 個のフォーム欄があります。`,
    fields: fillable.map((field) => ({
      key: field.name,
      label: `${field.name}${field.required ? ' *' : ''}`,
      type: field.type === 'checkbox' ? 'checkbox'
        : field.type === 'dropdown' || field.type === 'list' ? 'select' : 'text',
      options: field.options?.length
        ? Object.fromEntries(field.options.map((o) => [o, o])) : undefined,
      value: field.type === 'checkbox' ? field.value === 'Yes' : (field.value ?? ''),
    })),
    confirmLabel: '入力する',
  });
  if (!values) return;
  const payload = {};
  for (const field of fillable) {
    const value = values[field.name];
    payload[field.name] = field.type === 'checkbox' ? (value ? 'Yes' : 'Off') : value;
  }
  const response = await fetch(`/api/doc/${model.store.docId}/fields/fill`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values: payload }),
  });
  if (!response.ok) { toast('入力に失敗しました', 'error'); return; }
  const result = await response.json();
  await viewer.load(`/api/doc/${model.store.docId}/file?t=${Date.now()}`);
  refreshAll();
  toast(`${result.filled} 個のフォーム欄に入力しました`);
}

async function detectFields() {
  const preview = await (await fetch(`/api/doc/${model.store.docId}/fields/detect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page: viewer.currentPage }),
  })).json();
  const count = preview.candidates?.length || 0;
  if (!count) { toast('フォーム欄にできそうな罫線が見つかりませんでした', 'warn'); return; }
  const ok = await confirmDialog({
    title: 'フォーム欄を自動作成しますか',
    intro: `${viewer.currentPage + 1} ページ目の罫線や枠から、${count} 個の入力欄を作成できそうです。`,
    warning: '自動判定なので、不要な欄ができることがあります。作成後に個別に消せます。',
    confirmLabel: '作成する',
  });
  if (!ok) return;
  const response = await fetch(`/api/doc/${model.store.docId}/fields/detect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page: viewer.currentPage, create: true }),
  });
  const result = await response.json();
  await viewer.load(`/api/doc/${model.store.docId}/file?t=${Date.now()}`);
  refreshAll();
  toast(`${result.created} 個のフォーム欄を作成しました`);
}

async function exportFields(fmt) {
  const response = await fetch(`/api/doc/${model.store.docId}/fields/export/${fmt}`, { method: 'POST' });
  if (!response.ok) { toast('書き出しに失敗しました', 'error'); return; }
  downloadResponse(await response.blob(), response, `fields.${fmt}`);
}

$('#fdfInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`/api/doc/${model.store.docId}/fields/import`, {
    method: 'POST', body: form,
  });
  if (!response.ok) { toast('読み込めませんでした', 'error'); return; }
  const result = await response.json();
  await viewer.load(`/api/doc/${model.store.docId}/file?t=${Date.now()}`);
  refreshAll();
  toast(`${result.filled} 個のフォーム欄に読み込みました`);
});

$('#collateInput').addEventListener('change', async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  if (!files.length) return;
  const form = new FormData();
  for (const file of files) form.append('files', file);
  const response = await fetch(`/api/doc/${model.store.docId}/fields/collate`, {
    method: 'POST', body: form,
  });
  if (!response.ok) { toast('集計に失敗しました', 'error'); return; }
  downloadResponse(await response.blob(), response, 'collated.csv');
});

// ---------------------------------------------------------------- comparison

let compareMode = 'diff';

function startCompare(mode) {
  compareMode = mode;
  $('#compareInput').click();
}

$('#compareInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || !model.store.docId) return;
  await save({ quiet: true });
  toast('比較しています…');
  const form = new FormData();
  form.append('file', file);
  const url = `/api/doc/${model.store.docId}/compare?mode=${compareMode}`
    + `&author=${encodeURIComponent(getPref('author') || '')}`;
  const response = await fetch(url, { method: 'POST', body: form });
  if (!response.ok) { toast('比較に失敗しました', 'error'); return; }

  if (compareMode === 'overlay') {
    downloadResponse(await response.blob(), response, 'overlay.pdf');
    return;
  }
  const result = await response.json();
  if (!result.differences) { toast('差分は見つかりませんでした'); return; }
  model.addAnnots(result.annots, { select: false });
  state.filters.type = 'square';
  scheduleAutosave();
  toast(`${result.differences} 箇所の差分を雲形注釈にしました（コメント欄で1件ずつ確認できます）`);
});

async function deletePage() {
  const ok = await confirmDialog({
    title: `${viewer.currentPage + 1} ページ目を削除しますか`,
    intro: 'このページと、そのページ上の注釈がなくなります。',
    warning: '実行前の状態はスナップショットとして自動保存されます。',
    confirmLabel: '削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/pages/delete', { pages: currentPages() }, { label: 'ページ削除' });
  if (result) toast(`ページを削除しました（バックアップ: ${result.backup}）`);
}

async function extractPage() {
  await save({ quiet: true });
  const response = await fetch(`/api/doc/${model.store.docId}/pages/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pages: currentPages() }),
  });
  if (!response.ok) { toast('抽出に失敗しました', 'error'); return; }
  downloadResponse(await response.blob(), response, 'extract.pdf');
}

$('#mergeInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || !model.store.docId) return;
  await save({ quiet: true });
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`/api/doc/${model.store.docId}/merge`, { method: 'POST', body: form });
  if (!response.ok) { toast('結合に失敗しました', 'error'); return; }
  const result = await response.json();
  state.toc = result.toc || [];
  model.loadDocument(result);
  await viewer.load(`/api/doc/${model.store.docId}/file?t=${Date.now()}`);
  renderThumbs($('#panelThumbs'), viewer, viewer.currentPage, (p) => viewer.scrollToPage(p));
  refreshAll();
  toast(`${result.added} ページを結合しました`);
});

async function applyRedactions() {
  const count = model.store.annots.filter((a) => a.type === 'redact').length;
  const ok = await confirmDialog({
    title: '墨消しを適用しますか',
    intro: `${count} 箇所の墨消し指定を適用し、下にある文字と画像をファイルから実際に削除します。`,
    warning: 'これは取り消せません。適用するまでは、黒く見えていても文字はコピーすれば読める状態です。実行前の状態はスナップショットとして自動保存されます。',
    confirmLabel: '適用して削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/redact/apply', { images: true }, { label: '墨消しの適用' });
  if (result) toast(`${result.applied} 箇所を削除しました（バックアップ: ${result.backup}）`);
}

async function redactBySearch() {
  const values = await formDialog({
    title: '検索して墨消しを指定',
    intro: '一致した箇所すべてに墨消しの指定を付けます。適用は別操作です。',
    fields: [
      { key: 'query', label: '検索する文字列', placeholder: '例: 山田太郎' },
      { key: 'overlay', label: '黒塗りの上に出す文字', placeholder: '例: ［非開示］', hint: '空欄なら文字は出ません' },
    ],
    confirmLabel: '指定する',
  });
  if (!values || !values.query.trim()) return;
  const result = await structural('/redact/search', values, { label: '墨消しの指定' });
  if (result) {
    toast(result.marked
      ? `${result.marked} 箇所に墨消しを指定しました。「墨消しを適用」で実際に削除されます`
      : '一致する文字列が見つかりませんでした', result.marked ? '' : 'warn');
  }
}

async function scrubDocument() {
  const ok = await confirmDialog({
    title: '非表示情報を削除しますか',
    intro: '画面に出ないままファイルに残っている情報を取り除きます: メタデータ、埋め込みファイル、非表示テキスト、JavaScript、サムネイルなど。',
    warning: '取り消せません。実行前の状態はスナップショットとして自動保存されます。',
    confirmLabel: '削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/scrub', {}, { label: '非表示情報の削除' });
  if (result) toast(`非表示情報を削除しました（バックアップ: ${result.backup}）`);
}

async function addWatermark() {
  const values = await formDialog({
    title: '透かしを入れる',
    fields: [
      { key: 'text', label: '文字', value: '社外秘' },
      { key: 'colour', label: '色', type: 'colour', value: '#c0c0c0' },
      { key: 'size', label: 'サイズ', type: 'number', value: 54, min: 8, max: 200 },
      { key: 'opacity', label: '不透明度', type: 'number', value: 0.25, min: 0.05, max: 1, step: 0.05 },
      { key: 'angle', label: '角度', type: 'number', value: 45, min: -90, max: 90 },
      { key: 'allPages', label: '全ページに入れる', type: 'checkbox', value: true },
    ],
    confirmLabel: '入れる',
  });
  if (!values || !values.text.trim()) return;
  const result = await structural('/stamp-pages', {
    kind: 'watermark', ...values,
    pages: values.allPages ? null : currentPages(),
  }, { label: '透かし' });
  if (result) toast(`透かしを入れました（バックアップ: ${result.backup}）`);
}

async function addHeaderFooter() {
  const values = await formDialog({
    title: 'ヘッダー・フッターを入れる',
    intro: '{page} は現在のページ番号、{pages} は総ページ数に置き換わります。',
    fields: [
      { key: 'header', label: 'ヘッダー', placeholder: '例: レビュー用' },
      { key: 'footer', label: 'フッター', value: '- {page} / {pages} -' },
      { key: 'size', label: 'サイズ', type: 'number', value: 9, min: 5, max: 24 },
      { key: 'colour', label: '色', type: 'colour', value: '#555555' },
    ],
    confirmLabel: '入れる',
  });
  if (!values || (!values.header.trim() && !values.footer.trim())) return;
  const result = await structural('/stamp-pages', { kind: 'headerFooter', ...values },
    { label: 'ヘッダー・フッター' });
  if (result) toast(`ヘッダー・フッターを入れました（バックアップ: ${result.backup}）`);
}

async function addBates() {
  const values = await formDialog({
    title: 'ベイツ番号を振る',
    intro: '文書をまたいで連続させる通し番号です。訴訟文書の管理で使われます。',
    fields: [
      { key: 'prefix', label: '接頭辞', placeholder: '例: ABC-' },
      { key: 'start', label: '開始番号', type: 'number', value: 1, min: 0 },
      { key: 'digits', label: '桁数', type: 'number', value: 6, min: 1, max: 12 },
      { key: 'suffix', label: '接尾辞', placeholder: '' },
    ],
    confirmLabel: '振る',
  });
  if (!values) return;
  const result = await structural('/stamp-pages', { kind: 'bates', ...values }, { label: 'ベイツ番号' });
  if (result) toast(`ベイツ番号を振りました（バックアップ: ${result.backup}）`);
}

async function runOcr() {
  const status = await (await fetch('/api/ocr/status')).json();
  if (!status.installed) {
    await confirmDialog({
      title: 'Tesseract OCR が導入されていません',
      intro: 'OCR には Tesseract OCR が必要です。README の導入手順に沿ってインストールしてください。',
      warning: '既定のインストール先（C:\\Program Files\\Tesseract-OCR）を変えないでください。',
      confirmLabel: '閉じる',
    });
    return;
  }
  const langs = {};
  if (status.japanese) langs['jpn+eng'] = '日本語 + 英語';
  langs.eng = '英語のみ';
  if (status.japanese) langs.jpn = '日本語のみ';

  const values = await formDialog({
    title: 'OCR（スキャンPDFを検索可能に）',
    intro: 'テキストが入っていないページだけを処理します。認識した文字は画像の上に見えない層として埋め込まれ、検索・選択できるようになります。',
    fields: [
      { key: 'language', label: '言語', type: 'select', options: langs },
      { key: 'layout', label: 'レイアウト', type: 'select',
        options: { block: '通常の文書（推奨）', auto: '段組みのある文書', line: '1行だけ', sparse: 'まばらな文字' },
        hint: '日本語と英字が混ざる文書では「通常の文書」の方が精度が高いことを確認しています' },
      { key: 'dpi', label: '解像度(dpi)', type: 'number', value: 300, min: 150, max: 600, step: 50 },
      { key: 'allPages', label: '全ページを対象にする', type: 'checkbox', value: true },
    ],
    confirmLabel: '実行する',
  });
  if (!values) return;
  toast('OCRを実行しています…（ページ数によっては時間がかかります）');
  const result = await structural('/ocr', {
    ...values, pages: values.allPages ? null : currentPages(),
  }, { label: 'OCR' });
  if (result) {
    toast(result.pages
      ? `${result.pages} ページをOCRしました（${result.characters} 文字・バックアップ: ${result.backup}）`
      : `テキストが既に入っているため処理しませんでした（${result.skipped} ページ）`,
      result.pages ? '' : 'warn');
  }
}

async function searchReplaceText() {
  const values = await formDialog({
    title: '本文を検索して置換',
    intro: '注釈ではなく、ページの中身そのものを書き換えます。',
    warning: '元の文字は墨消しで実際に削除されてから、新しい文字が置かれます。取り消せません。',
    fields: [
      { key: 'query', label: '検索する文字列' },
      { key: 'replacement', label: '置き換える文字列' },
      { key: 'colour', label: '文字色', type: 'colour', value: '#000000' },
    ],
    confirmLabel: '置換する', danger: true,
  });
  if (!values || !values.query.trim()) return;
  const result = await structural('/text/search-replace', values, { label: '置換' });
  if (result) {
    toast(result.replaced
      ? `${result.replaced} 箇所を置換しました（バックアップ: ${result.backup}）`
      : '一致する文字列が見つかりませんでした', result.replaced ? '' : 'warn');
  }
}

async function protectDocument() {
  const values = await formDialog({
    title: 'パスワードで保護して書き出す',
    intro: '保護をかけた別ファイルとして書き出します。編集中の文書はそのままです。',
    fields: [
      { key: 'userPassword', label: '開くためのパスワード', type: 'password' },
      { key: 'ownerPassword', label: '権限変更用パスワード', type: 'password',
        hint: '開く用と別にしてください。同じにすると権限の制限が効きません' },
      { key: 'print', label: '印刷を許可', type: 'checkbox', value: true },
      { key: 'copy', label: 'コピーを許可', type: 'checkbox', value: true },
      { key: 'modify', label: '編集を許可', type: 'checkbox', value: false },
      { key: 'annotate', label: '注釈を許可', type: 'checkbox', value: true },
    ],
    confirmLabel: '書き出す',
  });
  if (!values) return;
  if (!values.userPassword && !values.ownerPassword) {
    toast('パスワードを入力してください', 'warn');
    return;
  }
  await save({ quiet: true });
  const response = await fetch(`/api/doc/${model.store.docId}/protect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      annots: model.store.annots,
      userPassword: values.userPassword,
      ownerPassword: values.ownerPassword,
      permissions: {
        print: values.print, copy: values.copy,
        modify: values.modify, annotate: values.annotate,
      },
    }),
  });
  if (!response.ok) { toast('保護に失敗しました', 'error'); return; }
  downloadResponse(await response.blob(), response, 'protected.pdf');
}

async function optimiseDocument() {
  toast('最適化しています…');
  const result = await structural('/optimise', {}, { label: '最適化' });
  if (!result) return;
  const kb = (n) => `${Math.round(n / 1024)}KB`;
  toast(result.saved > 1024
    ? `最適化しました: ${kb(result.before)} → ${kb(result.actual)}`
    : `すでに最適化済みでした（${kb(result.actual)}）`);
}

function downloadResponse(blob, response, fallback) {
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : fallback;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
  toast(`${name} を書き出しました`);
}

$('#xfdfInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file || !model.store.docId) return;
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`/api/doc/${model.store.docId}/import-xfdf`, {
    method: 'POST', body: form,
  });
  if (!response.ok) { toast('XFDFを読み込めませんでした', 'error'); return; }
  const { annots: incoming } = await response.json();
  if (!incoming.length) { toast('XFDFに注釈が入っていませんでした', 'warn'); return; }
  model.addAnnots(incoming, { select: false });
  scheduleAutosave();
  toast(`${incoming.length} 件の注釈を取り込みました`);
});

// ---------------------------------------------------------------- search

async function runSearch() {
  const query = $('#searchInput').value.trim();
  if (!query || !model.store.docId) {
    state.searchHits = [];
    state.searchIndex = -1;
    $('#searchCount').textContent = '';
    drawSearchHits();
    return;
  }
  const response = await fetch(`/api/doc/${model.store.docId}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  const data = await response.json();
  state.searchHits = data.hits || [];
  state.searchIndex = state.searchHits.length ? 0 : -1;
  $('#searchCount').textContent = state.searchHits.length ? `${state.searchHits.length} 件` : '該当なし';
  drawSearchHits();
  if (state.searchIndex >= 0) goToHit(0);
}

function drawSearchHits() {
  for (const view of viewer.pageViews) {
    for (const node of view.draw.querySelectorAll('.search-hit')) node.remove();
  }
  state.searchHits.forEach((hit, index) => {
    const view = viewer.pageViews[hit.page];
    if (!view) return;
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('class', `search-hit${index === state.searchIndex ? ' current' : ''}`);
    rect.setAttribute('x', hit.rect[0]);
    rect.setAttribute('y', hit.rect[1]);
    rect.setAttribute('width', hit.rect[2] - hit.rect[0]);
    rect.setAttribute('height', hit.rect[3] - hit.rect[1]);
    view.draw.append(rect);
  });
}

function goToHit(index) {
  if (!state.searchHits.length) return;
  state.searchIndex = (index + state.searchHits.length) % state.searchHits.length;
  const hit = state.searchHits[state.searchIndex];
  viewer.scrollToPage(hit.page, hit.rect[1]);
  drawSearchHits();
  $('#searchCount').textContent = `${state.searchIndex + 1} / ${state.searchHits.length}`;
}

$('#searchInput').addEventListener('change', runSearch);
$('#searchInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); state.searchHits.length ? goToHit(state.searchIndex + 1) : runSearch(); }
});
$('#btnSearchNext').addEventListener('click', () => goToHit(state.searchIndex + 1));
$('#btnSearchPrev').addEventListener('click', () => goToHit(state.searchIndex - 1));

$('#btnSearchMarkup').addEventListener('click', () => {
  if (!state.searchHits.length) { toast('先に検索してください', 'warn'); return; }
  const markupTools = ['highlight', 'underline', 'squiggly', 'strikeout'];
  const tool = markupTools.includes(tools.tool) ? tools.tool : 'highlight';
  const byPage = new Map();
  for (const hit of state.searchHits) {
    if (!byPage.has(hit.page)) byPage.set(hit.page, []);
    byPage.get(hit.page).push(hit.quad);
  }
  const items = [];
  for (const [page, quads] of byPage) {
    for (const quad of quads) {
      const xs = [quad[0], quad[2], quad[4], quad[6]];
      const ys = [quad[1], quad[3], quad[5], quad[7]];
      items.push({
        type: tool, page, quads: [quad],
        rect: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
        style: tools.style,
        author: getPref('author') || '',
        subject: `検索一括: ${$('#searchInput').value}`,
        flags: { print: true, locked: false, readOnly: false, hidden: false },
      });
    }
  }
  model.addAnnots(items, { select: false });
  toast(`${items.length} 箇所に${typeLabel({ type: tool })}を適用しました`);
  scheduleAutosave();
});

// ---------------------------------------------------------------- measuring

function startCalibration() {
  selectTool('calibrate');
  toast('図面上で、実寸のわかっている2点をクリックしてください');
}

tools.addEventListener('calibrated', async (e) => {
  const [p1, p2] = e.detail.points;
  const pagePoints = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
  const values = await formDialog({
    title: '縮尺を設定',
    intro: `なぞった長さは ${pagePoints.toFixed(1)} pt でした。これが実際に何メートル（何ミリ）にあたるかを入力してください。`,
    fields: [
      { key: 'realLength', label: '実際の長さ', type: 'number', value: 1, min: 0.0001, step: 0.01 },
      { key: 'unit', label: '単位', type: 'select', options: measure.UNIT_LABELS,
        value: measure.getScale().unit },
    ],
    confirmLabel: '設定する',
  });
  selectTool('select');
  if (!values || !(values.realLength > 0)) return;
  measure.calibrateFrom(p1, p2, values.realLength, values.unit);
  refreshPanels();
  const f = measure.factor();
  toast(`縮尺を設定しました（1 pt = ${f.toPrecision(4)} ${values.unit}）`);
});

tools.addEventListener('measured', () => {
  if (!measure.isCalibrated()) {
    toast('縮尺が未設定のため、値はページ上の長さのままです', 'warn');
  }
});

async function exportTakeoff() {
  if (!model.store.docId) return;
  const response = await fetch(`/api/doc/${model.store.docId}/takeoff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ annots: model.store.annots, csv: true }),
  });
  if (!response.ok) { toast('書き出しに失敗しました', 'error'); return; }
  downloadResponse(await response.blob(), response, 'takeoff.csv');
}

/** Draw the legend as annotations, so it moves and prints with the markup. */
function placeLegend() {
  const rows = measure.summarise(model.store.annots);
  if (!rows.length) return;
  const page = viewer.currentPage;
  const width = 210;
  const lineHeight = 15;
  const x = 40;
  let y = 40;
  const items = [{
    type: 'square', page,
    rect: [x - 8, y - 8, x + width, y + rows.length * lineHeight + 16],
    style: { stroke: '#1c1f26', fill: '#ffffff', width: 1, opacity: 0.95, cloudIntensity: 0 },
    subject: '凡例',
    author: getPref('author') || '',
    flags: { print: true, locked: false, readOnly: false, hidden: false },
  }];
  for (const row of rows) {
    items.push({
      type: 'square', page,
      rect: [x, y + 2, x + 10, y + 12],
      style: { stroke: row.colour, fill: row.colour, width: 1, opacity: 1 },
      subject: '凡例',
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    });
    const total = row.kind === 'count' ? `${row.count} 個` : `${row.count} 件 / ${row.total} ${row.unit}`;
    items.push({
      type: 'freetext', page,
      rect: [x + 16, y, x + width - 4, y + 14],
      text: `${row.label}  ${total}`,
      style: {
        stroke: '#1c1f26', fill: null, width: 0, opacity: 1,
        font: { family: 'japan', size: 9, color: '#1c1f26', align: 'left' },
      },
      subject: '凡例',
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    });
    y += lineHeight;
  }
  model.addAnnots(items, { select: false });
  viewer.scrollToPage(page, 0);
  scheduleAutosave();
  toast(`凡例を ${page + 1} ページ目に置きました`);
}

// ---------------------------------------------------------------- inline text editing

let editor = null;

tools.addEventListener('edit-text', (e) => startTextEdit(e.detail.id, e.detail.isNew));

function startTextEdit(id, isNew = false) {
  closeEditor();
  const annot = model.byId(id);
  if (!annot) return;
  const view = viewer.pageViews[annot.page];
  if (!view) return;

  const isNote = annot.type === 'note';
  const scale = viewer.scale;
  const [x0, y0, x1, y1] = annot.rect;

  const box = document.createElement('textarea');
  box.className = 'ft-editor';
  box.value = (isNote ? annot.contents : annot.text) || '';
  box.style.left = `${(isNote ? x1 + 4 : x0) * scale}px`;
  box.style.top = `${y0 * scale}px`;
  box.style.width = `${(isNote ? 200 : Math.max(60, x1 - x0)) * scale}px`;
  box.style.height = `${(isNote ? 70 : Math.max(20, y1 - y0)) * scale}px`;
  box.style.fontSize = `${(annot.style?.font?.size || 12) * scale}px`;
  box.style.textAlign = annot.style?.font?.align || 'left';

  view.wrap.append(box);
  box.focus();
  box.select();

  editor = { box, id, isNew, isNote };

  box.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeEditor({ cancel: true }); }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); closeEditor(); }
    event.stopPropagation();
  });
  box.addEventListener('blur', () => closeEditor());
}

function closeEditor({ cancel = false } = {}) {
  if (!editor) return;
  const { box, id, isNew, isNote } = editor;
  editor = null;
  const value = box.value;
  box.remove();
  const annot = model.byId(id);
  if (!annot) return;
  if (cancel && isNew && !value) { model.removeAnnots([id]); return; }
  if (isNew && !value.trim() && !isNote) { model.removeAnnots([id]); return; }
  model.updateAnnots([id], isNote ? { contents: value } : { text: value, contents: value });
  scheduleAutosave();
}

// ---------------------------------------------------------------- keyboard

const SHORTCUTS = {
  v: 'select', h: 'highlight', u: 'underline', k: 'strikeout', t: 'freetext',
  n: 'note', p: 'pen', e: 'eraser', l: 'lasso', i: 'line', r: 'square', c: 'circle',
};

window.addEventListener('keydown', (e) => {
  if (editor) return;
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
  const mod = e.ctrlKey || e.metaKey;

  if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
  if (mod && e.key.toLowerCase() === 'o') { e.preventDefault(); $('#fileInput').click(); return; }
  if (mod && e.key.toLowerCase() === 'f') { e.preventDefault(); $('#searchInput').focus(); return; }
  if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); model.undo(); return; }
  if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
    e.preventDefault(); model.redo(); return;
  }
  if (mod && e.key.toLowerCase() === 'a' && !typing) {
    e.preventDefault();
    model.select(model.onPage(viewer.currentPage).map((a) => a.id));
    return;
  }
  if (typing) return;

  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (model.store.selection.length) {
      e.preventDefault();
      model.removeAnnots(model.store.selection);
      scheduleAutosave();
    }
    return;
  }
  if (e.key === 'Escape') {
    for (const node of document.querySelectorAll('.order-mark')) node.remove();
    tools.cancelPoly();
    tools.cancelMeasure();
    model.select([]);
    return;
  }
  if (e.key === 'Enter' && tools.measuring) { e.preventDefault(); tools.finishMeasure(); return; }
  if (e.key === 'Enter' && tools.poly) { tools._finishPoly(); return; }

  const tool = SHORTCUTS[e.key.toLowerCase()];
  if (tool && !mod) { e.preventDefault(); selectTool(tool); }
});

status('準備完了 — PDFを開いてください');
