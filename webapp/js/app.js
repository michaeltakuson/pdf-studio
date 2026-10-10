import {
  $, stage, viewer, tools, state, hooks, toast, status, busy, paint, post, docUrl, structural,
  reloadFrom, errorDetail, downloadBlob, exportVia, finishedPdf, targetPages, describePages,
} from './ctx.js';
import * as model from './model.js';
import { renderPage } from './render.js';
import {
  renderFontGroup, renderStyleGroup, renderProps, renderComments, renderThumbs, updateThumbs,
  scrollThumbIntoView, renderOutline, renderSettings, renderTakeoff, refreshPanel, typeLabel, h, SHORTCUTS,
} from './panels.js';
import * as measure from './measure.js';
import { remember, getPref, setPref, styleFor } from './defaults.js';
import {
  confirmDialog, formDialog, infoDialog, customDialog, openMenu, openMenuAt, isComposing, node,
} from './dialogs.js';
import { Ribbon } from './ribbon.js';
import { iconSvg } from './icons.js';
import { TextEditor, fitRect, ensureFontLoaded, LINE_HEIGHT } from './textedit.js';
import { MARKUP_TOOLS, translated } from './tools.js';
import { signatureDialog, hankoDialog, normaliseImage, fileToDataUrl } from './stamps.js';
import * as ops from './docops.js';
import { keyFor, saveDraft, loadDraft, clearDraft, pruneDrafts } from './drafts.js';
import { rememberFile, recentFiles, forgetFile, SNIPPET_FIELDS, loadSnippets, saveSnippets } from './recent.js';

for (const holder of document.querySelectorAll('[data-icon]')) {
  holder.innerHTML = iconSvg(holder.dataset.icon, holder.classList.contains('small') || holder.classList.contains('sb-btn') ? 16 : 18);
}

const hasDoc = () => !!model.store.docId;
const FLAGS = () => ({ print: true, locked: false, readOnly: false, hidden: false });
const isTextTool = (tool) => tool === 'freetext' || tool === 'callout';

// ================================================================ opening

async function confirmDiscard() {
  if (!state.unsaved) return true;
  return confirmDialog({
    title: '保存していない変更があります',
    intro: `「${model.store.name}」への変更はまだファイルに保存されていません。このまま進むと失われます。`,
    confirmLabel: '保存せずに進む', cancelLabel: '戻る', danger: true,
  });
}

async function adopt(data, { handle = null, message = null, draftKey = null, keepPosition = false } = {}) {
  const previous = model.store.docId;
  flushEditing();
  clearTimeout(draftTimer);
  state.draftKey = draftKey;
  state.fileHandle = handle;
  state.toc = data.toc || [];
  state.pageSelection.clear();
  state.textLines.clear();
  state.searchHits = [];
  state.searchIndex = -1;
  $('#searchCount').textContent = '';
  model.loadDocument(data);
  $('#emptyState').classList.add('hidden');
  await viewer.load(`${docUrl('/file')}?t=${Date.now()}`, { keepPosition });
  state.unsaved = false;
  afterReload();
  refreshAll();
  if (handle) rememberFile(handle).then(showRecent);
  if (previous && previous !== data.id) {
    fetch(`/api/doc/${previous}/close`, { method: 'POST' }).catch(() => {});
  }
  // Pick up where the reader left off in this file last time.
  let resumed = '';
  try {
    const page = keepPosition ? 0 : Number(localStorage.getItem(positionKey()) || 0);
    if (page > 0 && page < model.store.pages.length) {
      viewer.scrollToPage(page);
      resumed = `（前回の続き ${page + 1} ページ目から）`;
    }
  } catch { /* storage blocked */ }
  toast(message || `${data.name} を開きました${resumed}`);
  // Asked once the "opening…" indicator is out of the way; awaiting it here
  // would leave the question sitting underneath the indicator, unanswerable.
  setTimeout(() => offerDraft(data), 60);
}

// ---------------------------------------------------------------- crash recovery

let draftTimer;

/** Copy the markup into browser storage shortly after it changes. */
function scheduleDraft() {
  clearTimeout(draftTimer);
  if (!state.draftKey || !hasDoc()) return;
  draftTimer = setTimeout(() => {
    if (!state.unsaved || model.history.structuralDepth > 0) return;
    // A text box that is open and still empty is not content yet.
    // Text still being typed lives in the editor, not the model yet.
    const typing = state.editor && !state.editor.meta.isLine ? { id: state.editor.meta.id, text: state.editor.text } : null;
    const keep = model.store.annots
      .map((item) => (typing && item.id === typing.id ? { ...item, text: typing.text, contents: typing.text } : item))
      .filter((item) => item.type !== 'freetext' || (item.text || '').length);
    saveDraft(state.draftKey, structuredClone(keep), model.store.pages.length);
  }, 2500);
}

async function offerDraft(data) {
  const draft = await loadDraft(state.draftKey);
  if (!draft || !draft.annots?.length || draft.pageCount !== model.store.pages.length) return;
  const strip = (items) => JSON.stringify(items.map((item) => {
    const { xref, modified, created, ...rest } = item;
    void xref; void modified; void created;
    return rest;
  }));
  if (strip(draft.annots) === strip(model.store.annots)) { clearDraft(state.draftKey); return; }
  const when = new Date(draft.savedAt).toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const ok = await confirmDialog({
    title: '保存されなかった書き込みがあります',
    intro: `「${data.name}」には、${when} の時点で保存されないまま閉じられた書き込み（${draft.annots.length} 件）が、このブラウザに残っています。`,
    confirmLabel: '復元する', cancelLabel: '破棄する',
  });
  if (!ok) { clearDraft(state.draftKey); return; }
  model.replaceAnnots(draft.annots);
  state.unsaved = true;
  refreshAll();
  // A box recovered mid-typing still has the size it had before the typing.
  const texts = model.store.annots.filter((item) => item.type === 'freetext');
  Promise.all(texts.map((item) => ensureFontLoaded(item.style?.font, item.text)))
    .then(() => refitText(texts.map((item) => item.id)));
  toast('書き込みを復元しました。Ctrl+S でファイルに保存してください');
}

function positionKey() {
  return `pdfstudio.pos.${model.store.name}.${model.store.pages.length}`;
}

/**
 * Show a PDF — and let the user start marking it up — while the editing
 * engine is still starting.
 *
 * Rendering needs only pdf.js, and adding text, markers, shapes or pen
 * strokes only changes the model in the browser; neither has to wait for
 * the engine. Anything that does need it (saving, searching, page
 * operations) simply waits its turn. When the engine is ready it opens the
 * same file and takes the document over, keeping every mark and the undo
 * history.
 */
async function previewFile(file, handle) {
  if (!(await confirmDiscard())) return;
  flushEditing();
  let settle;
  window.pdfStudioAdopted = new Promise((resolve, reject) => { settle = { resolve, reject }; });
  window.pdfStudioAdopted.catch(() => {});
  state.preview = { file, handle, settle };
  try {
    await viewer.load(new Uint8Array(await file.arrayBuffer()));
  } catch {
    // Encrypted or unusual files are left for the engine, which asks for the password.
    status('編集機能の準備ができしだい開きます…');
    return;
  }
  if (state.preview?.file !== file) return;
  state.fileHandle = null;
  state.draftKey = null;
  state.toc = [];
  state.pageSelection.clear();
  state.textLines.clear();
  model.loadDocument({
    id: 'preview', name: file.name, annots: [],
    pages: viewer.pageViews.map((view) => ({ index: view.index, width: view.width, height: view.height, rotation: view.rotation })),
  });
  state.unsaved = false;
  $('#emptyState').classList.add('hidden');
  renderThumbs($('#panelThumbs'), viewer, { current: 0, selected: new Set() }, thumbHandlers);
  renderOutlinePanel();
  syncZoomControls();
  refreshAll();
  toast(`${file.name} を開きました。書き込みはもう始められます（保存や検索は、準備ができしだい動きます）`);
  if (window.pdfStudioReady) finishPreview();
}

/** The engine is ready: have it open the previewed file and take over. */
async function finishPreview() {
  const waiting = state.preview;
  if (!waiting) return;
  if (model.store.docId !== 'preview') {
    // Nothing could be shown (an encrypted file, say): open it the ordinary way.
    state.preview = null;
    openFile(waiting.file, '', waiting.handle);
    return;
  }
  // Not in the middle of a word or a drag: wait for a quiet moment.
  if (state.editor || noteEditor || tools.pending) { setTimeout(finishPreview, 400); return; }
  try {
    const form = new FormData();
    form.append('file', waiting.file);
    const response = await fetch('/api/open', { method: 'POST', body: form });
    if (state.preview !== waiting) return;   // another file was opened meanwhile
    if (!response.ok) throw new Error(await errorDetail(response));
    const data = await response.json();
    if (state.editor || noteEditor || tools.pending) await new Promise((resolve) => setTimeout(resolve, 600));
    state.preview = null;
    window.pdfStudioRealId = data.id;
    const mine = model.store.annots.length;
    if (data.annots.length) {
      // The file already has markup. The preview painted it as part of the
      // page; from here the overlay draws it, so the page must stop doing so.
      flushEditing();
      await viewer.load(`/api/doc/${data.id}/file?t=${Date.now()}`, { keepPosition: true });
    }
    state.toc = data.toc || [];
    model.rebind(data);
    state.fileHandle = waiting.handle || null;
    state.draftKey = keyFor(waiting.file);
    waiting.settle.resolve();
    afterReload();
    refreshAll();
    if (waiting.handle) rememberFile(waiting.handle).then(showRecent);
    status(`${model.store.pages.length} ページ ／ 書き込み ${model.store.annots.length} 件`);
    if (!mine) setTimeout(() => offerDraft(data), 60);
  } catch (err) {
    waiting.settle.reject(err);
    if (state.preview === waiting) state.preview = null;
    toast(`この文書は編集用に開けませんでした: ${err.message}。読むことはできますが、保存はできません`, 'error');
  }
}

document.addEventListener('pdfstudio:ready', finishPreview);
document.addEventListener('pdfstudio:failed', (e) => {
  state.preview?.settle.reject(new Error(e.detail));
  toast(`編集機能を読み込めませんでした: ${e.detail}。インターネット接続を確認して、ページを再読み込みしてください`, 'error');
});

async function openFile(file, password = '', handle = null, { keepPosition = false } = {}) {
  if (!window.pdfStudioReady && !password) { previewFile(file, handle); return; }
  if (!password && !(await confirmDiscard())) return;
  state.preview = null;
  const done = busy(`${file.name} を開いています…`);
  try {
    await paint();
    const form = new FormData();
    form.append('file', file);
    if (password) form.append('password', password);
    const response = await fetch('/api/open', { method: 'POST', body: form });

    if (response.status === 401) {
      // Encrypted, and the password we sent (if any) did not open it.
      const { detail } = await response.json();
      done();
      const values = await formDialog({
        title: 'パスワードが必要です',
        intro: `${file.name} は保護されています。開くためのパスワードを入力してください。`,
        warning: password ? detail : undefined,
        fields: [{ key: 'password', label: 'パスワード', type: 'password' }],
        confirmLabel: '開く',
      });
      if (values?.password) { state.unsaved = false; await openFile(file, values.password, handle); }
      return;
    }
    if (!response.ok) {
      toast(`開けませんでした: ${await errorDetail(response)}`, 'error');
      return;
    }
    const data = await response.json();
    // Opening decrypts into the working copy, so say so rather than let the
    // reader assume the protection travelled with it.
    await adopt(data, {
      handle,
      keepPosition,
      draftKey: keyFor(file),
      message: data.wasProtected
        ? `${data.name} を開きました（保護を外した状態で編集します。保護を付け直すには「ファイル」→「パスワードを付けて書き出す」）`
        : null,
    });
  } catch (err) {
    toast(`開けませんでした: ${err.message}`, 'error');
  } finally {
    done();
  }
}

/** Open through the file picker where possible, so Save can write back to the same file. */
async function chooseFile() {
  if (!window.showOpenFilePicker) { $('#fileInput').click(); return; }
  try {
    const [handle] = await window.showOpenFilePicker({
      types: [{ description: 'PDF', accept: { 'application/pdf': ['.pdf'] } }],
    });
    await openFile(await handle.getFile(), '', handle);
  } catch (err) {
    if (err.name !== 'AbortError') $('#fileInput').click();
  }
}

async function newBlank() {
  if (!(await confirmDiscard())) return;
  const values = await formDialog({
    title: '白紙から作る',
    fields: [
      { key: 'size', label: '用紙', type: 'select', options: { a4p: 'A4 縦', a4l: 'A4 横', b5p: 'B5 縦', letter: 'レター 縦' } },
    ],
    confirmLabel: '作る',
  });
  if (!values) return;
  const [width, height] = { a4p: [595, 842], a4l: [842, 595], b5p: [516, 729], letter: [612, 792] }[values.size];
  const response = await fetch('/api/new', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: '無題.pdf', width, height }),
  });
  if (!response.ok) { toast('作成できませんでした', 'error'); return; }
  state.unsaved = false;
  await adopt(await response.json(), { message: '白紙の文書を作りました。「テキスト追加」やペンで書き込めます' });
  state.unsaved = true;
  refreshPanels();
}

async function pdfFromImages(files) {
  if (!files.length) return;
  if (!(await confirmDiscard())) return;
  const done = busy('画像からPDFを作っています…');
  try {
    await paint();
    const form = new FormData();
    for (const file of files) form.append('files', file);
    const response = await fetch('/api/from-images', { method: 'POST', body: form });
    if (!response.ok) { toast(`作成できませんでした: ${await errorDetail(response)}`, 'error'); return; }
    state.unsaved = false;
    await adopt(await response.json(), { message: `${files.length} 枚の画像からPDFを作りました` });
    state.unsaved = true;
    refreshPanels();
  } finally {
    done();
  }
}

/** The list of recently opened files on the start screen. */
async function showRecent() {
  const holder = $('#recentFiles');
  // An older cached page (offline, mid-update) may not have this element.
  if (!holder) return;
  const list = await recentFiles();
  holder.textContent = '';
  holder.hidden = !list.length;
  if (!list.length) return;
  holder.append(h('h2', { text: '最近使ったファイル' }));
  for (const item of list) {
    const when = new Date(item.at).toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' });
    holder.append(h('button', {
      class: 'recent-item', title: `${item.name} を開く`,
      html: iconSvg('pageblank', 17),
      onclick: () => openRecent(item),
    }, [h('span', { class: 'name', text: item.name }), h('span', { class: 'when', text: when })]));
  }
}

async function openRecent(item) {
  try {
    // The browser shows its own "allow this site to view the file?" prompt.
    if (await item.handle.queryPermission({ mode: 'read' }) !== 'granted'
      && await item.handle.requestPermission({ mode: 'read' }) !== 'granted') return;
    await openFile(await item.handle.getFile(), '', item.handle);
  } catch {
    toast(`${item.name} を開けませんでした（移動または削除された可能性があります）`, 'warn');
    await forgetFile(item.name);
    showRecent();
  }
}

$('#fileInput').addEventListener('change', (e) => {
  if (e.target.files[0]) openFile(e.target.files[0]);
  e.target.value = '';
});
$('#imagePdfInput').addEventListener('change', (e) => { pdfFromImages([...e.target.files]); e.target.value = ''; });
$('#imageInput').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (file) insertImage(await fileToDataUrl(file));
});
$('#mergeInput').addEventListener('change', (e) => { ops.mergeFiles([...e.target.files]); e.target.value = ''; });
$('#xfdfInput').addEventListener('change', (e) => { ops.importXfdf(e.target.files[0]); e.target.value = ''; });
$('#fdfInput').addEventListener('change', (e) => { ops.importFields(e.target.files[0]); e.target.value = ''; });
$('#collateInput').addEventListener('change', (e) => { ops.collateFields([...e.target.files]); e.target.value = ''; });
$('#compareInput').addEventListener('change', (e) => { ops.compareWith(e.target.files[0]); e.target.value = ''; });
$('#btnOpen2').addEventListener('click', chooseFile);
$('#btnNew2').addEventListener('click', newBlank);
$('#btnImages2').addEventListener('click', () => $('#imagePdfInput').click());

