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
    // A mouse wheel over the ribbon scrolls it sideways when it does not fit.
    bodyEl.addEventListener('wheel', (event) => {
      if (bodyEl.scrollWidth <= bodyEl.clientWidth || event.ctrlKey) return;
      event.preventDefault();
      bodyEl.scrollLeft += event.deltaY || event.deltaX;
    }, { passive: false });
    const hint = () => bodyEl.classList.toggle('more-right',
      bodyEl.scrollWidth - bodyEl.clientWidth - bodyEl.scrollLeft > 4);
    bodyEl.addEventListener('scroll', hint, { passive: true });
    window.addEventListener('resize', hint);
    this._hint = hint;
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
    // A full rebuild only when the tab changes. Otherwise buttons are updated
    // where they stand: replacing a button between the press and the release
    // of a click would swallow that click, and state changes (a save
    // finishing, the page scrolling) can arrive at any moment.
    if (!force && this._built === this.active) { this._update(); return; }
    const scroll = force && this._built !== this.active ? 0 : this.bodyEl.scrollLeft;
    this.bodyEl.textContent = '';
    this._buttons = [];
    this._customs = [];
    const tab = this.tabs.find((candidate) => candidate.id === this.active);
    for (const group of tab.groups) {
      const body = el('div', 'rgroup-body');
      for (const item of group.items) {
        const node = this._item(item);
        if (node) body.append(node);
      }
      const wrap = el('div', 'rgroup');
      wrap.append(body, el('div', 'rgroup-label', group.label));
      // A group whose only content is a live control that currently has
      // nothing to show stays in place, hidden, so it can come back.
      wrap.hidden = !body.childElementCount || [...body.children].every((child) => child.dataset.empty === '1');
      this.bodyEl.append(wrap);
    }
    this._built = this.active;
    this.bodyEl.scrollLeft = scroll;
    this._hint?.();
  }

  _update() {
    for (const { node, command } of this._buttons) {
      const disabled = command.enabled ? !command.enabled() : false;
      if (node.disabled !== disabled) node.disabled = disabled;
      node.classList.toggle('active', !!(command.active && command.active()));
    }
    const active = document.activeElement;
    for (const custom of this._customs) {
      const key = custom.item.key ? custom.item.key() : null;
      if (key !== null && key === custom.key) continue;
      // A control being typed into or dragged is left alone until it is let go of.
      if (active && custom.holder.contains(active)
        && active.matches('input:not([type=checkbox]):not([type=color]), textarea')) {
        if (!custom.waiting) {
          custom.waiting = true;
          active.addEventListener('blur', () => { custom.waiting = false; this.refresh(); }, { once: true });
        }
        continue;
      }
      this._fill(custom, key);
    }
    this._hint?.();
  }

  _fill(custom, key) {
    custom.key = key;
    custom.holder.textContent = '';
    const content = custom.item.custom();
    if (content) custom.holder.append(content);
    custom.holder.dataset.empty = content ? '0' : '1';
    const group = custom.holder.closest('.rgroup');
    if (group) {
      const body = group.querySelector('.rgroup-body');
      group.hidden = [...body.children].every((child) => child.dataset.empty === '1');
    }
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
    if (item.custom) {
      const holder = el('div', 'rcustom');
      const custom = { holder, item, key: undefined, waiting: false };
      this._customs.push(custom);
      this._fill(custom, item.key ? item.key() : null);
      return holder;
    }
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
    this._buttons.push({ node: button, command });
    return button;
  }
}
