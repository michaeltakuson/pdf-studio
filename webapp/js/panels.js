// The format controls in the ribbon, and the side panels: properties,
// comments, quantities, settings, page thumbnails and bookmarks.
//
// One set of format controls serves both "settings for the next thing drawn"
// and "settings for what is selected": change a value with nothing selected
// and it becomes the default, change it with a selection and it edits that.

import { SWATCHES, styleFor } from './defaults.js';
import { FONTS, familyKey, canBold } from './textedit.js';
import { iconSvg } from './icons.js';
import { openPopover, isComposing } from './dialogs.js';

const LINE_END_LABELS = {
  none: 'なし', openArrow: '矢印（開）', closedArrow: '矢印（塗り）',
  rOpenArrow: '逆矢印（開）', rClosedArrow: '逆矢印（塗り）',
  square: '四角', circle: '丸', diamond: 'ひし形', slash: 'スラッシュ', butt: '縦線',
};

const NOTE_ICONS = {
  Comment: 'コメント', Key: '鍵', Note: 'ノート', Help: 'ヘルプ',
  NewParagraph: '新規段落', Paragraph: '段落', Insert: '挿入',
};

const TYPE_LABELS = {
  highlight: 'マーカー', underline: '下線', squiggly: '波線', strikeout: '取り消し線',
  areaHighlight: '範囲マーカー', freetext: 'テキスト', note: '付箋', line: '線',
  square: '四角形', circle: '円', polygon: '多角形', polyline: '折れ線',
  ink: '手書き', stamp: 'スタンプ', redact: '墨消し', caret: '挿入記号', image: '画像',
};

export function typeLabel(annot) {
  if (annot.type === 'ink' && annot.tool === 'mark') return 'チェック';
  if (annot.type === 'line' && (annot.style?.lineEnds || []).some((e) => e && e !== 'none')) return '矢印';
  if (annot.type === 'freetext' && annot.callout) return '引き出し線';
  return TYPE_LABELS[annot.type] || annot.type;
}

const TEXT_TOOLS = ['freetext', 'callout'];

const FIELDS = {
  stroke: { tools: '*', except: [...TEXT_TOOLS, 'image', 'redact'] },
  fill: { tools: ['square', 'circle', 'polygon', 'areaHighlight', 'redact', 'measureArea', 'count'] },
  width: { tools: ['pen', 'marker', 'line', 'arrow', 'square', 'circle', 'polygon', 'polyline', 'underline', 'squiggly', 'strikeout', 'measureDistance', 'measureArea', 'measureAngle', 'mark'] },
  opacity: { tools: '*', except: ['image', 'redact', 'note', 'stamp'] },
  dash: { tools: ['line', 'arrow', 'square', 'circle', 'polygon', 'polyline'] },
  cloud: { tools: ['square', 'polygon'] },
  lineEnds: { tools: ['line', 'arrow'] },
  icon: { tools: ['note'] },
  stamp: { tools: ['stamp'] },
  mark: { tools: ['mark'] },
};

// Wording fixed by the spec, with a gloss so it is clear what will appear.
const STANDARD_STAMPS = [
  'APPROVED（承認済）', 'AS IS（現状のまま）', 'CONFIDENTIAL（社外秘）',
  'DEPARTMENTAL（部門用）', 'EXPERIMENTAL（試験的）', 'EXPIRED（期限切れ）',
  'FINAL（最終版）', 'FOR COMMENT（コメント用）', 'FOR PUBLIC RELEASE（公開可）',
  'NOT APPROVED（未承認）', 'NOT FOR PUBLIC RELEASE（公開不可）', 'SOLD（売却済）',
  'TOP SECRET（最高機密）', 'DRAFT（ドラフト）',
];

const FONT_SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72];

function applies(field, tool) {
  const spec = FIELDS[field];
  if (!spec) return false;
  if (spec.except && spec.except.includes(tool)) return false;
  return spec.tools === '*' || spec.tools.includes(tool);
}