// Files can be dropped anywhere on the window. Handling it only over the page
// area meant a drop on a toolbar made the browser navigate away to the file.
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { if (hasFiles(e)) { dragDepth += 1; document.body.classList.add('dragover'); } });
window.addEventListener('dragleave', (e) => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove('dragover');
});
window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('drop', async (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('dragover');
  const files = [...e.dataTransfer.files];
  const item = e.dataTransfer.items?.[0];
  // Must be requested synchronously, before the event finishes.
  const handlePromise = item?.getAsFileSystemHandle ? item.getAsFileSystemHandle().catch(() => null) : null;
  const isPdf = (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
  const pdfs = files.filter(isPdf);
  const images = files.filter((f) => f.type.startsWith('image/'));
  if (pdfs.length) {
    if (hasDoc() && (pdfs.length > 1 || e.shiftKey)) { ops.mergeFiles(pdfs); return; }
    const handle = handlePromise ? await handlePromise : null;
    openFile(pdfs[0], '', handle && handle.kind === 'file' ? handle : null);
  } else if (images.length) {
    if (hasDoc()) {
      const view = viewer.viewFromPoint(e.clientX, e.clientY);
      const at = view ? { view, point: viewer.toPageCoords(view, e) } : null;
      for (const image of images) await insertImage(await fileToDataUrl(image), { at });
    } else {
      pdfFromImages(images);
    }
  } else if (files.length) {
    toast('PDFか画像ファイルをドロップしてください', 'warn');
  }
});

// ================================================================ rendering

// Pages whose overlay is out of date but which are not on screen. Redrawing
// every page of a long, heavily marked-up document on each change made
// dragging one shape stutter; off-screen pages are caught up as they scroll in.
const stalePages = new Set();

function drawOverlay(view) {
  const editingId = state.editor?.meta?.id || null;
  renderPage(view, model.onPage(view.index), model.store.selection,
    { editingId, scale: viewer.scale, mask: state.study ? state.revealed : null });
  stalePages.delete(view.index);
}

function refreshOverlays() {
  overlaysQueued = false;
  const near = new Set(viewer.visibleViews().map((view) => view.index));
  for (const view of viewer.pageViews) {
    if (near.has(view.index)) drawOverlay(view);
    else stalePages.add(view.index);
  }
  placeSelectionBar();
}

function catchUpOverlays() {
  if (!stalePages.size) return;
  for (const view of viewer.visibleViews()) if (stalePages.has(view.index)) drawOverlay(view);
}

// Redrawing is coalesced to once a frame. Besides being cheaper during a
// drag, it keeps the element a pointer event landed on in the document until
// that event has finished being handled.
let overlaysQueued = false;
function scheduleOverlays() {
  if (overlaysQueued) return;
  overlaysQueued = true;
  requestAnimationFrame(() => { if (overlaysQueued) refreshOverlays(); });
}

function selectedAnnots() {
  return model.store.selection.map(model.byId).filter(Boolean);
}

function refreshPanels() {
  const selection = selectedAnnots();
  ribbon.refresh();

  const right = $('#rightPanel');
  if (!right.classList.contains('collapsed')) {
    const active = right.querySelector('.panel.active')?.id;
    if (active === 'panelProps') {
      refreshPanel($('#panelProps'), () => renderProps($('#panelProps'), {
        selection,
        // One merge token per field, so a run of typing is one undo step.
        onPatch: (patch, field) => {
          model.updateAnnots(selection.map((a) => a.id), patch,
            { merge: field ? `prop:${selection.map((a) => a.id).join()}:${field}` : null });
          if (field) endMergeWhenIdle();
        },
      }));
    } else if (active === 'panelComments') {
      refreshPanel($('#panelComments'), () => renderComments($('#panelComments'), {
        annots: model.store.annots,
        selection,
        filters: state.filters,
        onFilter: (patch) => { Object.assign(state.filters, patch); refreshPanels(); },
        onSelect: (annot) => {
          selectTool('select');
          model.select([annot.id]);
          viewer.scrollToPage(annot.page, annot.rect[1]);
        },
        onPatch: (id, patch) => model.updateAnnots([id], patch),
        onReply: (id, text) => {
          const annot = model.byId(id);
          if (!annot) return;
          model.updateAnnots([id], {
            replies: [...(annot.replies || []), {
              id: model.uid(), author: getPref('author') || '', contents: text,
              created: new Date().toISOString(),
            }],
          });
          toast('返信を追加しました');
        },
        onBulk: (action, items) => {
          if (action !== 'select') return;
          selectTool('select');
          model.select(items.map((a) => a.id));
          if (items.length) viewer.scrollToPage(items[0].page, items[0].rect[1]);
        },
      }));
    } else if (active === 'panelTakeoff') {
      refreshPanel($('#panelTakeoff'), () => renderTakeoff($('#panelTakeoff'), {
        rows: measure.summarise(model.store.annots),
        scale: measure.getScale(),
        calibrated: measure.isCalibrated(),
        unitLabels: measure.UNIT_LABELS,
        subject: state.takeoffSubject,
        onCalibrate: startCalibration,
        onUnit: (unit) => { measure.setScale({ unit }); refreshPanels(); },
        onSubject: (value) => { state.takeoffSubject = value; tools.subject = value; },
        onExportCsv: () => exportVia('/takeoff', { csv: true }, { label: '書き出し', fallback: 'takeoff.csv' }),
        onLegend: placeLegend,
      }));
    } else if (active === 'panelSettings') {
      refreshPanel($('#panelSettings'), () => renderSettings($('#panelSettings'), {
        getPref, setPref,
        onChange: (what) => { if (what === 'reset-defaults') location.reload(); },
      }));
    }
  }

  $('#btnUndo').disabled = !model.history.canUndo;
  $('#btnRedo').disabled = !model.history.canRedo;
  $('#qSave').disabled = !hasDoc();
  $('#qPrint').disabled = !hasDoc();
  $('#btnUndo').title = model.history.nextIsStructural
    ? '直前のページ操作を元に戻す (Ctrl+Z)' : '元に戻す (Ctrl+Z)';

  const name = hasDoc() ? (state.fileHandle?.name || model.store.name) : '文書が開かれていません';
  $('#docName').textContent = name;
  $('#docName').title = name;
  const saveState = $('#saveState');
  const auto = getPref('autosave') && state.fileHandle;
  saveState.textContent = !hasDoc() ? ''
    : state.preview ? (state.unsaved ? '● 未保存（保存は準備ができしだい）' : '保存・検索は準備中…')
    : state.unsaved ? (auto ? '● 変更あり（まもなく自動保存）' : '● 未保存の変更があります')
      : (auto ? '保存済み（自動保存オン）' : '保存済み');
  saveState.classList.toggle('dirty', state.unsaved);
  document.title = hasDoc() ? `${state.unsaved ? '● ' : ''}${name} — PDF Studio` : 'PDF Studio';

  if (hasDoc()) {
    const selected = selection.length ? ` ／ ${selection.length} 件を選択中` : '';
    const pages = state.pageSelection.size > 1 ? ` ／ ${state.pageSelection.size} ページを選択中` : '';
    status(`${model.store.pages.length} ページ ／ 書き込み ${model.store.annots.length} 件${selected}${pages}`);
  }
  // Counted from what is on screen, so it is right while a document is being
  // previewed before the engine has taken it over.
  const shown = viewer.pageViews.length;
  $('#pageTotal').textContent = `/ ${shown}`;
  if (document.activeElement !== $('#pageInput')) {
    $('#pageInput').value = shown ? String(Math.max(0, viewer.currentPage) + 1) : '';
  }
}

function refreshAll() {
  refreshOverlays();
  refreshPanels();
}

// ---------------------------------------------------------------- form fields on the page

let formTimer;

/**
 * Put a real input over every fillable form field, so a form is filled in
 * by clicking the blank and typing — not through a separate dialog.
 */
async function buildFormLayer() {
  for (const view of viewer.pageViews) view.forms.textContent = '';
  if (!hasDoc()) return;
  let data;
  try {
    const response = await fetch(docUrl('/fields'));
    if (!response.ok) return;
    data = await response.json();
  } catch { return; }
  if (data.hasXfa || !data.fields?.length) return;

  const send = async (name, value) => {
    const response = await post('/fields/fill', { values: { [name]: value } });
    if (!response.ok) { toast(`入力できませんでした: ${await errorDetail(response)}`, 'error'); return; }
    state.unsaved = true;
    refreshPanels();
  };

  let count = 0;
  for (const field of data.fields) {
    const view = viewer.pageViews[field.page];
    if (!view || field.readOnly || !field.rect) continue;
    const [x0, y0, x1, y1] = field.rect;
    const width = x1 - x0; const height = y1 - y0;
    if (width < 3 || height < 3) continue;
    let control;
    if (field.type === 'checkbox') {
      control = document.createElement('input');
      control.type = 'checkbox';
      control.checked = !['Off', '', null, undefined, false].includes(field.value);
      control.addEventListener('change', () => send(field.name, control.checked ? 'Yes' : 'Off'));
    } else if ((field.type === 'dropdown' || field.type === 'list') && field.options?.length) {
      control = document.createElement('select');
      control.append(new Option('', ''));
      for (const option of field.options) control.append(new Option(String(option), String(option)));
      control.value = field.value ?? '';
      control.addEventListener('change', () => send(field.name, control.value));
    } else if (field.type === 'text') {
      // A tall box is a multi-line field.
      control = document.createElement(height > 34 ? 'textarea' : 'input');
      control.value = field.value ?? '';
      if (field.maxLength) control.maxLength = field.maxLength;
      let sent = control.value;
      const commit = () => { if (control.value !== sent) { sent = control.value; send(field.name, sent); } };
      control.addEventListener('blur', commit);
      control.addEventListener('input', () => { state.unsaved = true; clearTimeout(formTimer); formTimer = setTimeout(commit, 1500); });
      control.addEventListener('keydown', (event) => {
        event.stopPropagation();
        if (event.key === 'Enter' && !isComposing(event) && control.tagName === 'INPUT') control.blur();
      });
    } else {
      continue; // buttons, radio groups and signature fields are left to the page image
    }
    control.title = field.label || field.name;
    control.style.left = `${(x0 / view.width) * 100}%`;
    control.style.top = `${(y0 / view.height) * 100}%`;
    control.style.width = `${(width / view.width) * 100}%`;
    control.style.height = `${(height / view.height) * 100}%`;
    const size = field.fontSize > 0 ? field.fontSize : Math.max(7, Math.min(12, height * 0.62));
    control.style.fontSize = `calc(${size}px * var(--scale-factor, 1))`;
    view.forms.append(control);
    count += 1;
  }
  if (count && !state.formHintShown) {
    state.formHintShown = true;
    toast(`入力欄が ${count} か所あります。青い枠をクリックして、そのまま入力できます`);
  }
}

function afterReload() {
  buildFormLayer();
  drawSearchHits();
  renderThumbs($('#panelThumbs'), viewer, { current: viewer.currentPage, selected: state.pageSelection }, thumbHandlers);
  renderOutlinePanel();
  syncZoomControls();
}

hooks.refreshAll = refreshAll;
hooks.afterReload = afterReload;
hooks.flushEditing = () => flushEditing();

// A burst of typing is one undo step; a pause starts the next.
let mergeIdleTimer;
function endMergeWhenIdle(delay = 1200) {
  clearTimeout(mergeIdleTimer);
  mergeIdleTimer = setTimeout(() => model.endMerge(), delay);
}

model.subscribe((reason) => {
  if (!['selection', 'document', 'saved', 'derived'].includes(reason)) { state.unsaved = true; scheduleDraft(); scheduleAutosave(); }
  if (state.editor && !state.editor.meta.isLine) {
    const annot = model.byId(state.editor.meta.id);
    if (annot) state.editor.applyStyle(annot); else { state.editor.discard(); state.editor = null; }
  }
  if (reason === 'selection') {
    // Clicking another annotation ends the edit of the open text box.
    if (state.editor && !state.editor.meta.isLine && !model.store.selection.includes(state.editor.meta.id)) flushEditing();
  }
  scheduleOverlays();
  refreshPanels();
});

viewer.addEventListener('zoom', () => {
  state.editor?.setScale(viewer.scale);
  if (noteEditor) closeNoteEditor();
  refreshOverlays();
  syncZoomControls();
});
viewer.addEventListener('page', (e) => {
  if (document.activeElement !== $('#pageInput')) $('#pageInput').value = String(e.detail.page + 1);
  updateThumbs($('#panelThumbs'), { current: e.detail.page, selected: state.pageSelection });
  scrollThumbIntoView($('#panelThumbs'), e.detail.page);
  ribbon.refresh();
  if (tools.tool === 'edittext') loadTextLines(e.detail.page);
  try { if (hasDoc()) localStorage.setItem(positionKey(), String(e.detail.page)); } catch { /* storage blocked */ }
});
stage.addEventListener('scroll', () => { hideSelectionBar(); scheduleBar(); catchUpOverlays(); }, { passive: true });

// ================================================================ text boxes

function pageWidthOf(annot) {
  return model.store.pages[annot.page]?.width || viewer.pageViews[annot.page]?.width || 595;
}

function startTextEdit(id, { isNew = false, point = null } = {}) {
  if (state.editor?.meta.id === id) { state.editor.focus({ point }); return; }
  flushEditing();
  const annot = model.byId(id);
  if (!annot) return;
  if (annot.flags?.locked || annot.flags?.readOnly) { toast('この書き込みはロックされています', 'warn'); return; }
  const view = viewer.pageViews[annot.page];
  if (!view) return;
  if (annot.type === 'note') { openNoteEditor(annot, view, isNew); return; }
  if (annot.type !== 'freetext') return;

  if (!model.store.selection.includes(id) || model.store.selection.length !== 1) model.select([id]);
  hideSelectionBar();
  const editor = new TextEditor({
    wrap: view.wrap, annot, scale: viewer.scale, pageWidth: pageWidthOf(annot),
    placeholder: isNew ? 'ここに入力' : '',
    onCommit: (text, size, options) => commitText(id, isNew, text, size, options),
    onGrab: (event) => {
      // The frame was grabbed: finish typing and drag the box instead.
      editor.commit({ keepSelected: true });
      if (!model.byId(id)) return;
      const page = viewer.pageViews[annot.page];
      tools.beginMove(page, viewer.toPageCoords(page, event), event);
    },
  });
  editor.meta = { id, isNew };
  editor.node.addEventListener('input', () => { state.unsaved = true; scheduleDraft(); });
  state.editor = editor;
  refreshOverlays();
  editor.focus({ point, selectAll: false });
  ensureFontLoaded(annot.style?.font);
  ribbon.refresh();
}

function commitText(id, isNew, text, size, { keepSelected = false } = {}) {
  state.editor = null;
  const annot = model.byId(id);
  if (!annot) { refreshAll(); return; }
  if (!text) {
    // An empty text box is nothing: drop it rather than leave an invisible
    // object on the page.
    model.removeAnnots([id]);
    refreshAll();
    return;
  }
  const [x0, y0, x1, y1] = annot.rect;
  const rect = [
    x0, y0,
    annot.autoWidth ? x0 + size.width : x1,
    y0 + (annot.autoHeight === false ? Math.max(size.height, y1 - y0) : size.height),
  ];
  const patch = { text, contents: text, rect };
  const same = text === annot.text && rect.every((v, i) => Math.abs(v - annot.rect[i]) < 0.02);
  if (!same) model.updateAnnots([id], patch);
  if (!keepSelected && model.store.selection.includes(id)) model.select([]);
  refreshAll();
}

function flushEditing() {
  if (state.editor) state.editor.commit({ keepSelected: true });
  if (noteEditor) closeNoteEditor();
}

/** Re-measure text boxes after something that changes how much room they need. */
function refitText(ids, merge = null) {
  for (const id of ids) {
    const annot = model.byId(id);
    if (!annot || annot.type !== 'freetext' || state.editor?.meta.id === id || !annot.text) continue;
    const rect = fitRect(annot, pageWidthOf(annot));
    if (rect.some((v, i) => Math.abs(v - annot.rect[i]) > 0.02)) {
      model.updateAnnots([id], { rect }, { merge });
    }
  }
}

// A box measured before its font had loaded was measured in a fallback face.
document.fonts?.addEventListener?.('loadingdone', () => {
  for (const annot of model.store.annots) {
    if (annot.type !== 'freetext' || state.editor?.meta.id === annot.id) continue;
    const rect = fitRect(annot, pageWidthOf(annot));
    if (rect.some((v, i) => Math.abs(v - annot.rect[i]) > 0.02)) model.silentUpdate(annot.id, { rect });
  }
});

// A press outside the open text box finishes it. This runs in the capture
// phase so the tool handler sees the document as it is after the commit.
stage.addEventListener('pointerdown', (e) => {
  if (e.target.closest?.('.ft-host, .note-editor')) return;
  if (!state.editor && !noteEditor) return;
  const typing = !!state.editor;
  flushEditing();
  // With the text tool, this press only closes the box; it does not also
  // start a new one where the user clicked away.
  if (typing && tools.tool === 'edittext') tools.swallowNext = true;
  if (typing && isTextTool(tools.tool) && !e.target.closest?.('.annot.is-text')) tools.swallowNext = true;
}, true);

let noteEditor = null;

function openNoteEditor(annot, view, isNew) {
  closeNoteEditor();
  const scale = viewer.scale;
  const box = document.createElement('textarea');
  box.className = 'note-editor';
  box.placeholder = '付箋の内容を入力（枠の外をクリックで確定）';
  box.value = annot.contents || '';
  const left = Math.min(annot.rect[2] * scale + 6, Math.max(0, view.width * scale - 240));
  box.style.left = `${left}px`;
  box.style.top = `${annot.rect[1] * scale}px`;
  view.wrap.append(box);
  noteEditor = { box, id: annot.id, isNew };
  box.focus();
  box.addEventListener('keydown', (event) => {
    if (!isComposing(event) && (event.key === 'Escape' || (event.key === 'Enter' && (event.ctrlKey || event.metaKey)))) {
      event.preventDefault();
      closeNoteEditor();
    }
    event.stopPropagation();
  });
  for (const type of ['pointerdown', 'pointerup', 'dblclick', 'contextmenu', 'paste', 'copy', 'cut', 'keyup']) {
    box.addEventListener(type, (event) => event.stopPropagation());
  }
  if (!model.store.selection.includes(annot.id)) model.select([annot.id]);
}

function closeNoteEditor() {
  if (!noteEditor) return;
  const { box, id, isNew } = noteEditor;
  noteEditor = null;
  const value = box.value.replace(/\s+$/, '');
  box.remove();
  const annot = model.byId(id);
  if (!annot) return;
  if (isNew && !value) { model.removeAnnots([id]); return; }
  if (value !== (annot.contents || '')) model.updateAnnots([id], { contents: value });
}

// ================================================================ tool events

tools.addEventListener('edited', () => refreshPanels());
tools.addEventListener('tool', () => { hideSelectionBar(); refreshPanels(); });
tools.addEventListener('edit-text', (e) => startTextEdit(e.detail.id, e.detail));
tools.addEventListener('tool-done', () => { setToolButton('select'); });
tools.addEventListener('hint', (e) => toast(e.detail.message));
tools.addEventListener('refit', (e) => refitText(e.detail.ids));
tools.addEventListener('resized', (e) => { refitText([e.detail.id]); model.endMerge(); });
tools.addEventListener('lassoed', (e) => {
  if (!e.detail.ids.length) { toast('囲んだ中に書き込みがありませんでした', 'warn'); return; }
  selectTool('select');
  model.select(e.detail.ids);
});
tools.addEventListener('open-props', () => showRightPanel('props'));

// ---- correction tape: a patch the colour of the paper over what is there
function paperColour(view, rect) {
  // Read the page itself around the edge of the patch, so tape on a tinted
  // or scanned page matches it instead of standing out as a white block.
  try {
    const canvas = view.canvas;
    const context = canvas.getContext('2d', { alpha: false });
    const sx = canvas.width / view.width;
    const sy = canvas.height / view.height;
    const votes = new Map();
    const probe = (x, y) => {
      const px = Math.max(0, Math.min(canvas.width - 1, Math.round(x * sx)));
      const py = Math.max(0, Math.min(canvas.height - 1, Math.round(y * sy)));
      const [r, g, b] = context.getImageData(px, py, 1, 1).data;
      // Round a little so scanner noise still agrees with itself.
      const key = [r, g, b].map((v) => Math.min(255, Math.round(v / 6) * 6)).join(',');
      votes.set(key, (votes.get(key) || 0) + 1);
    };
    const [x0, y0, x1, y1] = rect;
    for (let i = 0; i <= 8; i += 1) {
      const fx = x0 + ((x1 - x0) * i) / 8;
      const fy = y0 + ((y1 - y0) * i) / 8;
      probe(fx, y0 - 1.5); probe(fx, y1 + 1.5); probe(x0 - 1.5, fy); probe(x1 + 1.5, fy);
    }
    const [best] = [...votes.entries()].sort((a, b) => b[1] - a[1])[0];
    return `#${best.split(',').map((v) => Number(v).toString(16).padStart(2, '0')).join('')}`;
  } catch {
    return '#ffffff';
  }
}

tools.addEventListener('whiteout', (e) => {
  const { view, rect, base } = e.detail;
  const colour = paperColour(view, rect);
  model.addAnnots([{
    ...base, type: 'square', rect, tool: 'whiteout',
    style: { ...base.style, stroke: colour, fill: colour, width: 0, opacity: 1 },
  }], { select: false });
});

tools.addEventListener('region', async (e) => {
  const { tool: which, view, rect } = e.detail;
  tools.setTool('select');
  if (which === 'snapshot') { copyRegion(view, rect); return; }
  const values = await formDialog({
    title: 'この範囲に切り取る（トリミング）',
    intro: '囲んだ範囲だけが見えるようにします。余白の多い資料を大きく表示・印刷したいときに。',
    warning: '範囲の外は見えなくなるだけで、ファイルには残ります。本当に消すには墨消しを使ってください。Ctrl+Z で戻せます。',
    fields: [{ key: 'scope', label: '対象', type: 'select', options: {
      one: `${view.index + 1} ページ目だけ`, all: 'すべてのページ（同じ範囲）',
    } }],
    confirmLabel: '切り取る',
  });
  if (!values) return;
  const pages = values.scope === 'all' ? model.store.pages.map((_, i) => i) : [view.index];
  const result = await structural('/pages/crop', { pages, rect }, { label: 'トリミング' });
  if (result) toast('切り取りました（Ctrl+Z で戻せます。「ページ」→「切り取りを解除」でも戻ります）');
});

/** Copy a region of the page, markup included, to the clipboard as a picture. */
async function copyRegion(view, rect) {
  const done = busy('切り抜いています…');
  try {
    await paint();
    const response = await post('/snapshot', { page: view.index, rect, dpi: 200 }, { withAnnots: true });
    if (!response.ok) { toast(`切り抜けませんでした: ${await errorDetail(response)}`, 'error'); return; }
    const blob = await response.blob();
    try {
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      toast('切り抜いた画像をコピーしました。Word やノートアプリに Ctrl+V で貼れます');
    } catch {
      // No clipboard access (older browser, or permission refused): save it.
      downloadBlob(blob, `${(model.store.name || 'page').replace(/\.pdf$/i, '')}_p${view.index + 1}_切り抜き.png`);
      toast('切り抜いた画像をダウンロードしました');
    }
  } finally {
    done();
  }
}

// ---- study mode: markers hide the words until clicked
state.study = false;
state.revealed = new Set();

function toggleStudy() {
  state.study = !state.study;
  state.revealed = new Set();
  $('#pages').classList.toggle('study', state.study);
  if (state.study) {
    selectTool('select');
    model.select([]);
    const count = model.store.annots.filter((a) => a.type === 'highlight' || a.type === 'areaHighlight').length;
    toast(count
      ? `暗記シート: マーカー ${count} か所を隠しました。クリックすると見えます（もう一度クリックで隠れます）`
      : '暗記シート: 覚えたい語句にマーカーを引くと、そこが隠れます', count ? '' : 'warn');
  }
  refreshAll();
}

// In study mode a press on a covered marker reveals it instead of selecting it.
stage.addEventListener('pointerdown', (e) => {
  if (!state.study) return;
  const hit = e.target.closest?.('.annot');
  const annot = hit ? model.byId(hit.dataset.id) : null;
  if (!annot || (annot.type !== 'highlight' && annot.type !== 'areaHighlight')) return;
  e.stopPropagation();
  e.preventDefault();
  if (state.revealed.has(annot.id)) state.revealed.delete(annot.id); else state.revealed.add(annot.id);
  scheduleOverlays();
}, true);

function lookUp(kind) {
  const text = document.getSelection()?.toString().trim();
  if (!text) return;
  const japanese = (text.match(/[\u3040-\u30ff\u4e00-\u9fff]/g) || []).length > text.length * 0.3;
  const q = encodeURIComponent(text.slice(0, 1800));
  const url = kind === 'translate'
    ? `https://translate.google.com/?sl=auto&tl=${japanese ? 'en' : 'ja'}&text=${q}&op=translate`
    : `https://www.google.com/search?q=${q}`;
  window.open(url, '_blank', 'noopener');
}

/** Hand back to the pointer after a one-shot tool, leaving any text box it opened open. */
function setToolButton(tool) {
  tools.setTool(tool);
}

function selectTool(tool) {
  if (!hasDoc() && tool !== 'select') {
    toast('先にPDFを開いてください', 'warn');
    return;
  }
  flushEditing();
  clearTextLineHover();
  tools.setTool(tool);
  if (tool === 'edittext') {
    loadTextLines(viewer.currentPage);
    toast('書き換えたい行をクリックしてください（元のフォントとは見た目が変わることがあります）');
  }
  if (MARKUP_TOOLS.has(tool)) toast('文字をなぞって選ぶと引かれます');
}

// ================================================================ format

function applyStylePatch(patch, gesture) {
  const selection = selectedAnnots().filter((a) => !a.flags?.locked);
  if (selection.length) {
    const ids = selection.map((a) => a.id);
    const merge = gesture ? `style:${ids.join()}:${gesture}` : model.uid();
    model.updateAnnots(ids, { style: patch }, { merge });
    if (patch.font || patch.width !== undefined) refitText(ids, merge);
    if (!gesture) model.endMerge();
    // What was just chosen is also what the next one should look like.
    remember(selection[0].type === 'freetext' ? (selection[0].callout ? 'callout' : 'freetext') : toolKeyOf(selection[0]), patch);
  } else {
    remember(tools.tool, patch);
  }
  state.editor?.focus();
  ribbon.refresh();
}

function toolKeyOf(annot) {
  if (annot.type === 'ink') return annot.tool === 'mark' ? 'mark' : (annot.tool === 'marker' ? 'marker' : 'pen');
  if (annot.type === 'line') return (annot.style?.lineEnds || []).some((v) => v && v !== 'none') ? 'arrow' : 'line';
  return annot.type;
}

function formatContext() {
  return {
    tool: tools.tool,
    selection: selectedAnnots(),
    onChange: applyStylePatch,
    onCommit: () => model.endMerge(),
    onExtra: (patch) => {
      const ids = model.store.selection;
      if (ids.length) model.updateAnnots(ids, patch);
    },
    markKind: tools.markKind,
    onMarkKind: (kind) => { tools.markKind = kind; ribbon.refresh(); },
  };
}

/**
 * The font controls are always there, as in a word processor. With a text
 * box selected (or open) they change it; otherwise they set what the next
 * text box will look like.
 */
function fontGroup() {
  const context = formatContext();
  const group = renderFontGroup(context);
  if (group) return group;
  return renderFontGroup({
    ...context, tool: 'freetext', selection: [],
    onChange: (patch) => { remember('freetext', patch); ribbon.refresh(); },
  });
}

function styleGroup() {
  return renderStyleGroup(formatContext());
}

// ================================================================ saving

/**
 * Save to a real file on disk: the opened file, or one the person picks.
 * Browsers without the File System Access API fall back to a download.
 */
async function saveToDisk({ saveAs = false } = {}) {
  if (!hasDoc()) return false;
  flushEditing();
  // The file picker must open from the click itself; ask for it before the
  // slow part, or the browser refuses it as "not a user gesture".
  let handle = saveAs ? null : state.fileHandle;
  try {
    if (handle && handle.queryPermission && await handle.queryPermission({ mode: 'readwrite' }) !== 'granted') {
      if (await handle.requestPermission({ mode: 'readwrite' }) !== 'granted') handle = null;
    }
    if (!handle && window.showSaveFilePicker) {
      const base = (state.fileHandle?.name || model.store.name || 'document.pdf').replace(/\.pdf$/i, '');
      handle = await window.showSaveFilePicker({
        suggestedName: `${base}${saveAs ? '' : ''}.pdf`,
        types: [{ description: 'PDF', accept: { 'application/pdf': ['.pdf'] } }],
      });
    }
  } catch (err) {
    if (err.name === 'AbortError') return false;
    handle = null;
  }

  const done = busy('保存しています…');
  try {
    await paint();
    const { blob, name } = await finishedPdf();
    if (handle) {
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      state.fileHandle = handle;
      model.store.name = handle.name;
      toast(`${handle.name} に保存しました`);
    } else {
      downloadBlob(blob, name);
      toast(`${name} をダウンロードフォルダに保存しました`);
    }
    state.unsaved = false;
    model.markClean();
    // What was at risk is now in a file; the recovery copy has done its job.
    clearTimeout(draftTimer);
    clearDraft(state.draftKey);
    if (handle) { try { state.draftKey = keyFor(await handle.getFile()); } catch { state.draftKey = null; } }
    if (handle) rememberFile(handle);
    return true;
  } catch (err) {
    toast(`保存に失敗しました: ${err.message}${/NoModificationAllowed|locked|InvalidState/i.test(String(err.name)) ? '（ファイルが他のアプリで開かれていないか確認してください）' : ''}`, 'error');
    return false;
  } finally {
    done();
    refreshPanels();
  }
}

async function downloadCopy() {
  if (!hasDoc()) return;
  const done = busy('書き出しています…');
  try {
    await paint();
    const { blob, name } = await finishedPdf();
    downloadBlob(blob, name);
    toast(`${name} をダウンロードフォルダに書き出しました`);
  } catch (err) {
    toast(`書き出しに失敗しました: ${err.message}`, 'error');
  } finally {
    done();
  }
}

let printFrame = null;
async function printDocument() {
  if (!hasDoc()) return;
  const done = busy('印刷の準備をしています…');
  try {
    await paint();
    const { blob } = await finishedPdf();
    const url = URL.createObjectURL(blob);
    printFrame?.remove();
    printFrame = document.createElement('iframe');
    printFrame.style.cssText = 'position:fixed;right:0;bottom:0;width:1px;height:1px;border:0;opacity:0;';
    printFrame.src = url;
    printFrame.addEventListener('load', () => {
      setTimeout(() => {
        try {
          printFrame.contentWindow.focus();
          printFrame.contentWindow.print();
        } catch {
          // Some browsers will not print a PDF from a frame: open it instead.
          window.open(url, '_blank');
          toast('新しいタブで開きました。そこから印刷してください');
        }
      }, 400);
    });
    document.body.append(printFrame);
    setTimeout(() => URL.revokeObjectURL(url), 5 * 60 * 1000);
  } catch (err) {
    toast(`印刷の準備に失敗しました: ${err.message}`, 'error');
  } finally {
    done();
  }
}

window.addEventListener('beforeunload', (e) => {
  if (state.unsaved) { e.preventDefault(); e.returnValue = ''; }
});

// ================================================================ undo / redo

async function undo() {
  flushEditing();
  const outcome = model.undo();
  if (outcome !== 'structural') return;
  const done = busy('元に戻しています…');
  try {
    await paint();
    const response = await post('/undo', {});
    if (!response.ok) { toast(`元に戻せませんでした: ${await errorDetail(response)}`, 'error'); return; }
    await reloadFrom(await response.json());
    state.unsaved = true;
    state.pageSelection.clear();
    refreshAll();
    toast('直前の操作を元に戻しました');
  } finally {
    done();
  }
}

function redo() {
  flushEditing();
  model.redo();
}

$('#btnUndo').addEventListener('click', undo);
$('#btnRedo').addEventListener('click', redo);
$('#qSave').addEventListener('click', () => saveToDisk());
$('#qPrint').addEventListener('click', printDocument);

// ================================================================ clipboard

let clip = [];
let pasteCount = 0;

function copySelection() {
  const annots = selectedAnnots();
  if (!annots.length) return false;
  clip = annots.map((a) => structuredClone(a));
  pasteCount = 0;
  const text = annots.map((a) => a.text || a.contents || '').filter(Boolean).join('\n');
  if (text) navigator.clipboard?.writeText(text).catch(() => {});
  toast(`${annots.length} 件をコピーしました`);
  return true;
}

function deleteSelection() {
  const ids = selectedAnnots().filter((a) => !a.flags?.locked).map((a) => a.id);
  if (!ids.length) {
    if (model.store.selection.length) toast('ロックされているため削除できません', 'warn');
    return;
  }
  flushEditing();
  model.removeAnnots(ids);
}

function cutSelection() {
  if (copySelection()) deleteSelection();
}

function pasteAnnots(source = clip, { offset = true, page = viewer.currentPage } = {}) {
  if (!source.length || !hasDoc()) return false;
  pasteCount += 1;
  const samePage = source.every((a) => a.page === page);
  const shift = offset && samePage ? 12 * pasteCount : 0;
  const size = model.store.pages[page];
  const items = source.map((a) => {
    const copy = structuredClone(a);
    delete copy.id; delete copy.xref;
    copy.replies = [];
    copy.page = page;
    Object.assign(copy, translated(copy, shift, shift));
    // Keep a pasted item on the paper when pages differ in size.
    const over = Math.max(0, copy.rect[2] - size.width + 4);
    const under = Math.max(0, copy.rect[3] - size.height + 4);
    if (over || under) Object.assign(copy, translated(copy, -over, -under));
    return copy;
  });
  selectTool('select');
  model.addAnnots(items);
  return true;
}

function duplicateSelection() {
  const annots = selectedAnnots();
  if (!annots.length) return;
  pasteCount = 0;
  // A duplicate stays on the page of the original, wherever the view is.
  pasteAnnots(annots.map((a) => structuredClone(a)), { page: annots[0].page });
}

document.addEventListener('paste', async (e) => {
  if (!hasDoc()) return;
  const target = e.target;
  if (target.closest?.('input, textarea, [contenteditable]')) return;
  const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
  if (files.length) {
    e.preventDefault();
    for (const file of files) await insertImage(await fileToDataUrl(file));
    return;
  }
  if (clip.length) { e.preventDefault(); pasteAnnots(); return; }
  const text = e.clipboardData?.getData('text/plain');
  if (text && text.trim()) {
    e.preventDefault();
    addTextBox(text.replace(/\r\n?/g, '\n').trim());
  }
});

// ================================================================ inserting

/** The middle of what is on screen, in the coordinates of the page in view. */
function visibleCentre() {
  const view = viewer.pageViews[viewer.currentPage] || viewer.pageViews[0];
  const box = view.wrap.getBoundingClientRect();
  const frame = stage.getBoundingClientRect();
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  let x = clamp(((frame.left + frame.right) / 2 - box.left) / viewer.scale, 20, view.width - 20);
  let y = clamp(((frame.top + frame.bottom) / 2 - box.top) / viewer.scale, 20, view.height - 20);
  // Several things inserted in a row should not land exactly on top of each
  // other, where the earlier ones would be hidden.
  const key = `${view.index}:${Math.round(x)}:${Math.round(y)}`;
  if (lastCentre.key === key) lastCentre.count += 1; else { lastCentre.key = key; lastCentre.count = 0; }
  x = clamp(x + lastCentre.count * 16, 20, view.width - 20);
  y = clamp(y + lastCentre.count * 16, 20, view.height - 20);
  return { view, point: { x, y } };
}
const lastCentre = { key: '', count: 0 };

async function insertImage(source, { at = null, points = null } = {}) {
  if (!hasDoc()) { toast('先にPDFを開いてください', 'warn'); return; }
  let picture;
  try {
    picture = source.image ? source : await normaliseImage(source);
  } catch (err) {
    toast(err.message, 'error');
    return;
  }
  const { view, point } = at || visibleCentre();
  let width; let height;
  if (points) {
    [width, height] = points;
  } else {
    // 96 px per inch is how a screen image is "meant" to be sized; never let
    // it take more than half the page until the user asks for that.
    width = Math.min(picture.width * 0.75, view.width * 0.5);
    height = width * (picture.height / picture.width);
    if (height > view.height * 0.5) { height = view.height * 0.5; width = height * (picture.width / picture.height); }
  }
  const x = Math.max(2, Math.min(point.x - width / 2, view.width - width - 2));
  const y = Math.max(2, Math.min(point.y - height / 2, view.height - height - 2));
  selectTool('select');
  model.addAnnots([{
    type: 'image', page: view.index, rect: [x, y, x + width, y + height], image: picture.image,
    style: { opacity: 1 }, author: getPref('author') || '', flags: FLAGS(),
  }]);
  toast('画像を置きました。ドラッグで移動、角のハンドルで大きさを変えられます');
}

async function insertSignature() {
  const result = await signatureDialog({ defaultName: getPref('author') || '' });
  if (!result) return;
  const height = 42;
  await insertImage(result, { points: [Math.min(260, height * (result.width / result.height)), height] });
}

async function insertHanko() {
  const result = await hankoDialog({ defaultName: getPref('author') || '' });
  if (result) await insertImage(result, { points: result.points });
}

function addTextBox(text, { at = null, style = null, edit = false } = {}) {
  if (!hasDoc()) { toast('先にPDFを開いてください', 'warn'); return null; }
  const { view, point } = at || visibleCentre();
  const base = style || styleFor('freetext');
  const size = base.font?.size || 12;
  const x = Math.max(2, point.x - (at ? 0 : 60));
  const y = Math.max(2, point.y - (size * LINE_HEIGHT) / 2 - 2);
  selectTool('select');
  const [created] = model.addAnnots([{
    type: 'freetext', page: view.index, rect: [x, y, x + size + 6, y + size * LINE_HEIGHT + 4],
    text, contents: text, autoWidth: true, style: base,
    author: getPref('author') || '', flags: FLAGS(),
  }]);
  if (edit) startTextEdit(created.id, { isNew: true });
  else ensureFontLoaded(base.font, text).then(() => refitText([created.id]));
  return created;
}

async function insertDate() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const era = new Intl.DateTimeFormat('ja-JP-u-ca-japanese', { era: 'long', year: 'numeric', month: 'long', day: 'numeric' }).format(now);
  const options = {
    a: `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日`,
    b: `${now.getFullYear()}/${pad(now.getMonth() + 1)}/${pad(now.getDate())}`,
    c: era,
    d: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
  };
  const values = await formDialog({
    title: '今日の日付を入れる',
    fields: [{ key: 'format', label: '書き方', type: 'select', options }],
    confirmLabel: '入れる',
  });
  if (values) addTextBox(options[values.format]);
}

