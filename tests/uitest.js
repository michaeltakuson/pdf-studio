// Browser-side test harness. Loaded into the running app and driven from
// Playwright, so every check exercises the real UI rather than a stub.
//
//   const t = await import('/tests/uitest.js');   // served from the frontend
//
// Each helper mirrors what a person would actually do: real pointer events on
// the page, real clicks on menus, real keyboard input.

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => [...document.querySelectorAll(sel)];

export async function model() {
  return import('/js/model.js');
}

function wrap(pageIndex = null) {
  return pageIndex === null
    ? document.querySelector('.page-wrap')
    : document.querySelector(`.page-wrap[data-page="${pageIndex}"]`);
}

function draw(pageIndex = null) {
  return wrap(pageIndex).querySelector('.draw-layer');
}

function at(x, y, pageIndex = null) {
  const box = wrap(pageIndex).getBoundingClientRect();
  const scale = Number(wrap(pageIndex).style.getPropertyValue('--scale-factor')) || 1;
  return { clientX: box.left + x * scale, clientY: box.top + y * scale };
}

/**
 * Dispatch a pointer event in page coordinates (points, not screen pixels).
 *
 * The target is whatever is actually on top at that point — the same element a
 * real click would hit. Dispatching on a fixed layer would bypass the app's
 * own pointer-events juggling (the draw layer goes inert for the select tool),
 * and hit-testing that depends on `event.target` would never be exercised.
 */
export function pointer(type, x, y, opts = {}) {
  const { page = null, kind = 'mouse', pressure = 0.5, shift = false, id = 9 } = opts;
  const down = type === 'pointerdown' || type === 'pointermove';
  const position = at(x, y, page);
  const target = document.elementFromPoint(position.clientX, position.clientY) || draw(page);
  target.dispatchEvent(new PointerEvent(type, {
    bubbles: true, cancelable: true, composed: true,
    pointerId: id, pointerType: kind, isPrimary: true,
    pressure: down ? pressure : 0,
    ...position,
    button: 0, buttons: down ? 1 : 0, shiftKey: shift,
  }));
}

export async function click(x, y, opts = {}) {
  pointer('pointerdown', x, y, opts);
  await sleep(20);
  pointer('pointerup', x, y, opts);
  await sleep(opts.settle ?? 120);
}

export async function drag(x0, y0, x1, y1, opts = {}) {
  const steps = opts.steps ?? 8;
  pointer('pointerdown', x0, y0, opts);
  for (let i = 1; i <= steps; i += 1) {
    pointer('pointermove', x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, opts);
    await sleep(6);
  }
  pointer('pointerup', x1, y1, opts);
  await sleep(opts.settle ?? 160);
}

export async function strokeInk(points, opts = {}) {
  pointer('pointerdown', points[0][0], points[0][1], { kind: 'pen', pressure: points[0][2] ?? 0.5, ...opts });
  for (const [x, y, p] of points.slice(1)) {
    pointer('pointermove', x, y, { kind: 'pen', pressure: p ?? 0.5, ...opts });
    await sleep(5);
  }
  const last = points[points.length - 1];
  pointer('pointerup', last[0], last[1], { kind: 'pen', ...opts });
  await sleep(opts.settle ?? 200);
}

export async function tool(name) {
  const button = document.querySelector(`[data-tool="${name}"]`);
  if (!button) throw new Error(`tool not found: ${name}`);
  button.click();
  await sleep(120);
}

export async function tab(panel) {
  const t = $$('.side.right .side-tab').find((x) => x.dataset.panel === panel);
  t.click();
  await sleep(150);
}

export async function key(k, opts = {}) {
  window.dispatchEvent(new KeyboardEvent('keydown', {
    key: k, bubbles: true, ctrlKey: !!opts.ctrl, shiftKey: !!opts.shift,
  }));
  await sleep(opts.settle ?? 150);
}

export async function menu(button, prefix, { settle = 400 } = {}) {
  document.querySelector('.menu')?.remove();
  $(button).click();
  await sleep(250);
  const item = $$('.menu button').find((b) => b.textContent.startsWith(prefix));
  if (!item) {
    const available = $$('.menu button').map((b) => b.textContent);
    document.querySelector('.menu')?.remove();
    throw new Error(`menu item not found: ${prefix}\navailable: ${available.join(' | ')}`);
  }
  const disabled = item.disabled;
  item.click();
  await sleep(settle);
  return { disabled };
}

export function dialog() {
  const d = $('.dialog');
  if (!d) return null;
  return {
    title: d.querySelector('h2')?.textContent,
    text: d.querySelector('p')?.textContent,
    warning: d.querySelector('.warn-box')?.textContent,
    fields: [...d.querySelectorAll('.dialog-form input, .dialog-form select')],
    buttons: [...d.querySelectorAll('.dialog-actions button')].map((b) => b.textContent),
  };
}

export function setField(index, value) {
  const el = $$('.dialog-form input, .dialog-form select')[index];
  if (!el) throw new Error(`dialog field ${index} not found`);
  if (el.type === 'checkbox') el.checked = value;
  else el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

export async function confirm(label, { settle = 1200 } = {}) {
  const button = $$('.dialog-actions button').find((b) => b.textContent === label);
  if (!button) {
    const available = $$('.dialog-actions button').map((b) => b.textContent);
    throw new Error(`dialog button not found: ${label} (have: ${available.join(', ')})`);
  }
  button.click();
  await sleep(settle);
}

export function dismiss() {
  document.querySelector('.dialog-backdrop')?.remove();
  document.querySelector('.menu')?.remove();
}

export async function selectText(pageIndex, from, to) {
  const spans = $$(`.page-wrap[data-page="${pageIndex}"] .text-layer span`);
  if (spans.length <= to) throw new Error(`only ${spans.length} spans on page ${pageIndex}`);
  const range = document.createRange();
  range.setStart(spans[from].firstChild, 0);
  range.setEnd(spans[to].firstChild, (spans[to].textContent || '').length);
  const sel = document.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  const text = sel.toString();
  $('#stage').dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  await sleep(200);
  return text;
}

export const toast = () => $('#toast').textContent;
export const status = () => $('#statusLeft').textContent;

export async function save(settle = 1800) {
  $('#btnSave').click();
  await sleep(settle);
  return toast();
}

/** Collect downloads instead of writing files, so exports can be asserted. */
export function captureDownloads() {
  const names = [];
  const real = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function patched() {
    if (this.download) names.push(this.download);
    else real.call(this);
  };
  return {
    names,
    restore() { HTMLAnchorElement.prototype.click = real; },
  };
}

const results = [];

export function check(condition, label, detail = '') {
  results.push({ ok: !!condition, label, detail });
  return !!condition;
}

export function report() {
  const failed = results.filter((r) => !r.ok);
  return {
    total: results.length,
    failed: failed.length,
    failures: failed.map((r) => `${r.label}${r.detail ? ` — ${r.detail}` : ''}`),
  };
}

export function reset() {
  results.length = 0;
}
