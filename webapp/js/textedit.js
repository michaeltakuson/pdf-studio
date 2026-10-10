// Text boxes: the fonts they use, how big they need to be, where their lines
// break, and the in-place editor.
//
// One rule holds the whole thing together: the editor, the on-page rendering
// and the hidden measuring box all use the same CSS (.ft-text) at the same
// unscaled size. The browser therefore breaks every line identically in all
// three, and the line breaks sent to the PDF writer are the ones on screen.

export const LINE_HEIGHT = 1.3;   // keep in step with textap.py
const PAD_Y = 2;                  // and with .ft-text padding

const FALLBACK = '"Yu Gothic", "Meiryo", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif';
const FALLBACK_SERIF = '"Yu Mincho", "MS Mincho", "Hiragino Mincho ProN", "Noto Serif JP", serif';

export const FONTS = {
  gothic: { label: 'ゴシック', css: 'PS Gothic', files: { 400: 'BIZUDPGothic-Regular.ttf', 700: 'BIZUDPGothic-Bold.ttf' } },
  mincho: { label: '明朝', css: 'PS Mincho', serif: true, files: { 400: 'BIZUDPMincho-Regular.ttf' } },
  maru: { label: '丸ゴシック', css: 'PS Maru', files: { 400: 'ZenMaruGothic-Regular.ttf' } },
  klee: { label: '教科書体', css: 'PS Klee', files: { 400: 'KleeOne-Regular.ttf' } },
  yomogi: { label: '手書き風', css: 'PS Yomogi', files: { 400: 'Yomogi-Regular.ttf' } },
};

export function familyKey(font) {
  const key = font?.family;
  return FONTS[key] ? key : 'gothic';
}

/** Bold only exists where the face ships a bold cut; faking it would change widths. */
export function canBold(font) {
  return !!FONTS[familyKey(font)].files[700];
}

function weightOf(font) {
  return font?.bold && canBold(font) ? 700 : 400;
}

export function cssFamily(font) {
  const spec = FONTS[familyKey(font)];
  return `"${spec.css}", ${spec.serif ? FALLBACK_SERIF : FALLBACK}`;
}

/** Inline style shared by the editor, the renderer and the measurer. */
export function textCss(font) {
  const size = Number(font?.size) || 12;
  return `font-family:${cssFamily(font)};font-size:${size}px;line-height:${size * LINE_HEIGHT}px;`
    + `font-weight:${weightOf(font)};color:${font?.color || '#000000'};text-align:${font?.align || 'left'};`;
}

/** Font files the PDF writer needs for these annotations. */
export function fontFilesFor(annots) {
  const files = new Set();
  for (const annot of annots) {
    if (annot.type !== 'freetext') continue;
    const font = annot.style?.font || {};
    files.add(FONTS[familyKey(font)].files[weightOf(font)]);
  }
  return [...files];
}

export async function ensureFontLoaded(font, sample = 'あ漢A') {
  if (!document.fonts?.load) return;
  const size = Number(font?.size) || 12;
  try {
    await document.fonts.load(`${weightOf(font)} ${size}px "${FONTS[familyKey(font)].css}"`, sample);
  } catch { /* offline or blocked: the fallback stack still renders */ }
}

export function fontReady(font) {
  if (!document.fonts?.check) return true;
  try {
    return document.fonts.check(`${weightOf(font)} 12px "${FONTS[familyKey(font)].css}"`, 'あ');
  } catch { return true; }
}

// ---------------------------------------------------------------- measuring

let measurer = null;
function getMeasurer() {
  if (!measurer) {
    measurer = document.createElement('div');
    measurer.className = 'ft-text ft-measure';
    document.body.append(measurer);
  }
  return measurer;
}

const round64 = (value) => Math.ceil(value * 64 - 1e-6) / 64;

function configure(node, annot, pageWidth) {
  const font = annot.style?.font || {};
  const size = Number(font.size) || 12;
  const [x0, , x1] = annot.rect;
  let css = textCss(font);
  if (annot.autoWidth) {
    const room = Math.max(size * 3, (pageWidth || 595) - x0 - 2);
    css += `width:max-content;max-width:${room}px;min-width:${size + 6}px;`;
  } else {
    css += `width:${Math.max(size + 6, x1 - x0)}px;`;
  }
  node.style.cssText = css;
}