// ================================================================ arranging

/** Line several selected things up, or space them evenly. */
function arrange(how) {
  const items = selectedAnnots().filter((a) => !a.flags?.locked);
  if (items.length < 2) { toast('2つ以上選んでください（Shift を押しながらクリック、またはドラッグで囲む）', 'warn'); return; }
  const merge = model.uid();
  const move = (annot, dx, dy) => { if (dx || dy) model.updateAnnots([annot.id], translated(annot, dx, dy), { merge }); };
  const edge = (index, pick) => pick(...items.map((a) => a.rect[index]));
  if (how === 'left') { const x = edge(0, Math.min); for (const a of items) move(a, x - a.rect[0], 0); }
  if (how === 'right') { const x = edge(2, Math.max); for (const a of items) move(a, x - a.rect[2], 0); }
  if (how === 'top') { const y = edge(1, Math.min); for (const a of items) move(a, 0, y - a.rect[1]); }
  if (how === 'bottom') { const y = edge(3, Math.max); for (const a of items) move(a, 0, y - a.rect[3]); }
  if (how === 'spreadX' || how === 'spreadY') {
    if (items.length < 3) { toast('等間隔にするには3つ以上選んでください', 'warn'); return; }
    const i = how === 'spreadX' ? 0 : 1;
    const sorted = [...items].sort((p, q) => p.rect[i] - q.rect[i]);
    const first = sorted[0].rect[i];
    const last = sorted[sorted.length - 1].rect[i];
    sorted.forEach((annot, n) => {
      const want = first + ((last - first) * n) / (sorted.length - 1);
      move(annot, i === 0 ? want - annot.rect[0] : 0, i === 1 ? want - annot.rect[1] : 0);
    });
  }
  model.endMerge();
}

