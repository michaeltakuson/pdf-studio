// Shared state and plumbing: the viewer, the tool controller, talking to the
// PDF engine, and the small UI helpers every other module needs.

import { Viewer } from './viewer.js';
import { ToolController } from './tools.js';
import * as model from './model.js';
import { computeLayout, ensureFontLoaded, fontFilesFor } from './textedit.js';

export const $ = (sel) => document.querySelector(sel);

export const stage = $('#stage');
export const viewer = new Viewer($('#pages'), stage);
export const tools = new ToolController(viewer);

export const state = {
  toc: [],
  filters: { query: '', checked: 'all', author: 'all', state: 'all', type: 'all', sort: 'page' },
  takeoffSubject: '',
  searchHits: [],
  searchIndex: -1,
  searchQuery: '',
  fileHandle: null,     // File System Access handle of the opened/saved file, when the browser gives one
  draftKey: null,       // where the crash-recovery copy of this document's markup is kept
  unsaved: false,       // changes not yet written to a file on disk
  pageSelection: new Set(),
  pageAnchor: 0,
  editor: null,         // the open text box editor, if any
  textLines: new Map(), // page index -> body text lines, for the edit-text tool
  working: false,
};

/** Set by app.js once the rest of the UI exists. */
export const hooks = {
  refreshAll: () => {},
  afterReload: () => {},
  flushEditing: () => {},
};

// ---------------------------------------------------------------- feedback

let toastTimer;
export function toast(message, kind = '') {
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, kind === 'error' ? 6000 : 3400);
}

export function status(message) {
  $('#statusLeft').textContent = message;
}

/** Show that the page is working. Long engine work would otherwise look like a hang. */
export function busy(message) {
  $('#busyText').textContent = message;
  $('#busy').hidden = false;
  state.working = true;
  return () => { $('#busy').hidden = true; state.working = false; };
}

/** Let the busy indicator actually reach the screen before blocking work starts. */
export const paint = () => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));

// ---------------------------------------------------------------- engine calls

/**
 * The markup as it should be written to the file: every text box carries the
 * line breaks the browser is showing, and the list of font files it needs.
 */
export async function prepareAnnots() {
  hooks.flushEditing();
  const annots = model.store.annots;
  const texts = annots.filter((a) => a.type === 'freetext' && (a.text || '').length);
  // Line breaks measured before the face has loaded would be measured in a
  // fallback font and come out in the wrong places.
  await Promise.all(texts.map((a) => ensureFontLoaded(a.style?.font, a.text)));
  return {
    annots: annots.map((a) => (a.type === 'freetext' ? { ...a, layout: computeLayout(a) } : a)),
    fonts: fontFilesFor(annots),
  };
}

export function docUrl(path = '') {
  return `/api/doc/${model.store.docId}${path}`;
}

/** POST JSON to the engine. `withAnnots` sends the current markup along. */
export async function post(path, body = {}, { withAnnots = false } = {}) {
  const payload = withAnnots ? { ...(await prepareAnnots()), ...body } : body;
  return fetch(docUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function errorDetail(response) {
  try { return (await response.json()).detail || response.status; } catch { return response.status; }
}

/**
 * Run an operation that rewrites the document itself, then reload it.
 *
 * Every such call takes the same path: write the markup, run the operation
 * (the engine snapshots first, which is what makes it undoable), reload the
 * pages where the reader was, and mark the document as changed.
 */
export async function structural(path, payload = {}, { label = '処理' } = {}) {
  if (!model.store.docId) return null;
  const done = busy(`${label}しています…`);
  try {
    await paint();
    const response = await post(path, payload, { withAnnots: true });
    if (!response.ok) {
      toast(`${label}に失敗しました: ${await errorDetail(response)}`, 'error');
      return null;
    }
    const result = await response.json();
    await reloadFrom(result);
    state.unsaved = true;
    hooks.refreshAll();
    return result;
  } catch (err) {
    toast(`${label}に失敗しました: ${err.message}`, 'error');
    return null;
  } finally {
    done();
  }
}

/** Take a fresh description of the document from the engine and redraw. */
export async function reloadFrom(result, { keepPosition = true } = {}) {
  state.toc = result.toc || [];
  state.textLines.clear();
  model.loadDocument(result, { keepName: true });
  await viewer.load(`${docUrl('/file')}?t=${Date.now()}`, { keepPosition });
  hooks.afterReload();
}

// ---------------------------------------------------------------- downloads

export function fileNameOf(response, fallback) {
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  return match ? decodeURIComponent(match[1]) : fallback;
}

export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export async function downloadResponse(response, fallback) {
  const name = fileNameOf(response, fallback);
  downloadBlob(await response.blob(), name);
  toast(`${name} を書き出しました`);
}

/** POST, then hand whatever comes back to the browser as a download. */
export async function exportVia(path, body, { label = '書き出し', fallback = 'export', withAnnots = true } = {}) {
  if (!model.store.docId) return false;
  const done = busy(`${label}しています…`);
  try {
    await paint();
    const response = await post(path, body, { withAnnots });
    if (!response.ok) { toast(`${label}に失敗しました: ${await errorDetail(response)}`, 'error'); return false; }
    await downloadResponse(response, fallback);
    return true;
  } catch (err) {
    toast(`${label}に失敗しました: ${err.message}`, 'error');
    return false;
  } finally {
    done();
  }
}

/** The finished PDF, markup included, as bytes. */
export async function finishedPdf() {
  const response = await post('/download', {}, { withAnnots: true });
  if (!response.ok) throw new Error(await errorDetail(response));
  return { blob: await response.blob(), name: fileNameOf(response, 'document.pdf') };
}

/** Pages an operation should act on: the thumbnails selected, else the page in view. */
export function targetPages() {
  const picked = [...state.pageSelection].filter((p) => p < model.store.pages.length).sort((a, b) => a - b);
  return picked.length ? picked : [viewer.currentPage];
}

export function describePages(pages) {
  if (pages.length === 1) return `${pages[0] + 1} ページ目`;
  if (pages.length <= 4) return `${pages.map((p) => p + 1).join('・')} ページ目`;
  return `選択した ${pages.length} ページ`;
}