export function h(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    // `value` and `checked` must be set as properties — setAttribute does not
    // fill a <textarea> and does not update a live checkbox.
    else if (key === 'value' || key === 'checked' || key === 'disabled') node[key] = value;
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

/**
 * Re-render a panel — unless the user is typing in it.
 *
 * Rebuilding a panel replaces its inputs, and replacing an input in the
 * middle of a kana-kanji conversion throws the conversion away: typing 「ら」
 * came out as 「rあ」. So a panel with a focused text field is left exactly as
 * it is and brought up to date once the field loses focus.
 */
export function refreshPanel(container, render) {
  const active = document.activeElement;
  const typing = active && container.contains(active)
    && (active.tagName === 'TEXTAREA' || (active.tagName === 'INPUT' && !['checkbox', 'radio', 'range', 'color', 'button'].includes(active.type)));
  if (typing) {
    container._pending = render;
    if (!container._waiting) {
      container._waiting = true;
      container.addEventListener('focusout', () => {
        container._waiting = false;
        // Wait a tick: focus may be moving to another field in the same panel.
        setTimeout(() => {
          const next = container._pending;
          container._pending = null;
          if (next) refreshPanel(container, next);
        }, 0);
      }, { once: true });
    }
    return;
  }
  const scroll = container.scrollTop;
  render();
  container.scrollTop = scroll;
}

function select(options, value, onChange, props = {}) {
  const node = h('select', { class: 'select', ...props, onchange: (e) => onChange(e.target.value) });
  for (const [key, label] of Object.entries(options)) {
    node.append(h('option', { value: key, text: label }));
  }
  node.value = String(value);
  return node;
}

/** Keep focus where it is (the open text box) when a ribbon button is pressed. */
const keepFocus = { onmousedown: (e) => e.preventDefault() };

function iconButton(icon, title, onClick, { active = false, disabled = false } = {}) {
  return h('button', {
    class: `rbtn iconly${active ? ' active' : ''}`, title, disabled, html: iconSvg(icon, 17),
    ...keepFocus, onclick: onClick,
  });
}

/** A button that shows the current colour and opens a palette. */
function colorButton(icon, title, current, onPick, { allowNone = false } = {}) {
  const bar = h('span', { class: `bar${current ? '' : ' none'}`, style: current ? `background:${current}` : '' });
  const button = h('button', { class: 'rbtn colorbtn', title, ...keepFocus, html: iconSvg(icon, 16) }, bar);
  const show = (colour) => {
    bar.className = `bar${colour ? '' : ' none'}`;
    bar.style.background = colour || '';
  };
  button.addEventListener('click', () => {
    openPopover(button, (close) => {
      const grid = h('div', { class: 'palette' });
      for (const colour of SWATCHES) {
        grid.append(h('button', {
          class: `swatch${colour.toLowerCase() === (current || '').toLowerCase() ? ' active' : ''}`,
          style: `background:${colour}`, title: colour, ...keepFocus,
          onclick: () => { current = colour; show(colour); onPick(colour); close(); },
        }));
      }
      const foot = h('div', { class: 'palette-foot' }, [
        h('label', { text: 'その他の色' }),
        h('input', {
          type: 'color', value: current || '#000000',
          oninput: (e) => { current = e.target.value; show(current); onPick(current); },
        }),
        allowNone ? h('button', {
          class: 'btn small', text: 'なし', ...keepFocus,
          onclick: () => { current = null; show(null); onPick(null); close(); },
        }) : null,
      ]);
      return h('div', {}, [grid, foot]);
    });
  });
  return button;
}

function rfield(label, control) {
  return h('div', { class: 'rfield' }, [label ? h('label', { text: label }) : null, control]);
}

export function toolOf(annot) {
  if (annot.type === 'ink') return annot.tool === 'mark' ? 'mark' : (annot.tool === 'marker' ? 'marker' : 'pen');
  if (annot.type === 'freetext') return annot.callout ? 'callout' : 'freetext';
  if (annot.type === 'line') return 'line';
  return annot.type;
}

const STROKE_LABEL = {
  highlight: 'マーカーの色', marker: 'マーカーの色', areaHighlight: 'マーカーの色', note: '付箋の色',
};

/**
 * The font group of the ribbon. Returns the element, or null when what is
 * selected (or about to be drawn) has no text.
 */
export function renderFontGroup({ tool, selection, onChange }) {
  const target = selection.length ? selection[0] : null;
  const effective = target ? toolOf(target) : tool;
  const isStampText = target?.tool === 'stamp' || (!target && tool === 'stamp' && (styleFor('stamp').stampIndex ?? 0) < 0);
  if (!TEXT_TOOLS.includes(effective) && !isStampText) return null;
  const base = styleFor(target ? effective : tool);
  const style = target ? { ...base, ...(target.style || {}), font: { ...base.font, ...(target.style?.font || {}) } } : base;
  const font = style.font || {};
  const family = familyKey(font);

  const sizes = [...FONT_SIZES];
  const size = Number(font.size) || 12;
  if (!sizes.includes(size)) { sizes.push(size); sizes.sort((a, b) => a - b); }

  const row1 = h('div', { class: 'rrow' }, [
    select(Object.fromEntries(Object.entries(FONTS).map(([key, spec]) => [key, spec.label])), family,
      (value) => onChange({ font: { family: value, bold: font.bold && !!FONTS[value].files[700] } }),
      { title: 'フォント', style: `width:104px;height:23px;padding:1px 4px;font-family:"${FONTS[family].css}"` }),
    select(Object.fromEntries(sizes.map((s) => [s, String(s)])), size,
      (value) => onChange({ font: { size: Number(value) } }),
      { title: '文字サイズ', style: 'width:58px;height:23px;padding:1px 4px' }),
  ]);
  const align = font.align || 'left';
  const row2 = h('div', { class: 'rrow' }, [
    iconButton('bold', canBold(font) ? '太字' : 'このフォントには太字がありません',
      () => onChange({ font: { bold: !font.bold } }), { active: !!font.bold && canBold(font), disabled: !canBold(font) }),
    colorButton('fontcolor', '文字の色', font.color || '#000000',
      (value) => onChange({ font: { color: value }, stroke: value })),
    h('span', { class: 'rsep' }),
    iconButton('alignleft', '左揃え', () => onChange({ font: { align: 'left' } }), { active: align === 'left' }),
    iconButton('aligncenter', '中央揃え', () => onChange({ font: { align: 'center' } }), { active: align === 'center' }),
    iconButton('alignright', '右揃え', () => onChange({ font: { align: 'right' } }), { active: align === 'right' }),
  ]);
  const row3 = h('div', { class: 'rrow' }, [
    colorButton('fill', '背景の色', style.fill || null, (value) => onChange({ fill: value }), { allowNone: true }),
    rfield('枠線', select({ 0: 'なし', 0.5: '細い', 1: '標準', 2: '太い', 3: '極太' },
      [0, 0.5, 1, 2, 3].includes(Number(style.width)) ? Number(style.width) : 1,
      (value) => onChange({ width: Number(value) }), { title: '枠線の太さ（枠線は文字と同じ色になります）' })),
  ]);
  return h('div', { class: 'rcol' }, [row1, row2, row3]);
}

/**
 * The shape/pen format group of the ribbon. Returns the element, or null
 * when there is nothing to set.
 */
export function renderStyleGroup({ tool, selection, onChange, onExtra, onCommit = () => {}, markKind, onMarkKind }) {
  const target = selection.length ? selection[0] : null;
  const effective = target ? toolOf(target) : tool;
  const NO_SETTINGS = ['select', 'pan', 'eraser', 'lasso', 'calibrate', 'edittext', 'image'];
  if (NO_SETTINGS.includes(effective) || TEXT_TOOLS.includes(effective)) return null;
  if (target?.tool === 'stamp' && target.type === 'freetext') return null;
  const style = target ? { ...styleFor(effective), ...(target.style || {}) } : styleFor(effective);

  const col1 = h('div', { class: 'rcol' });
  const col2 = h('div', { class: 'rcol' });
  const row = (...items) => h('div', { class: 'rrow' }, items);

  const first = [];
  if (applies('stroke', effective)) {
    first.push(colorButton('stroke', STROKE_LABEL[effective] || '線の色', style.stroke, (value) => onChange({ stroke: value })));
  }
  if (applies('fill', effective)) {
    first.push(colorButton('fill', '塗りつぶしの色', style.fill || null,
      (value) => onChange({ fill: value }), { allowNone: effective !== 'redact' }));
  }
  if (applies('width', effective)) {
    first.push(rfield('太さ', h('input', {
      type: 'number', class: 'input num', min: '0.25', max: '40', step: '0.5',
      value: String(style.width ?? 1.5),
      oninput: (e) => { if (Number(e.target.value) > 0) onChange({ width: Number(e.target.value) }, 'width'); },
      onchange: onCommit,
    })));
  }
  if (first.length) col1.append(row(...first));

  if (applies('opacity', effective)) {
    const out = h('span', { class: 'muted', text: `${Math.round((style.opacity ?? 1) * 100)}%` });
    col1.append(rfield('濃さ', h('div', { class: 'rrow' }, [
      h('input', {
        type: 'range', min: '5', max: '100', value: String(Math.round((style.opacity ?? 1) * 100)),
        // Scrubbing is one undo step; releasing the slider closes it.
        oninput: (e) => {
          out.textContent = `${e.target.value}%`;
          onChange({ opacity: Number(e.target.value) / 100 }, 'opacity');
        },
        onchange: onCommit,
      }),
      out,
    ])));
  }
  if (applies('dash', effective)) {
    col1.append(rfield('線種', select(
      { solid: '実線', dashed: '破線' },
      style.borderStyle === 'dashed' || (style.dash || []).length ? 'dashed' : 'solid',
      (value) => onChange({ borderStyle: value, dash: value === 'dashed' ? [4, 3] : [] }),
    )));
  }

  if (applies('lineEnds', effective)) {
    const ends = style.lineEnds || ['none', 'none'];
    col2.append(rfield('始点', select(LINE_END_LABELS, ends[0], (value) => onChange({ lineEnds: [value, ends[1]] }))));
    col2.append(rfield('終点', select(LINE_END_LABELS, ends[1], (value) => onChange({ lineEnds: [ends[0], value] }))));
  }
  if (applies('cloud', effective)) {
    col2.append(rfield('雲形', select({ 0: 'なし', 1: '弱', 2: '中', 3: '強' },
      String(style.cloudIntensity || 0), (value) => onChange({ cloudIntensity: Number(value) }))));
  }
  if (applies('icon', effective) && target) {
    col2.append(rfield('アイコン', select(NOTE_ICONS, target.icon || 'Comment', (value) => onExtra({ icon: value }))));
  }
  if (applies('mark', effective) && !target) {
    col2.append(row(
      ...[['check', 'チェック'], ['cross', 'バツ'], ['ring', '丸'], ['dot', '黒丸']].map(([kind, label]) => (
        iconButton(kind, label, () => onMarkKind(kind), { active: markKind === kind })
      )),
    ));
    col2.append(rfield('大きさ', select({ 10: '小', 14: '標準', 20: '大', 28: '特大' },
      String(style.markSize || 14), (value) => onChange({ markSize: Number(value) }))));
  }
  if (applies('stamp', effective)) {
    const index = target ? (target.stampIndex ?? 0) : (style.stampIndex ?? 0);
    const options = { ...Object.fromEntries(STANDARD_STAMPS.map((s, i) => [i, s])), '-1': '自由な文言…' };
    col2.append(rfield('種類', select(options, String(index), (value) => {
      const next = Number(value);
      if (target) onExtra({ stampIndex: next });
      else onChange({ stampIndex: next });
    }, { style: 'width:170px' })));
    if (index < 0 && !target) {
      col2.append(rfield('文言', h('input', {
        class: 'input', style: 'width:150px', value: style.stampText || '確認済',
        title: '{name} {date} {time} は押した瞬間の値に置き換わります',
        oninput: (e) => onChange({ stampText: e.target.value }, 'stampText'),
      })));
    }
  }
  if (!col1.childElementCount && !col2.childElementCount) return null;
  return h('div', { class: 'rrow', style: 'align-items:flex-start;gap:10px' }, [
    col1.childElementCount ? col1 : null, col2.childElementCount ? col2 : null,
  ]);
}

// ---------------------------------------------------------------- properties

const STATE_LABELS = { accepted: '承諾', rejected: '却下', completed: '完了', cancelled: '取り消し' };
const STATE_CHOICES = { null: '未設定', ...STATE_LABELS };

export function renderProps(container, { selection, onPatch }) {
  container.textContent = '';
  if (!selection.length) {
    container.append(h('div', { class: 'prop-empty', text: 'ページ上の書き込みを選ぶと、ここで作成者・状態・ロックなどを設定できます。\n\n色や太さは上のリボンの「書式」で、文字はページ上で直接クリックして編集します。', style: 'white-space:pre-wrap' }));
    return;
  }
  const single = selection.length === 1 ? selection[0] : null;

  if (single) {
    const section = h('div', { class: 'prop-section' }, h('h3', { text: `${typeLabel(single)}（${single.page + 1} ページ）` }));
    const textField = (key, props) => h(props.tag || 'input', {
      class: 'input', ...props.attrs,
      value: single[key] || '',
      oninput: (e) => onPatch({ [key]: e.target.value }, key),
    });
    if (single.type !== 'freetext') {
      section.append(h('div', { class: 'prop-row' },
        textField('contents', { tag: 'textarea', attrs: { placeholder: 'コメント（この書き込みへのメモ）' } })));
    }
    section.append(h('div', { class: 'prop-row' }, [h('label', { text: '作成者' }), textField('author', {})]));
    section.append(h('div', { class: 'prop-row' }, [h('label', { text: '件名' }), textField('subject', {})]));
    container.append(section);
  } else {
    container.append(h('div', { class: 'prop-section muted', text: `${selection.length} 件を選択中。下の変更はすべてに適用されます。` }));
  }

  const status = h('div', { class: 'prop-section' }, h('h3', { text: 'レビュー' }));
  status.append(h('div', { class: 'prop-row' }, [
    h('label', { text: '状態' }),
    select(STATE_CHOICES, String(single?.state ?? 'null'),
      (value) => onPatch({ state: value === 'null' ? null : value })),
  ]));
  status.append(h('div', { class: 'prop-row' }, [
    h('label', { text: 'チェック済' }),
    h('input', {
      type: 'checkbox', checked: !!single?.checked,
      onchange: (e) => onPatch({ checked: e.target.checked }),
    }),
  ]));
  container.append(status);

  const behaviour = h('div', { class: 'prop-section' }, h('h3', { text: '動作' }));
  const flags = single?.flags || {};
  const labels = { print: '印刷する', locked: 'ロック（動かせなくする）', readOnly: '編集不可', hidden: '非表示' };
  for (const [key, label] of Object.entries(labels)) {
    behaviour.append(h('div', { class: 'prop-row' }, [
      h('label', { text: label }),
      h('input', {
        type: 'checkbox', checked: key === 'print' ? flags[key] !== false : !!flags[key],
        onchange: (e) => onPatch({ flags: { [key]: e.target.checked } }),
      }),
    ]));
  }
  container.append(behaviour);
}

// ---------------------------------------------------------------- comments

const SORTS = { page: 'ページ順', author: '作成者', type: '種類', state: 'ステータス', created: '作成日時' };

function sorted(items, mode) {
  const list = [...items];
  const byPage = (a, b) => a.page - b.page || (a.rect?.[1] ?? 0) - (b.rect?.[1] ?? 0);
  const compare = {
    page: byPage,
    author: (a, b) => (a.author || '').localeCompare(b.author || '', 'ja') || byPage(a, b),
    type: (a, b) => typeLabel(a).localeCompare(typeLabel(b), 'ja') || byPage(a, b),
    state: (a, b) => (a.state || '').localeCompare(b.state || '') || byPage(a, b),
    created: (a, b) => String(a.created || '').localeCompare(String(b.created || '')) || byPage(a, b),
  }[mode] || byPage;
  return list.sort(compare);
}

function summaryOf(annot) {
  if (annot.type === 'freetext') return annot.text || '（空のテキスト）';
  if (annot.contents) return annot.contents;
  if (annot.subject) return `「${annot.subject}」`;
  return '（コメントなし）';
}

export function renderComments(container, {
  annots, selection, filters, onSelect, onFilter, onPatch, onReply, onBulk,
}) {
  container.textContent = '';

  container.append(h('div', { class: 'comment-filters' }, [
    h('input', {
      class: 'input', type: 'search', placeholder: '書き込みを検索', value: filters.query || '',
      oninput: (e) => onFilter({ query: e.target.value }),
    }),
  ]));

  const authors = [...new Set(annots.map((a) => a.author).filter(Boolean))];
  const kinds = [...new Set(annots.map((a) => a.type))];
  container.append(h('div', { class: 'comment-filters' }, [
    select({ all: '全種類', ...Object.fromEntries(kinds.map((k) => [k, TYPE_LABELS[k] || k])) },
      kinds.includes(filters.type) ? filters.type : 'all', (value) => onFilter({ type: value })),
    select({ all: '全ステータス', none: '未設定', ...STATE_LABELS },
      filters.state || 'all', (value) => onFilter({ state: value })),
  ]));
  container.append(h('div', { class: 'comment-filters' }, [
    select({ all: 'すべて', unchecked: '未チェック', checked: 'チェック済' },
      filters.checked || 'all', (value) => onFilter({ checked: value })),
    authors.length > 1
      ? select({ all: '全作成者', ...Object.fromEntries(authors.map((a) => [a, a])) },
        filters.author || 'all', (value) => onFilter({ author: value }))
      : select(SORTS, filters.sort || 'page', (value) => onFilter({ sort: value })),
  ]));

  const visible = sorted(annots.filter((a) => matches(a, filters, kinds)), filters.sort);

  container.append(h('div', { class: 'comment-summary' }, [
    h('span', { class: 'muted', text: `${visible.length} / ${annots.length} 件` }),
    h('span', { class: 'spacer' }),
    h('button', {
      class: 'btn small', text: '表示中をすべて選択',
      title: '表示中の書き込みをすべて選択して、まとめて書式やステータスを変更します',
      onclick: () => onBulk('select', visible),
    }),
  ]));

  if (!visible.length) {
    container.append(h('div', { class: 'prop-empty', text: annots.length ? '条件に合う書き込みがありません。' : 'まだ書き込みがありません。マーカーや付箋、テキストを追加すると、ここに一覧が出ます。' }));
    return;
  }

  const selectedIds = new Set(selection.map((a) => a.id));
  // A long list is built in slices so opening the panel on a document with
  // thousands of marks does not freeze the page.
  const LIMIT = 400;
  for (const annot of visible.slice(0, LIMIT)) {
    const isSelected = selectedIds.has(annot.id);
    const head = h('div', { class: 'comment-head' }, [
      h('span', { class: 'comment-dot', style: `background:${annot.style?.font?.color && annot.type === 'freetext' ? annot.style.font.color : (annot.style?.stroke || '#888')}` }),
      h('span', { text: typeLabel(annot) }),
      h('span', { text: `p.${annot.page + 1}` }),
      annot.author ? h('span', { text: annot.author }) : null,
      annot.state ? h('span', { class: `state-chip ${annot.state}`, text: STATE_LABELS[annot.state] }) : null,
      h('span', { class: 'spacer' }),
      h('input', {
        type: 'checkbox', title: 'チェック済にする', checked: !!annot.checked,
        onclick: (e) => { e.stopPropagation(); onPatch(annot.id, { checked: e.target.checked }); },
      }),
    ]);

    const item = h('div', {
      class: `comment-item${isSelected ? ' selected' : ''}`,
      onclick: () => onSelect(annot),
    }, [head, h('div', { class: 'comment-body', text: summaryOf(annot) })]);

    for (const reply of annot.replies || []) {
      item.append(h('div', { class: 'comment-reply' }, [
        h('div', { class: 'comment-head' }, [h('span', { text: reply.author || '返信' })]),
        h('div', { text: reply.contents || '' }),
      ]));
    }

    if (isSelected) {
      item.append(h('div', { class: 'comment-actions', onclick: (e) => e.stopPropagation() }, [
        select(STATE_CHOICES, String(annot.state ?? 'null'),
          (value) => onPatch(annot.id, { state: value === 'null' ? null : value })),
      ]));
      item.append(h('div', { class: 'comment-actions', onclick: (e) => e.stopPropagation() }, [
        h('input', {
          class: 'input', placeholder: '返信を書いて Enter',
          onkeydown: (e) => {
            // Enter that confirms a kana-kanji conversion is not "send".
            if (e.key !== 'Enter' || isComposing(e) || !e.target.value.trim()) return;
            e.preventDefault();
            const text = e.target.value.trim();
            e.target.value = '';
            e.target.blur();
            onReply(annot.id, text);
          },
        }),
      ]));
    }
    container.append(item);
  }
  if (visible.length > LIMIT) {
    container.append(h('div', { class: 'prop-empty', text: `ほか ${visible.length - LIMIT} 件。検索や種類で絞り込んでください。` }));
  }
}

function matches(annot, filters, kinds) {
  if (filters.checked === 'checked' && !annot.checked) return false;
  if (filters.checked === 'unchecked' && annot.checked) return false;
  if (filters.state === 'none' && annot.state) return false;
  if (filters.state && !['all', 'none'].includes(filters.state) && annot.state !== filters.state) return false;
  if (filters.type && filters.type !== 'all' && kinds.includes(filters.type) && annot.type !== filters.type) return false;
  if (filters.author && filters.author !== 'all' && annot.author !== filters.author) return false;
  if (filters.query) {
    const haystack = `${annot.text || ''} ${annot.contents || ''} ${annot.author || ''} ${annot.subject || ''}`.toLowerCase();
    if (!haystack.includes(filters.query.toLowerCase())) return false;
  }
  return true;
}

// ---------------------------------------------------------------- take-off

/**
 * The quantity table, plus the scale everything depends on. Nothing here means
 * anything until the scale is calibrated, so that comes first and says so.
 */
export function renderTakeoff(container, {
  rows, scale, calibrated, unitLabels, onCalibrate, onUnit, onSubject,
  subject, onExportCsv, onLegend,
}) {
  container.textContent = '';

  const scaleBox = h('div', { class: 'prop-section' }, h('h3', { text: '縮尺' }));
  if (!calibrated) {
    scaleBox.append(h('div', { class: 'warn-inline', text: '縮尺が未設定です。図面上の長さが分かっている2点をなぞって設定してください。設定するまで計測値は実寸になりません。' }));
  } else {
    scaleBox.append(h('div', { class: 'muted', text: `1 pt = ${(scale.realLength / scale.pagePoints).toPrecision(4)} ${scale.unit}` }));
  }
  scaleBox.append(h('div', { class: 'prop-row' }, [
    h('label', { text: '単位' }),
    select(unitLabels, scale.unit, onUnit),
  ]));
  scaleBox.append(h('div', { class: 'prop-row' }, h('button', {
    class: 'btn primary', text: calibrated ? '縮尺を測り直す' : '縮尺を設定する',
    onclick: onCalibrate,
  })));
  container.append(scaleBox);

  const group = h('div', { class: 'prop-section' }, h('h3', { text: '分類' }));
  group.append(h('div', { class: 'prop-row' }, [
    h('label', { text: '記録先' }),
    h('input', {
      class: 'input', value: subject || '',
      placeholder: '例: 床面積 / 配管長 / コンセント',
      oninput: (e) => onSubject(e.target.value),
    }),
  ]));
  group.append(h('div', { class: 'muted', text: 'ここに入れた名前ごとに集計されます。凡例の見出しにもなります。' }));
  container.append(group);

  const table = h('div', { class: 'prop-section' }, h('h3', { text: '集計' }));
  if (!rows.length) {
    table.append(h('div', { class: 'prop-empty', text: '「ツール」タブの距離・面積・カウントを使うと、ここに集計が出ます。' }));
  } else {
    for (const row of rows) {
      table.append(h('div', { class: 'takeoff-row' }, [
        h('span', { class: 'comment-dot', style: `background:${row.colour}` }),
        h('span', { class: 'takeoff-label', text: row.label }),
        h('span', { class: 'takeoff-count', text: `${row.count} 件` }),
        h('span', { class: 'takeoff-total', text: row.summable ? `${row.total} ${row.unit}` : '' }),
      ]));
      table.append(h('div', { class: 'takeoff-pages muted', text: `p. ${row.pages.join(', ')}` }));
    }
    table.append(h('div', { class: 'prop-row', style: 'margin-top:10px;gap:6px' }, [
      h('button', { class: 'btn small', text: 'CSVで書き出す', onclick: onExportCsv }),
      h('button', { class: 'btn small', text: '凡例をページに置く', onclick: onLegend }),
    ]));
  }
  container.append(table);
}

// ---------------------------------------------------------------- settings

export const SHORTCUTS = {
  'Ctrl+O': '開く', 'Ctrl+S': '上書き保存', 'Ctrl+Shift+S': '名前を付けて保存', 'Ctrl+P': '印刷',
  'Ctrl+F': '検索', 'Ctrl+Z': '元に戻す', 'Ctrl+Y': 'やり直し',
  'Ctrl+C / X / V': '書き込みのコピー・切り取り・貼り付け', 'Ctrl+D': '複製',
  'Ctrl+A': 'このページの書き込みを全選択', 'Delete': '選択した書き込みを削除',
  '矢印キー': '選択した書き込みを少し動かす（Shift で大きく）',
  'Esc': '入力を確定／作業中の操作を取り消し／選択ツールに戻る',
  'Enter': '多角形・計測を確定',
  'F5': 'スライドショー（全画面で1ページずつ）',
  'Ctrl+ホイール': '拡大・縮小', 'Ctrl+0 / + / −': '幅に合わせる・拡大・縮小',
  'PageUp / PageDown': '前後のページ', 'Home / End': '先頭・末尾のページ',
};

const PEN_PREFS = {
  pressure: ['筆圧を線の太さに反映', 'ペンの筆圧（マウスでは描く速さ）で線の太さが変わります'],
  penOnly: ['ペン専用モード', 'ペン以外（指・マウス）では描かなくなります。指はスクロールになります'],
};

export function renderSettings(container, { getPref, setPref, onChange }) {
  container.textContent = '';

  const identity = h('div', { class: 'prop-section' }, h('h3', { text: '作成者名' }));
  identity.append(h('div', { class: 'prop-row' }, [
    h('label', { text: '名前' }),
    h('input', {
      class: 'input', placeholder: '書き込みに記録される名前',
      value: getPref('author') || '',
      oninput: (e) => { setPref('author', e.target.value); onChange(); },
    }),
  ]));
  identity.append(h('div', { class: 'muted', text: 'これ以降に作る書き込みの作成者として記録され、スタンプの {name} にも使われます。' }));
  container.append(identity);

  const pen = h('div', { class: 'prop-section' }, h('h3', { text: 'ペン・タッチ' }));
  for (const [key, [label, hint]] of Object.entries(PEN_PREFS)) {
    pen.append(h('div', { class: 'prop-row', title: hint }, [
      h('label', { text: label }),
      h('input', {
        type: 'checkbox', checked: !!getPref(key),
        onchange: (e) => { setPref(key, e.target.checked); onChange(); },
      }),
    ]));
  }
  pen.append(h('div', { class: 'muted', text: 'ペンを一度使うと、それ以降は手のひらが触れても線になりません。描いている途中でも二本指でスクロールできます。' }));
  container.append(pen);

  const keys = h('div', { class: 'prop-section' }, h('h3', { text: 'キーボード' }));
  for (const [combo, label] of Object.entries(SHORTCUTS)) {
    keys.append(h('div', { class: 'shortcut-row' }, [h('kbd', { text: combo }), h('span', { text: label })]));
  }
  container.append(keys);

  const storage = h('div', { class: 'prop-section' }, h('h3', { text: '書式の既定値' }));
  storage.append(h('div', { class: 'muted', text: '直前に使った色・太さ・フォントが、次に描くときの既定になります。' }));
  storage.append(h('div', { class: 'prop-row' }, h('button', {
    class: 'btn', text: '既定値を最初の状態に戻す',
    onclick: () => {
      try { localStorage.removeItem('pdfstudio.defaults.v2'); } catch { /* ignore */ }
      onChange('reset-defaults');
    },
  })));
  container.append(storage);
}

// ---------------------------------------------------------------- thumbnails

let thumbObserver = null;

/**
 * Page thumbnails. They are drawn only as they scroll into view — drawing
 * all of them up front made a long document slow to open.
 */
export function renderThumbs(container, viewer, { current, selected }, handlers) {
  thumbObserver?.disconnect();
  container.textContent = '';
  if (!viewer.pageViews.length) return;
  container.append(h('div', { class: 'thumb-hint', text: 'ドラッグで並べ替え、右クリックでページ操作。Ctrl / Shift で複数選択。' }));

  thumbObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const canvas = entry.target.querySelector('canvas');
      thumbObserver.unobserve(entry.target);
      if (!canvas || canvas.dataset.done) continue;
      canvas.dataset.done = '1';
      const view = viewer.pageViews[Number(entry.target.dataset.page)];
      if (!view) continue;
      const viewport = view.page.getViewport({ scale: (132 / view.width) * Math.min(window.devicePixelRatio || 1, 2) });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      view.page.render({ canvasContext: canvas.getContext('2d'), viewport, canvas, annotationMode: 1 })
        .promise.catch(() => {});
    }
  }, { root: container.closest('.side-body'), rootMargin: '300px 0px' });

  for (const view of viewer.pageViews) {
    const height = Math.round((132 / view.width) * view.height);
    const canvas = h('canvas', { class: 'thumb', style: `width:132px;height:${height}px` });
    const item = h('div', {
      class: 'thumb-item', 'data-page': String(view.index), draggable: 'true', title: `${view.index + 1} ページ`,
      onclick: (e) => handlers.onClick(view.index, e),
      oncontextmenu: (e) => { e.preventDefault(); handlers.onContext(view.index, e); },
      ondragstart: (e) => {
        handlers.onDragStart(view.index);
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/x-pdfstudio-page', String(view.index));
      },
      ondragover: (e) => {
        if (![...e.dataTransfer.types].includes('text/x-pdfstudio-page')) return;
        e.preventDefault();
        const box = item.getBoundingClientRect();
        const before = e.clientY < box.top + box.height / 2;
        item.classList.toggle('drop-before', before);
        item.classList.toggle('drop-after', !before);
      },
      ondragleave: () => item.classList.remove('drop-before', 'drop-after'),
      ondrop: (e) => {
        if (![...e.dataTransfer.types].includes('text/x-pdfstudio-page')) return;
        e.preventDefault();
        e.stopPropagation();
        const before = item.classList.contains('drop-before');
        item.classList.remove('drop-before', 'drop-after');
        handlers.onDrop(before ? view.index : view.index + 1);
      },
    }, [canvas, h('div', { class: 'thumb-label', text: String(view.index + 1) })]);
    container.append(item);
    thumbObserver.observe(item);
  }
  updateThumbs(container, { current, selected });
}