// ================================================================ dictation

let recognition = null;

/**
 * Type by voice into a text box. Uses the browser's own speech recognition
 * (Chrome and Edge send the audio to their speech service to do it), so it
 * is only started when the button is pressed, and says so.
 */
function toggleDictation() {
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) { toast('このブラウザは音声入力に対応していません（Chrome か Edge で使えます）', 'warn'); return; }
  if (recognition) { recognition.stop(); return; }
  if (!hasDoc()) { toast('先にPDFを開いてください', 'warn'); return; }
  // Speak into the open text box, or start a new one in the middle of the view.
  if (!state.editor || state.editor.meta.isLine) {
    const made = addTextBox('', { edit: true });
    if (!made) return;
  }
  recognition = new Speech();
  recognition.lang = 'ja-JP';
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      if (!event.results[i].isFinal) continue;
      const text = event.results[i][0].transcript;
      if (state.editor && !state.editor.meta.isLine) {
        state.editor.node.focus();
        document.execCommand('insertText', false, text);
      }
    }
  };
  recognition.onerror = (event) => {
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') toast('マイクの使用が許可されていません。アドレスバーの鍵のアイコンから許可してください', 'warn');
    else if (event.error !== 'no-speech' && event.error !== 'aborted') toast(`音声入力を続けられませんでした（${event.error}）`, 'warn');
  };
  recognition.onend = () => { recognition = null; ribbon.refresh(); };
  try {
    recognition.start();
    toast('音声入力中です。話した言葉が文字になります（もう一度押すと止まります。音声はブラウザの音声認識サービスに送られます）');
  } catch {
    recognition = null;
  }
  ribbon.refresh();
}

// ================================================================ snippets

async function editSnippets() {
  const saved = loadSnippets();
  const values = await formDialog({
    title: '定型文を登録する',
    intro: '申込書などによく書く内容を登録しておくと、「定型文」からワンクリックで入れられます。このブラウザの中だけに保存されます。',
    fields: SNIPPET_FIELDS.map(([key, label]) => ({ key, label, value: saved[key] || '' })),
    confirmLabel: '保存',
    wide: true,
  });
  if (values) { saveSnippets(values); toast('定型文を保存しました'); }
}

function snippetMenu(anchor) {
  const saved = loadSnippets();
  const entries = SNIPPET_FIELDS
    .filter(([key]) => (saved[key] || '').trim())
    .map(([key, label]) => ({ label: `${label}: ${saved[key].length > 22 ? `${saved[key].slice(0, 22)}…` : saved[key]}`, action: () => addTextBox(saved[key].trim()) }));
  openMenu(anchor, [
    ...(entries.length ? entries : [{ note: 'まだ登録がありません。氏名・住所などを登録しておくと、ここから1クリックで入れられます。' }]),
    '-',
    { label: '定型文を登録・編集…', icon: 'settings', action: editSnippets },
  ]);
}

// ================================================================ slideshow

const present = { on: false, index: 0, zoom: 'fit-width', bar: null, laser: null, timer: 0 };

function showSlide(index) {
  present.index = Math.max(0, Math.min(viewer.pageViews.length - 1, index));
  for (const view of viewer.pageViews) view.wrap.classList.toggle('showing', view.index === present.index);
  viewer.currentPage = present.index;
  viewer.setZoom('fit-page', { keep: false });
  stage.scrollTop = 0;
  drawOverlay(viewer.pageViews[present.index]);
  present.bar.textContent = `${present.index + 1} / ${viewer.pageViews.length}　← → で移動 ・ マウスを押している間はポインター ・ Esc で終了`;
  present.bar.classList.add('show');
  clearTimeout(present.timer);
  present.timer = setTimeout(() => present.bar.classList.remove('show'), 2600);
}

async function startSlideshow() {
  if (!viewer.pageViews.length || present.on) return;
  flushEditing();
  selectTool('select');
  model.select([]);
  hideSelectionBar();
  present.on = true;
  present.zoom = viewer.zoomMode;
  present.spread = viewer.spread;
  if (viewer.spread) viewer.setSpread(false);
  const start = viewer.currentPage;
  present.bar = document.createElement('div');
  present.bar.className = 'present-bar';
  document.body.append(present.bar);
  document.body.classList.add('present');
  try { await document.documentElement.requestFullscreen?.(); } catch { /* still works windowed */ }
  // Let the browser finish resizing before fitting the page to the screen.
  setTimeout(() => showSlide(start), 120);
}

function stopSlideshow() {
  if (!present.on) return;
  present.on = false;
  const at = present.index;
  document.body.classList.remove('present');
  present.bar?.remove();
  present.laser?.remove();
  present.laser = null;
  for (const view of viewer.pageViews) view.wrap.classList.remove('showing');
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  setTimeout(() => {
    if (present.spread) viewer.setSpread(true);
    viewer.setZoom(present.zoom, { keep: false });
    viewer.scrollToPage(at);
    refreshAll();
  }, 120);
}

document.addEventListener('fullscreenchange', () => {
  if (present.on && !document.fullscreenElement) stopSlideshow();
  else if (present.on) setTimeout(() => showSlide(present.index), 120);
});
window.addEventListener('resize', () => { if (present.on) showSlide(present.index); });

// In a slideshow every key and click is navigation; nothing reaches the tools.
window.addEventListener('keydown', (e) => {
  if (!present.on) return;
  e.stopImmediatePropagation();
  const forward = ['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Enter'];
  const back = ['ArrowLeft', 'ArrowUp', 'PageUp', 'Backspace'];
  if (e.key === 'Escape') { e.preventDefault(); stopSlideshow(); }
  else if (forward.includes(e.key)) { e.preventDefault(); showSlide(present.index + 1); }
  else if (back.includes(e.key)) { e.preventDefault(); showSlide(present.index - 1); }
  else if (e.key === 'Home') showSlide(0);
  else if (e.key === 'End') showSlide(viewer.pageViews.length - 1);
}, true);

for (const type of ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'click', 'dblclick', 'contextmenu', 'wheel']) {
  stage.addEventListener(type, (e) => {
    if (!present.on) return;
    e.stopImmediatePropagation();
    if (type === 'contextmenu' || type === 'wheel' || type === 'mousedown') e.preventDefault();
    if (type === 'wheel') {
      // One notch, one slide — however many events the wheel sends for it.
      const now = Date.now();
      if (now - (present.wheel || 0) > 350) { present.wheel = now; showSlide(present.index + (e.deltaY > 0 ? 1 : -1)); }
    } else if (type === 'contextmenu') {
      showSlide(present.index - 1);
    } else if (type === 'pointerdown' && e.button === 0) {
      present.down = { x: e.clientX, y: e.clientY, at: Date.now(), moved: false };
      present.laser = document.createElement('div');
      present.laser.className = 'laser';
      present.laser.style.left = `${e.clientX}px`;
      present.laser.style.top = `${e.clientY}px`;
      document.body.append(present.laser);
    } else if (type === 'pointermove' && present.laser) {
      present.laser.style.left = `${e.clientX}px`;
      present.laser.style.top = `${e.clientY}px`;
      if (present.down && Math.hypot(e.clientX - present.down.x, e.clientY - present.down.y) > 6) present.down.moved = true;
    } else if (type === 'pointerup') {
      present.laser?.remove();
      present.laser = null;
      // A quick press without movement turns the page; a held or dragged
      // press was pointing at something.
      if (present.down && !present.down.moved && Date.now() - present.down.at < 350 && e.button === 0) showSlide(present.index + 1);
      present.down = null;
    }
  }, true);
}

// ================================================================ autosave to the file

let autosaveTimer;
let autosaving = false;

/**
 * With autosave on, changes are written back to the opened file by
 * themselves, the way a word processor does it. Off by default: it
 * overwrites the original, and that should be something the user chose.
 */
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  if (!getPref('autosave') || !state.fileHandle || !hasDoc()) return;
  autosaveTimer = setTimeout(runAutosave, 20000);
}

