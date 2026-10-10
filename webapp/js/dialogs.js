// Dialogs, menus and popovers.
//
// Operations that rewrite the file itself state plainly what will be lost
// before they run.

import { iconSvg, hasIcon } from './icons.js';

function node(tag, props = {}, children = []) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key === 'html') el.innerHTML = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'value' || key === 'checked') el[key] = value;
    else if (value !== null && value !== undefined && value !== false) el.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) el.append(child);
  return el;
}

/** True while a key event belongs to an input method (kana-kanji conversion). */
export function isComposing(event) {
  return event.isComposing || event.keyCode === 229;
}

function shell({ title, intro, warning, body, confirmLabel = 'OK', cancelLabel = 'キャンセル', danger, wide, hideCancel }) {
  const dialog = node('div', { class: `dialog${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true' }, [
    node('h2', { text: title }),
    intro ? node('p', { text: intro }) : null,
    warning ? node('div', { class: 'warn-box', text: warning }) : null,
  ]);
  if (body) dialog.append(body);
  const cancel = node('button', { class: 'btn', text: cancelLabel });
  const confirm = node('button', { class: `btn ${danger ? '' : 'primary'}`, text: confirmLabel });
  if (danger) confirm.style.cssText = 'background:var(--danger);border-color:var(--danger);color:#fff';
  dialog.append(node('div', { class: 'dialog-actions' }, [hideCancel ? null : cancel, confirm]));
  const backdrop = node('div', { class: 'dialog-backdrop' }, dialog);
  return { backdrop, dialog, cancel, confirm };
}

function mount(backdrop, finish) {
  // Close only when both the press and the release land on the backdrop;
  // otherwise selecting text in a field and letting go outside would close it.
  let pressedOutside = false;
  backdrop.addEventListener('pointerdown', (e) => { pressedOutside = e.target === backdrop; });
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop && pressedOutside) finish(null); });
  backdrop.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Escape' && !isComposing(e)) { e.preventDefault(); finish(null); }
  });
  document.body.append(backdrop);
}

export function confirmDialog(options) {
  return new Promise((resolve) => {
    const { backdrop, cancel, confirm } = shell(options);
    const finish = (value) => { backdrop.remove(); resolve(!!value); };
    cancel.addEventListener('click', () => finish(false));
    confirm.addEventListener('click', () => finish(true));
    mount(backdrop, finish);
    confirm.focus();
  });
}

/** A message with a single button. */
export function infoDialog(options) {
  return confirmDialog({ confirmLabel: '閉じる', hideCancel: true, ...options });
}

/**
 * A dialog built from a field list. Resolves to the collected values, or null.
 * Fields: {key, label, type: text|textarea|number|colour|select|checkbox|password, options, value, hint}
 */
export function formDialog({ fields, validate, ...options }) {
  return new Promise((resolve) => {
    const form = node('div', { class: 'dialog-form' });
    const inputs = new Map();

    for (const field of fields) {
      let input;
      if (field.type === 'select') {
        input = node('select', { class: 'select' });
        for (const [value, label] of Object.entries(field.options)) {
          input.append(node('option', { value, text: label }));
        }
        input.value = String(field.value ?? Object.keys(field.options)[0]);
      } else if (field.type === 'checkbox') {
        input = node('input', { type: 'checkbox', checked: !!field.value });
      } else if (field.type === 'colour') {
        input = node('input', { type: 'color', value: field.value || '#000000' });
      } else if (field.type === 'textarea') {
        input = node('textarea', { class: 'input', value: field.value ?? '', placeholder: field.placeholder || '', rows: field.rows || 3 });
      } else {
        input = node('input', {
          class: 'input', type: field.type || 'text',
          value: field.value ?? '',
          placeholder: field.placeholder || '',
          min: field.min, max: field.max, step: field.step,
          autocomplete: 'off',
        });
      }
      inputs.set(field.key, { input, field });
      form.append(node('div', { class: 'prop-row' }, [
        node('label', { text: field.label }),
        input,
      ]));
      if (field.hint) form.append(node('div', { class: 'field-hint', text: field.hint }));
    }
    const problem = node('div', { class: 'warn-inline', hidden: '' });
    form.append(problem);

    const { backdrop, cancel, confirm } = shell({ ...options, body: form });
    const collect = () => {
      const values = {};
      for (const [key, { input, field }] of inputs) {
        if (field.type === 'checkbox') values[key] = input.checked;
        else if (field.type === 'number') values[key] = Number(input.value);
        else values[key] = input.value;
      }
      return values;
    };
    const finish = (ok) => {
      if (!ok) { backdrop.remove(); resolve(null); return; }
      const values = collect();
      const complaint = validate ? validate(values) : null;
      if (complaint) {
        problem.textContent = complaint;
        problem.hidden = false;
        return;
      }
      backdrop.remove();
      resolve(values);
    };
    cancel.addEventListener('click', () => finish(false));
    confirm.addEventListener('click', () => finish(true));
    form.addEventListener('keydown', (e) => {
      // Enter confirms the dialog — but not while it is confirming a
      // kana-kanji conversion, and not inside a multi-line field.
      if (e.key === 'Enter' && !isComposing(e) && e.target.tagName !== 'TEXTAREA') {
        e.preventDefault();
        finish(true);
      }
    });
    mount(backdrop, () => finish(false));
    const first = [...inputs.values()][0];
    if (first) { first.input.focus(); first.input.select?.(); }
  });
}

/** A dialog whose body the caller builds. `build(close)` returns a DOM node. */
export function customDialog({ build, onConfirm, ...options }) {
  return new Promise((resolve) => {
    let closed = false;
    const finish = (value) => {
      if (closed) return;
      closed = true;
      parts.backdrop.remove();
      resolve(value ?? null);
    };
    const body = build(finish);
    const parts = shell({ ...options, body });
    parts.cancel.addEventListener('click', () => finish(null));
    parts.confirm.addEventListener('click', async () => {
      const value = onConfirm ? await onConfirm() : true;
      if (value !== undefined && value !== null && value !== false) finish(value);
    });
    mount(parts.backdrop, finish);
  });
}

// ---------------------------------------------------------------- menus

function placeMenu(menu, x, y, anchorBottom = null) {
  document.body.append(menu);
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const left = Math.max(6, Math.min(x, window.innerWidth - width - 6));
  let top = y;
  if (top + height > window.innerHeight - 6) {
    // Not enough room below: open upwards if that fits, otherwise scroll.
    const above = (anchorBottom ?? y) - height - 4;
    top = above >= 6 ? above : Math.max(6, window.innerHeight - height - 6);
  }
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.style.maxHeight = `${window.innerHeight - 12}px`;
}

function closeOnOutside(element, onClose) {
  const close = (e) => {
    if (e && element.contains(e.target)) return;
    element.remove();
    document.removeEventListener('pointerdown', close, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('blur', close);
    onClose?.();
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  setTimeout(() => {
    document.addEventListener('pointerdown', close, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('blur', close);
  }, 0);
  return close;
}

function buildMenu(entries) {
  document.querySelector('.menu')?.remove();
  const menu = node('div', { class: 'menu', role: 'menu' });
  let close = () => menu.remove();
  for (const entry of entries) {
    if (!entry) continue;
    if (entry === '-') { menu.append(document.createElement('hr')); continue; }
    if (entry.heading) { menu.append(node('div', { class: 'menu-heading', text: entry.heading })); continue; }
    if (entry.note) { menu.append(node('div', { class: 'menu-note', text: entry.note })); continue; }
    const button = node('button', {
      class: entry.danger ? 'danger' : '',
      role: 'menuitem',
      onclick: () => { close(); entry.action(); },
    });
    if (entry.icon && hasIcon(entry.icon)) button.insertAdjacentHTML('beforeend', iconSvg(entry.icon, 17));
    button.append(node('span', { text: entry.label }));
    if (entry.key) button.append(node('span', { class: 'key', text: entry.key }));
    button.disabled = !!entry.disabled;
    if (entry.title) button.title = entry.title;
    menu.append(button);
  }
  return { menu, setClose: (fn) => { close = fn; } };
}

export function openMenu(anchor, entries) {
  const { menu, setClose } = buildMenu(entries);
  const box = anchor.getBoundingClientRect();
  placeMenu(menu, box.left, box.bottom + 3, box.top);
  setClose(closeOnOutside(menu));
}

export function openMenuAt(x, y, entries) {
  const { menu, setClose } = buildMenu(entries);
  placeMenu(menu, x, y);
  setClose(closeOnOutside(menu));
}

/** A small floating panel anchored under a control (colour palettes etc.). */
export function openPopover(anchor, build) {
  document.querySelector('.popover')?.remove();
  const pop = node('div', { class: 'popover' });
  let close = () => pop.remove();
  pop.append(build(() => close()));
  document.body.append(pop);
  const box = anchor.getBoundingClientRect();
  const left = Math.max(6, Math.min(box.left, window.innerWidth - pop.offsetWidth - 6));
  const below = box.bottom + 4;
  pop.style.left = `${left}px`;
  pop.style.top = `${below + pop.offsetHeight > window.innerHeight - 6 ? Math.max(6, box.top - pop.offsetHeight - 4) : below}px`;
  close = closeOnOutside(pop);
  return close;
}

export { node };
