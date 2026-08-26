// Context bar, property inspector, comment list, thumbnails and outline.
//
// One property surface serves both "settings for the next annotation" and
// "settings for what is selected", so there is never a separate dialog to hunt
// through: change a value with nothing selected and it becomes the default,
// change it with a selection and it edits those annotations.

import * as model from './model.js';
import { SWATCHES, styleFor, remember } from './defaults.js';

const LINE_END_LABELS = {
  none: 'なし', openArrow: '開いた矢', closedArrow: '閉じた矢',
  rOpenArrow: '逆・開いた矢', rClosedArrow: '逆・閉じた矢',
  square: '四角', circle: '円', diamond: '菱形', slash: 'スラッシュ', butt: '突き当て',
};

const NOTE_ICONS = {
  Comment: 'コメント', Key: '鍵', Note: 'ノート', Help: 'ヘルプ',
  NewParagraph: '新規段落', Paragraph: '段落', Insert: '挿入',
};

const TYPE_LABELS = {
  highlight: 'ハイライト', underline: '下線', squiggly: '波線', strikeout: '取消線',
  areaHighlight: '範囲塗り', freetext: 'テキスト', note: '付箋', line: '線',
  square: '矩形', circle: '円', polygon: '多角形', polyline: '折れ線',
  ink: '手書き', stamp: 'スタンプ', redact: '墨消し', caret: '挿入',
};

export function typeLabel(annot) {
  return TYPE_LABELS[annot.type] || annot.type;
}

// FreeText has no border colour of its own — the text colour draws the border
// and the callout line too — so those tools get a single "文字色" swatch
// instead of a stroke swatch that would not survive saving.
const TEXT_TOOLS = ['freetext', 'callout'];

const FIELDS = {
  stroke: { tools: '*', except: TEXT_TOOLS },
  fontColour: { tools: TEXT_TOOLS },
  fill: { tools: ['square', 'circle', 'polygon', 'freetext', 'callout', 'areaHighlight', 'redact'] },
  width: { tools: ['pen', 'marker', 'line', 'square', 'circle', 'polygon', 'polyline', 'underline', 'squiggly', 'strikeout', 'freetext', 'callout', 'measureDistance', 'measureArea', 'measureAngle', 'count'] },
  opacity: { tools: '*' },
  dash: { tools: ['line', 'square', 'circle', 'polygon', 'polyline', 'freetext', 'callout'] },
  cloud: { tools: ['square', 'polygon'] },
  lineEnds: { tools: ['line'] },
  font: { tools: ['freetext', 'callout', 'redact'] },
  icon: { tools: ['note'] },
  stamp: { tools: ['stamp'] },
};

// Wording fixed by the spec, with a gloss so it is clear what will appear.
const STANDARD_STAMPS = [
  'APPROVED（承認済）', 'AS IS（現状のまま）', 'CONFIDENTIAL（社外秘）',
  'DEPARTMENTAL（部門用）', 'EXPERIMENTAL（試験的）', 'EXPIRED（期限切れ）',
  'FINAL（最終版）', 'FOR COMMENT（コメント用）', 'FOR PUBLIC RELEASE（公開可）',
  'NOT APPROVED（未承認）', 'NOT FOR PUBLIC RELEASE（公開不可）', 'SOLD（売却済）',
  'TOP SECRET（最高機密）', 'DRAFT（ドラフト）',
];

function applies(field, tool) {
  const spec = FIELDS[field];
  if (!spec) return false;
  if (spec.except && spec.except.includes(tool)) return false;
  return spec.tools === '*' || spec.tools.includes(tool);
}