async function runAutosave() {
  if (!getPref('autosave') || !state.unsaved || !state.fileHandle || autosaving) return;
  // Not in the middle of typing, a dialog or another operation.
  if (state.editor || noteEditor || state.working || tools.pending || document.querySelector('.dialog-backdrop')) { scheduleAutosave(); return; }
  const handle = state.fileHandle;
  try {
    if (await handle.queryPermission({ mode: 'readwrite' }) !== 'granted') return; // needs a click; Ctrl+S will ask
  } catch { return; }
  autosaving = true;
  $('#saveState').textContent = '自動保存中…';
  try {
    const { blob } = await finishedPdf();
    if (state.fileHandle !== handle) return;
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    state.unsaved = false;
    model.markClean();
    clearDraft(state.draftKey);
    try { state.draftKey = keyFor(await handle.getFile()); } catch { state.draftKey = null; }
  } catch (err) {
    console.warn('autosave failed', err);
  } finally {
    autosaving = false;
    refreshPanels();
  }
}

function toggleAutosave() {
  const next = !getPref('autosave');
  setPref('autosave', next);
  if (next && !window.showSaveFilePicker) {
    toast('このブラウザでは自動保存を使えません（Chrome か Edge で使えます）', 'warn');
  } else if (next) {
    toast(state.fileHandle
      ? '自動保存をオンにしました。変更は少し後に、元のファイルへ自動で上書きされます'
      : '自動保存をオンにしました。「開く」から開いたファイル（または一度保存したファイル）に自動で上書きされます');
    scheduleAutosave();
  } else {
    toast('自動保存をオフにしました');
  }
  refreshPanels();
}

// ================================================================ edit body text

async function loadTextLines(page) {
  if (state.textLines.has(page) || !hasDoc()) return state.textLines.get(page) || [];
  state.textLines.set(page, []);
  try {
    const response = await fetch(docUrl(`/text/${page}`));
    if (response.ok) state.textLines.set(page, (await response.json()).lines || []);
  } catch { /* leave it empty */ }
  return state.textLines.get(page);
}

function lineAt(view, point) {
  const lines = state.textLines.get(view.index) || [];
  return lines.find((l) => point.x >= l.rect[0] - 2 && point.x <= l.rect[2] + 2
    && point.y >= l.rect[1] - 1 && point.y <= l.rect[3] + 1) || null;
}

let hoverLine = null;
function clearTextLineHover() {
  hoverLine?.remove();
  hoverLine = null;
}

stage.addEventListener('pointermove', (e) => {
  if (tools.tool !== 'edittext' || state.editor) return;
  const view = viewer.viewFromEvent(e);
  if (!view) { clearTextLineHover(); return; }
  if (!state.textLines.has(view.index)) { loadTextLines(view.index); return; }
  const line = lineAt(view, viewer.toPageCoords(view, e));
  clearTextLineHover();
  if (!line) return;
  hoverLine = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  hoverLine.setAttribute('class', 'text-line hover');
  hoverLine.setAttribute('x', line.rect[0] - 1);
  hoverLine.setAttribute('y', line.rect[1] - 1);
  hoverLine.setAttribute('width', line.rect[2] - line.rect[0] + 2);
  hoverLine.setAttribute('height', line.rect[3] - line.rect[1] + 2);
  view.draw.append(hoverLine);
});

tools.addEventListener('text-line', async (e) => {
  const { view, point } = e.detail;
  await loadTextLines(view.index);
  const line = lineAt(view, point);
  if (!line) { toast('この場所に書き換えられる文字が見つかりません（画像の中の文字は書き換えられません）', 'warn'); return; }
  clearTextLineHover();
  const size = line.size;
  const lineHeight = size * LINE_HEIGHT;
  const middle = (line.rect[1] + line.rect[3]) / 2;
  const pseudo = {
    id: `line-${view.index}`, type: 'freetext', page: view.index, text: line.text, autoWidth: true,
    rect: [line.rect[0] - 3, middle - lineHeight / 2 - 2, line.rect[2] + 3, middle + lineHeight / 2 + 2],
    style: { font: { family: line.serif ? 'mincho' : 'gothic', size, color: line.colour, bold: line.bold, align: 'left' } },
  };
  const editor = new TextEditor({
    wrap: view.wrap, annot: pseudo, scale: viewer.scale, pageWidth: view.width,
    onCommit: async (text) => {
      state.editor = null;
      if (text === line.text) return;
      const result = await structural('/text/replace', {
        page: view.index, pageRect: line.pageRect, origin: line.origin, text,
        size: line.size, colour: line.colour, serif: line.serif, bold: line.bold,
      }, { label: '本文の書き換え' });
      if (result) toast(text ? '本文を書き換えました（Ctrl+Z で戻せます）' : '行を削除しました（Ctrl+Z で戻せます）');
    },
  });
  editor.meta = { id: pseudo.id, isNew: false, isLine: true };
  state.editor = editor;
  editor.focus({ point: { x: e.detail.event.clientX, y: e.detail.event.clientY } });
});

// ================================================================ floating bar

const bar = $('#selectionBar');
bar.addEventListener('mousedown', (e) => e.preventDefault());
let barTimer;

function hideSelectionBar() {
  bar.hidden = true;
}

function barButton(icon, label, run, { text = false } = {}) {
  const button = document.createElement('button');
  button.className = `rbtn${text ? '' : ' iconly'}`;
  button.title = label;
  // The visible label is the part before any bracketed explanation.
  button.innerHTML = iconSvg(icon, 17) + (text ? `<span class="lbl">${label.split('（')[0]}</span>` : '');
  button.addEventListener('click', (e) => { e.stopPropagation(); run(); });
  return button;
}

function showBarAt(x, y, buttons) {
  bar.textContent = '';
  bar.append(...buttons.filter(Boolean));
  bar.hidden = false;
  const width = bar.offsetWidth;
  const left = Math.max(6, Math.min(x - width / 2, window.innerWidth - width - 6));
  const top = y - bar.offsetHeight - 8;
  bar.style.left = `${left}px`;
  bar.style.top = `${top < 150 ? y + 26 : top}px`;
}

function placeSelectionBar() {
  if (state.editor || noteEditor || tools.pending || tools.tool !== 'select') { hideSelectionBar(); return; }
  const selection = selectedAnnots();
  if (!selection.length) {
    if (!textSelectionActive()) hideSelectionBar();
    return;
  }
  const page = selection[0].page;
  const view = viewer.pageViews[page];
  if (!view || selection.some((a) => a.page !== page)) { hideSelectionBar(); return; }
  const box = view.wrap.getBoundingClientRect();
  const frame = stage.getBoundingClientRect();
  const x0 = Math.min(...selection.map((a) => a.rect[0]));
  const x1 = Math.max(...selection.map((a) => a.rect[2]));
  const y0 = Math.min(...selection.map((a) => a.rect[1]));
  const cx = box.left + ((x0 + x1) / 2) * viewer.scale;
  const cy = box.top + y0 * viewer.scale;
  if (cy < frame.top - 10 || cy > frame.bottom || cx < frame.left || cx > frame.right) { hideSelectionBar(); return; }
  const single = selection.length === 1 ? selection[0] : null;
  const sep = () => { const s = document.createElement('span'); s.className = 'rsep'; return s; };
  showBarAt(cx, cy, [
    single && (single.type === 'freetext' || single.type === 'note')
      ? barButton('edittext', '文字を編集', () => startTextEdit(single.id), { text: true }) : null,
    single && single.type !== 'freetext'
      ? barButton('comments', 'コメントを付ける', () => showRightPanel('props')) : null,
    sep(),
    barButton('duplicate', '複製 (Ctrl+D)', duplicateSelection),
    barButton('copy', 'コピー (Ctrl+C)', copySelection),
    barButton('trash', '削除 (Delete)', deleteSelection),
  ]);
}

function scheduleBar() {
  clearTimeout(barTimer);
  barTimer = setTimeout(() => { if (!textSelectionActive()) placeSelectionBar(); else showTextSelectionBar(); }, 180);
}

function textSelectionActive() {
  const selection = document.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return false;
  const anchor = selection.anchorNode?.parentElement;
  return !!anchor?.closest('.text-layer');
}

/** Selected page text gets the markup tools right next to it. */
function showTextSelectionBar() {
  if (tools.tool !== 'select' || !textSelectionActive() || tools._activePointers.size) { return; }
  const range = document.getSelection().getRangeAt(0);
  const rects = [...range.getClientRects()].filter((r) => r.width > 1 && r.height > 1);
  if (!rects.length) return;
  const first = rects[0];
  const mark = (kind) => () => { tools.markupSelection(kind); hideSelectionBar(); };
  showBarAt(first.left + Math.min(first.width, 240) / 2, first.top, [
    barButton('highlight', 'マーカー', mark('highlight'), { text: true }),
    ...['#ffe14d', '#8ee59a', '#ff9ec4', '#8fd3ff'].map((colour) => {
      // One click per colour: students keep a colour code (term, definition,
      // example…), and picking the colour first each time would be tedious.
      const dot = document.createElement('button');
      dot.className = 'dot';
      dot.style.background = colour;
      dot.title = 'この色でマーカーを引く';
      dot.addEventListener('click', (e) => { e.stopPropagation(); remember('highlight', { stroke: colour }); mark('highlight')(); });
      return dot;
    }),
    barButton('underline', '下線', mark('underline')),
    barButton('strikeout', '取り消し線', mark('strikeout')),
    barButton('squiggly', '波線', mark('squiggly')),
    (() => { const s = document.createElement('span'); s.className = 'rsep'; return s; })(),
    barButton('note', 'コメント（選んだ文字にマーカーを引いて、メモを付ける）', () => {
      const before = new Set(model.store.annots.map((a) => a.id));
      if (!tools.markupSelection('highlight')) return;
      hideSelectionBar();
      const made = model.store.annots.filter((a) => !before.has(a.id));
      if (!made.length) return;
      model.select([made[0].id]);
      showRightPanel('props');
      // The comment box is the first field of the panel: put the caret in it.
      setTimeout(() => $('#panelProps textarea')?.focus(), 60);
    }, { text: true }),
    barButton('copy', 'コピー (Ctrl+C)', () => {
      navigator.clipboard?.writeText(document.getSelection().toString()).then(() => toast('コピーしました')).catch(() => document.execCommand('copy'));
      hideSelectionBar();
    }),
    barButton('search', 'この語句を検索', () => {
      $('#searchInput').value = document.getSelection().toString().trim().slice(0, 80);
      hideSelectionBar();
      runSearch();
    }),
    barButton('redact', '墨消しを指定', mark('redact')),
    (() => { const s = document.createElement('span'); s.className = 'rsep'; return s; })(),
    barButton('translate', '翻訳（選んだ文字を Google 翻訳に送って新しいタブで開きます）', () => lookUp('translate'), { text: true }),
    barButton('web', 'Web で調べる（選んだ文字を Google 検索に送ります）', () => lookUp('search')),
  ]);
}

document.addEventListener('selectionchange', () => {
  if (tools.tool !== 'select') return;
  if (!textSelectionActive()) { if (!selectedAnnots().length) hideSelectionBar(); return; }
  scheduleBar();
});
document.addEventListener('pointerup', () => { if (tools.tool === 'select') scheduleBar(); });
stage.addEventListener('pointerdown', () => hideSelectionBar());

// ================================================================ context menu

stage.addEventListener('contextmenu', (e) => {
  if (e.target.closest?.('.ft-host, .note-editor')) return;
  if (!hasDoc()) return;
  e.preventDefault();
  flushEditing();
  const view = viewer.viewFromEvent(e);
  const hit = e.target.closest?.('.annot');
  if (hit && tools.tool !== 'select') selectTool('select');
  if (hit) {
    const id = hit.dataset.id;
    if (!model.store.selection.includes(id)) model.select([id]);
    const selection = selectedAnnots();
    const single = selection.length === 1 ? selection[0] : null;
    const locked = selection.some((a) => a.flags?.locked);
    openMenuAt(e.clientX, e.clientY, [
      single && (single.type === 'freetext' || single.type === 'note')
        ? { label: '文字を編集', icon: 'edittext', action: () => startTextEdit(single.id) } : null,
      { label: '切り取り', icon: 'cut', key: 'Ctrl+X', disabled: locked, action: cutSelection },
      { label: 'コピー', icon: 'copy', key: 'Ctrl+C', action: copySelection },
      { label: '複製', icon: 'duplicate', key: 'Ctrl+D', action: duplicateSelection },
      { label: '削除', icon: 'trash', key: 'Delete', disabled: locked, danger: true, action: deleteSelection },
      '-',
      ...(selection.length > 1 ? [
        { heading: `${selection.length} 件をそろえる` },
        { label: '左をそろえる', icon: 'alignleft', action: () => arrange('left') },
        { label: '上をそろえる', icon: 'moveup', action: () => arrange('top') },
        { label: '右をそろえる', icon: 'alignright', action: () => arrange('right') },
        { label: '下をそろえる', icon: 'movedown', action: () => arrange('bottom') },
        { label: '横に等間隔', action: () => arrange('spreadX') },
        { label: '縦に等間隔', action: () => arrange('spreadY') },
        '-',
      ] : []),
      { label: locked ? 'ロックを解除' : 'ロック（動かせなくする）', icon: 'lock',
        action: () => model.updateAnnots(selection.map((a) => a.id), { flags: { locked: !locked } }) },
      { label: 'コメント・プロパティ…', icon: 'comments', action: () => showRightPanel('props') },
    ]);
    return;
  }
  const at = view ? { view, point: viewer.toPageCoords(view, e) } : null;
  openMenuAt(e.clientX, e.clientY, [
    { label: '貼り付け', icon: 'paste', key: 'Ctrl+V', disabled: !clip.length, action: () => pasteAnnots() },
    '-',
    { label: 'ここにテキストを追加', icon: 'text', disabled: !at, action: () => addTextBox('', { at, edit: true }) },
    { label: 'ここに付箋を貼る', icon: 'note', disabled: !at, action: () => {
      selectTool('note');
      tools._createNote(at.view, at.point);
    } },
    { label: '画像を挿入…', icon: 'image', action: () => $('#imageInput').click() },
    '-',
    { label: 'このページの書き込みを全選択', key: 'Ctrl+A', action: selectAllOnPage },
    { label: `${viewer.currentPage + 1} ページ目を右に回転`, icon: 'rotatecw', action: () => ops.rotatePages(90) },
    { label: 'ページを画像にする…', icon: 'image', action: ops.exportImages },
  ]);
});

function selectAllOnPage() {
  selectTool('select');
  model.select(model.onPage(viewer.currentPage).map((a) => a.id));
}

// ================================================================ side panels

for (const tabs of document.querySelectorAll('.side-tabs')) {
  tabs.addEventListener('click', (e) => {
    const close = e.target.closest('.side-close');
    if (close) { tabs.closest('.side').classList.add('collapsed'); onLayoutChange(); return; }
    const tab = e.target.closest('.side-tab');
    if (!tab) return;
    activateSideTab(tabs.closest('.side'), tab.dataset.panel);
  });
}

function activateSideTab(side, name) {
  for (const other of side.querySelectorAll('.side-tab')) other.classList.toggle('active', other.dataset.panel === name);
  for (const panel of side.querySelectorAll('.panel')) {
    panel.classList.toggle('active', panel.id.toLowerCase() === `panel${name}`.toLowerCase());
  }
  refreshPanels();
}

function onLayoutChange() {
  if (viewer.zoomMode.startsWith('fit')) viewer.setZoom(viewer.zoomMode);
  refreshPanels();
}

function showRightPanel(name) {
  const side = $('#rightPanel');
  const wasHidden = side.classList.contains('collapsed');
  side.classList.remove('collapsed');
  activateSideTab(side, name);
  if (wasHidden) onLayoutChange();
}

function toggleSide(id) {
  $(id).classList.toggle('collapsed');
  onLayoutChange();
}

