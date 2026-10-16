// Turning files into PDFs and PDFs into other files: Word / PowerPoint / Excel in and out, pictures in and out,
// and putting several PDFs together.
//
// All of it runs in the browser (see the engine's convert.py); nothing is uploaded, and there is no limit on how
// many files or how often. The one guard is against sizes that would run the tab out of memory.

import * as model from './model.js';
import { confirmDialog, customDialog, formDialog, infoDialog, node } from './dialogs.js';
import {
  state, toast, busy, paint, hooks, errorDetail, downloadBlob, fileNameOf, metaOf, exportVia, targetPages,
  describePages, docUrl, finishedPdf,
} from './ctx.js';
import { iconSvg } from './icons.js';

const hasDoc = () => !!model.store.docId;
const MB = 1024 * 1024;
/** Fonts the converters draw with; fetched once by the engine and cached. */
export const FONT_FILES = ['BIZUDPGothic-Regular.ttf', 'BIZUDPGothic-Bold.ttf', 'BIZUDPMincho-Regular.ttf'];
const SOFT_LIMIT = 250 * MB;     // ask before going on
const HARD_LIMIT = 900 * MB;     // the tab would not survive it

export const KINDS = {
  word: {
    label: 'Word', icon: 'word', ext: ['docx'], legacy: ['doc', 'odt', 'rtf'],
    accept: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  },
  powerpoint: {
    label: 'PowerPoint', icon: 'powerpoint', ext: ['pptx'], legacy: ['ppt', 'odp'],
    accept: '.pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation',
  },
  excel: {
    label: 'Excel', icon: 'excel', ext: ['xlsx'], legacy: ['xls', 'ods', 'csv'],
    accept: '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  },
};
export const OFFICE_ACCEPT = Object.values(KINDS).map((k) => k.accept).join(',');
const IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,image/bmp,image/tiff,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff';

const extOf = (file) => (/\.([^.]+)$/.exec(file.name) || [, ''])[1].toLowerCase();

/** 'pdf' | 'word' | 'powerpoint' | 'excel' | 'image' | 'legacy' | 'other' */
export function kindOf(file) {
  const ext = extOf(file);
  if (ext === 'pdf' || file.type === 'application/pdf') return 'pdf';
  for (const [name, kind] of Object.entries(KINDS)) if (kind.ext.includes(ext)) return name;
  if (Object.values(KINDS).some((k) => k.legacy.includes(ext) && ext !== 'csv')) return 'legacy';
  if (file.type.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'tif', 'tiff'].includes(ext)) return 'image';
  return 'other';
}

export const isOffice = (file) => ['word', 'powerpoint', 'excel'].includes(kindOf(file));