export function updateThumbs(container, { current, selected }) {
  for (const item of container.querySelectorAll('.thumb-item')) {
    const index = Number(item.dataset.page);
    item.classList.toggle('current', index === current);
    item.classList.toggle('selected', selected.has(index));
  }
}

export function scrollThumbIntoView(container, index) {
  const item = container.querySelector(`.thumb-item[data-page="${index}"]`);
  item?.scrollIntoView({ block: 'nearest' });
}

export function renderOutline(container, toc, { onGo, onAdd, onAuto, onRemove, onRename, canEdit }) {
  container.textContent = '';
  if (canEdit) {
    container.append(h('div', { class: 'prop-row', style: 'flex-direction:column;align-items:stretch;gap:4px' }, [
      h('button', { class: 'btn small', text: '＋ 今のページをしおりに追加', onclick: onAdd }),
      h('button', {
        class: 'btn small', text: '見出しから自動で作る', onclick: onAuto,
        title: '文字の大きさから見出しを見つけて、しおり（目次）を作ります',
      }),
    ]));
  }
  if (!toc || !toc.length) {
    container.append(h('div', { class: 'prop-empty', text: 'しおりがありません。' }));
    return;
  }
  toc.forEach(([level, title, page], index) => {
    container.append(h('div', {
      class: 'outline-item',
      style: `padding-left:${(level - 1) * 12 + 4}px;display:flex;gap:4px;align-items:baseline`,
      title: `${page} ページ（右クリックで名前の変更・削除）`,
      onclick: () => onGo(page - 1),
      oncontextmenu: (e) => { e.preventDefault(); onRename(index, e); },
    }, [
      h('span', { text: title, style: 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis' }),
      h('span', { class: 'muted', text: String(page), style: 'font-size:11px' }),
    ]));
  });
  void onRemove;
}