const thumbHandlers = {
  onClick: (index, e) => {
    if (e.ctrlKey || e.metaKey) {
      if (state.pageSelection.has(index)) state.pageSelection.delete(index); else state.pageSelection.add(index);
      state.pageAnchor = index;
    } else if (e.shiftKey) {
      const [from, to] = [Math.min(state.pageAnchor, index), Math.max(state.pageAnchor, index)];
      state.pageSelection = new Set(Array.from({ length: to - from + 1 }, (_, i) => from + i));
    } else {
      state.pageSelection = new Set([index]);
      state.pageAnchor = index;
      viewer.scrollToPage(index);
    }
    updateThumbs($('#panelThumbs'), { current: viewer.currentPage, selected: state.pageSelection });
    refreshPanels();
  },
  onContext: (index, e) => {
    if (!state.pageSelection.has(index)) {
      state.pageSelection = new Set([index]);
      state.pageAnchor = index;
      updateThumbs($('#panelThumbs'), { current: viewer.currentPage, selected: state.pageSelection });
    }
    const pages = targetPages();
    const label = describePages(pages);
    openMenuAt(e.clientX, e.clientY, [
      { heading: label },
      { label: '右に90°回転', icon: 'rotatecw', action: () => ops.rotatePages(90) },
      { label: '左に90°回転', icon: 'rotateccw', action: () => ops.rotatePages(-90) },
      { label: '複製', icon: 'pagecopy', action: ops.duplicatePages },
      { label: 'このあとに白紙を挿入', icon: 'pageblank', action: ops.insertBlank },
      { label: 'このあとに別のPDFを結合…', icon: 'merge', action: ops.pickMerge },
      '-',
      { label: '上へ移動', icon: 'moveup', action: () => ops.movePagesBy(-1) },
      { label: '下へ移動', icon: 'movedown', action: () => ops.movePagesBy(1) },
      '-',
      { label: '抜き出して別のPDFにする…', icon: 'pageextract', action: ops.extractPages },
      { label: '画像にする…', icon: 'image', action: ops.exportImages },
      '-',
      { label: '削除', icon: 'pagedelete', danger: true, action: ops.deletePages },
    ]);
  },
  onDragStart: (index) => {
    if (!state.pageSelection.has(index)) {
      state.pageSelection = new Set([index]);
      state.pageAnchor = index;
      updateThumbs($('#panelThumbs'), { current: viewer.currentPage, selected: state.pageSelection });
    }
  },
  onDrop: (insertAt) => ops.movePages(targetPages(), insertAt),
};

async function saveOutline(toc) {
  const response = await post('/outline', { toc });
  if (!response.ok) { toast(`しおりを保存できませんでした: ${await errorDetail(response)}`, 'error'); return; }
  state.toc = (await response.json()).toc || [];
  state.unsaved = true;
  renderOutlinePanel();
  refreshPanels();
}

function renderOutlinePanel() {
  renderOutline($('#panelOutline'), state.toc, {
    canEdit: hasDoc(),
    onGo: (page) => viewer.scrollToPage(page),
    onAuto: async () => {
      if (state.toc.length) {
        const ok = await confirmDialog({
          title: 'しおりを作り直しますか',
          intro: `今ある ${state.toc.length} 件のしおりを、見出しから作ったものに置き換えます。`,
          confirmLabel: '作り直す',
        });
        if (!ok) return;
      }
      const response = await post('/outline-auto', {});
      if (!response.ok) { toast(await errorDetail(response), 'warn'); return; }
      state.toc = (await response.json()).toc || [];
      state.unsaved = true;
      renderOutlinePanel();
      refreshPanels();
      toast(`見出しから ${state.toc.length} 件のしおりを作りました`);
    },
    onAdd: async () => {
      const page = viewer.currentPage + 1;
      const values = await formDialog({
        title: 'しおりを追加',
        fields: [{ key: 'title', label: '名前', value: `${page} ページ` }],
        confirmLabel: '追加',
      });
      if (!values || !values.title.trim()) return;
      const toc = [...state.toc.map((row) => [...row]), [1, values.title.trim(), page]];
      toc.sort((a, b) => a[2] - b[2]);
      saveOutline(toc);
    },
    onRename: (index, e) => {
      openMenuAt(e.clientX, e.clientY, [
        { label: '名前を変更…', action: async () => {
          const values = await formDialog({
            title: 'しおりの名前を変更',
            fields: [{ key: 'title', label: '名前', value: state.toc[index][1] }],
            confirmLabel: '変更',
          });
          if (!values || !values.title.trim()) return;
          const toc = state.toc.map((row) => [...row]);
          toc[index][1] = values.title.trim();
          saveOutline(toc);
        } },
        { label: '削除', danger: true, action: () => saveOutline(state.toc.filter((_, i) => i !== index).map((row) => [...row])) },
      ]);
    },
  });
}

// ================================================================ zoom & paging

function syncZoomControls() {
  const select = $('#zoomSelect');
  $('#zoomSlider').value = String(Math.round(viewer.scale * 100));
  if (viewer.zoomMode.startsWith('fit')) { select.value = viewer.zoomMode; return; }
  const exact = [...select.options].find((o) => Math.abs(Number(o.value) - viewer.scale) < 0.001);
  if (exact) { select.value = exact.value; return; }
  // The presets do not cover every level, and assigning an unlisted value to
  // a <select> blanks it out — so keep an entry for the current level.
  let custom = select.querySelector('option[data-custom]');
  if (!custom) { custom = document.createElement('option'); custom.dataset.custom = 'true'; }
  custom.value = String(viewer.scale);
  custom.textContent = `${Math.round(viewer.scale * 100)}%`;
  const next = [...select.options].find((o) => !o.dataset.custom && Number(o.value) > viewer.scale);
  select.insertBefore(custom, next ?? null);
  select.value = custom.value;
}

for (const button of document.querySelectorAll('[data-zoom]')) {
  button.addEventListener('click', () => viewer.nudgeZoom(button.dataset.zoom === 'in' ? 1 : -1));
}
$('#zoomSelect').addEventListener('change', (e) => viewer.setZoom(e.target.value));
$('#zoomSlider').addEventListener('input', (e) => viewer.setZoom(String(Number(e.target.value) / 100)));

function goToPage(index) {
  if (!viewer.pageViews.length) return;
  viewer.scrollToPage(Math.max(0, Math.min(viewer.pageViews.length - 1, index)));
}
$('#btnPagePrev').addEventListener('click', () => goToPage(viewer.currentPage - 1));
$('#btnPageNext').addEventListener('click', () => goToPage(viewer.currentPage + 1));
$('#pageInput').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || isComposing(e)) return;
  const wanted = Number(e.target.value.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)));
  if (Number.isFinite(wanted) && wanted >= 1) goToPage(Math.round(wanted) - 1);
  e.target.blur();
});
$('#pageInput').addEventListener('focus', (e) => e.target.select());
$('#pageInput').addEventListener('blur', (e) => { e.target.value = viewer.pageViews.length ? String(viewer.currentPage + 1) : ''; });

// ================================================================ search

async function runSearch() {
  const query = $('#searchInput').value.trim();
  state.searchQuery = query;
  if (!query || !hasDoc()) {
    state.searchHits = [];
    state.searchIndex = -1;
    $('#searchCount').textContent = '';
    drawSearchHits();
    return;
  }
  const response = await post('/search', { query });
  if (!response.ok) { toast('検索に失敗しました', 'error'); return; }
  const data = await response.json();
  if ($('#searchInput').value.trim() !== query) return; // the query moved on while this ran
  state.searchHits = data.hits || [];
  state.searchIndex = state.searchHits.length ? 0 : -1;
  $('#searchCount').textContent = state.searchHits.length ? `${state.searchHits.length} 件` : '該当なし';
  drawSearchHits();
  ribbon.refresh();
  if (state.searchIndex >= 0) {
    // Start from the first hit at or after the page being read.
    const from = state.searchHits.findIndex((hit) => hit.page >= viewer.currentPage);
    goToHit(from >= 0 ? from : 0);
  } else if (!model.store.pages.length) {
    toast('検索できる文字がありません');
  }
}

function drawSearchHits() {
  for (const view of viewer.pageViews) {
    for (const hit of view.draw.querySelectorAll('.search-hit')) hit.remove();
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

let searchTimer;
$('#searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  // Typing through an input method fires input events for the unconverted
  // kana; wait for the conversion to finish before searching.
  if (e.isComposing) return;
  searchTimer = setTimeout(runSearch, 400);
});
$('#searchInput').addEventListener('compositionend', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });
$('#searchInput').addEventListener('keydown', (e) => {
  e.stopPropagation();
  if (isComposing(e)) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    clearTimeout(searchTimer);
    if (state.searchHits.length && state.searchQuery === e.target.value.trim()) goToHit(state.searchIndex + (e.shiftKey ? -1 : 1));
    else runSearch();
  } else if (e.key === 'Escape') {
    e.target.value = '';
    runSearch();
    e.target.blur();
  }
});
$('#btnSearchNext').addEventListener('click', () => (state.searchHits.length ? goToHit(state.searchIndex + 1) : runSearch()));
$('#btnSearchPrev').addEventListener('click', () => goToHit(state.searchIndex - 1));

function markAllHits() {
  if (!state.searchHits.length) { toast('先に右上の検索欄で検索してください', 'warn'); return; }
  const kind = MARKUP_TOOLS.has(tools.tool) ? tools.tool : 'highlight';
  const items = state.searchHits.map((hit) => {
    const quad = hit.quad;
    const xs = [quad[0], quad[2], quad[4], quad[6]];
    const ys = [quad[1], quad[3], quad[5], quad[7]];
    return {
      type: kind, page: hit.page, quads: [quad],
      rect: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
      style: styleFor(kind), author: getPref('author') || '',
      subject: state.searchQuery, flags: FLAGS(),
    };
  });
  model.addAnnots(items, { select: false });
  toast(`「${state.searchQuery}」${items.length} 箇所に${typeLabel({ type: kind })}を引きました`);
}

// ================================================================ measuring

function startCalibration() {
  selectTool('calibrate');
  toast('図面上で、実際の長さが分かっている2点を順にクリックしてください');
}

tools.addEventListener('calibrated', async (e) => {
  const [p1, p2] = e.detail.points;
  const pagePoints = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
  const values = await formDialog({
    title: '縮尺を設定',
    intro: 'なぞった2点の間は、実際にはどれだけの長さですか。',
    fields: [
      { key: 'realLength', label: '実際の長さ', type: 'number', value: 1, min: 0.0001, step: 0.01 },
      { key: 'unit', label: '単位', type: 'select', options: measure.UNIT_LABELS, value: measure.getScale().unit },
    ],
    validate: (v) => (v.realLength > 0 ? null : '0より大きい長さを入力してください'),
    confirmLabel: '設定する',
  });
  selectTool('select');
  if (!values) return;
  measure.calibrateFrom(p1, p2, values.realLength, values.unit);
  refreshPanels();
  toast(`縮尺を設定しました（図面上 ${pagePoints.toFixed(0)}pt = ${values.realLength}${values.unit}）`);
});

tools.addEventListener('measured', () => {
  if (!measure.isCalibrated()) toast('縮尺が未設定のため、値は紙の上の長さです。「縮尺」で設定できます', 'warn');
});

/** Draw the legend as annotations, so it moves and prints with the markup. */
function placeLegend() {
  const rows = measure.summarise(model.store.annots);
  if (!rows.length) return;
  const page = viewer.currentPage;
  const width = 210;
  const lineHeight = 16;
  const x = 40;
  let y = 40;
  const items = [{
    type: 'square', page,
    rect: [x - 8, y - 8, x + width, y + rows.length * lineHeight + 12],
    style: { stroke: '#1c1f26', fill: '#ffffff', width: 1, opacity: 0.95, cloudIntensity: 0 },
    subject: '凡例', author: getPref('author') || '', flags: FLAGS(),
  }];
  const texts = [];
  for (const row of rows) {
    items.push({
      type: 'square', page, rect: [x, y + 3, x + 10, y + 13],
      style: { stroke: row.colour, fill: row.colour, width: 1, opacity: 1 },
      subject: '凡例', flags: FLAGS(),
    });
    const total = row.kind === 'count' ? `${row.count} 個` : `${row.count} 件 / ${row.total} ${row.unit}`;
    const id = model.uid();
    texts.push(id);
    items.push({
      id, type: 'freetext', page, rect: [x + 14, y, x + width - 4, y + lineHeight],
      text: `${row.label}  ${total}`, contents: `${row.label}  ${total}`, autoWidth: true,
      style: { stroke: '#1c1f26', fill: null, width: 0, opacity: 1,
        font: { family: 'gothic', size: 9, color: '#1c1f26', align: 'left', bold: false } },
      subject: '凡例', flags: FLAGS(),
    });
    y += lineHeight;
  }
  model.addAnnots(items, { select: false });
  ensureFontLoaded({ family: 'gothic', size: 9 }).then(() => refitText(texts));
  viewer.scrollToPage(page, 0);
  toast(`凡例を ${page + 1} ページ目の左上に置きました`);
}

// ================================================================ extras

let speaking = false;
async function readAloud() {
  if (!('speechSynthesis' in window)) { toast('このブラウザは読み上げに対応していません', 'warn'); return; }
  if (speaking) { window.speechSynthesis.cancel(); speaking = false; ribbon.refresh(); return; }
  const selected = document.getSelection()?.toString().trim();
  let text = selected;
  if (!text) {
    const response = await fetch(docUrl(`/page-text/${viewer.currentPage}`));
    text = response.ok ? (await response.json()).text : '';
  }
  if (!text || !text.trim()) { toast('このページには読み上げられる文字がありません', 'warn'); return; }
  const utterance = new SpeechSynthesisUtterance(text.replace(/\s*\n\s*/g, ' '));
  // Mostly-Latin text is read with an English voice, otherwise Japanese.
  const latin = (text.match(/[A-Za-z]/g) || []).length;
  utterance.lang = latin > text.length * 0.5 ? 'en-US' : 'ja-JP';
  utterance.onend = () => { speaking = false; ribbon.refresh(); };
  utterance.onerror = utterance.onend;
  speaking = true;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  ribbon.refresh();
  toast(selected ? '選択した文字を読み上げています（もう一度押すと止まります）' : `${viewer.currentPage + 1} ページ目を読み上げています（もう一度押すと止まります）`);
}

/**
 * Recognise the text in scanned pages so they can be searched, selected and
 * highlighted. Runs entirely in the browser; the recogniser and its language
 * data are downloaded the first time (about 20 MB) and cached after that.
 */
async function runOcr() {
  const values = await formDialog({
    title: 'スキャンした文書を文字認識する（OCR）',
    intro: '画像になっている文字を読み取り、検索・選択・マーカーができるようにします。見た目は変わりません。',
    warning: '初回は認識エンジンと言語データ（約20MB）をダウンロードします。1ページあたり数秒〜十数秒かかります。処理はこの端末の中だけで行われます。',
    fields: [
      { key: 'scope', label: '対象', type: 'select', options: { picked: describePages(targetPages()), all: `すべてのページ（${model.store.pages.length}）` } },
      { key: 'lang', label: '言語', type: 'select', options: { 'jpn+eng': '日本語 + 英語', eng: '英語のみ', jpn: '日本語のみ' } },
    ],
    confirmLabel: '認識する',
  });
  if (!values) return;
  const pages = values.scope === 'all' ? model.store.pages.map((_, i) => i) : targetPages();
  const done = busy('文字認識の準備をしています…');
  let worker = null;
  try {
    await paint();
    const tesseract = await import('https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.esm.min.js');
    const createWorker = tesseract.createWorker || tesseract.default.createWorker;
    worker = await createWorker(values.lang.split('+'));
    const sheets = [];
    for (const [count, index] of pages.entries()) {
      $('#busyText').textContent = `文字を認識しています… ${count + 1} / ${pages.length} ページ`;
      const view = viewer.pageViews[index];
      const scale = Math.min(3, 2200 / Math.max(view.width, view.height));
      const viewport = view.page.getViewport({ scale });
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      await view.page.render({ canvasContext: canvas.getContext('2d'), viewport, canvas, annotationMode: 0 }).promise;
      const { data } = await worker.recognize(canvas);
      const words = [];
      for (const w of (data.words || [])) {
        if (!w.text || !w.text.trim() || w.confidence < 35) continue;
        // The line's height gives a steadier text size than one word's own
        // box, which shrinks around small kana and punctuation.
        const line = w.line?.bbox || w.bbox;
        const cjk = /[　-鿿＀-￯]/.test(w.text);
        // Japanese has no spaces, so the recogniser's "words" are arbitrary
        // runs; placing each character where it was seen keeps a selection
        // or a search hit exactly over the glyphs.
        const parts = cjk && w.symbols?.length ? w.symbols : [w];
        for (const part of parts) {
          if (!part.text || !part.text.trim()) continue;
          words.push({
            text: part.text.trim(),
            rect: [part.bbox.x0 / scale, line.y0 / scale, part.bbox.x1 / scale, line.y1 / scale],
          });
        }
      }
      sheets.push({ page: index, words });
    }
    await worker.terminate();
    worker = null;
    done();
    const total = sheets.reduce((sum, sheet) => sum + sheet.words.length, 0);
    if (!total) { toast('文字を認識できませんでした', 'warn'); return; }
    const result = await structural('/ocr-apply', { pages: sheets }, { label: '認識結果の埋め込み' });
    if (result) toast(`${pages.length} ページから ${result.characters} 文字を認識しました。検索やマーカーが使えます`);
  } catch (err) {
    toast(`文字認識に失敗しました: ${err.message}（初回はインターネット接続が必要です）`, 'error');
  } finally {
    try { await worker?.terminate(); } catch { /* already gone */ }
    done();
  }
}

