// Annotation store: the editable source of truth while the user works.
// Every mutation goes through a command so undo/redo stays exact, and the
// server only sees the result when the document is saved.

const listeners = new Set();

export const store = {
  docId: null,
  name: '',
  pages: [],
  annots: [],
  selection: [],
  dirty: false,
};

let undoStack = [];
let redoStack = [];

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(reason) {
  for (const fn of listeners) fn(reason);
}

export function uid() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

export function loadDocument({ id, name, pages, annots }) {
  store.docId = id;
  store.name = name;
  store.pages = pages;
  store.annots = (annots || []).map(normalise);
  store.selection = [];
  store.dirty = false;
  undoStack = [];
  redoStack = [];
  emit('document');
}

function normalise(a) {
  return {
    id: a.id || uid(),
    checked: false,
    replies: [],
    state: null,
    contents: '',
    author: '',
    subject: '',
    ...a,
  };
}

export function byId(id) {
  return store.annots.find((a) => a.id === id) || null;
}

export function onPage(pageIndex) {
  return store.annots.filter((a) => a.page === pageIndex);
}

// ---------------------------------------------------------------- commands

function run(command, reason) {
  command.redo();
  undoStack.push(command);
  redoStack = [];
  store.dirty = true;
  emit(reason || 'change');
}

export function undo() {
  const command = undoStack.pop();
  if (!command) return;
  command.undo();
  redoStack.push(command);
  store.dirty = true;
  emit('change');
}

export function redo() {
  const command = redoStack.pop();
  if (!command) return;
  command.redo();
  undoStack.push(command);
  store.dirty = true;
  emit('change');
}

export const history = {
  get canUndo() { return undoStack.length > 0; },
  get canRedo() { return redoStack.length > 0; },
};

export function addAnnots(items, { select = true } = {}) {
  const created = items.map((item) => normalise({ ...item, id: item.id || uid() }));
  const previous = store.selection;
  run({
    redo() {
      store.annots.push(...created);
      if (select) store.selection = created.map((a) => a.id);
    },
    undo() {
      const ids = new Set(created.map((a) => a.id));
      store.annots = store.annots.filter((a) => !ids.has(a.id));
      store.selection = previous;
    },
  }, 'add');
  return created;
}

export function removeAnnots(ids) {
  const set = new Set(ids);
  const removed = store.annots
    .map((a, index) => ({ a, index }))
    .filter(({ a }) => set.has(a.id));
  if (!removed.length) return;
  const previous = store.selection;
  run({
    redo() {
      store.annots = store.annots.filter((a) => !set.has(a.id));
      store.selection = [];
    },
    undo() {
      for (const { a, index } of removed) store.annots.splice(index, 0, a);
      store.selection = previous;
    },
  }, 'remove');
}

function restore(snapshots) {
  for (const snapshot of snapshots) {
    const index = store.annots.findIndex((a) => a.id === snapshot.id);
    if (index >= 0) store.annots[index] = structuredClone(snapshot);
  }
}

/**
 * Apply a patch to many annotations at once (bulk formatting, moves, edits).
 *
 * `merge` takes a token identifying one continuous gesture — a single drag, a
 * single slider scrub, one run of typing. Calls sharing a token collapse into
 * one undo step; a new token starts a new one. Undo and redo both work from
 * snapshots rather than by replaying the patch, because during a merged
 * gesture the patch only ever describes the latest increment: replaying it
 * would redo one small step of a long drag instead of the whole thing.
 */
export function updateAnnots(ids, patch, { merge = null } = {}) {
  const targets = ids.map(byId).filter(Boolean);
  if (!targets.length) return;

  const last = undoStack[undoStack.length - 1];
  if (merge && last && last.mergeToken && last.mergeToken === merge) {
    applyPatch(targets, patch);
    last.after = ids.map(byId).filter(Boolean).map((a) => structuredClone(a));
    store.dirty = true;
    emit('change');
    return;
  }

  const before = targets.map((a) => structuredClone(a));
  applyPatch(targets, patch);
  const after = ids.map(byId).filter(Boolean).map((a) => structuredClone(a));

  undoStack.push({
    mergeToken: merge || null,
    after,
    redo() { restore(this.after); },
    undo() { restore(before); },
  });
  redoStack = [];
  store.dirty = true;
  emit('update');
}

/** Close the current merge run, so the next edit starts a fresh undo step. */
export function endMerge() {
  const last = undoStack[undoStack.length - 1];
  if (last) last.mergeToken = null;
}

function applyPatch(targets, patch) {
  for (const target of targets) deepAssign(target, patch);
}

function deepAssign(target, patch) {
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      target[key] = deepAssign({ ...(target[key] || {}) }, value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

export function select(ids, { additive = false } = {}) {
  const next = additive ? [...new Set([...store.selection, ...ids])] : ids;
  if (next.length === store.selection.length && next.every((id, i) => id === store.selection[i])) return;
  store.selection = next;
  emit('selection');
}

export function markClean() {
  store.dirty = false;
  emit('saved');
}

export function touch(reason) {
  emit(reason || 'change');
}