function h(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    // `value` and `checked` must be set as properties — setAttribute does not
    // fill a <textarea> and does not update a live checkbox.
    else if (key === 'value' || key === 'checked') node[key] = value;
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function field(label, control) {
  return h('div', { class: 'field' }, [h('label', { text: label }), control]);
}

/**
 * Panels are rebuilt wholesale on every model change, which would otherwise
 * yank the caret out of whatever the user is typing into. Fields carry a
 * stable `data-field` key so focus and selection survive the rebuild.
 */
export function keepingFocus(container, render) {
  const active = document.activeElement;
  const key = container.contains(active) ? active.dataset?.field : null;
  const start = key ? active.selectionStart : null;
  const end = key ? active.selectionEnd : null;
  const scroll = container.scrollTop;

  render();

  if (key) {
    const restored = container.querySelector(`[data-field="${CSS.escape(key)}"]`);
    if (restored) {
      restored.focus();
      if (start !== null && restored.setSelectionRange) {
        try { restored.setSelectionRange(start, end); } catch { /* not a text input */ }
      }
    }
  }
  container.scrollTop = scroll;
}

/**
 * @param onChange (patch) => void  — receives a partial style patch
 */
export function renderContextBar(container, {
  tool, selection, onChange, onExtra, onCommit = () => {},
}) {
  container.textContent = '';
  const target = selection.length ? selection[0] : null;
  const effectiveTool = target ? toolOf(target) : tool;
  const NO_SETTINGS = ['select', 'pan', 'eraser', 'lasso', 'calibrate'];
  if (!selection.length && NO_SETTINGS.includes(effectiveTool)) {
    // The bar keeps its height either way, so say what it is for rather than
    // leaving a blank strip.
    container.append(h('span', {
      class: 'muted',
      text: tool === 'select'
        ? '注釈を選ぶか、ツールを選ぶと、ここに設定が出ます'
        : 'このツールに設定はありません',
    }));
    return;
  }
  const style = target ? { ...styleFor(effectiveTool), ...(target.style || {}) } : styleFor(effectiveTool);

  if (applies('stroke', effectiveTool)) {
    container.append(field('色', swatchRow(style.stroke, (value) => onChange({ stroke: value }))));
  }
  if (applies('fontColour', effectiveTool)) {
    container.append(field('文字色', swatchRow(style.font?.color, (value) => onChange({
      font: { color: value }, stroke: value,
    }))));
  }
  if (applies('fill', effectiveTool)) {
    container.append(field('塗り', h('div', { class: 'field' }, [
      h('input', {
        type: 'color', value: style.fill || '#ffffff',
        oninput: (e) => onChange({ fill: e.target.value }),
      }),
      h('button', {
        class: 'btn icon', title: '塗りなし', text: '⊘',
        onclick: () => onChange({ fill: null }),
      }),
    ])));
  }
  if (applies('width', effectiveTool)) {
    container.append(field('太さ', h('input', {
      type: 'number', class: 'input num', min: '0', max: '40', step: '0.5',
      value: String(style.width ?? 1.5),
      oninput: (e) => onChange({ width: Number(e.target.value) }),
    })));
  }
  if (applies('opacity', effectiveTool)) {
    const out = h('span', { class: 'muted', text: `${Math.round((style.opacity ?? 1) * 100)}%` });
    container.append(field('不透明度', h('div', { class: 'field' }, [
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
  if (applies('dash', effectiveTool)) {
    container.append(field('線種', select(
      { solid: '実線', dashed: '破線', beveled: 'ベベル', inset: 'インセット', underline: '下線' },
      style.borderStyle || 'solid',
      (value) => onChange({ borderStyle: value, dash: value === 'dashed' ? [4, 3] : [] }),
    )));
  }
  if (applies('cloud', effectiveTool)) {
    container.append(field('雲形', select(
      { 0: 'なし', 1: '弱', 2: '中', 3: '強' },
      String(style.cloudIntensity || 0),
      (value) => onChange({ cloudIntensity: Number(value) }),
    )));
  }
  if (applies('lineEnds', effectiveTool)) {
    const ends = style.lineEnds || ['none', 'none'];
    container.append(field('始点', select(LINE_END_LABELS, ends[0],
      (value) => onChange({ lineEnds: [value, ends[1]] }))));
    container.append(field('終点', select(LINE_END_LABELS, ends[1],
      (value) => onChange({ lineEnds: [ends[0], value] }))));
  }
  if (applies('font', effectiveTool)) {
    const font = style.font || {};
    container.append(field('文字', h('div', { class: 'field' }, [
      h('input', {
        type: 'number', class: 'input num', min: '4', max: '96', step: '1',
        value: String(font.size || 12),
        oninput: (e) => onChange({ font: { size: Number(e.target.value) } }),
      }),
      select({ left: '左', center: '中央', right: '右' }, font.align || 'left',
        (value) => onChange({ font: { align: value } })),
    ])));
    if (TEXT_TOOLS.includes(effectiveTool)) {
      container.append(h('span', { class: 'muted', text: '枠線と引き出し線は文字色で描かれます' }));
    }
  }
  if (applies('icon', effectiveTool) && target) {
    container.append(field('アイコン', select(NOTE_ICONS, target.icon || 'Comment',
      (value) => onExtra({ icon: value }))));
  }
  if (applies('stamp', effectiveTool)) {
    const index = target ? (target.stampIndex ?? 0) : (style.stampIndex ?? 0);
    const options = { ...Object.fromEntries(STANDARD_STAMPS.map((s, i) => [i, s])), '-1': 'カスタム文言…' };
    container.append(field('スタンプ', select(options, String(index), (value) => {
      const next = Number(value);
      if (target) onExtra({ stampIndex: next });
      else onChange({ stampIndex: next });
    })));
    if (index < 0 && !target) {
      container.append(field('文言', h('input', {
        class: 'input', 'data-field': 'stampText', value: style.stampText || '確認済',
        oninput: (e) => onChange({ stampText: e.target.value }),
      })));
      container.append(h('span', {
        class: 'muted',
        text: '{name} {date} {time} は押した瞬間の値に置き換わります',
      }));
    } else {
      container.append(h('span', {
        class: 'muted', text: '標準スタンプの文言は規格で決まっています',
      }));
    }
  }

  if (!selection.length) {
    container.append(h('span', { class: 'spacer' }));
    container.append(h('span', { class: 'muted', text: '設定した値は次に描くときの既定になります' }));
  }
}

function swatchRow(current, onPick) {
  const row = h('div', { class: 'swatches' });
  // The bar is not rebuilt on a value change, so the chosen swatch marks
  // itself — otherwise clicking a colour would give no feedback.
  const mark = (colour) => {
    for (const node of row.querySelectorAll('.swatch')) {
      node.classList.toggle('active', node.dataset.colour === colour);
    }
  };
  for (const colour of SWATCHES) {
    row.append(h('div', {
      class: `swatch${colour.toLowerCase() === (current || '').toLowerCase() ? ' active' : ''}`,
      'data-colour': colour,
      style: `background:${colour}`,
      title: colour,
      onclick: () => { mark(colour); picker.value = colour; onPick(colour); },
    }));
  }
  const picker = h('input', {
    type: 'color', value: current || '#000000',
    title: 'その他の色',
    oninput: (e) => { mark(null); onPick(e.target.value); },
  });
  row.append(picker);
  return row;
}

function select(options, value, onChange) {
  const node = h('select', { class: 'select', onchange: (e) => onChange(e.target.value) });
  for (const [key, label] of Object.entries(options)) {
    node.append(h('option', { value: key, text: label, selected: String(key) === String(value) }));
  }
  node.value = String(value);
  return node;
}

function toolOf(annot) {
  if (annot.type === 'ink') return annot.tool || 'pen';
  if (annot.type === 'freetext') return annot.callout ? 'callout' : 'freetext';
  return annot.type;
}

// ---------------------------------------------------------------- properties

const STATE_LABELS = { accepted: '承諾', rejected: '却下', completed: '完了', cancelled: '取り消し' };
const STATE_CHOICES = { null: '未設定', ...STATE_LABELS };

export function renderProps(container, { selection, onPatch, onCommit = () => {} }) {
  container.textContent = '';
  if (!selection.length) {
    container.append(h('div', { class: 'prop-empty', text: '注釈を選ぶと、ここで内容とプロパティを編集できます。' }));
    return;
  }
  const single = selection.length === 1 ? selection[0] : null;

  if (single) {
    const section = h('div', { class: 'prop-section' }, h('h3', { text: '内容' }));
    const textField = (key, props) => h(props.tag || 'input', {
      class: 'input', 'data-field': key, ...props.attrs,
      value: single[key] || '',
      oninput: (e) => onPatch({ [key]: e.target.value }, key),
    });

    if (single.type === 'freetext') {
      section.append(h('div', { class: 'prop-row' },
        textField('text', { tag: 'textarea', attrs: { placeholder: '本文' } })));
    }
    section.append(h('div', { class: 'prop-row' },
      textField('contents', { tag: 'textarea', attrs: { placeholder: 'コメント' } })));
    section.append(h('div', { class: 'prop-row' }, [
      h('label', { text: '作成者' }), textField('author', {}),
    ]));
    section.append(h('div', { class: 'prop-row' }, [
      h('label', { text: '主題' }), textField('subject', {}),
    ]));
    container.append(section);
  }

  const status = h('div', { class: 'prop-section' }, h('h3', { text: 'ステータス' }));
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

  const behaviour = h('div', { class: 'prop-section' }, h('h3', { text: '振る舞い' }));
  const flags = single?.flags || {};
  for (const [key, label] of Object.entries({ print: '印刷する', locked: 'ロック', readOnly: '読み取り専用', hidden: '非表示' })) {
    behaviour.append(h('div', { class: 'prop-row' }, [
      h('label', { text: label }),
      h('input', {
        type: 'checkbox', checked: key === 'print' ? flags[key] !== false : !!flags[key],
        onchange: (e) => onPatch({ flags: { [key]: e.target.checked } }),
      }),
    ]));
  }
  container.append(behaviour);

  if (selection.length > 1) {
    container.append(h('div', { class: 'prop-section muted', text: `${selection.length} 件を選択中。上の変更はすべてに適用されます。` }));
  }
}

// ---------------------------------------------------------------- comments

const SORTS = {
  page: 'ページ順',
  author: '作成者',
  type: '種類',
  state: 'ステータス',
  created: '作成日時',
};

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

export function renderComments(container, {
  annots, selection, filters, onSelect, onFilter, onPatch, onReply, onBulk,
}) {
  container.textContent = '';

  container.append(h('div', { class: 'comment-filters' }, [
    h('input', {
      class: 'input', type: 'search', 'data-field': 'commentQuery',
      placeholder: 'コメントを検索', value: filters.query || '',
      oninput: (e) => onFilter({ query: e.target.value }),
    }),
  ]));

  const authors = [...new Set(annots.map((a) => a.author).filter(Boolean))];
  const kinds = [...new Set(annots.map((a) => a.type))];
  container.append(h('div', { class: 'comment-filters' }, [
    select({ all: 'すべて', unchecked: '未チェック', checked: 'チェック済' },
      filters.checked || 'all', (value) => onFilter({ checked: value })),
    select({ all: '全ステータス', none: '未設定', ...STATE_LABELS },
      filters.state || 'all', (value) => onFilter({ state: value })),
  ]));
  container.append(h('div', { class: 'comment-filters' }, [
    select({ all: '全種類', ...Object.fromEntries(kinds.map((k) => [k, TYPE_LABELS[k] || k])) },
      filters.type || 'all', (value) => onFilter({ type: value })),
    authors.length > 1
      ? select({ all: '全作成者', ...Object.fromEntries(authors.map((a) => [a, a])) },
        filters.author || 'all', (value) => onFilter({ author: value }))
      : null,
  ]));
  container.append(h('div', { class: 'comment-filters' }, [
    h('label', { class: 'muted', text: '並べ替え' }),
    select(SORTS, filters.sort || 'page', (value) => onFilter({ sort: value })),
  ]));

  const visible = sorted(annots.filter((a) => matches(a, filters)), filters.sort);

  container.append(h('div', { class: 'comment-summary' }, [
    h('span', { class: 'muted', text: `${visible.length} / ${annots.length} 件` }),
    h('span', { class: 'spacer' }),
    h('button', {
      class: 'btn small', text: '絞り込み中を選択',
      title: '表示中のコメントをすべて選択して、まとめて書式やステータスを変更します',
      onclick: () => onBulk('select', visible),
    }),
  ]));

  if (!visible.length) {
    container.append(h('div', { class: 'prop-empty', text: '表示できるコメントがありません。' }));
    return;
  }

  const selectedIds = new Set(selection.map((a) => a.id));
  for (const annot of visible) {
    const isSelected = selectedIds.has(annot.id);
    const head = h('div', { class: 'comment-head' }, [
      h('span', { class: 'comment-dot', style: `background:${annot.style?.stroke || '#888'}` }),
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
    }, [
      head,
      h('div', { class: 'comment-body', text: annot.text || annot.contents || '（コメントなし）' }),
    ]);

    for (const reply of annot.replies || []) {
      item.append(h('div', { class: 'comment-reply' }, [
        h('div', { class: 'comment-head' }, [
          h('span', { text: reply.author || '返信' }),
        ]),
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
          class: 'input', 'data-field': `reply-${annot.id}`, placeholder: '返信を書く…',
          onkeydown: (e) => {
            if (e.key !== 'Enter' || !e.target.value.trim()) return;
            e.preventDefault();
            onReply(annot.id, e.target.value.trim());
            e.target.value = '';
          },
        }),
      ]));
    }
    container.append(item);
  }
}

function matches(annot, filters) {
  if (filters.checked === 'checked' && !annot.checked) return false;
  if (filters.checked === 'unchecked' && annot.checked) return false;
  if (filters.state === 'none' && annot.state) return false;
  if (filters.state && !['all', 'none'].includes(filters.state) && annot.state !== filters.state) return false;
  if (filters.type && filters.type !== 'all' && annot.type !== filters.type) return false;
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
    scaleBox.append(h('div', { class: 'warn-inline', text: '縮尺が未設定です。図面上の既知の寸法をなぞって設定してください。設定するまで計測値は実寸になりません。' }));
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
      class: 'input', 'data-field': 'takeoffSubject', value: subject || '',
      placeholder: '例: 床面積 / 配管長 / コンセント',
      oninput: (e) => onSubject(e.target.value),
    }),
  ]));
  group.append(h('div', { class: 'muted', text: 'ここに入れた名前ごとに集計されます。凡例の見出しにもなります。' }));
  container.append(group);

  const table = h('div', { class: 'prop-section' }, h('h3', { text: '集計' }));
  if (!rows.length) {
    table.append(h('div', { class: 'prop-empty', text: '計測やカウントを行うと、ここに集計が出ます。' }));
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

// Kept beside the settings they belong with: shortcuts nobody can find are
// shortcuts nobody uses.
const SHORTCUTS = {
  'V': '選択', 'H': 'ハイライト', 'U': '下線', 'K': '取り消し線',
  'T': 'テキスト', 'N': '付箋', 'P': 'ペン', 'E': '消しゴム',
  'L': '投げ縄', 'I': '線', 'R': '矩形', 'C': '円',
  'Ctrl+O': '開く', 'Ctrl+S': '保存', 'Ctrl+F': '検索',
  'Ctrl+Z': '元に戻す', 'Ctrl+Y': 'やり直し', 'Ctrl+A': 'このページを全選択',
  'Delete': '選択した注釈を削除', 'Enter': '多角形・計測を確定',
  'Esc': '作業中の操作を取り消す',
};

const PEN_PREFS = {
  pressure: ['筆圧を線幅に反映', 'ペンの筆圧、マウスでは描画速度から線幅を変えます'],
  penOnly: ['スタイラス専用モード', 'ペン以外（指・マウス）では描かなくなります'],
  leftHanded: ['左利きモード', 'ツールの並びを左手向けに寄せます'],
  shapeRecognition: ['図形認識', '雑に描いた丸や四角を整った図形に補正します'],
  snapAngle: ['角度拘束', 'Shiftを押しながらで15°刻みに拘束します'],
};

export function renderSettings(container, { getPref, setPref, onChange }) {
  container.textContent = '';

  const identity = h('div', { class: 'prop-section' }, h('h3', { text: '作成者' }));
  identity.append(h('div', { class: 'prop-row' }, [
    h('label', { text: '名前' }),
    h('input', {
      class: 'input', 'data-field': 'prefAuthor', placeholder: '注釈に記録される名前',
      value: getPref('author') || '',
      oninput: (e) => { setPref('author', e.target.value); onChange(); },
    }),
  ]));
  identity.append(h('div', { class: 'muted', text: 'ここで設定した名前が、これ以降に作る注釈の作成者として記録されます。' }));
  container.append(identity);

  const pen = h('div', { class: 'prop-section' }, h('h3', { text: 'ペン・入力' }));
  for (const [key, [label, hint]] of Object.entries(PEN_PREFS)) {
    pen.append(h('div', { class: 'prop-row' }, [
      h('label', { text: label, title: hint }),
      h('input', {
        type: 'checkbox', checked: !!getPref(key),
        onchange: (e) => { setPref(key, e.target.checked); onChange(); },
      }),
    ]));
  }
  pen.append(h('div', { class: 'muted', text: '手のひらが触れても描かないパームリジェクションと、二本指スクロールは常に有効です。' }));
  container.append(pen);

  const keys = h('div', { class: 'prop-section' }, h('h3', { text: 'キーボード' }));
  for (const [combo, label] of Object.entries(SHORTCUTS)) {
    keys.append(h('div', { class: 'shortcut-row' }, [
      h('kbd', { text: combo }),
      h('span', { text: label }),
    ]));
  }
  container.append(keys);

  const storage = h('div', { class: 'prop-section' }, h('h3', { text: '書式の既定値' }));
  storage.append(h('div', { class: 'muted', text: '直前に使った色・太さ・フォントが自動的に次の既定になります。' }));
  storage.append(h('div', { class: 'prop-row' }, h('button', {
    class: 'btn', text: '既定値をすべて初期化',
    onclick: () => {
      try { localStorage.removeItem('pdfstudio.defaults.v1'); } catch { /* ignore */ }
      location.reload();
    },
  })));
  container.append(storage);
}

// ---------------------------------------------------------------- side panels

export async function renderThumbs(container, viewer, currentPage, onGo) {
  container.textContent = '';
  for (const view of viewer.pageViews) {
    const canvas = document.createElement('canvas');
    canvas.className = `thumb${view.index === currentPage ? ' current' : ''}`;
    const scale = 150 / view.width;
    const viewport = view.page.getViewport({ scale });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.addEventListener('click', () => onGo(view.index));
    container.append(canvas, h('div', { class: 'thumb-label', text: String(view.index + 1) }));
    view.page.render({
      canvasContext: canvas.getContext('2d'), viewport, canvas, annotationMode: 0,
    }).promise.catch(() => {});
  }
}

export function renderOutline(container, toc, onGo) {
  container.textContent = '';
  if (!toc || !toc.length) {
    container.append(h('div', { class: 'prop-empty', text: 'しおりがありません。' }));
    return;
  }
  for (const [level, title, page] of toc) {
    container.append(h('div', {
      class: 'outline-item',
      style: `padding-left:${(level - 1) * 12 + 4}px`,
      text: title,
      onclick: () => onGo(page - 1),
    }));
  }
}

export { remember };