export function sizeText(bytes) {
  if (bytes >= MB) return `${(bytes / MB).toFixed(bytes >= 10 * MB ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function engineReady() {
  if (window.pdfStudioReady) return Promise.resolve();
  toast('編集機能の準備ができしだい始めます…');
  return new Promise((resolve) => document.addEventListener('pdfstudio:ready', resolve, { once: true }));
}

/** Choose files with the browser's own picker. Resolves with [] when nothing was chosen. */
export function pickFiles(accept, multiple = true) {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.multiple = multiple;
    input.hidden = true;
    input.addEventListener('change', () => { resolve([...input.files]); input.remove(); });
    input.addEventListener('cancel', () => { resolve([]); input.remove(); });
    document.body.append(input);
    input.click();
  });
}

function sizeGuard(files) {
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (total > HARD_LIMIT) {
    return infoDialog({
      title: 'ファイルが大きすぎます',
      intro: `合計 ${sizeText(total)} あります。ブラウザの中では処理しきれず、途中で止まってしまいます。\n数回に分けて、または小さくしてから試してください。`,
    }).then(() => false);
  }
  if (total > SOFT_LIMIT) {
    return confirmDialog({
      title: '大きなファイルです',
      intro: `合計 ${sizeText(total)} あります。変換に時間がかかり、パソコンのメモリによっては途中で止まることがあります。続けますか？`,
      confirmLabel: '続ける', cancelLabel: 'やめる',
    });
  }
  return Promise.resolve(true);
}

// ================================================================ the list of files

/**
 * A dialog for choosing several files and putting them in order. Resolves to {items: [{file, range}], options} or null.
 * `options(box)` fills in the extra choices and returns a function that reads them.
 */
function chooseFilesDialog({ title, intro, warning, accept, accepts, initial = [], ranges = false, thumbs = false, options, confirmLabel,
  emptyText, minFiles = 1 }) {
  let items = [];
  let counter = 0;
  const urls = [];
  let readOptions = () => ({});
  let render = () => {};
  const addFiles = (files) => {
    const bad = [];
    for (const file of files) {
      if (accepts(file)) items.push({ id: ++counter, file, range: '' });
      else bad.push(file.name);
    }
    if (bad.length) toast(`対応していないファイルは除きました: ${bad.slice(0, 3).join('、')}${bad.length > 3 ? ' ほか' : ''}`, 'warn');
    render();
  };

  return customDialog({
    title, intro, warning, confirmLabel, wide: true,
    build: () => {
      const root = node('div', { class: 'filelist-wrap' });
      const addButton = node('button', { class: 'btn', type: 'button', text: 'ファイルを追加…', onclick: async () => addFiles(await pickFiles(accept, true)) });
      const sortName = node('button', { class: 'btn link', type: 'button', text: '名前順', onclick: () => { items.sort((a, b) => a.file.name.localeCompare(b.file.name, 'ja', { numeric: true })); render(); } });
      const reverse = node('button', { class: 'btn link', type: 'button', text: '逆順', onclick: () => { items.reverse(); render(); } });
      const bar = node('div', { class: 'filelist-bar' }, [addButton, node('span', { class: 'spacer' }), sortName, reverse]);
      const list = node('div', { class: 'filelist', role: 'list' });
      const empty = node('div', { class: 'filelist-empty', text: emptyText || 'ここにファイルをドラッグ＆ドロップするか、「ファイルを追加」を押してください' });
      const summary = node('div', { class: 'filelist-summary' });
      root.append(bar, list, summary);
      const optionBox = node('div', { class: 'filelist-options' });
      root.append(optionBox);
      readOptions = options ? options(optionBox) : () => ({});

      // Dropping files here adds them to the list (and must not reach the page's own drop handler)
      const guard = (e) => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.stopPropagation(); e.preventDefault(); } };
      for (const type of ['dragenter', 'dragover', 'dragleave']) root.addEventListener(type, (e) => { guard(e); root.classList.toggle('dropping', type !== 'dragleave'); });
      root.addEventListener('drop', (e) => { guard(e); root.classList.remove('dropping'); addFiles([...e.dataTransfer.files]); });

      let dragged = null;
      render = () => {
        list.textContent = '';
        while (urls.length) URL.revokeObjectURL(urls.pop());
        if (!items.length) list.append(empty);
        items.forEach((item, index) => {
          const row = node('div', { class: 'fileitem', role: 'listitem', draggable: 'true' });
          row.addEventListener('dragstart', (e) => { dragged = item; row.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', item.file.name); });
          row.addEventListener('dragend', () => { dragged = null; row.classList.remove('dragging'); });
          row.addEventListener('dragover', (e) => { if (dragged) { e.preventDefault(); e.stopPropagation(); row.classList.add('over'); } });
          row.addEventListener('dragleave', () => row.classList.remove('over'));
          row.addEventListener('drop', (e) => {
            if (!dragged) return;
            e.preventDefault(); e.stopPropagation();
            const from = items.indexOf(dragged);
            items.splice(from, 1);
            items.splice(items.indexOf(item) + (from < index ? 1 : 0), 0, dragged);
            render();
          });
          row.append(node('span', { class: 'fi-grip', title: 'ドラッグして順番を変える', html: iconSvg('grip', 16) }));
          if (thumbs) {
            const url = URL.createObjectURL(item.file);
            urls.push(url);
            row.append(node('img', { class: 'fi-thumb', src: url, alt: '' }));
          } else {
            row.append(node('span', { class: 'fi-icon', html: iconSvg(kindIcon(item.file), 22) }));
          }
          row.append(node('div', { class: 'fi-main' }, [
            node('div', { class: 'fi-name', text: item.file.name, title: item.file.name }),
            node('div', { class: 'fi-meta', text: sizeText(item.file.size) }),
          ]));
          if (ranges) {
            const range = node('input', { class: 'input fi-range', type: 'text', placeholder: '全ページ', value: item.range, title: '使うページ（例: 1-3, 5, 8-）。空欄なら全ページ', autocomplete: 'off', 'aria-label': `${item.file.name} の使うページ` });
            range.addEventListener('input', () => { item.range = range.value; });
            row.append(range);
          }
          const btn = (icon, label, disabled, fn) => node('button', { class: 'icon-btn', type: 'button', title: label, 'aria-label': label, disabled: disabled ? '' : null, html: iconSvg(icon, 16), onclick: fn });
          row.append(
            btn('moveup', '上へ', index === 0, () => { items.splice(index - 1, 0, items.splice(index, 1)[0]); render(); }),
            btn('movedown', '下へ', index === items.length - 1, () => { items.splice(index + 1, 0, items.splice(index, 1)[0]); render(); }),
            btn('close', '一覧から外す', false, () => { items.splice(index, 1); render(); }),
          );
          list.append(row);
        });
        const total = items.reduce((sum, i) => sum + i.file.size, 0);
        summary.textContent = items.length ? `${items.length} 個のファイル ・ 合計 ${sizeText(total)}` : '';
        summary.classList.toggle('warn', total > SOFT_LIMIT);
        if (total > SOFT_LIMIT) summary.textContent += '（大きいので時間がかかります）';
        sortName.disabled = reverse.disabled = items.length < 2;
      };
      addFiles(initial);
      render();
      return root;
    },
    onConfirm: () => {
      if (items.length < minFiles) { toast(minFiles > 1 ? `${minFiles} 個以上のファイルを選んでください` : 'ファイルを選んでください', 'warn'); return false; }
      const result = { items: items.map((i) => ({ file: i.file, range: i.range.trim() })), options: readOptions() };
      while (urls.length) URL.revokeObjectURL(urls.pop());
      return result;
    },
  });
}

function kindIcon(file) {
  const kind = kindOf(file);
  return { word: 'word', powerpoint: 'powerpoint', excel: 'excel', pdf: 'pdffile', image: 'image' }[kind] || 'pdffile';
}

// small option builders for the dialogs above
function optionRow(label, control, hint) {
  const row = node('div', { class: 'prop-row' }, [node('label', { text: label }), control]);
  const out = [row];
  if (hint) out.push(node('div', { class: 'field-hint', text: hint }));
  return out;
}

function selectControl(choices, value) {
  const select = node('select', { class: 'select' });
  for (const [v, text] of Object.entries(choices)) select.append(node('option', { value: v, text }));
  select.value = value ?? Object.keys(choices)[0];
  return select;
}

function checkControl(value) {
  return node('input', { type: 'checkbox', checked: !!value });
}

/** What to do with a finished PDF: the default depends on whether a document is already open. */
function afterChoice() {
  return selectControl({
    open: '編集画面で開く（保存は Ctrl+S）', download: 'ダウンロードする',
  }, hasDoc() ? 'download' : 'open');
}

// ================================================================ delivering a new document

/** Hand over a document the engine has just created (it is open in the engine, with this id). */
async function deliverDocument(data, after, message) {
  if (after === 'open') {
    state.unsaved = false;       // already agreed to leave the old one
    await hooks.adopt(data, { message });
    state.unsaved = true;
    hooks.refreshAll();
    return;
  }
  try {
    const response = await fetch(`/api/doc/${data.id}/download`);
    if (!response.ok) throw new Error(await errorDetail(response));
    const name = fileNameOf(response, data.name);
    downloadBlob(await response.blob(), name);
    toast(`${name} をダウンロードしました`);
  } finally {
    fetch(`/api/doc/${data.id}/close`, { method: 'POST' }).catch(() => {});
  }
}

async function readyToReplace(after) {
  if (after !== 'open' || !hasDoc() || !state.unsaved) return true;
  return hooks.confirmDiscard();
}

// ================================================================ PDFs together

export async function mergePdfs(initial = []) {
  await engineReady();
  let current = null;
  const result = await chooseFilesDialog({
    title: 'PDFを結合',
    intro: 'いくつかのPDFを、並べた順に1つにまとめます。ドラッグか ↑↓ で順番を変えられます。右の欄にページを書くと、そのページだけを使います。',
    accept: 'application/pdf,.pdf', accepts: (f) => kindOf(f) === 'pdf', initial, ranges: true, minFiles: 1,
    emptyText: 'PDFをここにドラッグ＆ドロップするか、「ファイルを追加」を押してください',
    confirmLabel: '結合する',
    options: (box) => {
      const marks = checkControl(true);
      const after = afterChoice();
      const rows = [...optionRow('ファイル名のしおりを付ける', marks, '結合したあと、各ファイルの先頭ページに名前のしおり（目次）が付きます')];
      if (hasDoc()) {
        current = checkControl(false);
        rows.unshift(...optionRow(`いま開いている「${model.store.name}」も先頭に入れる`, current));
      }
      rows.push(...optionRow('結合したら', after));
      box.append(...rows);
      return () => ({ bookmarks: marks.checked, current: !!current?.checked, after: after.value });
    },
  });
  if (!result) return;
  const files = result.items.map((i) => i.file);
  let list = result.items;
  if (result.options.current) {
    const { blob, name } = await finishedPdf();
    list = [{ file: new File([blob], name, { type: 'application/pdf' }), range: '' }, ...list];
  }
  if (!(await sizeGuard(list.map((i) => i.file)))) return;
  if (!(await readyToReplace(result.options.after))) return;
  const done = busy(`${list.length} 個のPDFを結合しています…`);
  try {
    await paint();
    const form = new FormData();
    for (const item of list) form.append('files', item.file);
    form.append('meta', JSON.stringify(list.map((i) => ({ ranges: i.range }))));
    if (result.options.bookmarks) form.append('bookmarks', '1');
    const response = await fetch('/api/merge-files', { method: 'POST', body: form });
    if (!response.ok) { toast(`結合できませんでした: ${await errorDetail(response)}`, 'error'); return; }
    const data = await response.json();
    await deliverDocument(data, result.options.after, `${list.length} 個のPDF（${data.pageCount} ページ）を結合しました`);
  } catch (err) {
    toast(`結合できませんでした: ${err.message}`, 'error');
  } finally {
    done();
  }
}

// ================================================================ pictures to PDF

export async function imagesToPdf(initial = []) {
  await engineReady();
  const result = await chooseFilesDialog({
    title: '画像からPDFを作る',
    intro: '写真やスキャン画像を、並べた順に1ページずつのPDFにします。',
    accept: IMAGE_ACCEPT, accepts: (f) => kindOf(f) === 'image', initial, thumbs: true, minFiles: 1,
    emptyText: '画像をここにドラッグ＆ドロップするか、「ファイルを追加」を押してください',
    confirmLabel: 'PDFにする',
    options: (box) => {
      const paper = selectControl({ a4: 'A4', a3: 'A3', a5: 'A5', b5: 'B5', b4: 'B4', letter: 'レター', fit: '画像の大きさに合わせる' }, 'a4');
      const orientation = selectControl({ auto: '画像に合わせて自動', portrait: '縦', landscape: '横' }, 'auto');
      const margin = selectControl({ 0: 'なし', 18: '小さい', 36: '大きい' }, '18');
      const after = afterChoice();
      paper.addEventListener('change', () => { orientation.disabled = margin.disabled = paper.value === 'fit'; });
      box.append(...optionRow('用紙', paper), ...optionRow('向き', orientation), ...optionRow('余白', margin), ...optionRow('作ったら', after));
      return () => ({ paper: paper.value, orientation: orientation.value, margin: margin.value, after: after.value });
    },
  });
  if (!result) return;
  const files = result.items.map((i) => i.file);
  if (!(await sizeGuard(files))) return;
  if (!(await readyToReplace(result.options.after))) return;
  const done = busy('画像からPDFを作っています…');
  try {
    await paint();
    const form = new FormData();
    for (const file of files) form.append('files', file);
    form.append('paper', result.options.paper);
    form.append('orientation', result.options.orientation);
    form.append('margin', result.options.paper === 'fit' ? '0' : result.options.margin);
    const response = await fetch('/api/from-images', { method: 'POST', body: form });
    if (!response.ok) { toast(`作成できませんでした: ${await errorDetail(response)}`, 'error'); return; }
    const data = await response.json();
    await deliverDocument(data, result.options.after, `${files.length} 枚の画像からPDFを作りました`);
  } catch (err) {
    toast(`作成できませんでした: ${err.message}`, 'error');
  } finally {
    done();
  }
}

// ================================================================ Word / PowerPoint / Excel to PDF

const OFFICE_TEXT = {
  word: '文書の見た目に近いPDFにします。フォントは近い日本語フォントに置き換わるため、行や改ページが Word と少し違うことがあります。',
  powerpoint: 'スライド1枚を1ページにします。フォントは近い日本語フォントに置き換わります。アニメーションと動画は含まれません。',
  excel: 'シートを、印刷するときと同じ要領で（印刷範囲・用紙・倍率・改ページの設定に従って）PDFにします。',
  any: 'Word・PowerPoint・Excel のファイルを、まとめてPDFにします。フォントは近い日本語フォントに置き換わります。',
};

export async function officeToPdf(kind = 'any', initial = []) {
  await engineReady();
  const accept = kind === 'any' ? OFFICE_ACCEPT : KINDS[kind].accept;
  const accepts = (f) => (kind === 'any' ? isOffice(f) : kindOf(f) === kind);
  const label = kind === 'any' ? 'Word / PowerPoint / Excel' : KINDS[kind].label;
  const legacyNote = kind === 'any' ? '古い形式（.doc .ppt .xls）は、Office で「名前を付けて保存」から新しい形式にしてください。' :
    `古い形式（.${KINDS[kind].legacy.filter((e) => e !== 'csv').join(' .')}）は、${KINDS[kind].label} で「名前を付けて保存」から新しい形式（.${KINDS[kind].ext[0]}）にしてください。`;
  const result = await chooseFilesDialog({
    title: `${label} を PDF に変換`,
    intro: `${OFFICE_TEXT[kind]}\n変換はすべてこの端末の中で行われ、ファイルは外部に送られません。${legacyNote}`,
    accept, accepts, initial, minFiles: 1,
    emptyText: `${label} のファイルをここにドラッグ＆ドロップするか、「ファイルを追加」を押してください`,
    confirmLabel: '変換する',
    options: (box) => {
      const merge = selectControl({ one: '1つのPDFにまとめる', each: 'ファイルごとに別々のPDFにする（ZIPで保存）' }, 'one');
      const after = afterChoice();
      const mergeRow = optionRow('複数のファイルは', merge);
      box.append(...mergeRow, ...optionRow('変換したら', after, 'ファイルごとに別々にした場合は、ZIPをダウンロードします'));
      return () => ({ merge: merge.value === 'one', after: after.value });
    },
  });
  if (!result) return;
  await runOfficeToPdf(result.items.map((i) => i.file), result.options);
}

/** Convert and then hand the result over. `options`: {merge, after}. */
async function runOfficeToPdf(files, { merge = true, after = 'open' } = {}) {
  if (!(await sizeGuard(files))) return false;
  const single = files.length === 1 || merge;
  if (single && !(await readyToReplace(after))) return false;
  const done = busy(files.length === 1 ? `${files[0].name} を変換しています…` : `${files.length} 個のファイルを変換しています…`);
  try {
    await paint();
    const form = new FormData();
    for (const file of files) form.append('files', file);
    if (merge) form.append('merge', '1');
    form.append('fonts', JSON.stringify(FONT_FILES));
    const response = await fetch('/api/office-to-pdf', { method: 'POST', body: form });
    if (!response.ok) { toast(`変換できませんでした: ${await errorDetail(response)}`, 'error'); return false; }
    const meta = metaOf(response);
    const name = fileNameOf(response, 'PDF変換.pdf');
    const blob = await response.blob();
    const isZip = /\.zip$/i.test(name);
    if (!isZip && after === 'open') {
      state.unsaved = false;
      await hooks.openFile(new File([blob], name, { type: 'application/pdf' }));
      state.unsaved = true;
      hooks.refreshAll();
    hooks.refreshAll();
    } else {
      downloadBlob(blob, name);
      toast(`${name} をダウンロードしました`);
    }
    await reportProblems(meta, files.length);
    return true;
  } catch (err) {
    toast(`変換できませんでした: ${err.message}`, 'error');
    return false;
  } finally {
    done();
  }
}

async function reportProblems(meta, count) {
  const lines = [...(meta.failed || []).map((f) => `${f[0]}: ${f[1]}`), ...(meta.warnings || [])];
  if (!lines.length) return;
  await infoDialog({
    title: (meta.failed || []).length ? `変換できなかったファイルがあります（${meta.failed.length} / ${count}）` : '変換しました（お知らせがあります）',
    intro: lines.slice(0, 12).join('\n') + (lines.length > 12 ? `\nほか ${lines.length - 12} 件` : ''),
  });
}

/** Opening a Word / PowerPoint / Excel file from the ordinary "open": convert it, and show the PDF. */
export async function openOfficeFile(file) {
  await engineReady();
  if (!(await readyToReplace('open'))) return;
  const kind = kindOf(file);
  const done = busy(`${file.name} をPDFに変換しています…`);
  try {
    await paint();
    const form = new FormData();
    form.append('files', file);
    form.append('fonts', JSON.stringify(FONT_FILES));
    const response = await fetch('/api/office-to-pdf', { method: 'POST', body: form });
    if (!response.ok) { toast(`開けませんでした: ${await errorDetail(response)}`, 'error'); return; }
    const meta = metaOf(response);
    const name = fileNameOf(response, file.name.replace(/\.[^.]+$/, '') + '.pdf');
    const blob = await response.blob();
    state.unsaved = false;
    await hooks.openFile(new File([blob], name, { type: 'application/pdf' }));
    state.unsaved = true;
    hooks.refreshAll();
    toast(`${KINDS[kind]?.label || ''} ファイルをPDFに変換して開きました。保存すると PDF になります`);
    await reportProblems(meta, 1);
  } catch (err) {
    toast(`開けませんでした: ${err.message}`, 'error');
  } finally {
    done();
  }
}

// ================================================================ PDF to Word / PowerPoint / Excel

const OFFICE_OUT = {
  docx: { label: 'Word', ext: 'docx' },
  pptx: { label: 'PowerPoint', ext: 'pptx' },
  xlsx: { label: 'Excel', ext: 'xlsx' },
};

/** Zero-based page numbers from text like "1-3, 5, 8-"; null for "all". Throws a message for a bad range. */
export function parseRanges(text, count) {
  const source = (text || '').trim();
  if (!source) return null;
  const pages = new Set();
  for (const part of source.split(/[,、，\s]+/).filter(Boolean)) {
    const m = /^(\d*)\s*[-–〜~ー]\s*(\d*)$/.exec(part) || /^(\d+)$/.exec(part);
    if (!m) throw new Error(`「${part}」は読み取れません（例: 1-3, 5, 8-）`);
    let a;
    let b;
    if (m[2] === undefined) { a = b = Number(m[1]); } else { a = m[1] ? Number(m[1]) : 1; b = m[2] ? Number(m[2]) : count; }
    if (a < 1 || b < 1 || a > count || b > count || a > b) throw new Error(`「${part}」は 1〜${count} ページの範囲で指定してください`);
    for (let i = a; i <= b; i += 1) pages.add(i - 1);
  }
  return [...pages].sort((x, y) => x - y);
}

async function pagesWithoutText() {
  try {
    const stats = await (await fetch(docUrl('/stats'))).json();
    return stats.pagesWithoutText || 0;
  } catch { return 0; }
}

/** PDF → Word (docx), PowerPoint (pptx) or Excel (xlsx). With no document open, asks for a PDF first. */
export async function pdfToOffice(fmt) {
  const out = OFFICE_OUT[fmt];
  await engineReady();
  if (!hasDoc()) {
    const [file] = await pickFiles('application/pdf,.pdf', false);
    if (!file) return;
    await hooks.openFile(file);
    if (!hasDoc()) return;
  }
  const count = model.store.pages.length;
  const scanned = await pagesWithoutText();
  const fields = [];
  fields.push({
    key: 'scope', label: '変換するページ', type: 'select',
    options: { all: `すべてのページ（${count}）`, current: describePages(targetPages()), ranges: '範囲を指定' },
  });
  fields.push({ key: 'ranges', label: '範囲', placeholder: '例: 1-3, 5, 8-', hint: '「範囲を指定」を選んだときに使います' });
  if (fmt === 'docx') {
    fields.push({
      key: 'mode', label: '変換のしかた', type: 'select', options: {
        flow: '文章として編集しやすく（段落・表・見出しを作り直す）',
        layout: '見た目をそのまま（図や罫線は画像、文字は位置どおりの枠）',
      },
    });
  }
  if (fmt === 'xlsx') {
    fields.push({
      key: 'mode', label: 'シートの分け方', type: 'select', options: {
        per_page: '1ページを1シートに', single: '全ページを1つのシートに', tables: '表だけを取り出して、表ごとにシートに',
      },
    });
    fields.push({ key: 'numbers', label: '数字や日付を、計算できる値にする', type: 'checkbox', value: true });
  }
  if (scanned) {
    fields.push({
      key: 'ocr', label: `文字のないページ（${scanned}）`, type: 'select', options: {
        yes: '文字認識（OCR）をしてから変換する（推奨）', no: '文字認識せず、画像のまま変換する',
      },
    });
  }
  const values = await formDialog({
    title: `PDF を ${out.label} に変換`,
    intro: {
      docx: '文字・表・画像を取り出して Word の文書にします。元のPDFは変わりません。',
      pptx: '1ページを1枚のスライドにします。文字は編集できる文字、図や背景は画像になります。',
      xlsx: '表を見つけてセルにします。罫線のない表も、文字の並びから作ります。',
    }[fmt] + '\n変換はすべてこの端末の中で行われます。',
    fields,
    validate: (v) => {
      if (v.scope === 'ranges') {
        if (!v.ranges.trim()) return '変換するページの範囲を入力してください';
        try { parseRanges(v.ranges, count); } catch (e) { return e.message; }
      }
      return null;
    },
    confirmLabel: '変換する',
  });
  if (!values) return;
  if (scanned && values.ocr === 'yes') await hooks.runOcr();
  let pages = null;
  if (values.scope === 'current') pages = targetPages();
  else if (values.scope === 'ranges') pages = parseRanges(values.ranges, count);
  const result = await exportVia('/to-office', {
    fmt, mode: values.mode || '', pages, numbers: values.numbers !== false,
  }, { label: `${out.label}に変換`, fallback: `${model.store.name.replace(/\.pdf$/i, '')}.${out.ext}` });
  if (result && result.warnings?.length) {
    await infoDialog({ title: '変換しました（お知らせがあります）', intro: result.warnings.join('\n') });
  }
}

// ================================================================ pictures out of a PDF

export async function extractImages() {
  if (!hasDoc()) return;
  const count = model.store.pages.length;
  const values = await formDialog({
    title: 'PDFに入っている画像を取り出す',
    intro: 'ページに貼られている写真や図を、元の画質のまま取り出します（小さなアイコンは除きます）。複数あるときはZIPにまとめます。\n文字や線で描かれたページをそのまま画像にするときは、「ページを画像にする」を使います。',
    fields: [
      { key: 'scope', label: '対象', type: 'select', options: { all: `すべてのページ（${count}）`, picked: describePages(targetPages()) } },
      { key: 'format', label: '形式', type: 'select', options: { original: '元のまま（JPEG・PNGなど）', jpg: 'すべてJPEGにする', png: 'すべてPNGにする' } },
    ],
    confirmLabel: '取り出す',
  });
  if (!values) return;
  await exportVia('/extract-images', { pages: values.scope === 'all' ? null : targetPages(), format: values.format },
    { label: '画像の取り出し', fallback: 'images.zip' });
}
