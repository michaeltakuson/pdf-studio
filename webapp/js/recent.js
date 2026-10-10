// Recently opened files, and the small personal lists the app remembers.
//
// A file opened through the browser's file picker comes with a handle that
// can be kept (in this browser only) and used to open the same file again
// with one click — the browser asks the user to confirm access each session.
// Nothing here leaves the machine.

const DB_NAME = 'pdfstudio-recent';
const STORE = 'files';
const LIMIT = 8;

function open() {
  return new Promise((resolve, reject) => {
    if (!('indexedDB' in window)) { reject(new Error('no indexedDB')); return; }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run(mode, work) {
  const db = await open();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const request = work(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(request?.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

/** Remember a file that was opened or saved through a handle. */
export async function rememberFile(handle) {
  if (!handle?.name) return;
  try {
    const list = await recentFiles();
    // The same file may be reached through a new handle object; isSameEntry
    // tells them apart from two different files that share a name.
    const kept = [];
    for (const item of list) {
      let same = false;
      try { same = await item.handle.isSameEntry(handle); } catch { same = item.name === handle.name; }
      if (!same) kept.push(item);
    }
    kept.unshift({ handle, name: handle.name, at: Date.now() });
    await run('readwrite', (store) => store.put(kept.slice(0, LIMIT), 'list'));
  } catch { /* remembering is a convenience; never let it break opening a file */ }
}

export async function recentFiles() {
  try {
    return (await run('readonly', (store) => store.get('list'))) || [];
  } catch {
    return [];
  }
}

export async function forgetFile(name) {
  try {
    const list = (await recentFiles()).filter((item) => item.name !== name);
    await run('readwrite', (store) => store.put(list, 'list'));
  } catch { /* nothing to forget */ }
}

// ---------------------------------------------------------------- snippets

const SNIPPET_KEY = 'pdfstudio.snippets.v1';

export const SNIPPET_FIELDS = [
  ['name', '氏名'], ['kana', 'ふりがな'], ['address', '住所'], ['postal', '郵便番号'],
  ['phone', '電話番号'], ['email', 'メールアドレス'], ['org', '所属（学校・会社）'],
  ['number', '学籍番号・社員番号'], ['birth', '生年月日'], ['free1', '自由1'], ['free2', '自由2'],
];

export function loadSnippets() {
  try { return JSON.parse(localStorage.getItem(SNIPPET_KEY) || '{}'); } catch { return {}; }
}

export function saveSnippets(values) {
  try { localStorage.setItem(SNIPPET_KEY, JSON.stringify(values)); } catch { /* storage blocked */ }
}