/** The size a text box needs for its text: {width, height} in points. */
export function measureBox(annot, pageWidth, text = annot.text) {
  const node = getMeasurer();
  configure(node, annot, pageWidth);
  node.textContent = text || ' ';
  const box = node.getBoundingClientRect();
  return { width: round64(box.width), height: round64(box.height) };
}

/**
 * The rectangle a text box should have after its text or font changed.
 * An auto-width box hugs its text; a fixed-width box keeps its width and
 * only grows or shrinks in height.
 */
export function fitRect(annot, pageWidth) {
  const { width, height } = measureBox(annot, pageWidth);
  const [x0, y0, x1, y1] = annot.rect;
  const keepHeight = annot.autoHeight === false ? Math.max(height, y1 - y0) : height;
  return [x0, y0, annot.autoWidth ? x0 + width : x1, y0 + keepHeight];
}

let metricCanvas = null;
function fontMetrics(font) {
  const size = Number(font?.size) || 12;
  try {
    metricCanvas = metricCanvas || document.createElement('canvas').getContext('2d');
    metricCanvas.font = `${weightOf(font)} ${size}px ${cssFamily(font)}`;
    const m = metricCanvas.measureText('あ');
    if (m.fontBoundingBoxAscent > 0) return { ascent: m.fontBoundingBoxAscent, descent: m.fontBoundingBoxDescent };
  } catch { /* fall through */ }
  return { ascent: size * 0.88, descent: size * 0.12 };
}

/**
 * Where each line of a text box sits, as the browser lays it out:
 * {lines: [{x, y, t}]} with x the line's left edge and y its baseline, both
 * relative to the box's top-left corner. The PDF writer draws exactly this.
 */
export function computeLayout(annot) {
  const text = annot.text || '';
  if (!text) return { lines: [] };
  const font = annot.style?.font || {};
  const size = Number(font.size) || 12;
  const step = size * LINE_HEIGHT;
  const { ascent, descent } = fontMetrics(font);
  const lead = (step - (ascent + descent)) / 2;

  const node = getMeasurer();
  configure(node, { ...annot, autoWidth: false }, 0);
  node.textContent = text;
  const textNode = node.firstChild;
  const base = node.getBoundingClientRect();
  const range = document.createRange();
  const rows = new Map();

  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    const pair = code >= 0xd800 && code <= 0xdbff && i + 1 < text.length;
    const ch = pair ? text.slice(i, i + 2) : text[i];
    if (ch !== '\n' && ch !== '\r') {
      range.setStart(textNode, i);
      range.setEnd(textNode, i + ch.length);
      const rects = range.getClientRects();
      const rect = rects[rects.length - 1];
      if (rect) {
        const row = Math.max(0, Math.round((rect.top - base.top - PAD_Y - lead) / step));
        if (!rows.has(row)) rows.set(row, { x: rect.left - base.left, t: '' });
        const line = rows.get(row);
        // A space left hanging at the end of a wrapped line has no width.
        line.x = Math.min(line.x, rect.left - base.left);
        line.t += ch;
      }
    }
    if (pair) i += 1;
  }
  const lines = [...rows.entries()].sort((a, b) => a[0] - b[0]).map(([row, line]) => ({
    x: Number(line.x.toFixed(3)),
    y: Number((PAD_Y + row * step + lead + ascent).toFixed(3)),
    t: line.t.replace(/\s+$/, ''),
  })).filter((line) => line.t);
  return { lines };
}

// ---------------------------------------------------------------- editor

const PLAINTEXT_ONLY = (() => {
  const probe = document.createElement('div');
  try { probe.contentEditable = 'plaintext-only'; } catch { return false; }
  return probe.contentEditable === 'plaintext-only';
})();

/**
 * The in-place editor for a text box.
 *
 * It is a contenteditable element rather than a textarea so it can size
 * itself to its text as it is typed, and it is never rebuilt while it has
 * focus — rebuilding an input mid-composition is what used to turn 「ら」
 * into 「rあ」.
 */
