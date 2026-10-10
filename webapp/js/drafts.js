// Crash recovery for work that has not been saved to a file yet.
//
// Everything lives in one browser tab until the user saves. A closed lid, a
// crashed tab or an update restart would otherwise take an evening of notes
// with it. So the markup is copied into the browser's own storage a moment
// after each change, keyed by the file it belongs to, and offered back the
// next time that file is opened. A successful save clears it.
//
// Only markup is kept here. Operations that rewrite the pages themselves
// (deleting pages, redaction) cannot be replayed from a list of annotations,
// so a draft is not kept once one of those has run.

const DB_NAME = 'pdfstudio-drafts';
const STORE = 'drafts';
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

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

/** A name for "this exact file": same name, same size, same modification time. */
export function keyFor(file) {
  if (!file) return null;
  return `${file.name}|${file.size}|${file.lastModified}`;
}

export async function saveDraft(key, annots, pageCount) {
  if (!key) return;
  try {
    await run('readwrite', (store) => store.put({ annots, pageCount, savedAt: Date.now() }, key));
  } catch { /* storage full or blocked: recovery is a courtesy, not a promise */ }
}

export async function loadDraft(key) {
  if (!key) return null;
  try {
    const draft = await run('readonly', (store) => store.get(key));
    if (!draft || Date.now() - draft.savedAt > MAX_AGE_MS) return null;
    return draft;
  } catch {
    return null;
  }
}

export async function clearDraft(key) {
  if (!key) return;
  try { await run('readwrite', (store) => store.delete(key)); } catch { /* nothing to clear */ }
}

/** Drop drafts nobody came back for. */
export async function pruneDrafts() {
  try {
    const db = await open();
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    const cursor = store.openCursor();
    cursor.onsuccess = () => {
      const row = cursor.result;
      if (!row) return;
      if (Date.now() - (row.value?.savedAt || 0) > MAX_AGE_MS) row.delete();
      row.continue();
    };
    tx.oncomplete = () => db.close();
  } catch { /* ignore */ }
}
