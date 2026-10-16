// The page organizer: every page of the document as a card in one grid, to be reordered, turned, copied, deleted,
// and mixed with pages from other PDFs or blank sheets in a single go — and applied (or not) at the end.
//
// It works on a plan, not on the document: nothing changes until 「適用する」, and Esc / キャンセル leaves the
// document exactly as it was.

import * as model from './model.js';
import * as pdfjsLib from '../vendor/pdfjs/build/pdf.mjs';
import { confirmDialog } from './dialogs.js';
import { iconSvg } from './icons.js';
import { viewer, state, toast, busy, paint, hooks, prepareAnnots, docUrl, errorDetail, reloadFrom } from './ctx.js';
import { pickFiles, kindOf } from './convert.js';

const VENDOR = new URL('../vendor/pdfjs/', import.meta.url).href;
const SIZE_KEY = 'pdfstudio.organizerSize';

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

let open = false;

export async function openOrganizer() {
  if (open || !model.store.docId) return;
  hooks.flushEditing();
  open = true;
  try {
    await run();
  } finally {
    open = false;
  }
}

function run() {
  return new Promise((resolve) => {
    // ---------------------------------------------------------------- sources of pages
    // -1 is this document; 0.. are PDFs added here; 'blank' pages carry their own size
    const sources = new Map();
    const pages0 = viewer.pageViews.map((v) => ({ page: v.page, w: v.width, h: v.height, rot: v.rotation || 0 }));
    sources.set(-1, { name: model.store.name, count: pages0.length, get: async (i) => pages0[i].page, size: (i) => pages0[i] });
    const extra = [];     // {file, pdf, pages: [{w, h}]}
    let nextId = 1;
    let items = pages0.map((p, i) => ({ id: nextId++, src: -1, page: i, rot: 0 }));
    const original = items.map((i) => `${i.src}:${i.page}:${i.rot}`).join('|');
    let selected = new Set();
    let anchor = null;
    const undoStack = [];
    const redoStack = [];
    let size = 150;
    try { size = Math.min(300, Math.max(90, Number(localStorage.getItem(SIZE_KEY)) || 150)); } catch { /* storage blocked */ }

    const snapshot = () => items.map((i) => ({ ...i }));
    const commit = (mutate) => {
      undoStack.push({ items: snapshot(), selected: new Set(selected) });
      if (undoStack.length > 100) undoStack.shift();
      redoStack.length = 0;
      mutate();
      renderGrid();
    };
    const undo = () => {
      const last = undoStack.pop();
      if (!last) return;
      redoStack.push({ items: snapshot(), selected: new Set(selected) });
      items = last.items; selected = last.selected;
      renderGrid();
    };
    const redo = () => {
      const next = redoStack.pop();
      if (!next) return;
      undoStack.push({ items: snapshot(), selected: new Set(selected) });
      items = next.items; selected = next.selected;
      renderGrid();
    };
    const changed = () => items.map((i) => `${i.src}:${i.page}:${i.rot}`).join('|') !== original;

    // ---------------------------------------------------------------- the screen
    const grid = el('div', { class: 'og-grid', role: 'listbox', 'aria-multiselectable': 'true', 'aria-label': 'ページの一覧' });
    const count = el('div', { class: 'og-count' });
    const root = el('div', { class: 'organizer', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'ページを整理', tabindex: '-1' });
    const button = (icon, label, fn, { primary = false, title = label, key = null } = {}) => {
      const b = el('button', { class: `og-btn${primary ? ' primary' : ''}`, type: 'button', title: key ? `${title}（${key}）` : title, html: iconSvg(icon, 18), onclick: fn }, [el('span', { text: label })]);
      return b;
    };
    const btnRotR = button('rotatecw', '右に回転', () => rotate(90), { key: 'R' });
    const btnRotL = button('rotateccw', '左に回転', () => rotate(-90), { key: 'Shift+R' });
    const btnDup = button('pagecopy', '複製', () => duplicate(), { key: 'Ctrl+D' });
    const btnDel = button('pagedelete', '削除', () => remove(), { key: 'Delete' });
    const btnUp = button('moveup', '前へ', () => nudge(-1), { title: '選んだページを1つ前へ', key: 'Alt+←' });
    const btnDown = button('movedown', '後ろへ', () => nudge(1), { title: '選んだページを1つ後ろへ', key: 'Alt+→' });
    const btnBlank = button('pageblank', '白紙を追加', () => addBlank());
    const btnFile = button('merge', 'PDFを追加…', () => addFromFiles(), { title: '別のPDFのページを、選んだページのあと（なければ最後）に入れる' });
    const btnUndo = button('undo', '元に戻す', undo, { key: 'Ctrl+Z' });
    const btnRedo = button('redo', 'やり直し', redo, { key: 'Ctrl+Y' });
    const menuSel = el('select', { class: 'select og-select', 'aria-label': '選び方', title: 'ページの選び方' }, [
      el('option', { value: '', text: '選ぶ…' }),
      el('option', { value: 'all', text: 'すべて（Ctrl+A）' }),
      el('option', { value: 'odd', text: '奇数ページ（1, 3, 5…）' }),
      el('option', { value: 'even', text: '偶数ページ（2, 4, 6…）' }),
      el('option', { value: 'blank', text: '追加した白紙' }),
      el('option', { value: 'none', text: '選択を解除' }),
    ]);
    menuSel.addEventListener('change', () => {
      const v = menuSel.value;
      menuSel.value = '';
      if (v === 'all') selected = new Set(items.map((i) => i.id));
      else if (v === 'none') selected = new Set();
      else if (v === 'odd') selected = new Set(items.filter((_, k) => k % 2 === 0).map((i) => i.id));
      else if (v === 'even') selected = new Set(items.filter((_, k) => k % 2 === 1).map((i) => i.id));
      else if (v === 'blank') selected = new Set(items.filter((i) => i.src === 'blank').map((i) => i.id));
      renderSelection();
    });
    const zoom = el('input', { type: 'range', min: '90', max: '300', step: '10', value: String(size), 'aria-label': 'ページの大きさ', title: 'ページの大きさ', class: 'og-zoom' });
    zoom.addEventListener('input', () => {
      size = Number(zoom.value);
      try { localStorage.setItem(SIZE_KEY, String(size)); } catch { /* storage blocked */ }
      grid.style.setProperty('--og-size', `${size}px`);
      renderGrid(true);
    });
    const cancel = el('button', { class: 'btn', type: 'button', text: 'キャンセル', onclick: () => close(false) });
    const apply = el('button', { class: 'btn primary', type: 'button', text: '適用する', onclick: () => close(true) });
    const bar = el('div', { class: 'og-bar' }, [
      el('div', { class: 'og-title', text: 'ページを整理' }),
      el('div', { class: 'og-tools' }, [btnRotL, btnRotR, btnUp, btnDown, btnDup, btnDel, el('span', { class: 'og-sep' }), btnBlank, btnFile, el('span', { class: 'og-sep' }), btnUndo, btnRedo]),
      el('div', { class: 'og-right' }, [menuSel, el('span', { class: 'og-zoomlabel', html: iconSvg('thumbs', 16) }), zoom, count, cancel, apply]),
    ]);
    const hint = el('div', { class: 'og-hint', text: 'クリックで選び（Ctrl / Shift で複数）、ドラッグで並べ替えます。ここでの操作は「適用する」を押すまで、文書には反映されません。' });
    root.append(bar, hint, grid);
    grid.style.setProperty('--og-size', `${size}px`);

    // ---------------------------------------------------------------- thumbnails
    const thumbCache = new Map();           // "src:page" -> small canvas
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        paintThumb(entry.target);
      }
    }, { root: grid, rootMargin: '400px 0px' });

    async function paintThumb(card) {
      const canvas = card.querySelector('canvas');
      if (!canvas || canvas.dataset.done) return;
      const src = card.dataset.src;
      const index = Number(card.dataset.page);
      if (src === 'blank') { canvas.dataset.done = '1'; return; }
      const key = `${src}:${index}`;
      const cached = thumbCache.get(key);
      if (cached) {
        canvas.width = cached.width; canvas.height = cached.height;
        canvas.getContext('2d').drawImage(cached, 0, 0);
        canvas.dataset.done = '1';
        return;
      }
      canvas.dataset.done = '1';
      try {
        const page = src === '-1' ? pages0[index].page : await extra[Number(src)].pdf.getPage(index + 1);
        const base = page.getViewport({ scale: 1 });
        const scale = (260 / Math.max(base.width, base.height)) * Math.min(window.devicePixelRatio || 1, 2);
        const viewport = page.getViewport({ scale });
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        await page.render({ canvasContext: canvas.getContext('2d'), viewport, canvas, annotationMode: 1 }).promise;
        const copy = document.createElement('canvas');
        copy.width = canvas.width; copy.height = canvas.height;
        copy.getContext('2d').drawImage(canvas, 0, 0);
        thumbCache.set(key, copy);
      } catch { canvas.dataset.done = ''; }
    }

    const dims = (item) => {
      if (item.src === 'blank') return { w: item.w, h: item.h };
      const s = item.src === -1 ? pages0[item.page] : extra[item.src].pages[item.page];
      return { w: s.w, h: s.h };
    };

    // ---------------------------------------------------------------- the grid
    let dragIds = null;
    function renderGrid(keepScroll = false) {
      const top = grid.scrollTop;
      observer.disconnect();
      grid.textContent = '';
      items.forEach((item, index) => {
        const { w, h } = dims(item);
        const turned = item.rot % 180 !== 0;
        const dw = turned ? h : w;
        const dh = turned ? w : h;
        const fit = Math.min(size / dw, (size * 1.3) / dh);
        const cw = w * fit;
        const ch = h * fit;
        const canvas = el('canvas', { class: 'og-canvas', style: `width:${cw}px;height:${ch}px;transform:rotate(${item.rot}deg)` });
        const frame = el('div', { class: `og-frame${item.src === 'blank' ? ' blank' : ''}`, style: `width:${size}px;height:${size * 1.3}px` }, canvas);
        const badge = el('div', { class: 'og-badge', text: String(index + 1) });
        const tag = item.src === -1 ? null : el('div', { class: 'og-tag', text: item.src === 'blank' ? '白紙' : extra[item.src].file.name.replace(/\.pdf$/i, ''), title: item.src === 'blank' ? '追加した白紙' : extra[item.src].file.name });
        const quick = el('div', { class: 'og-quick' }, [
          el('button', { type: 'button', title: '右に回転', 'aria-label': `${index + 1}ページ目を右に回転`, html: iconSvg('rotatecw', 15), onclick: (e) => { e.stopPropagation(); commit(() => { item.rot = (item.rot + 90) % 360; }); } }),
          el('button', { type: 'button', title: '削除', 'aria-label': `${index + 1}ページ目を削除`, html: iconSvg('trash', 15), onclick: (e) => { e.stopPropagation(); commit(() => { items = items.filter((x) => x !== item); selected.delete(item.id); }); } }),
        ]);
        const card = el('div', {
          class: 'og-card', role: 'option', tabindex: '0', draggable: 'true',
          'data-id': String(item.id), 'data-src': String(item.src), 'data-page': String(item.page),
          'aria-label': `${index + 1}ページ目`, 'aria-selected': selected.has(item.id) ? 'true' : 'false',
        }, [frame, badge, tag, quick]);
        card.addEventListener('click', (e) => select(item, e));
        card.addEventListener('dblclick', () => { selected = new Set([item.id]); renderSelection(); });
        card.addEventListener('dragstart', (e) => {
          if (!selected.has(item.id)) { selected = new Set([item.id]); renderSelection(); }
          dragIds = items.filter((x) => selected.has(x.id)).map((x) => x.id);
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/x-pdfstudio-organize', String(dragIds.length));
          for (const c of grid.querySelectorAll('.og-card')) if (dragIds.includes(Number(c.dataset.id))) c.classList.add('dragging');
        });
        card.addEventListener('dragend', () => { dragIds = null; for (const c of grid.querySelectorAll('.og-card')) c.classList.remove('dragging', 'drop-before', 'drop-after'); });
        card.addEventListener('dragover', (e) => {
          if (!dragIds) return;
          e.preventDefault();
          const box = card.getBoundingClientRect();
          const before = e.clientX < box.left + box.width / 2;
          card.classList.toggle('drop-before', before);
          card.classList.toggle('drop-after', !before);
        });
        card.addEventListener('dragleave', () => card.classList.remove('drop-before', 'drop-after'));
        card.addEventListener('drop', (e) => {
          if (!dragIds) return;
          e.preventDefault(); e.stopPropagation();
          const before = card.classList.contains('drop-before');
          card.classList.remove('drop-before', 'drop-after');
          moveTo(item, before);
        });
        grid.append(card);
        observer.observe(card);
      });
      if (!items.length) grid.append(el('div', { class: 'og-empty', text: 'ページがありません。「元に戻す」で戻すか、「PDFを追加」や「白紙を追加」でページを入れてください。' }));
      if (keepScroll) grid.scrollTop = top;
      renderSelection();
    }

    function renderSelection() {
      for (const card of grid.querySelectorAll('.og-card')) {
        const on = selected.has(Number(card.dataset.id));
        card.classList.toggle('selected', on);
        card.setAttribute('aria-selected', on ? 'true' : 'false');
      }
      const some = selected.size > 0;
      for (const b of [btnRotL, btnRotR, btnDup, btnDel, btnUp, btnDown]) b.disabled = !some;
      btnUndo.disabled = !undoStack.length;
      btnRedo.disabled = !redoStack.length;
      const kept = items.length;
      count.textContent = `${kept} ページ${some ? `（${selected.size} 選択）` : ''}`;
      apply.disabled = !kept;
      apply.textContent = changed() ? '適用する' : '閉じる';
    }

    function select(item, e) {
      const index = items.indexOf(item);
      if (e.shiftKey && anchor !== null) {
        const a = Math.min(anchor, index);
        const b = Math.max(anchor, index);
        selected = new Set(items.slice(a, b + 1).map((i) => i.id));
      } else if (e.ctrlKey || e.metaKey) {
        if (selected.has(item.id)) selected.delete(item.id); else selected.add(item.id);
        anchor = index;
      } else {
        selected = new Set([item.id]);
        anchor = index;
      }
      renderSelection();
    }

    const selectedItems = () => items.filter((i) => selected.has(i.id));

    function rotate(deg) {
      if (!selected.size) return;
      commit(() => { for (const i of selectedItems()) i.rot = (i.rot + deg + 360) % 360; });
    }
    function remove() {
      if (!selected.size) return;
      const first = items.findIndex((i) => selected.has(i.id));
      commit(() => { items = items.filter((i) => !selected.has(i.id)); selected = new Set(); });
      const next = items[Math.min(first, items.length - 1)];
      if (next) { selected = new Set([next.id]); renderSelection(); }
    }
    function duplicate() {
      if (!selected.size) return;
      commit(() => {
        const out = [];
        const fresh = new Set();
        for (const i of items) {
          out.push(i);
          if (selected.has(i.id)) { const copy = { ...i, id: nextId++ }; out.push(copy); fresh.add(copy.id); }
        }
        items = out;
        selected = fresh;
      });
    }
    function nudge(delta) {
      if (!selected.size) return;
      const idx = items.map((i, k) => (selected.has(i.id) ? k : -1)).filter((k) => k >= 0);
      if ((delta < 0 && idx[0] === 0) || (delta > 0 && idx[idx.length - 1] === items.length - 1)) return;
      commit(() => {
        const next = items.slice();
        const order = delta < 0 ? idx : idx.slice().reverse();
        for (const k of order) { const j = k + delta; [next[k], next[j]] = [next[j], next[k]]; }
        items = next;
      });
    }
    function moveTo(target, before) {
      const moving = items.filter((i) => dragIds.includes(i.id));
      if (moving.includes(target)) return;
      commit(() => {
        const rest = items.filter((i) => !dragIds.includes(i.id));
        const at = rest.indexOf(target) + (before ? 0 : 1);
        items = [...rest.slice(0, at), ...moving, ...rest.slice(at)];
      });
    }
    // dropping on the empty part of the grid sends the pages to the end
    grid.addEventListener('dragover', (e) => { if (dragIds) e.preventDefault(); });
    grid.addEventListener('drop', (e) => {
      if (!dragIds) return;
      e.preventDefault();
      const moving = items.filter((i) => dragIds.includes(i.id));
      commit(() => { items = [...items.filter((i) => !dragIds.includes(i.id)), ...moving]; });
    });

    function insertAfterSelection(newItems) {
      const idx = items.map((i, k) => (selected.has(i.id) ? k : -1)).filter((k) => k >= 0);
      const at = idx.length ? idx[idx.length - 1] + 1 : items.length;
      commit(() => {
        items = [...items.slice(0, at), ...newItems, ...items.slice(at)];
        selected = new Set(newItems.map((i) => i.id));
      });
      const card = grid.querySelector(`[data-id="${newItems[0].id}"]`);
      card?.scrollIntoView({ block: 'nearest' });
    }
    function addBlank() {
      const idx = items.map((i, k) => (selected.has(i.id) ? k : -1)).filter((k) => k >= 0);
      const ref = items[idx.length ? idx[idx.length - 1] : items.length - 1];
      const d = ref ? dims(ref) : { w: 595, h: 842 };
      const turned = ref && ref.rot % 180 !== 0;
      insertAfterSelection([{ id: nextId++, src: 'blank', page: 0, rot: 0, w: turned ? d.h : d.w, h: turned ? d.w : d.h }]);
    }
    async function addFromFiles() {
      const files = (await pickFiles('application/pdf,.pdf', true)).filter((f) => kindOf(f) === 'pdf');
      if (!files.length) return;
      const done = busy('PDFを読み込んでいます…');
      try {
        await paint();
        const added = [];
        for (const file of files) {
          let pdf;
          try {
            pdf = await pdfjsLib.getDocument({
              data: new Uint8Array(await file.arrayBuffer()), cMapUrl: `${VENDOR}cmaps/`, cMapPacked: true,
              standardFontDataUrl: `${VENDOR}standard_fonts/`, wasmUrl: `${VENDOR}wasm/`, iccUrl: `${VENDOR}iccs/`,
            }).promise;
          } catch (err) {
            toast(err?.name === 'PasswordException' ? `${file.name} はパスワードで保護されています。先に開いてパスワードを外してから追加してください` : `${file.name} を読み込めませんでした`, 'warn');
            continue;
          }
          const pages = [];
          for (let i = 1; i <= pdf.numPages; i += 1) {
            const v = (await pdf.getPage(i)).getViewport({ scale: 1 });
            pages.push({ w: v.width, h: v.height });
          }
          extra.push({ file, pdf, pages });
          const src = extra.length - 1;
          pages.forEach((_, page) => added.push({ id: nextId++, src, page, rot: 0 }));
        }
        if (added.length) {
          insertAfterSelection(added);
          toast(`${added.length} ページを追加しました`);
        }
      } finally {
        done();
      }
    }

    // ---------------------------------------------------------------- keys
    root.addEventListener('keydown', (e) => {
      if (e.target.closest('select, input[type="range"]') && !['Escape'].includes(e.key)) return;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (key === 'escape') { e.preventDefault(); e.stopPropagation(); close(false); return; }
      if (mod && key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return; }
      if (mod && (key === 'y' || (key === 'z' && e.shiftKey))) { e.preventDefault(); redo(); return; }
      if (mod && key === 'a') { e.preventDefault(); selected = new Set(items.map((i) => i.id)); renderSelection(); return; }
      if (mod && key === 'd') { e.preventDefault(); duplicate(); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); remove(); return; }
      if (!mod && !e.altKey && key === 'r') { e.preventDefault(); rotate(e.shiftKey ? -90 : 90); return; }
      if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowUp')) { e.preventDefault(); nudge(-1); return; }
      if (e.altKey && (e.key === 'ArrowRight' || e.key === 'ArrowDown')) { e.preventDefault(); nudge(1); return; }
      if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key) && !e.altKey && items.length) {
        e.preventDefault();
        const cards = [...grid.querySelectorAll('.og-card')];
        const cols = Math.max(1, Math.round(grid.clientWidth / (size + 24)));
        const current = Math.max(0, items.findIndex((i) => selected.has(i.id)));
        const lastSel = Math.max(0, items.map((i, k) => (selected.has(i.id) ? k : -1)).reduce((a, b) => Math.max(a, b), -1));
        let at = lastSel;
        if (e.key === 'ArrowLeft') at = lastSel - 1;
        else if (e.key === 'ArrowRight') at = lastSel + 1;
        else if (e.key === 'ArrowUp') at = lastSel - cols;
        else if (e.key === 'ArrowDown') at = lastSel + cols;
        else if (e.key === 'Home') at = 0;
        else at = items.length - 1;
        at = Math.max(0, Math.min(items.length - 1, at));
        if (e.shiftKey && anchor !== null) {
          const a = Math.min(anchor, at);
          const b = Math.max(anchor, at);
          selected = new Set(items.slice(a, b + 1).map((i) => i.id));
        } else {
          selected = new Set([items[at].id]);
          anchor = at;
        }
        renderSelection();
        cards[at]?.focus({ preventScroll: true });
        cards[at]?.scrollIntoView({ block: 'nearest' });
      }
    });

    // ---------------------------------------------------------------- closing
    let finished = false;
    async function close(applyIt) {
      if (finished) return;
      if (!applyIt) {
        if (changed() && !(await confirmDialog({ title: '変更を破棄しますか', intro: 'ページの並べ替えなどは、まだ文書に反映されていません。閉じると破棄されます。', confirmLabel: '破棄して閉じる', cancelLabel: '整理を続ける', danger: true }))) return;
        finish(false);
        return;
      }
      if (!changed()) { finish(false); return; }
      if (!items.length) { toast('ページが1枚もありません', 'warn'); return; }
      finish(true);
    }

    async function finish(applyIt) {
      finished = true;
      observer.disconnect();
      document.removeEventListener('focusin', trap, true);
      root.remove();
      for (const e of extra) e.pdf?.destroy?.();
      const lastFocus = document.querySelector('#ribbon button, #stage');
      if (applyIt) await send();
      lastFocus?.blur?.();
      resolve(applyIt);
    }

    async function send() {
      const used = [...new Set(items.filter((i) => typeof i.src === 'number' && i.src >= 0).map((i) => i.src))];
      const form = new FormData();
      // pages added from other PDFs; blanks are taken from a blank page of this document's size (see below)
      for (const k of used) form.append('files', extra[k].file);
      const remap = new Map(used.map((k, n) => [k, n]));
      const plan = [];
      let needBlank = false;
      for (const i of items) {
        if (i.src === 'blank') { needBlank = true; plan.push({ blank: true, w: i.w, h: i.h, rotate: i.rot }); }
        else plan.push({ src: i.src === -1 ? -1 : remap.get(i.src), page: i.page, rotate: i.rot });
      }
      form.append('items', JSON.stringify(plan));
      const prepared = await prepareAnnots();
      form.append('annots', JSON.stringify(prepared.annots));
      form.append('fonts', JSON.stringify(prepared.fonts));
      const label = 'ページの整理';
      const done = busy(`${label}をしています…`);
      try {
        await paint();
        const response = await fetch(docUrl('/organize'), { method: 'POST', body: form });
        if (!response.ok) { toast(`${label}に失敗しました: ${await errorDetail(response)}`, 'error'); return; }
        const result = await response.json();
        await reloadFrom(result);
        state.unsaved = true;
        state.pageSelection.clear();
        hooks.refreshAll();
        toast(`${items.length} ページに整理しました（Ctrl+Z で戻せます）`);
      } catch (err) {
        toast(`${label}に失敗しました: ${err.message}`, 'error');
      } finally {
        done();
      }
    }

    // keep the keyboard inside the organizer while it is open
    const trap = (e) => { if (!root.contains(e.target) && !e.target.closest?.('.dialog-backdrop, .toast')) root.focus({ preventScroll: true }); };
    document.addEventListener('focusin', trap, true);
    document.body.append(root);
    renderGrid();
    root.focus({ preventScroll: true });
  });
}