export class TextEditor {
  constructor({ wrap, annot, scale, pageWidth, placeholder = '', onCommit, onGrab }) {
    this.annot = annot;
    this.pageWidth = pageWidth;
    this.onCommit = onCommit;
    this.onGrab = onGrab;
    this.closed = false;
    this.composing = false;

    this.host = document.createElement('div');
    this.host.className = 'ft-host';
    this.grip = document.createElement('div');
    this.grip.className = 'ft-grip';
    this.node = document.createElement('div');
    this.node.className = 'ft-text ft-editor';
    this.node.contentEditable = PLAINTEXT_ONLY ? 'plaintext-only' : 'true';
    this.node.spellcheck = false;
    this.node.setAttribute('data-placeholder', placeholder);
    this.node.textContent = annot.text || '';
    this.host.append(this.grip, this.node);
    wrap.append(this.host);

    this.applyStyle(annot);
    this.setScale(scale);

    this.node.addEventListener('compositionstart', () => { this.composing = true; });
    this.node.addEventListener('compositionend', () => { this.composing = false; });
    this.node.addEventListener('keydown', (event) => {
      // Keys pressed while an IME is converting belong to the IME: Enter
      // confirms a candidate, Escape cancels it. Neither may close the box.
      const converting = event.isComposing || this.composing || event.keyCode === 229;
      if (!converting && event.key === 'Escape') { event.preventDefault(); this.commit({ keepSelected: true }); }
      if (!converting && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); this.commit(); }
      event.stopPropagation();
    });
    for (const type of ['keyup', 'keypress', 'copy', 'cut']) {
      this.node.addEventListener(type, (event) => event.stopPropagation());
    }
    this.node.addEventListener('paste', (event) => {
      event.stopPropagation();
      if (PLAINTEXT_ONLY) return;
      // Without plaintext-only the browser would paste formatted HTML.
      event.preventDefault();
      const text = event.clipboardData?.getData('text/plain') || '';
      document.execCommand('insertText', false, text);
    });
    this.grip.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      this.onGrab?.(event);
    });
    for (const type of ['pointerdown', 'pointerup', 'dblclick', 'click', 'mouseup', 'contextmenu']) {
      this.node.addEventListener(type, (event) => event.stopPropagation());
    }
  }

  focus({ selectAll = false, point = null } = {}) {
    this.node.focus({ preventScroll: true });
    const selection = window.getSelection();
    if (!selection) return;
    if (point && document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(point.x, point.y);
      if (pos && this.node.contains(pos.offsetNode)) {
        selection.collapse(pos.offsetNode, pos.offset);
        return;
      }
    } else if (point && document.caretRangeFromPoint) {
      const hit = document.caretRangeFromPoint(point.x, point.y);
      if (hit && this.node.contains(hit.startContainer)) {
        selection.removeAllRanges();
        selection.addRange(hit);
        return;
      }
    }
    const range = document.createRange();
    range.selectNodeContents(this.node);
    if (!selectAll) range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  /** Follow a format change made in the ribbon while the box is open. */
  applyStyle(annot) {
    this.annot = annot;
    configure(this.node, annot, this.pageWidth);
    this.host.style.width = 'max-content';
  }

  setScale(scale) {
    this.scale = scale;
    const [x0, y0] = this.annot.rect;
    this.host.style.left = `${x0 * scale}px`;
    this.host.style.top = `${y0 * scale}px`;
    this.host.style.transform = `scale(${scale})`;
  }

  get text() {
    const raw = PLAINTEXT_ONLY ? this.node.textContent : this.node.innerText;
    return (raw || '').replace(/\r\n?/g, '\n').replace(/ /g, ' ').replace(/[ \t\n]+$/, '');
  }

  /** The box the text needs right now, in points. */
  size() {
    const box = this.node.getBoundingClientRect();
    return { width: round64(box.width / this.scale), height: round64(box.height / this.scale) };
  }

  commit(options = {}) {
    if (this.closed) return;
    this.closed = true;
    const text = this.text;
    const size = this.size();
    this.host.remove();
    this.onCommit?.(text, size, options);
  }

  discard() {
    if (this.closed) return;
    this.closed = true;
    this.host.remove();
  }
}
