// Operations on the document itself: pages, redaction, page furniture,
// forms, comparison, export, protection.
//
// Everything that rewrites the file goes through structural(), so each one
// can be stepped back over with Ctrl+Z.

import * as model from './model.js';
import { getPref } from './defaults.js';
import { confirmDialog, formDialog, infoDialog } from './dialogs.js';
import {
  $, viewer, state, toast, busy, paint, post, docUrl, structural, reloadFrom, errorDetail,
  downloadResponse, exportVia, targetPages, describePages, hooks,
} from './ctx.js';

const UNDO_NOTE = '実行後も Ctrl+Z で元に戻せます（このタブを開いている間）。';

// ---------------------------------------------------------------- pages

export function rotatePages(degrees) {
  const pages = targetPages();
  return structural('/pages/rotate', { pages, degrees }, { label: '回転' });
}

export function rotateAll(degrees) {
  return structural('/pages/rotate', { pages: [], degrees }, { label: '回転' });
}

export async function deletePages() {
  const pages = targetPages();
  if (pages.length >= model.store.pages.length) {
    toast('すべてのページは削除できません', 'warn');
    return;
  }
  const ok = await confirmDialog({
    title: `${describePages(pages)}を削除しますか`,
    intro: 'ページと、その上の書き込みがなくなります。',
    warning: UNDO_NOTE,
    confirmLabel: '削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/pages/delete', { pages }, { label: 'ページ削除' });
  if (result) { state.pageSelection.clear(); hooks.refreshAll(); toast(`${pages.length} ページを削除しました（Ctrl+Z で戻せます）`); }
}

export async function duplicatePages() {
  const pages = targetPages();
  const result = await structural('/pages/duplicate', { pages }, { label: '複製' });
  if (result) toast(`${describePages(pages)}を複製しました`);
}

export async function insertBlank() {
  const at = Math.max(...targetPages()) + 1;
  const current = model.store.pages[viewer.currentPage] || { width: 595, height: 842 };
  const result = await structural('/pages/blank', { at, width: current.width, height: current.height }, { label: '白紙の挿入' });
  if (result) { viewer.scrollToPage(at); toast(`${at + 1} ページ目に白紙を挿入しました`); }
}

/** Move pages so they sit before `insertAt` (an index in the current order). */
export async function movePages(pages, insertAt) {
  const count = model.store.pages.length;
  const moving = new Set(pages);
  const rest = [];
  for (let i = 0; i < count; i += 1) if (!moving.has(i)) rest.push(i);
  const before = [...Array(insertAt).keys()].filter((i) => !moving.has(i)).length;
  const order = [...rest.slice(0, before), ...[...moving].sort((a, b) => a - b), ...rest.slice(before)];
  if (order.every((value, index) => value === index)) return;
  const result = await structural('/pages/reorder', { order }, { label: '並べ替え' });
  if (result) {
    state.pageSelection = new Set(order.map((value, index) => (moving.has(value) ? index : -1)).filter((i) => i >= 0));
    hooks.refreshAll();
    toast('ページを並べ替えました');
  }
}

export function movePagesBy(delta) {
  const pages = targetPages();
  const first = pages[0];
  const last = pages[pages.length - 1];
  if (delta < 0 && first === 0) return;
  if (delta > 0 && last >= model.store.pages.length - 1) return;
  movePages(pages, delta < 0 ? first - 1 : last + 2);
}

export async function extractPages() {
  const pages = targetPages();
  const values = await formDialog({
    title: 'ページを抜き出して別のPDFにする',
    intro: '指定したページだけの新しいPDFを書き出します。開いている文書は変わりません。',
    fields: [{
      key: 'ranges', label: 'ページ範囲', value: toRanges(pages),
      hint: `例: 1-3, 5, 8-（全 ${model.store.pages.length} ページ）`,
    }],
    confirmLabel: '書き出す',
  });
  if (!values) return;
  await exportVia('/extract-ranges', { ranges: values.ranges }, { label: '抜き出し', fallback: 'extract.pdf' });
}

function toRanges(pages) {
  const out = [];
  let start = null; let previous = null;
  for (const page of pages) {
    if (start === null) { start = page; previous = page; continue; }
    if (page === previous + 1) { previous = page; continue; }
    out.push(start === previous ? `${start + 1}` : `${start + 1}-${previous + 1}`);
    start = page; previous = page;
  }
  if (start !== null) out.push(start === previous ? `${start + 1}` : `${start + 1}-${previous + 1}`);
  return out.join(', ');
}

export async function splitDocument() {
  const count = model.store.pages.length;
  const values = await formDialog({
    title: 'PDFを分割する',
    intro: '分けたPDFをまとめて1つのZIPで書き出します。開いている文書は変わりません。',
    fields: [
      { key: 'mode', label: '分け方', type: 'select', options: { every: '決まったページ数ごと', ranges: 'ページ範囲を指定' } },
      { key: 'every', label: '何ページごと', type: 'number', value: 1, min: 1, max: Math.max(1, count) },
      { key: 'ranges', label: 'ページ範囲', placeholder: '例: 1-3, 4-10, 11-', hint: 'カンマ区切りの1つ1つが別のPDFになります' },
    ],
    validate: (v) => (v.mode === 'ranges' && !v.ranges.trim() ? 'ページ範囲を入力してください' : null),
    confirmLabel: '分割して書き出す',
  });
  if (!values) return;
  await exportVia('/split', values.mode === 'every' ? { every: values.every } : { ranges: values.ranges },
    { label: '分割', fallback: 'split.zip' });
}

export async function addMargins() {
  const values = await formDialog({
    title: 'ノート用の余白を足す',
    intro: 'ページの外側に余白を足して、書き込むスペースを作ります。中身の大きさは変わりません。',
    fields: [
      { key: 'side', label: '余白を足す場所', type: 'select', options: { right: '右', bottom: '下', left: '左', top: '上', all: '四方すべて' } },
      { key: 'amount', label: '余白の幅（mm）', type: 'number', value: 60, min: 5, max: 300 },
      { key: 'scope', label: '対象', type: 'select', options: { all: 'すべてのページ', picked: `${describePages(targetPages())}だけ` } },
    ],
    confirmLabel: '余白を足す',
  });
  if (!values) return;
  const points = (values.amount * 72) / 25.4;
  const sides = { left: 0, top: 0, right: 0, bottom: 0 };
  if (values.side === 'all') for (const key of Object.keys(sides)) sides[key] = points;
  else sides[values.side] = points;
  const result = await structural('/pages/margins', { pages: values.scope === 'all' ? [] : targetPages(), ...sides }, { label: '余白の追加' });
  if (result) toast('余白を足しました（Ctrl+Z で戻せます）');
}

export async function handout() {
  const values = await formDialog({
    title: '配布資料にする（複数ページを1枚に）',
    intro: 'スライドなどを1枚のA4に並べたPDFを書き出します。印刷枚数を減らしたいときに。書き込みも一緒に入ります。',
    fields: [
      { key: 'perSheet', label: '1枚あたり', type: 'select', value: '4', options: { 2: '2ページ（横向き）', 4: '4ページ', 6: '6ページ', 8: '8ページ（横向き）', 9: '9ページ' } },
      { key: 'border', label: '枠線を付ける', type: 'checkbox', value: true },
    ],
    confirmLabel: '書き出す',
  });
  if (!values) return;
  await exportVia('/nup', { perSheet: Number(values.perSheet), border: values.border }, { label: '配布資料の作成', fallback: 'handout.pdf' });
}

export async function exportImages() {
  const pages = targetPages();
  const values = await formDialog({
    title: 'ページを画像にする',
    intro: '書き込みも含めた見た目のまま画像にします。複数ページはZIPにまとめます。',
    fields: [
      { key: 'scope', label: '対象', type: 'select', options: { picked: describePages(pages), all: `すべてのページ（${model.store.pages.length}）` } },
      { key: 'format', label: '形式', type: 'select', options: { png: 'PNG（くっきり）', jpg: 'JPEG（軽い）' } },
      { key: 'dpi', label: '解像度', type: 'select', value: '150', options: { 96: '96 dpi（画面用）', 150: '150 dpi（標準）', 200: '200 dpi', 300: '300 dpi（印刷用）' } },
    ],
    confirmLabel: '書き出す',
  });
  if (!values) return;
  await exportVia('/images', { pages: values.scope === 'all' ? null : pages, format: values.format, dpi: Number(values.dpi) },
    { label: '画像の書き出し', fallback: 'pages.zip' });
}

export async function exportText() {
  const response = await fetch(docUrl('/plain-text'));
  if (!response.ok) { toast('書き出しに失敗しました', 'error'); return; }
  await downloadResponse(response, 'text.txt');
}

// ---------------------------------------------------------------- merge

export function pickMerge() { $('#mergeInput').click(); }

export async function mergeFiles(files) {
  if (!files.length || !model.store.docId) return;
  const form = new FormData();
  for (const file of files) form.append('files', file);
  const done = busy('結合しています…');
  try {
    await paint();
    // Markup drawn since the last save has to be in the file before pages move.
    const saved = await post('/annots', {}, { withAnnots: true });
    if (!saved.ok) throw new Error(await errorDetail(saved));
    const at = Math.max(...targetPages()) + 1;
    const response = await fetch(`${docUrl('/merge')}?at=${at}`, { method: 'POST', body: form });
    if (!response.ok) throw new Error(await errorDetail(response));
    const result = await response.json();
    await reloadFrom(result);
    state.unsaved = true;
    hooks.refreshAll();
    viewer.scrollToPage(at);
    toast(`${result.added} ページを ${at} ページ目のあとに結合しました`);
  } catch (err) {
    toast(`結合に失敗しました: ${err.message}`, 'error');
  } finally {
    done();
  }
}

// ---------------------------------------------------------------- redaction

export async function applyRedactions() {
  const count = model.store.annots.filter((a) => a.type === 'redact').length;
  if (!count) { toast('墨消しの指定がありません。先に「墨消し」で消したい場所を囲んでください', 'warn'); return; }
  const ok = await confirmDialog({
    title: '墨消しを適用しますか',
    intro: `${count} 箇所の指定を適用し、下にある文字と画像をファイルから実際に削除します。`,
    warning: '適用するまでは、黒く見えていても文字はコピーすれば読める状態です。適用後に保存したファイルからは復元できません。',
    confirmLabel: '適用して削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/redact/apply', { images: true }, { label: '墨消しの適用' });
  if (result) toast(`${result.applied} 箇所を削除しました`);
}

export async function redactBySearch() {
  const values = await formDialog({
    title: '検索して墨消しを指定',
    intro: '一致した箇所すべてに墨消しの指定を付けます。実際に消すのは「墨消しを適用」です。',
    fields: [
      { key: 'query', label: '検索する文字列', placeholder: '例: 山田太郎', value: state.searchQuery || '' },
      { key: 'overlay', label: '黒塗りの上に出す文字', placeholder: '例: ［非開示］', hint: '空欄なら文字は出ません' },
    ],
    validate: (v) => (v.query.trim() ? null : '検索する文字列を入力してください'),
    confirmLabel: '指定する',
  });
  if (!values) return;
  const result = await structural('/redact/search', values, { label: '墨消しの指定' });
  if (result) {
    toast(result.marked
      ? `${result.marked} 箇所に墨消しを指定しました。「墨消しを適用」で実際に削除されます`
      : '一致する文字列が見つかりませんでした', result.marked ? '' : 'warn');
  }
}

export async function scrubDocument() {
  const ok = await confirmDialog({
    title: '隠れた情報を削除しますか',
    intro: '画面に出ないままファイルに残っている情報を取り除きます: 作成者などのメタデータ、埋め込みファイル、非表示テキスト、JavaScript、サムネイルなど。',
    warning: UNDO_NOTE,
    confirmLabel: '削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/scrub', {}, { label: '隠れた情報の削除' });
  if (result) toast('隠れた情報を削除しました');
}

// ---------------------------------------------------------------- page furniture

export async function addWatermark() {
  const values = await formDialog({
    title: '透かしを入れる',
    fields: [
      { key: 'text', label: '文字', value: '社外秘' },
      { key: 'colour', label: '色', type: 'colour', value: '#c0c0c0' },
      { key: 'size', label: 'サイズ', type: 'number', value: 54, min: 8, max: 200 },
      { key: 'opacity', label: '濃さ（0〜1）', type: 'number', value: 0.25, min: 0.05, max: 1, step: 0.05 },
      { key: 'angle', label: '角度', type: 'number', value: 45, min: -90, max: 90 },
      { key: 'allPages', label: '全ページに入れる', type: 'checkbox', value: true },
    ],
    validate: (v) => (v.text.trim() ? null : '文字を入力してください'),
    confirmLabel: '入れる',
  });
  if (!values) return;
  const result = await structural('/stamp-pages', {
    kind: 'watermark', ...values, pages: values.allPages ? null : targetPages(),
  }, { label: '透かし' });
  if (result) toast('透かしを入れました（Ctrl+Z で戻せます）');
}

export async function addHeaderFooter() {
  const values = await formDialog({
    title: 'ページ番号・ヘッダー・フッター',
    intro: '{page} は今のページ番号、{pages} は総ページ数に置き換わります。',
    fields: [
      { key: 'header', label: 'ヘッダー（上）', placeholder: '例: レビュー用' },
      { key: 'footer', label: 'フッター（下）', value: '{page} / {pages}' },
      { key: 'size', label: '文字サイズ', type: 'number', value: 9, min: 5, max: 24 },
      { key: 'colour', label: '色', type: 'colour', value: '#555555' },
    ],
    validate: (v) => (v.header.trim() || v.footer.trim() ? null : 'ヘッダーかフッターを入力してください'),
    confirmLabel: '入れる',
  });
  if (!values) return;
  const result = await structural('/stamp-pages', { kind: 'headerFooter', ...values }, { label: 'ヘッダー・フッター' });
  if (result) toast('入れました（Ctrl+Z で戻せます）');
}

export async function addBates() {
  const values = await formDialog({
    title: '通し番号（ベイツ番号）を振る',
    intro: '全ページの右下に連番を入れます。証拠書類や資料集の管理に。',
    fields: [
      { key: 'prefix', label: '頭に付ける文字', placeholder: '例: ABC-' },
      { key: 'start', label: '開始番号', type: 'number', value: 1, min: 0 },
      { key: 'digits', label: '桁数', type: 'number', value: 6, min: 1, max: 12 },
      { key: 'suffix', label: '末尾に付ける文字', placeholder: '' },
    ],
    confirmLabel: '振る',
  });
  if (!values) return;
  const result = await structural('/stamp-pages', { kind: 'bates', ...values }, { label: '通し番号' });
  if (result) toast('通し番号を振りました（Ctrl+Z で戻せます）');
}

export async function searchReplaceText() {
  const values = await formDialog({
    title: '本文を検索して置換',
    intro: '書き込みではなく、ページの中身そのものを書き換えます。',
    warning: '元の文字はファイルから削除され、新しい文字が同梱の日本語フォントで置かれます。元のフォントとは見た目が変わることがあります。',
    fields: [
      { key: 'query', label: '検索する文字列', value: state.searchQuery || '' },
      { key: 'replacement', label: '置き換える文字列' },
      { key: 'colour', label: '文字色', type: 'colour', value: '#000000' },
    ],
    validate: (v) => (v.query.trim() ? null : '検索する文字列を入力してください'),
    confirmLabel: '置換する',
  });
  if (!values) return;
  const result = await structural('/text/search-replace', values, { label: '置換' });
  if (result) {
    toast(result.replaced
      ? `${result.replaced} 箇所を置換しました（Ctrl+Z で戻せます）`
      : '一致する文字列が見つかりませんでした', result.replaced ? '' : 'warn');
  }
}

// ---------------------------------------------------------------- annotations in bulk

export async function exportAnnots(fmt) {
  const colourTags = { '#ffe14d': '重要', '#3fb950': '実行項目', '#e0403a': '要検討', '#2f6df6': 'メモ' };
  await exportVia(`/export/${fmt}`, { colourTags }, { label: '書き出し', fallback: `export.${fmt}` });
}

export async function flattenAnnots() {
  const ok = await confirmDialog({
    title: '書き込みをページに焼き付けますか',
    intro: `${model.store.annots.length} 件の書き込みを、ページの中身として固定します。相手の環境で確実に同じ表示になり、あとから動かされることもなくなります。`,
    warning: `焼き付けた書き込みは、選択・移動・削除ができなくなります。${UNDO_NOTE}`,
    confirmLabel: '焼き付ける', danger: true,
  });
  if (!ok) return;
  const result = await structural('/flatten', {}, { label: '焼き付け' });
  if (result) toast('書き込みをページに焼き付けました');
}

export async function clearAnnots() {
  const ok = await confirmDialog({
    title: '書き込みをすべて削除しますか',
    intro: `${model.store.annots.length} 件の書き込みを取り除きます。ページの中身はそのまま残ります。`,
    warning: UNDO_NOTE,
    confirmLabel: 'すべて削除する', danger: true,
  });
  if (!ok) return;
  const result = await structural('/clear-annots', {}, { label: '削除' });
  if (result) toast(`${result.removed} 件の書き込みを削除しました`);
}

export async function importXfdf(file) {
  if (!file || !model.store.docId) return;
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(docUrl('/import-xfdf'), { method: 'POST', body: form });
  if (!response.ok) { toast(`XFDFを読み込めませんでした: ${await errorDetail(response)}`, 'error'); return; }
  const { annots: incoming } = await response.json();
  if (!incoming.length) { toast('XFDFに書き込みが入っていませんでした', 'warn'); return; }
  // Ids from another copy of the document may collide with ones already here.
  for (const item of incoming) item.id = model.uid();
  model.addAnnots(incoming, { select: false });
  toast(`${incoming.length} 件の書き込みを取り込みました`);
}

// ---------------------------------------------------------------- forms

async function reloadCurrent() {
  const response = await fetch(docUrl(''));
  if (response.ok) { await reloadFrom(await response.json()); hooks.refreshAll(); }
}

export async function showFields() {
  const data = await (await fetch(docUrl('/fields'))).json();
  if (data.hasXfa) {
    await infoDialog({
      title: 'XFAフォームです',
      intro: 'この文書はAdobe独自のXFA形式のフォームを含んでいます。XFAはPDF 2.0で廃止されており、Acrobat以外では開けません。このアプリも対応していません。',
      warning: '作成元に、通常のフォーム（AcroForm）形式での再出力を依頼してください。',
    });
    return;
  }
  if (!data.fields.length) {
    toast('この文書には入力欄（フォーム）がありません。申込書などに書き込むには「テキスト追加」を使ってください', 'warn');
    return;
  }
  const fillable = data.fields.filter((f) => !['button', 'signature'].includes(f.type));
  const values = await formDialog({
    title: 'フォームに入力',
    intro: `${fillable.length} 個の入力欄があります。`,
    fields: fillable.map((field) => ({
      key: field.name,
      label: `${field.name}${field.required ? ' *' : ''}`,
      type: field.type === 'checkbox' ? 'checkbox'
        : (field.type === 'dropdown' || field.type === 'list') && field.options?.length ? 'select' : 'text',
      options: field.options?.length ? Object.fromEntries(field.options.map((o) => [o, o])) : undefined,
      value: field.type === 'checkbox' ? !['Off', '', null, undefined, false].includes(field.value) : (field.value ?? ''),
    })),
    confirmLabel: '入力する',
    wide: true,
  });
  if (!values) return;
  const payload = {};
  for (const field of fillable) {
    const value = values[field.name];
    payload[field.name] = field.type === 'checkbox' ? (value ? 'Yes' : 'Off') : value;
  }
  const saved = await post('/annots', {}, { withAnnots: true });
  if (!saved.ok) { toast('入力に失敗しました', 'error'); return; }
  const response = await post('/fields/fill', { values: payload });
  if (!response.ok) { toast(`入力に失敗しました: ${await errorDetail(response)}`, 'error'); return; }
  const result = await response.json();
  await reloadCurrent();
  state.unsaved = true;
  hooks.refreshAll();
  toast(`${result.filled} 個の入力欄に入力しました`);
}

export async function detectFields() {
  const preview = await (await post('/fields/detect', { page: viewer.currentPage })).json();
  const count = preview.candidates?.length || 0;
  if (!count) { toast('入力欄にできそうな罫線が見つかりませんでした', 'warn'); return; }
  const ok = await confirmDialog({
    title: '入力欄を自動で作りますか',
    intro: `${viewer.currentPage + 1} ページ目の罫線や枠から、${count} 個の入力欄を作れそうです。`,
    warning: '自動判定なので、不要な欄ができることがあります。',
    confirmLabel: '作成する',
  });
  if (!ok) return;
  await post('/annots', {}, { withAnnots: true });
  const result = await (await post('/fields/detect', { page: viewer.currentPage, create: true })).json();
  await reloadCurrent();
  state.unsaved = true;
  hooks.refreshAll();
  toast(`${result.created} 個の入力欄を作成しました`);
}

export async function exportFields(fmt) {
  const response = await fetch(docUrl(`/fields/export/${fmt}`), { method: 'POST' });
  if (!response.ok) { toast('書き出しに失敗しました', 'error'); return; }
  await downloadResponse(response, `fields.${fmt}`);
}

export async function importFields(file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(docUrl('/fields/import'), { method: 'POST', body: form });
  if (!response.ok) { toast('読み込めませんでした', 'error'); return; }
  const result = await response.json();
  await reloadCurrent();
  state.unsaved = true;
  hooks.refreshAll();
  toast(`${result.filled} 個の入力欄に読み込みました`);
}

export async function collateFields(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const file of files) form.append('files', file);
  const response = await fetch(docUrl('/fields/collate'), { method: 'POST', body: form });
  if (!response.ok) { toast('集計に失敗しました', 'error'); return; }
  await downloadResponse(response, 'collated.csv');
}

// ---------------------------------------------------------------- comparison

let compareMode = 'diff';

export function startCompare(mode) {
  compareMode = mode;
  $('#compareInput').click();
}

export async function compareWith(file) {
  if (!file || !model.store.docId) return;
  const done = busy('比較しています…');
  try {
    await paint();
    await post('/annots', {}, { withAnnots: true });
    const form = new FormData();
    form.append('file', file);
    const url = `${docUrl('/compare')}?mode=${compareMode}&author=${encodeURIComponent(getPref('author') || '')}`;
    const response = await fetch(url, { method: 'POST', body: form });
    if (!response.ok) { toast(`比較に失敗しました: ${await errorDetail(response)}`, 'error'); return; }
    if (compareMode === 'overlay') { await downloadResponse(response, 'overlay.pdf'); return; }
    const result = await response.json();
    if (!result.differences) { toast('違いは見つかりませんでした'); return; }
    for (const item of result.annots) item.id = model.uid();
    model.addAnnots(result.annots, { select: false });
    toast(`${result.differences} 箇所の違いを雲形の枠で示しました（コメント一覧で1件ずつ確認できます）`);
  } finally {
    done();
  }
}

// ---------------------------------------------------------------- file-level

export async function protectDocument() {
  const values = await formDialog({
    title: 'パスワードを付けて書き出す',
    intro: '保護をかけた別ファイルとして書き出します。編集中の文書はそのままです。',
    fields: [
      { key: 'userPassword', label: '開くためのパスワード', type: 'password' },
      { key: 'ownerPassword', label: '権限変更用パスワード', type: 'password',
        hint: '開く用と別にしてください。同じにすると下の制限が効きません' },
      { key: 'print', label: '印刷を許可', type: 'checkbox', value: true },
      { key: 'copy', label: 'コピーを許可', type: 'checkbox', value: true },
      { key: 'modify', label: '編集を許可', type: 'checkbox', value: false },
      { key: 'annotate', label: '注釈を許可', type: 'checkbox', value: true },
    ],
    validate: (v) => (v.userPassword || v.ownerPassword ? null : 'パスワードを入力してください'),
    confirmLabel: '書き出す',
  });
  if (!values) return;
  await exportVia('/protect', {
    userPassword: values.userPassword,
    ownerPassword: values.ownerPassword,
    permissions: { print: values.print, copy: values.copy, modify: values.modify, annotate: values.annotate },
  }, { label: '保護', fallback: 'protected.pdf' });
}

const kb = (n) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`);

export async function compressDocument() {
  const values = await formDialog({
    title: 'ファイルを軽くする',
    intro: '中の画像を縮小してファイルサイズを減らします。メール添付や提出システムの容量制限に。',
    fields: [
      { key: 'level', label: '軽さ', type: 'select', value: 'medium', options: {
        light: '画質優先（200dpi）', medium: '標準（150dpi）', strong: 'できるだけ軽く（100dpi）',
      } },
    ],
    warning: UNDO_NOTE,
    confirmLabel: '軽くする',
  });
  if (!values) return;
  const preset = { light: [200, 85], medium: [150, 75], strong: [100, 60] }[values.level];
  const result = await structural('/compress', { dpi: preset[0], quality: preset[1] }, { label: '軽量化' });
  if (!result) return;
  toast(result.saved > 2048
    ? `軽くしました: ${kb(result.before)} → ${kb(result.actual)}`
    : `これ以上は軽くなりませんでした（${kb(result.actual)}）`);
}

export async function documentProperties() {
  const data = await (await fetch(docUrl(''))).json();
  const meta = data.metadata || {};
  let summary = `${model.store.pages.length} ページ`;
  try {
    const s = await (await fetch(docUrl('/stats'))).json();
    const size = s.bytes > 1024 * 1024 ? `${(s.bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(s.bytes / 1024))} KB`;
    summary = `${s.pages} ページ ／ ${s.characters.toLocaleString()} 文字（空白を除く）／ 英単語 ${s.words.toLocaleString()} 語 ／ ${size}`
      + (s.pagesWithoutText ? `\n文字情報のないページが ${s.pagesWithoutText} ページあります（スキャン画像。「ツール」→「文字認識」で検索できるようになります）` : '');
  } catch { /* the counts are a nicety */ }
  const values = await formDialog({
    title: '文書のプロパティ',
    intro: summary,
    fields: [
      { key: 'title', label: 'タイトル', value: meta.title || '' },
      { key: 'author', label: '作成者', value: meta.author || '' },
      { key: 'subject', label: '件名', value: meta.subject || '' },
      { key: 'keywords', label: 'キーワード', value: meta.keywords || '' },
    ],
    confirmLabel: '保存',
  });
  if (!values) return;
  const response = await post('/metadata', values);
  if (response.ok) { state.unsaved = true; hooks.refreshAll(); toast('プロパティを更新しました'); }
}

// ---------------------------------------------------------------- accessibility

const SEVERITY = { high: '重要', medium: '中', low: '軽微' };

export async function runAccessibilityAudit() {
  const report = await (await fetch(docUrl('/accessibility'))).json();
  if (report.passed) {
    await infoDialog({ title: 'アクセシビリティ点検', intro: '問題は見つかりませんでした。タグ・言語・代替テキストがそろっています。' });
    return;
  }
  const body = report.issues
    .map((i) => `【${SEVERITY[i.severity] || i.severity}】${i.title}\n  ${i.detail}\n  → ${i.fix}`)
    .join('\n\n');
  const needsTags = report.issues.some((i) => i.id === 'tags' || i.id === 'lang');
  const ok = await confirmDialog({
    title: `アクセシビリティの問題が ${report.issues.length} 件`,
    intro: body,
    warning: needsTags ? 'タグと言語は「自動でタグを付ける」で一度に直せます。文字サイズから見出しを推定するので、結果は確認してください。' : undefined,
    confirmLabel: needsTags ? '自動でタグを付ける' : '閉じる',
    wide: true,
  });
  if (!ok || !needsTags) return;
  const result = await structural('/accessibility/autotag', { language: 'ja-JP' }, { label: 'タグ付け' });
  if (!result) return;
  const remaining = result.audit?.issues?.length || 0;
  toast(remaining
    ? `${result.elements} 要素にタグを付けました。残りの指摘は ${remaining} 件です`
    : `${result.elements} 要素にタグを付けました（見出し ${result.headings} 個）`);
}

export async function showReadingOrder() {
  const data = await (await fetch(docUrl(`/accessibility/order/${viewer.currentPage}`))).json();
  if (!data.blocks.length) { toast('このページにはテキストがありません。スキャンした文書かもしれません', 'warn'); return; }
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
  toast(`読み上げ順序を ${data.blocks.length} ブロック分表示しました（Esc で消えます）`);
}

export async function addSignatureField() {
  const values = await formDialog({
    title: '署名欄を作る',
    intro: '空の署名欄を置きます。この文書を受け取った人が、対応するビューアで電子署名できます。',
    fields: [{ key: 'name', label: '欄の名前', value: '承認者' }],
    confirmLabel: '作る',
  });
  if (!values) return;
  const page = model.store.pages[viewer.currentPage];
  const rect = [page.width - 250, page.height - 150, page.width - 60, page.height - 110];
  const result = await structural('/sign', { kind: 'field', name: values.name, page: viewer.currentPage, rect }, { label: '署名欄の作成' });
  if (result) toast('署名欄を右下に作りました');
}

export async function showSignatureState() {
  const signatures = await (await fetch(docUrl('/signatures'))).json();
  const lines = signatures.fields.length
    ? signatures.fields.map((f) => `・${f.name || '(無名)'} — ${f.page + 1}ページ・${f.signed ? '署名済み' : '未署名'}`).join('\n')
    : '署名欄はありません。';
  await infoDialog({ title: '電子署名の状態', intro: lines, warning: signatures.note });
}