function showHelp() {
  const rows = [
    ['文字を書き込む', '「ホーム」→「テキスト追加」→ 書きたい場所をクリックして、そのまま入力。枠の外をクリックで確定'],
    ['書いた文字を直す', '文字をクリックするだけで、その場で編集できます。枠をドラッグすると移動'],
    ['フォント・色・サイズ', '文字を選んだ状態で「ホーム」の「フォント」。ゴシック・明朝・丸ゴシック・教科書体・手書き風'],
    ['マーカー・下線', '文字をなぞって選ぶと、その場にボタンが出ます。またはマーカーを選んでからなぞる'],
    ['申込書などに記入', '「テキスト追加」で文字、「挿入」→「チェック」で ✓ や ○、「はんこ」「署名」で押印・サイン'],
    ['元の文章を直す', '「ホーム」→「本文を編集」→ 直したい行をクリック'],
    ['手書き', '「描画」タブのペン・蛍光ペン。ペン対応タブレットでは筆圧が効きます'],
    ['ページの整理', '左のページ一覧でドラッグして並べ替え、右クリックで回転・削除・複製・結合'],
    ['講義資料にメモ欄', '「ページ」→「余白を足す」でスライドの横にノート用スペース'],
    ['スキャンを検索可能に', '「ツール」→「文字認識（OCR）」'],
    ['保存', 'Ctrl+S で元のファイルに上書き。タイトルバーに ● が出ている間は未保存です'],
    ['元に戻す', 'Ctrl+Z。ページの削除や墨消しの適用も戻せます'],
  ];
  const body = node('div', { class: 'help-body' }, [
    node('table', {}, rows.map(([what, how]) => node('tr', {}, [node('td', { text: what }), node('td', { text: how })]))),
    node('h3', { text: 'キーボード' }),
    node('table', {}, Object.entries(SHORTCUTS).map(([key, label]) => node('tr', {}, [node('td', { text: key }), node('td', { text: label })]))),
    node('h3', { text: 'プライバシー' }),
    node('p', { text: 'PDFはこの端末のブラウザの中だけで処理され、サーバーには送信されません。インターネット接続が要るのは、最初の起動と、初めて文字認識（OCR）を使うときだけです。' }),
  ]);
  customDialog({ title: 'PDF Studio の使い方', wide: true, confirmLabel: '閉じる', hideCancel: true, build: () => body });
}

// ================================================================ commands

const tool = (name, label, icon, extra = {}) => ({
  label, icon, run: () => selectTool(tools.tool === name && name !== 'select' ? 'select' : name),
  active: () => tools.tool === name, enabled: hasDoc, ...extra,
});
const needsDoc = { enabled: hasDoc };
const needsSelection = { enabled: () => model.store.selection.length > 0 };

const commands = {
  // clipboard
  paste: { label: '貼り付け', icon: 'paste', key: 'Ctrl+V', run: () => { if (!pasteAnnots()) toast('貼り付けるものがありません。書き込みをコピーするか、画像をコピーして Ctrl+V', 'warn'); }, ...needsDoc },
  cut: { label: '切り取り', icon: 'cut', key: 'Ctrl+X', run: cutSelection, ...needsSelection },
  copy: { label: 'コピー', icon: 'copy', key: 'Ctrl+C', run: copySelection, ...needsSelection },
  duplicate: { label: '複製', icon: 'duplicate', key: 'Ctrl+D', run: duplicateSelection, ...needsSelection },
  remove: { label: '削除', icon: 'trash', key: 'Delete', run: deleteSelection, ...needsSelection },
  // pointer
  select: tool('select', '選択', 'select', { title: '選択（書き込みを選ぶ・動かす／文字を選ぶ）', key: 'Esc' }),
  pan: tool('pan', '手のひら', 'pan', { title: '手のひら（ドラッグでページを動かす）' }),
  // text
  freetext: tool('freetext', 'テキスト追加', 'text', { short: 'テキスト\n追加', title: 'テキスト追加（クリックした場所に文字を書き込む）' }),
  edittext: tool('edittext', '本文を編集', 'edittext', { short: '本文を\n編集', title: '本文を編集（PDFに元からある文字を書き換える）' }),
  callout: tool('callout', '引き出し線', 'callout', { title: '引き出し線つきテキスト（指したい場所から引っぱる）' }),
  note: tool('note', '付箋', 'note', { title: '付箋（クリックした場所にメモを貼る）' }),
  // markup
  highlight: tool('highlight', 'マーカー', 'highlight', { title: 'マーカー（文字をなぞる）' }),
  underline: tool('underline', '下線', 'underline'),
  strikeout: tool('strikeout', '取り消し線', 'strikeout'),
  squiggly: tool('squiggly', '波線', 'squiggly'),
  areaHighlight: tool('areaHighlight', '範囲マーカー', 'areahighlight', { title: '範囲マーカー（スキャンした文書や図に。囲んだ範囲を塗る）' }),
  // drawing
  pen: tool('pen', 'ペン', 'pen'),
  marker: tool('marker', '蛍光ペン', 'marker', { title: '蛍光ペン（フリーハンド）' }),
  eraser: tool('eraser', '消しゴム', 'eraser', { title: '消しゴム（手書きの線をなぞって消す）' }),
  lasso: tool('lasso', '投げ縄', 'lasso', { title: '投げ縄（囲んだ中の書き込みをまとめて選ぶ）' }),
  // shapes
  line: tool('line', '直線', 'line'),
  arrow: tool('arrow', '矢印', 'arrow'),
  square: tool('square', '四角形', 'square'),
  circle: tool('circle', '円・だ円', 'circle'),
  polygon: tool('polygon', '多角形', 'polygon'),
  polyline: tool('polyline', '折れ線', 'polyline'),
  // inserts
  image: { label: '画像', icon: 'image', title: '画像を挿入（写真・図・スクリーンショット。Ctrl+V でも貼れます）', run: () => $('#imageInput').click(), ...needsDoc },
  signature: { label: '署名', icon: 'signature', title: '署名を入れる（手書き・入力・画像）', run: insertSignature, ...needsDoc },
  hanko: { label: 'はんこ', icon: 'hanko', title: 'はんこを作って押す（認印・日付印・角印）', run: insertHanko, ...needsDoc },
  stamp: tool('stamp', 'スタンプ', 'stamp', { title: 'スタンプ（承認済・社外秘など。自由な文言も可）' }),
  date: { label: '日付', icon: 'date', title: '今日の日付を入れる', run: insertDate, ...needsDoc },
  mark: tool('mark', 'チェック', 'check', { title: 'チェック・バツ・丸（申込書などの記入に。クリックした場所に置く）' }),
  whiteout: tool('whiteout', '修正テープ', 'whiteout', { title: '修正テープ（囲んだところを紙の色で隠す。上から書き直せます。隠すだけで、文字はファイルに残ります）' }),
  snapshot: tool('snapshot', '切り抜きコピー', 'snapshot', { short: '切り抜き\nコピー', title: '切り抜きコピー（囲んだ範囲を画像としてコピー。図や表をレポート・ノートに貼るときに）' }),
  crop: tool('crop', '切り取り', 'crop', { title: 'トリミング（囲んだ範囲だけを表示する）' }),
  uncrop: { label: '切り取りを解除', icon: 'crop', title: 'トリミングを解除して、ページ全体を表示する',
    run: async () => { const r = await structural('/pages/reset-crop', { pages: model.store.pages.map((_, i) => i) }, { label: '解除' }); if (r) toast('ページ全体の表示に戻しました'); }, ...needsDoc },
  study: { label: '暗記シート', icon: 'study', title: '暗記シート（マーカーを引いたところを隠す。クリックで答え合わせ）', run: toggleStudy, active: () => state.study, ...needsDoc },
  dictate: { label: '音声入力', icon: 'mic', title: '音声入力（話した言葉をテキストボックスに入力。Chrome / Edge）', run: toggleDictation,
    active: () => !!recognition, ...needsDoc },
  snippet: { label: '定型文', icon: 'snippet', title: '定型文（登録した氏名・住所などを1クリックで入れる）', menu: true, ...needsDoc,
    run: (e, button) => snippetMenu(button) },
  slideshow: { label: 'スライドショー', icon: 'slideshow', short: 'スライド\nショー', title: 'スライドショー（全画面で1ページずつ。発表に）', key: 'F5', run: startSlideshow,
    enabled: () => viewer.pageViews.length > 0 },
  // pages
  rotatecw: { label: '右に回転', icon: 'rotatecw', run: () => ops.rotatePages(90), ...needsDoc },
  rotateccw: { label: '左に回転', icon: 'rotateccw', run: () => ops.rotatePages(-90), ...needsDoc },
  rotateall: { label: '全ページ回転', icon: 'rotatecw', title: 'すべてのページを右に90°回転', run: () => ops.rotateAll(90), ...needsDoc },
  pagedelete: { label: '削除', icon: 'pagedelete', title: 'ページを削除', run: ops.deletePages, ...needsDoc },
  pagecopy: { label: '複製', icon: 'pagecopy', title: 'ページを複製', run: ops.duplicatePages, ...needsDoc },
  pageblank: { label: '白紙を挿入', icon: 'pageblank', title: '今のページのあとに白紙を挿入', run: ops.insertBlank, ...needsDoc },
  merge: { label: 'PDFを結合', icon: 'merge', short: 'PDFを\n結合', title: '別のPDFを今のページのあとに結合（複数選べます）', run: ops.pickMerge, ...needsDoc },
  moveup: { label: '前へ移動', icon: 'moveup', run: () => ops.movePagesBy(-1), ...needsDoc },
  movedown: { label: '後ろへ移動', icon: 'movedown', run: () => ops.movePagesBy(1), ...needsDoc },
  extract: { label: '抜き出す', icon: 'pageextract', title: 'ページを抜き出して別のPDFにする', run: ops.extractPages, ...needsDoc },
  split: { label: '分割', icon: 'split', title: 'PDFを複数のファイルに分割', run: ops.splitDocument, ...needsDoc },
  margins: { label: '余白を足す', icon: 'margins', short: '余白を\n足す', title: 'ノート用の余白を足す（スライドの横にメモ欄を作る）', run: ops.addMargins, ...needsDoc },
  handout: { label: '配布資料', icon: 'nup', title: '複数ページを1枚にまとめる（2/4/6/8/9面）', run: ops.handout, ...needsDoc },
  toimages: { label: '画像にする', icon: 'image', title: 'ページを画像（PNG/JPEG）にする', run: ops.exportImages, ...needsDoc },
  headerfooter: { label: 'ページ番号', icon: 'number', title: 'ページ番号・ヘッダー・フッターを入れる', run: ops.addHeaderFooter, ...needsDoc },
  watermark: { label: '透かし', icon: 'watermark', run: ops.addWatermark, ...needsDoc },
  bates: { label: '通し番号', icon: 'headerfooter', title: '通し番号（ベイツ番号）を振る', run: ops.addBates, ...needsDoc },
  // review
  comments: { label: 'コメント一覧', icon: 'comments', short: 'コメント\n一覧', title: '書き込みの一覧（返信・ステータス・絞り込み）', run: () => showRightPanel('comments') },
  redact: tool('redact', '墨消し', 'redact', { title: '墨消しの指定（消したい場所を囲む。「適用」で実際に消える）' }),
  redactsearch: { label: '検索して指定', icon: 'searchredact', title: '検索した語句すべてに墨消しを指定', run: ops.redactBySearch, ...needsDoc },
  redactapply: { label: '墨消しを適用', icon: 'redactapply', title: '指定した墨消しを適用して、文字を実際に削除する', run: ops.applyRedactions, enabled: () => model.store.annots.some((a) => a.type === 'redact') },
  scrub: { label: '隠れた情報を削除', icon: 'scrub', title: 'メタデータ・埋め込みファイルなど、見えない情報を削除', run: ops.scrubDocument, ...needsDoc },
  compare: { label: '比較', icon: 'compare', title: '別の版と比べる', menu: true, ...needsDoc,
    run: (e, button) => openMenu(button, [
      { label: '違いを枠で示す', action: () => ops.startCompare('diff') },
      { label: '2つの版を色分けして重ねたPDFを書き出す', action: () => ops.startCompare('overlay') },
    ]) },
  annotexport: { label: '書き込みを書き出す', icon: 'export', title: '書き込みの一覧を書き出す・取り込む', menu: true, ...needsDoc,
    run: (e, button) => {
      const has = model.store.annots.length;
      openMenu(button, [
        { label: `一覧をPDFにする（${has} 件）`, disabled: !has, action: () => ops.exportAnnots('summary') },
        { label: 'Excel用のCSVにする', disabled: !has, action: () => ops.exportAnnots('csv') },
        { label: 'Markdownにする（ノートアプリ用）', disabled: !has, action: () => ops.exportAnnots('markdown') },
        { label: 'XFDFにする（書き込みだけを渡す）', disabled: !has, action: () => ops.exportAnnots('xfdf') },
        '-',
        { label: 'XFDFを取り込む…', action: () => $('#xfdfInput').click() },
      ]);
    } },
  flatten: { label: '焼き付け', icon: 'flatten', title: '書き込みをページに焼き付けて、動かせなくする', run: ops.flattenAnnots, enabled: () => model.store.annots.length > 0 },
  clearall: { label: 'すべて削除', icon: 'trash', title: '書き込みをすべて削除', run: ops.clearAnnots, enabled: () => model.store.annots.length > 0 },
  markall: { label: '検索結果にマーカー', icon: 'highlight', title: '検索で見つかった箇所すべてにマーカーを引く', run: markAllHits, enabled: () => state.searchHits.length > 0 },
  // view
  zoomin: { label: '拡大', icon: 'zoomin', key: 'Ctrl++', run: () => viewer.nudgeZoom(1), ...needsDoc },
  zoomout: { label: '縮小', icon: 'zoomout', key: 'Ctrl+−', run: () => viewer.nudgeZoom(-1), ...needsDoc },
  fitwidth: { label: '幅に合わせる', icon: 'fitwidth', key: 'Ctrl+0', run: () => viewer.setZoom('fit-width'), active: () => viewer.zoomMode === 'fit-width', ...needsDoc },
  fitpage: { label: '全体表示', icon: 'fitpage', run: () => viewer.setZoom('fit-page'), active: () => viewer.zoomMode === 'fit-page', ...needsDoc },
  actual: { label: '100%', icon: 'actual', run: () => viewer.setZoom('1'), ...needsDoc },
  thumbs: { label: 'ページ一覧', icon: 'thumbs', run: () => toggleSide('#leftPanel'), active: () => !$('#leftPanel').classList.contains('collapsed') },
  sidepane: { label: '右パネル', icon: 'sidepane', title: 'コメント・プロパティのパネル', run: () => toggleSide('#rightPanel'), active: () => !$('#rightPanel').classList.contains('collapsed') },
  spread: { label: '見開き', icon: 'spread', title: '見開き表示（2ページを並べて表示。本や楽譜に）',
    run: () => { setPref('spread', !getPref('spread')); viewer.setSpread(getPref('spread')); ribbon.refresh(); },
    active: () => !!getPref('spread'), ...needsDoc },
  theme: { label: 'ダークモード', icon: 'theme', run: toggleTheme, active: () => document.body.dataset.theme === 'dark' },
  invert: { label: 'ページも暗く', icon: 'invert', title: 'ページの白黒を反転（夜に読むとき。保存されるPDFは変わりません）',
    run: () => { setPref('invert', !getPref('invert')); viewer.setInvert(getPref('invert'));
viewer.setSpread(getPref('spread')); ribbon.refresh(); }, active: () => !!getPref('invert') },
  fullscreen: { label: '全画面', icon: 'fullscreen', key: 'F11', run: () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.()) },
  speak: { label: '読み上げ', icon: 'speak', title: '今のページ（または選んだ文字）を読み上げる', run: readAloud, active: () => speaking, ...needsDoc },
  find: { label: '検索', icon: 'search', key: 'Ctrl+F', run: () => { $('#searchInput').focus(); $('#searchInput').select(); }, ...needsDoc },
  replace: { label: '置換', icon: 'replace', title: '本文を検索して置換', run: ops.searchReplaceText, ...needsDoc },
  // tools
  formfill: { label: 'フォーム入力', icon: 'form', short: 'フォーム\n入力', title: 'PDFの入力欄（フォーム）に入力', run: ops.showFields, ...needsDoc },
  formmore: { label: 'フォームの操作', icon: 'more', menu: true, ...needsDoc,
    run: (e, button) => openMenu(button, [
      { label: '罫線から入力欄を自動で作る', action: ops.detectFields },
      '-',
      { label: '入力内容を書き出す（CSV）', action: () => ops.exportFields('csv') },
      { label: '入力内容を書き出す（FDF）', action: () => ops.exportFields('fdf') },
      { label: '入力内容を読み込む…', action: () => $('#fdfInput').click() },
      { label: '複数の回答PDFを1枚のCSVに集計…', action: () => $('#collateInput').click() },
    ]) },
  distance: tool('measureDistance', '距離', 'ruler'),
  area: tool('measureArea', '面積', 'area'),
  angle: tool('measureAngle', '角度', 'angle'),
  count: tool('count', 'カウント', 'count', { title: 'カウント（クリックした数を数える）' }),
  scale: { label: '縮尺', icon: 'scale', title: '図面の縮尺を設定', run: startCalibration, ...needsDoc },
  takeoff: { label: '集計', icon: 'order', title: '計測とカウントの集計', run: () => showRightPanel('takeoff') },
  ocr: { label: '文字認識', icon: 'ocr', short: '文字認識\n(OCR)', title: 'スキャンした文書を文字認識して、検索・選択できるようにする', run: runOcr, ...needsDoc },
  protect: { label: 'パスワード保護', icon: 'lock', title: 'パスワードを付けて書き出す', run: ops.protectDocument, ...needsDoc },
  compress: { label: '軽くする', icon: 'compress', title: 'ファイルサイズを小さくする', run: ops.compressDocument, ...needsDoc },
  a11y: { label: '点検', icon: 'accessibility', title: 'アクセシビリティを点検する', run: ops.runAccessibilityAudit, ...needsDoc },
  readorder: { label: '読み上げ順', icon: 'order', title: '読み上げ順序を確認する', run: ops.showReadingOrder, ...needsDoc },
  sigfield: { label: '電子署名欄', icon: 'signature', menu: true, ...needsDoc,
    run: (e, button) => openMenu(button, [
      { label: '署名欄を作る（相手に電子署名してもらう）', action: ops.addSignatureField },
      { label: '電子署名の状態を確認', action: ops.showSignatureState },
    ]) },
};

