// The tabbed ribbon across the top: tabs of grouped commands, the layout
// people already know from office software.
//
// A tab is a list of groups; a group is a list of items. An item is a command
// id (small button), {big: id}, {col: [ids]} (up to three stacked),
// {grid: [ids]} (icon-only, three rows) or {custom: fn} for live controls.

import { iconSvg } from './icons.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class Ribbon {
  constructor(tabsEl, bodyEl, { tabs, commands, onFile }) {
    this.tabsEl = tabsEl;
    this.bodyEl = bodyEl;
    this.tabs = tabs;
    this.commands = commands;
    this.active = tabs[0].id;
    this._queued = false;

    const file = el('button', 'tab file', 'ファイル');
    file.addEventListener('click', () => onFile(file));
    tabsEl.append(file);
    for (const tab of tabs) {
      const button = el('button', 'tab', tab.label);
      button.dataset.tab = tab.id;
      button.setAttribute('role', 'tab');
      button.addEventListener('click', () => this.setTab(tab.id));
      tabsEl.append(button);
    }
    this.setTab(this.active);
  }

  setTab(id) {
    if (!this.tabs.some((tab) => tab.id === id)) return;
    this.active = id;
    for (const button of this.tabsEl.querySelectorAll('.tab[data-tab]')) {
      button.classList.toggle('active', button.dataset.tab === id);
    }
    this._render(true);
  }

  /** Bring buttons and controls up to date; coalesced to once per frame. */
  refresh() {
    if (this._queued) return;
    this._queued = true;
    requestAnimationFrame(() => { this._queued = false; this._render(false); });
  }

  _render(force) {
    // A control being typed into or dragged must not be replaced under the
    // user; it is refreshed when they let go of it.
    const active = document.activeElement;
    if (!force && active && this.bodyEl.contains(active)
      && active.matches('input:not([type=checkbox]):not([type=color]), textarea')) {
      if (!this._waiting) {
        this._waiting = true;
        active.addEventListener('blur', () => { this._waiting = false; this.refresh(); }, { once: true });
      }
      return;
    }
    const scroll = this.bodyEl.scrollLeft;
    this.bodyEl.textContent = '';
    const tab = this.tabs.find((candidate) => candidate.id === this.active);
    for (const group of tab.groups) {
      const body = el('div', 'rgroup-body');
      for (const item of group.items) {
        const node = this._item(item);
        if (node) body.append(node);
      }
      if (!body.childElementCount) continue;
      const wrap = el('div', 'rgroup');
      wrap.append(body, el('div', 'rgroup-label', group.label));
      this.bodyEl.append(wrap);
    }
    this.bodyEl.scrollLeft = scroll;
  }

  _item(item) {
    if (typeof item === 'string') return this._button(item, 'small');
    if (item.big) return this._button(item.big, 'big');
    if (item.col) {
      const col = el('div', 'rcol');
      for (const id of item.col) { const b = this._button(id, 'small'); if (b) col.append(b); }
      return col;
    }
    if (item.grid) {
      const grid = el('div', 'rgrid');
      for (const id of item.grid) { const b = this._button(id, 'icon'); if (b) grid.append(b); }
      return grid;
    }
    if (item.custom) return item.custom();
    return null;
  }

  _button(id, size) {
    const command = this.commands[id];
    if (!command) return null;
    if (command.visible && !command.visible()) return null;
    const button = el('button', `rbtn${size === 'big' ? ' big' : size === 'icon' ? ' iconly' : ''}`);
    button.innerHTML = iconSvg(command.icon || 'more', size === 'big' ? 28 : 17);
    if (size !== 'icon') {
      const label = el('span', 'lbl', (size === 'big' && command.short) || command.label);
      button.append(label);
    }
    const key = command.key ? `（${command.key}）` : '';
    button.title = `${command.title || command.label}${key}`;
    button.disabled = command.enabled ? !command.enabled() : false;
    if (command.active && command.active()) button.classList.add('active');
    if (command.menu) button.insertAdjacentHTML('beforeend', iconSvg('caret', 11));
    // Pressing a ribbon button must not take the caret out of an open text box.
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', (event) => command.run(event, button));
    return button;
  }
}
