// Confirmation and small form dialogs.
//
// Operations in layers ② and ③ rewrite the file itself, so each one that
// destroys content states plainly what will be lost before it runs.

function node(tag, props = {}, children = []) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'value' || key === 'checked') el[key] = value;
    else if (value !== null && value !== undefined && value !== false) el.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) el.append(child);
  return el;
}

function shell({ title, intro, warning, body, confirmLabel, danger }) {
  const dialog = node('div', { class: 'dialog' }, [
    node('h2', { text: title }),
    intro ? node('p', { text: intro }) : null,
    warning ? node('div', { class: 'warn-box', text: warning }) : null,
  ]);
  if (body) dialog.append(body);
  const cancel = node('button', { class: 'btn', text: 'キャンセル' });
  const confirm = node('button', { class: `btn ${danger ? '' : 'primary'}`, text: confirmLabel });
  if (danger) confirm.style.cssText = 'background:var(--danger);border-color:var(--danger);color:#fff';
  dialog.append(node('div', { class: 'dialog-actions' }, [cancel, confirm]));
  const backdrop = node('div', { class: 'dialog-backdrop' }, dialog);
  return { backdrop, dialog, cancel, confirm };
}

export function confirmDialog(options) {
  return new Promise((resolve) => {
    const { backdrop, cancel, confirm } = shell(options);
    const finish = (value) => { backdrop.remove(); resolve(value); };
    cancel.addEventListener('click', () => finish(false));
    confirm.addEventListener('click', () => finish(true));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) finish(false); });
    document.body.append(backdrop);
    confirm.focus();
  });
}

/**
 * A dialog built from a field list. Resolves to the collected values, or null.
 * Fields: {key, label, type: text|number|colour|select|checkbox, options, value, hint}
 */
export function formDialog({ fields, ...options }) {
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
      } else {
        input = node('input', {
          class: 'input', type: field.type || 'text',
          value: field.value ?? '',
          placeholder: field.placeholder || '',
          min: field.min, max: field.max, step: field.step,
        });
      }
      inputs.set(field.key, { input, field });
      form.append(node('div', { class: 'prop-row' }, [
        node('label', { text: field.label }),
        input,
      ]));
      if (field.hint) form.append(node('div', { class: 'field-hint', text: field.hint }));
    }

    const { backdrop, cancel, confirm } = shell({ ...options, body: form });
    const finish = (ok) => {
      backdrop.remove();
      if (!ok) { resolve(null); return; }
      const values = {};
      for (const [key, { input, field }] of inputs) {
        if (field.type === 'checkbox') values[key] = input.checked;
        else if (field.type === 'number') values[key] = Number(input.value);
        else values[key] = input.value;
      }
      resolve(values);
    };
    cancel.addEventListener('click', () => finish(false));
    confirm.addEventListener('click', () => finish(true));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) finish(false); });
    form.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(true); });
    document.body.append(backdrop);
    const first = [...inputs.values()][0];
    if (first) first.input.focus();
  });
}

export function openMenu(anchor, entries) {
  document.querySelector('.menu')?.remove();
  const menu = node('div', { class: 'menu' });
  for (const entry of entries) {
    if (entry === '-') { menu.append(document.createElement('hr')); continue; }
    if (entry.heading) {
      menu.append(node('div', { class: 'menu-heading', text: entry.heading }));
      continue;
    }
    if (entry.note) {
      menu.append(node('div', { class: 'menu-note', text: entry.note }));
      continue;
    }
    const button = node('button', { text: entry.label, onclick: () => { menu.remove(); entry.action(); } });
    button.disabled = !!entry.disabled;
    if (entry.title) button.title = entry.title;
    menu.append(button);
  }
  document.body.append(menu);
  const box = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${box.bottom + 4}px`;
  menu.style.maxHeight = `${window.innerHeight - box.bottom - 20}px`;
  setTimeout(() => {
    const close = (e) => {
      if (menu.contains(e.target)) return;
      menu.remove();
      document.removeEventListener('pointerdown', close);
    };
    document.addEventListener('pointerdown', close);
  }, 0);
}