// A live group is rebuilt only when what it shows would change: the tool in
// hand, what is selected, and that thing's current formatting.
const formatKey = () => {
  const selection = selectedAnnots();
  const first = selection[0];
  return JSON.stringify([
    tools.tool, tools.markKind, selection.map((a) => a.id),
    first ? [first.style, first.icon, first.stampIndex, first.tool] : null,
    styleFor(tools.tool), styleFor('freetext'), !!state.editor,
  ]);
};
const custom = (fn, key = formatKey) => ({ custom: fn, key });

const tabs = [
  { id: 'home', label: 'ホーム', groups: [
    { label: 'クリップボード', items: [{ big: 'paste' }, { col: ['cut', 'copy', 'duplicate'] }] },
    { label: 'ツール', items: [{ big: 'select' }, { col: ['pan', 'find', 'remove'] }] },
    { label: 'テキスト', items: [{ big: 'freetext' }, { big: 'edittext' }, { col: ['dictate', 'callout'] }] },
    { label: 'フォント', items: [custom(fontGroup)] },
    { label: 'マーカー', items: [{ big: 'highlight' }, { col: ['underline', 'strikeout', 'squiggly'] }] },
    { label: '記入', items: [{ col: ['mark', 'hanko', 'signature'] }, { col: ['note', 'image', 'date'] }, { col: ['whiteout', 'snippet', 'snapshot'] }] },
    { label: '書式', items: [custom(styleGroup)] },
  ] },
  { id: 'insert', label: '挿入', groups: [
    { label: 'テキスト', items: [{ big: 'freetext' }, { col: ['callout', 'note', 'date'] }, { col: ['snippet'] }] },
    { label: '図形', items: [{ col: ['line', 'arrow', 'polyline'] }, { col: ['square', 'circle', 'polygon'] }] },
    { label: '画像・印', items: [{ big: 'image' }, { big: 'hanko' }, { big: 'signature' }, { col: ['stamp', 'mark', 'whiteout'] }] },
    { label: 'ページに入れる', items: [{ col: ['headerfooter', 'watermark', 'bates'] }] },
    { label: 'フォント', items: [custom(fontGroup)] },
    { label: '書式', items: [custom(styleGroup)] },
  ] },
  { id: 'draw', label: '描画', groups: [
    { label: 'ペン', items: [{ big: 'pen' }, { big: 'marker' }, { big: 'eraser' }] },
    { label: '選択', items: [{ big: 'select' }, { col: ['lasso', 'remove'] }] },
    { label: 'マーカー', items: [{ col: ['highlight', 'areaHighlight', 'underline'] }] },
    { label: '書式', items: [custom(styleGroup)] },
  ] },
  { id: 'pages', label: 'ページ', groups: [
    { label: '回転', items: [{ big: 'rotatecw' }, { col: ['rotateccw', 'rotateall'] }] },
    { label: '整理', items: [{ col: ['pagedelete', 'pagecopy', 'pageblank'] }, { col: ['moveup', 'movedown', 'thumbs'] }] },
    { label: '結合・分割', items: [{ big: 'merge' }, { col: ['extract', 'split'] }] },
    { label: 'ノート・印刷用', items: [{ big: 'margins' }, { col: ['handout', 'toimages', 'headerfooter'] }, { col: ['crop', 'uncrop'] }] },
    { label: '対象', items: [custom(() => h('div', { class: 'rhint', text: hasDoc()
      ? `対象: ${describePages(targetPages())}。左のページ一覧で Ctrl / Shift を押しながら選ぶと、複数ページをまとめて操作できます。`
      : 'PDFを開くと、ページの回転・削除・並べ替え・結合ができます。' }),
    () => (hasDoc() ? describePages(targetPages()) : ''))] },
  ] },
  { id: 'review', label: '校閲', groups: [
    { label: 'コメント', items: [{ big: 'comments' }, { col: ['note', 'callout', 'annotexport'] }] },
    { label: 'マーカー', items: [{ col: ['highlight', 'underline', 'strikeout'] }, { col: ['squiggly', 'areaHighlight', 'markall'] }] },
    { label: '墨消し（黒塗り）', items: [{ big: 'redact' }, { col: ['redactsearch', 'redactapply', 'scrub'] }] },
    { label: '比較', items: [{ col: ['compare'] }] },
    { label: '仕上げ', items: [{ col: ['flatten', 'clearall'] }] },
    { label: '書式', items: [custom(styleGroup)] },
  ] },
  { id: 'view', label: '表示', groups: [
    { label: 'ズーム', items: [{ big: 'fitwidth' }, { col: ['fitpage', 'actual', 'spread'] }, { col: ['zoomin', 'zoomout'] }] },
    { label: 'パネル', items: [{ col: ['thumbs', 'sidepane', 'comments'] }] },
    { label: '見やすさ', items: [{ col: ['theme', 'invert', 'fullscreen'] }] },
    { label: '読む・覚える・見せる', items: [{ big: 'slideshow' }, { big: 'study' }, { big: 'speak' }, { col: ['find', 'pan', 'snapshot'] }] },
  ] },
  { id: 'tools', label: 'ツール', groups: [
    { label: '文字認識', items: [{ big: 'ocr' }] },
    { label: 'フォーム', items: [{ big: 'formfill' }, { col: ['formmore', 'sigfield'] }] },
    { label: '計測', items: [{ col: ['distance', 'area', 'angle'] }, { col: ['count', 'scale', 'takeoff'] }] },
    { label: 'ファイル', items: [{ col: ['compress', 'protect', 'replace'] }] },
    { label: 'アクセシビリティ', items: [{ col: ['a11y', 'readorder'] }] },
    { label: '書式', items: [custom(styleGroup)] },
  ] },
];

function fileMenu(anchor) {
  const open = hasDoc();
  openMenu(anchor, [
    { label: '開く…', icon: 'open', key: 'Ctrl+O', action: chooseFile },
    { label: '白紙から作る…', icon: 'newdoc', action: newBlank },
    { label: '画像からPDFを作る…', icon: 'image', action: () => $('#imagePdfInput').click() },
    '-',
    { label: '上書き保存', icon: 'save', key: 'Ctrl+S', disabled: !open, action: () => saveToDisk() },
    { label: '名前を付けて保存…', icon: 'saveas', key: 'Ctrl+Shift+S', disabled: !open, action: () => saveToDisk({ saveAs: true }) },
    { label: 'コピーをダウンロード', icon: 'download', disabled: !open, action: downloadCopy },
    { label: `自動保存: ${getPref('autosave') ? 'オン（クリックでオフ）' : 'オフ（クリックでオン）'}`, icon: 'save',
      title: '変更を、開いた元のファイルへ自動で上書き保存します', action: toggleAutosave },
    { label: '印刷…', icon: 'print', key: 'Ctrl+P', disabled: !open, action: printDocument },
    '-',
    { heading: '書き出す' },
    { label: 'ページを画像にする（PNG / JPEG）…', icon: 'image', disabled: !open, action: ops.exportImages },
    { label: '文字だけを取り出す（テキストファイル）', icon: 'text2', disabled: !open, action: ops.exportText },
    { label: '配布資料にする（複数ページを1枚に）…', icon: 'nup', disabled: !open, action: ops.handout },
    { label: 'パスワードを付けて書き出す…', icon: 'lock', disabled: !open, action: ops.protectDocument },
    { label: 'ファイルを軽くする…', icon: 'compress', disabled: !open, action: ops.compressDocument },
    '-',
    { label: '文書のプロパティ…', icon: 'info', disabled: !open, action: ops.documentProperties },
    { label: '設定', icon: 'settings', action: () => showRightPanel('settings') },
    { label: '使い方', icon: 'help', action: showHelp },
  ]);
}

const ribbon = new Ribbon($('#ribbonTabs'), $('#ribbon'), { tabs, commands, onFile: fileMenu });

function toggleTheme() {
  const next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
  document.body.dataset.theme = next;
  setPref('theme', next);
  ribbon.refresh();
}
$('#btnTheme').addEventListener('click', toggleTheme);
$('#btnHelp').addEventListener('click', showHelp);
document.body.dataset.theme = getPref('theme') || 'light';
viewer.setInvert(getPref('invert'));

// ================================================================ keyboard

function nudge(dx, dy) {
  const annots = selectedAnnots().filter((a) => !a.flags?.locked);
  if (!annots.length) return false;
  const merge = `nudge:${annots.map((a) => a.id).join()}`;
  for (const annot of annots) model.updateAnnots([annot.id], translated(annot, dx, dy), { merge });
  endMergeWhenIdle(700);
  return true;
}

window.addEventListener('keydown', (e) => {
  if (document.querySelector('.dialog-backdrop')) return;
  const target = e.target;
  const typing = !!target.closest?.('input, textarea, select, [contenteditable]');
  const mod = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();

  if (mod && key === 's') { e.preventDefault(); saveToDisk({ saveAs: e.shiftKey }); return; }
  if (mod && key === 'o') { e.preventDefault(); chooseFile(); return; }
  if (mod && key === 'p') { e.preventDefault(); printDocument(); return; }
  if (mod && key === 'f') { e.preventDefault(); $('#searchInput').focus(); $('#searchInput').select(); return; }
  if (typing || isComposing(e)) return;

  if (mod && key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return; }
  if (mod && (key === 'y' || (key === 'z' && e.shiftKey))) { e.preventDefault(); redo(); return; }
  if (mod && key === 'a') { e.preventDefault(); selectAllOnPage(); return; }
  if (mod && key === 'c') { if (!textSelectionActive() && copySelection()) e.preventDefault(); return; }
  if (mod && key === 'x') { if (model.store.selection.length) { e.preventDefault(); cutSelection(); } return; }
  if (mod && key === 'd') { e.preventDefault(); duplicateSelection(); return; }
  if (mod && (key === '=' || key === '+' || key === ';')) { e.preventDefault(); viewer.nudgeZoom(1); return; }
  if (mod && key === '-') { e.preventDefault(); viewer.nudgeZoom(-1); return; }
  if (mod && key === '0') { e.preventDefault(); viewer.setZoom('fit-width'); return; }
  if (mod) return;

  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (model.store.selection.length) { e.preventDefault(); deleteSelection(); }
    return;
  }
  if (e.key === 'Escape') {
    for (const mark of document.querySelectorAll('.order-mark')) mark.remove();
    const busyWith = tools.poly || tools.measuring || tools.calibrating;
    tools.cancelPoly();
    tools.cancelMeasure();
    if (busyWith) return;
    // One press lets go of everything: the selection and the tool in hand.
    const hadSelection = model.store.selection.length > 0;
    if (hadSelection) model.select([]);
    if (tools.tool !== 'select') selectTool('select');
    if (hadSelection) return;
    window.getSelection()?.removeAllRanges();
    hideSelectionBar();
    return;
  }
  if (e.key === 'Enter') {
    if (tools.measuring) { e.preventDefault(); tools.finishMeasure(); return; }
    if (tools.poly) { e.preventDefault(); tools._finishPoly(); return; }
    const selection = selectedAnnots();
    if (selection.length === 1 && (selection[0].type === 'freetext' || selection[0].type === 'note')) {
      e.preventDefault();
      startTextEdit(selection[0].id);
    }
    return;
  }
  if (e.key === 'F5' && viewer.pageViews.length) { e.preventDefault(); startSlideshow(); return; }
  if (e.key === 'F2') {
    const selection = selectedAnnots();
    if (selection.length === 1) { e.preventDefault(); startTextEdit(selection[0].id); }
    return;
  }
  const step = e.shiftKey ? 10 : 1;
  const arrows = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (arrows[e.key] && model.store.selection.length) {
    if (nudge(...arrows[e.key])) e.preventDefault();
    return;
  }
  if (!viewer.pageViews.length) return;
  if (e.key === 'PageDown') { e.preventDefault(); goToPage(viewer.currentPage + 1); return; }
  if (e.key === 'PageUp') { e.preventDefault(); goToPage(viewer.currentPage - 1); return; }
  if (e.key === 'Home') { e.preventDefault(); goToPage(0); return; }
  if (e.key === 'End') { e.preventDefault(); goToPage(viewer.pageViews.length - 1); return; }
  if (e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    // Scroll the document even when the focus is somewhere inert.
    e.preventDefault();
    const amount = e.key === ' ' ? stage.clientHeight * 0.85 * (e.shiftKey ? -1 : 1) : (e.key === 'ArrowDown' ? 60 : -60);
    stage.scrollBy({ top: amount });
  }
});

// Typing with a text box selected (not yet open) starts editing it, the way
// a slide editor does — the first character is not lost.
window.addEventListener('compositionstart', (e) => {
  if (e.target.closest?.('input, textarea, [contenteditable]')) return;
  const selection = selectedAnnots();
  if (selection.length === 1 && selection[0].type === 'freetext') startTextEdit(selection[0].id);
});

// ================================================================ start

selectTool('select');
refreshAll();
status('準備完了 — PDFを開いてください');
pruneDrafts();
showRecent();

// Installed as an app, PDF Studio can be chosen under "Open with" for a PDF;
// the file arrives here, with a handle that lets Save write straight back.
window.launchQueue?.setConsumer?.(async (params) => {
  const handle = params.files?.[0];
  if (!handle) return;
  const start = async () => openFile(await handle.getFile(), '', handle);
  if (window.pdfStudioReady) start(); else document.addEventListener('pdfstudio:ready', start, { once: true });
});
document.addEventListener('pdfstudio:ready', () => {
  // Have the default face ready before the first text box is typed in.
  ensureFontLoaded({ family: 'gothic', size: 12 });
});
