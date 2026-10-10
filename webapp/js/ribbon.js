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
  constructor(tabsEl, bodyEl, { tabs, commands, onFile, collapsed = false, onCollapse = () => {} }) {
    this.collapsed = false;
    this.onCollapse = onCollapse;
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
      button.addEventListener('click', () => {
        // Folded away, a tab shows its buttons over the page until one is used.
        const reopen = this.collapsed && !(this.peeking && this.active === tab.id);
        if (this.collapsed && !reopen) { this._peek(false); return; }
        this.setTab(tab.id);
        if (reopen) this._peek(true);
      });
      button.addEventListener('dblclick', () => this.setCollapsed(!this.collapsed));
      tabsEl.append(button);
    }
    const fold = el('button', 'tab-fold');
    fold.addEventListener('click', () => this.setCollapsed(!this.collapsed));
    tabsEl.append(fold);
    this.foldEl = fold;
    // A peeked ribbon goes away once it has been used, or on a click elsewhere.
    document.addEventListener('pointerdown', (event) => {
      if (this.peeking && !bodyEl.contains(event.target) && !tabsEl.contains(event.target)
        && !event.target.closest?.('.menu, .popover, dialog')) this._peek(false);
    }, true);
    bodyEl.addEventListener('click', (event) => {
      if (this.peeking && event.target.closest('button') && !event.target.closest('[aria-haspopup]')) setTimeout(() => this._peek(false), 0);
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && this.peeking) this._peek(false); });
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
    this.setCollapsed(collapsed, true);
  }

  /** Fold the ribbon away to give the page more room (Ctrl+F1, or double-click a tab). */
  setCollapsed(collapsed, silent = false) {
    this.collapsed = !!collapsed;
    this._peek(false);
    this.bodyEl.classList.toggle('collapsed', this.collapsed);
    this.tabsEl.classList.toggle('folded', this.collapsed);
    this.foldEl.innerHTML = `<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${this.collapsed ? 'M3.5 6l4.5 4.5L12.500 6' : 'M3.5 10.500L8 6l4.500 4.500'}"/></svg>`;
    this.foldEl.title = this.collapsed ? 'リボンを常に表示（Ctrl+F1）' : 'リボンを折りたたんで紙面を広く（Ctrl+F1）';
    this.foldEl.setAttribute('aria-label', this.foldEl.title);
    if (!this.collapsed) this._hint();
    if (!silent) this.onCollapse(this.collapsed);
  }

  _peek(on) {
    this.peeking = !!on && this.collapsed;
    this.bodyEl.classList.toggle('peek', this.peeking);
    if (this.peeking) {
      this.bodyEl.style.top = `${this.tabsEl.getBoundingClientRect().bottom}px`;
      this._hint();
    } else {
      this.bodyEl.style.top = '';
    }
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
      if (command.locked) node.classList.toggle('locked', command.locked());
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
    // Rebuilding must not take the keyboard away from someone working the
    // controls with it (arrow keys in a drop-down): give the focus back to
    // the control in the same place.
    const FOCUSABLE = 'select, button, input, textarea';
    const active = document.activeElement;
    const at = active && custom.holder.contains(active)
      ? [...custom.holder.querySelectorAll(FOCUSABLE)].indexOf(active) : -1;
    custom.holder.textContent = '';
    const content = custom.item.custom();
    if (content) custom.holder.append(content);
    if (at >= 0) custom.holder.querySelectorAll(FOCUSABLE)[at]?.focus({ preventScroll: true });
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
    if (command.locked && command.locked()) button.classList.add('locked');
    if (command.menu) button.insertAdjacentHTML('beforeend', iconSvg('caret', 11));
    // Pressing a ribbon button must not take the caret out of an open text box.
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', (event) => command.run(event, button));
    if (command.dblrun) button.addEventListener('dblclick', (event) => command.dblrun(event, button));
    this._buttons.push({ node: button, command });
    return button;
  }
}
