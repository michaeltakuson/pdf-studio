// Pointer handling for every tool.
//
// All of it hangs off the stage. Which element a press lands on — a run of
// page text, an existing annotation, or bare page — is decided by CSS from
// the current mode (see .pages[data-mode] in app.css); nothing here sits on
// top of the page swallowing events, which is what used to stop the text
// markup tools from ever seeing the text.
//
// Pen support is deliberate: pressure comes from Pointer Events, palm contact
// is filtered out once a stylus has been seen, and a finger that is not
// drawing scrolls the page instead.

import { el, strokePath, pressurePath, cloudPath } from './render.js';
import * as model from './model.js';
import { styleFor, getPref } from './defaults.js';
import { compute, MEASURE_KINDS } from './measure.js';
import { LINE_HEIGHT } from './textedit.js';

export const MARKUP_TOOLS = new Set(['highlight', 'underline', 'squiggly', 'strikeout']);
const DRAG_SHAPES = new Set(['square', 'circle', 'line', 'arrow', 'areaHighlight', 'redact', 'stamp']);
const TEXT_TOOLS = new Set(['freetext', 'callout']);
const POLY_TOOLS = new Set(['polygon', 'polyline']);
const INK_TOOLS = new Set(['pen', 'marker']);
// Distance and angle are click-to-click; area closes a polygon like the
// polygon tool does.
const MEASURE_POINT_TOOLS = new Set([
  'measureDistance', 'measureArea', 'measureAngle', 'measurePerimeter', 'measureRadius',
]);
// Tools that hand back to the pointer once they have made their one thing,
// so the new shape can be adjusted straight away.
const ONE_SHOT = new Set([
  'square', 'circle', 'line', 'arrow', 'areaHighlight', 'stamp', 'polygon', 'polyline',
  'callout', 'note',
]);

const DRAG_THRESHOLD = 3; // screen pixels before a press becomes a drag

export function modeOf(tool) {
  if (tool === 'select' || tool === 'pan' || tool === 'edittext') return tool;
  if (MARKUP_TOOLS.has(tool)) return 'markup';
  if (TEXT_TOOLS.has(tool)) return 'text';
  if (INK_TOOLS.has(tool)) return 'ink';
  return 'draw';
}

const FLAGS = () => ({ print: true, locked: false, readOnly: false, hidden: false });

export class ToolController extends EventTarget {
  constructor(viewer) {
    super();
    this.viewer = viewer;
    this.tool = 'select';
    this.pending = null;
    this.sawPen = false;
    this.swallowNext = false;
    this.markKind = 'check';
    this._activePointers = new Map();

    const stage = viewer.stage;
    stage.addEventListener('pointerdown', (e) => this._onDown(e));
    stage.addEventListener('pointermove', (e) => this._onMove(e));
    stage.addEventListener('pointerup', (e) => this._onUp(e));
    stage.addEventListener('pointercancel', (e) => this._onUp(e, true));
    stage.addEventListener('dblclick', (e) => this._onDoubleClick(e));
    // A drag that starts on bare page must not start a text selection.
    stage.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest?.('.ft-host, .note-editor, .text-layer, input, textarea, select')) return;
      if (!e.target.closest?.('.page-wrap')) return;
      e.preventDefault();
      // A note's editor is opened by this very press (on pointerdown), so it
      // already has the focus by now and must keep it.
      const active = document.activeElement;
      if (active && active !== document.body && !active.closest?.('.ft-host, .note-editor')) active.blur?.();
      window.getSelection()?.removeAllRanges();
    });
    document.addEventListener('pointerup', () => {
      if (MARKUP_TOOLS.has(this.tool)) setTimeout(() => this.markupSelection(), 0);
    });
  }

  setTool(tool) {
    this._cancelPending();
    // Picking a drawing tool means "I am about to draw", so the format
    // controls should show that tool's defaults instead of a selection's.
    if (tool !== 'select' && tool !== 'pan' && model.store.selection.length) {
      model.select([]);
    }
    this.tool = tool;
    this.viewer.setMode(modeOf(tool));
    this.viewer.setCursor(
      tool === 'pan' ? 'grab'
        : INK_TOOLS.has(tool) || tool === 'eraser' ? 'pen'
          : MARKUP_TOOLS.has(tool) || TEXT_TOOLS.has(tool) || tool === 'edittext' ? 'text'
            : tool === 'mark' || tool === 'note' || tool === 'count' ? 'place'
              : tool === 'select' ? '' : 'cross',
    );
    this.dispatchEvent(new CustomEvent('tool', { detail: { tool } }));
  }

  get style() {
    return styleFor(this.tool);
  }

  _emit(name, detail = {}) {
    this.dispatchEvent(new CustomEvent(name, { detail }));
  }

  _done(ids = []) {
    this._emit('edited');
    if (ONE_SHOT.has(this.tool)) this._emit('tool-done', { ids });
  }

  // -------------------------------------------------------------- pointers

  /** Should this contact scroll the page instead of acting as the tool? */
  _isPalm(event) {
    if (event.pointerType !== 'touch') return false;
    const mode = modeOf(this.tool);
    if (mode !== 'ink' && mode !== 'draw') return false;
    // Once a stylus is in use, skin contact is a resting hand or a scroll.
    if (this.sawPen || getPref('penOnly')) return true;
    // A second finger means "scroll", not "draw".
    return this._activePointers.size > 1;
  }

  _onDown(event) {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    if (event.target.closest?.('.ft-host, .note-editor')) return;
    this._activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (event.pointerType === 'pen') this.sawPen = true;

    if (this.swallowNext) {
      // This press closed an open text box. Letting it also start a new one
      // would leave an empty box wherever the user clicked away.
      this.swallowNext = false;
      return;
    }

    if (this._isPalm(event)) {
      // Abandon a stroke the first finger had started, then scroll.
      if (this.pending?.kind === 'ink') { this.pending.node?.remove(); this.pending = null; }
      this.touchPan = { id: event.pointerId, x: event.clientX, y: event.clientY,
        left: this.viewer.stage.scrollLeft, top: this.viewer.stage.scrollTop };
      return;
    }
    if (event.pointerType !== 'pen' && getPref('penOnly') && INK_TOOLS.has(this.tool)) return;

    const view = this.viewer.viewFromEvent(event);
    if (!view) {
      if (this.tool === 'select') model.select([]);
      return;
    }
    const point = this.viewer.toPageCoords(view, event);
    const tool = this.tool;

    if (tool === 'pan') {
      this.pending = { kind: 'pan', startX: event.clientX, startY: event.clientY,
        scrollLeft: this.viewer.stage.scrollLeft, scrollTop: this.viewer.stage.scrollTop };
      this.viewer.stage.setPointerCapture?.(event.pointerId);
      return;
    }
    if (tool === 'select') { this._startSelect(view, point, event); return; }
    // A resize handle works whatever tool is active: with the text tool
    // still on, grabbing the edge of the box just typed must resize that box,
    // not start drawing another one on top of it.
    if (event.target.closest?.('[data-handle]')) { this._startSelect(view, point, event); return; }
    if (MARKUP_TOOLS.has(tool)) return; // the browser's own text selection does the work
    if (tool === 'edittext') { this._emit('text-line', { view, point, event }); return; }

    if (TEXT_TOOLS.has(tool)) {
      const existing = event.target.closest?.('.annot.is-text');
      if (existing) {
        this._emit('edit-text', { id: existing.dataset.id, point: { x: event.clientX, y: event.clientY } });
        return;
      }
      this.pending = { kind: 'shape', view, style: this.style, start: point, current: point,
        node: null, client: { x: event.clientX, y: event.clientY } };
      return;
    }
    if (tool === 'note') { this._createNote(view, point); return; }
    if (tool === 'mark') { this._placeMark(view, point); return; }
    if (tool === 'count') { this._addCount(view, point); return; }
    if (tool === 'calibrate') { this._addCalibrationPoint(view, point); return; }
    if (MEASURE_POINT_TOOLS.has(tool)) { this._addMeasurePoint(view, point, event); return; }
    if (POLY_TOOLS.has(tool)) { this._addPolyPoint(view, point, event); return; }

    event.preventDefault();
    // Capture keeps a fast stroke from escaping the page. It throws if the
    // pointer has already been lifted, which a quick tap can do.
    try { this.viewer.stage.setPointerCapture(event.pointerId); } catch { /* pointer gone */ }

    if (INK_TOOLS.has(tool)) { this._startInk(view, point, event); return; }
    if (tool === 'eraser') { this.pending = { kind: 'erase', view, hits: new Set() }; this._erase(view, point); return; }
    if (tool === 'lasso') { this.pending = { kind: 'lasso', view, pts: [[point.x, point.y]], node: null }; return; }
    if (DRAG_SHAPES.has(tool)) {
      this.pending = { kind: 'shape', view, style: this.style, start: point, current: point,
        node: null, client: { x: event.clientX, y: event.clientY } };
    }
  }

  _onMove(event) {
    if (this._activePointers.has(event.pointerId)) {
      this._activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
    if (this.touchPan && this.touchPan.id === event.pointerId) {
      this.viewer.stage.scrollLeft = this.touchPan.left - (event.clientX - this.touchPan.x);
      this.viewer.stage.scrollTop = this.touchPan.top - (event.clientY - this.touchPan.y);
      return;
    }
    const pending = this.pending;
    if (!pending) return;

    if (pending.kind === 'pan') {
      this.viewer.stage.scrollLeft = pending.scrollLeft - (event.clientX - pending.startX);
      this.viewer.stage.scrollTop = pending.scrollTop - (event.clientY - pending.startY);
      return;
    }

    const view = pending.view;
    if (!view) return;
    const point = this.viewer.toPageCoords(view, event);

    if (pending.client && !pending.dragging) {
      const far = Math.hypot(event.clientX - pending.client.x, event.clientY - pending.client.y);
      if (far < DRAG_THRESHOLD) return;
      pending.dragging = true;
    }

    switch (pending.kind) {
      case 'ink': this._extendInk(event, point); break;
      case 'erase': this._erase(view, point); break;
      case 'lasso': this._extendLasso(point); break;
      case 'shape': this._updateShape(point, event); break;
      case 'move': this._updateMove(point); break;
      case 'resize': this._updateResize(point, event); break;
      case 'marquee': this._updateMarquee(point); break;
      default: break;
    }
  }

  _onUp(event, cancelled = false) {
    this._activePointers.delete(event.pointerId);
    if (this.touchPan && this.touchPan.id === event.pointerId) { this.touchPan = null; return; }
    const pending = this.pending;
    if (!pending) return;
    this.pending = null;

    if (pending.node) pending.node.remove();
    if (pending.view && pending.kind !== 'move' && pending.kind !== 'resize') {
      for (const node of pending.view.draw.querySelectorAll('.preview')) node.remove();
    }
    if (cancelled && pending.kind !== 'move' && pending.kind !== 'resize') return;

    switch (pending.kind) {
      case 'ink': this._finishInk(pending); break;
      case 'erase': this._finishErase(pending); break;
      case 'lasso': this._finishLasso(pending); break;
      case 'shape': this._finishShape(pending, event); break;
      case 'marquee': this._finishMarquee(pending); break;
      case 'move': this._finishMove(pending, event); break;
      case 'resize':
        this._emit('resized', { id: pending.annot.id, handle: pending.handle });
        this._emit('edited');
        break;
      default: break;
    }
  }

  _onDoubleClick(event) {
    if (event.target.closest?.('.ft-host, .note-editor')) return;
    if (this.measuring) { this.finishMeasure(); return; }
    if (POLY_TOOLS.has(this.tool) && this.poly) { this._finishPoly(); return; }
    if (this.tool !== 'select') return;
    const target = event.target.closest?.('.annot');
    if (!target) return;
    const annot = model.byId(target.dataset.id);
    if (annot && (annot.type === 'freetext' || annot.type === 'note')) {
      this._emit('edit-text', { id: annot.id, point: { x: event.clientX, y: event.clientY } });
    } else if (annot) {
      this._emit('open-props', { id: annot.id });
    }
  }

  _cancelPending() {
    if (this.pending?.node) this.pending.node.remove();
    this.pending = null;
    this.cancelPoly();
    this.cancelMeasure();
    if (this.calibrating) { this.calibrating.node?.remove(); this.calibrating = null; }
  }

  // -------------------------------------------------------------- selection

  _startSelect(view, point, event) {
    const handle = event.target.closest?.('[data-handle]');
    if (handle) {
      const annot = model.byId(handle.dataset.id);
      if (!annot) return;
      event.preventDefault();
      try { this.viewer.stage.setPointerCapture(event.pointerId); } catch { /* gone */ }
      this.pending = { kind: 'resize', view, annot, handle: handle.dataset.handle,
        origin: [...annot.rect], snapshot: structuredClone(annot), start: point, gesture: model.uid() };
      return;
    }
    const hit = event.target.closest?.('.annot');
    if (hit) {
      const id = hit.dataset.id;
      const wasSelected = model.store.selection.includes(id);
      if (event.shiftKey || event.ctrlKey || event.metaKey) {
        // Shift/Ctrl-click adds to or removes from the selection.
        model.select(wasSelected
          ? model.store.selection.filter((other) => other !== id)
          : [...model.store.selection, id]);
        return;
      }
      if (!wasSelected) model.select([id]);
      this.beginMove(view, point, event, { hitId: id });
      return;
    }
    if (event.target.closest?.('.pdf-link')) return;
    if (event.target.closest?.('.text-layer') && event.target.tagName === 'SPAN') {
      // A press on page text starts an ordinary text selection.
      model.select([]);
      return;
    }
    if (event.pointerType === 'touch') { model.select([]); return; } // let the page scroll
    model.select([]);
    this.pending = { kind: 'marquee', view, start: point, node: null,
      client: { x: event.clientX, y: event.clientY } };
  }

  /** Start dragging the current selection. Also used by the text editor's frame. */
  beginMove(view, point, event, { hitId = null } = {}) {
    const annots = model.store.selection.map(model.byId).filter(Boolean);
    const movable = !annots.some((a) => a.flags?.locked);
    try { this.viewer.stage.setPointerCapture(event.pointerId); } catch { /* gone */ }
    this.pending = { kind: 'move', view, start: point, gesture: model.uid(), hitId, movable,
      client: { x: event.clientX, y: event.clientY }, moved: false,
      origins: annots.map((a) => ({ id: a.id, snapshot: structuredClone(a) })) };
  }

  _updateMove(point) {
    if (!this.pending.movable) return;
    this.pending.moved = true;
    const dx = point.x - this.pending.start.x;
    const dy = point.y - this.pending.start.y;
    for (const { id, snapshot } of this.pending.origins) {
      model.updateAnnots([id], translated(snapshot, dx, dy), { merge: this.pending.gesture });
    }
  }

  _finishMove(pending, event) {
    if (pending.moved) { model.endMerge(); this._emit('edited'); return; }
    // A press and release without movement on a text box means "type here".
    const annot = pending.hitId ? model.byId(pending.hitId) : null;
    if (!annot || annot.flags?.locked || annot.flags?.readOnly) return;
    if (model.store.selection.length !== 1) return;
    if (annot.type === 'freetext' || annot.type === 'note') {
      this._emit('edit-text', { id: annot.id, point: { x: event.clientX, y: event.clientY } });
    }
  }

  _updateResize(point, event) {
    const { annot, handle, origin, start, snapshot } = this.pending;
    const dx = point.x - start.x;
    const dy = point.y - start.y;

    if (handle === 'p0' || handle === 'p1') {
      const points = snapshot.points.map((p) => [...p]);
      const index = handle === 'p0' ? 0 : 1;
      let target = { x: snapshot.points[index][0] + dx, y: snapshot.points[index][1] + dy };
      if (event.shiftKey) {
        const other = snapshot.points[1 - index];
        target = constrain({ x: other[0], y: other[1] }, target, true);
      }
      points[index] = [target.x, target.y];
      model.updateAnnots([annot.id], { points, rect: boundsOf(points, 10) }, { merge: this.pending.gesture });
      return;
    }

    let [x0, y0, x1, y1] = origin;
    if (handle.includes('w')) x0 += dx;
    if (handle.includes('e')) x1 += dx;
    if (handle.includes('n')) y0 += dy;
    if (handle.includes('s')) y1 += dy;
    // Pictures keep their proportions from a corner unless Shift frees them;
    // everything else is free unless Shift locks it.
    const corner = handle.length === 2;
    const lockRatio = corner && (snapshot.type === 'image' ? !event.shiftKey : event.shiftKey);
    if (lockRatio && (origin[3] - origin[1]) > 0) {
      const ratio = (origin[2] - origin[0]) / (origin[3] - origin[1]);
      const height = Math.abs(x1 - x0) / ratio;
      if (handle.includes('n')) y0 = y1 - height; else y1 = y0 + height;
    }
    const rect = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    if (rect[2] - rect[0] < 4) rect[2] = rect[0] + 4;
    if (rect[3] - rect[1] < 4) rect[3] = rect[1] + 4;
    const patch = { rect };
    if (snapshot.points) patch.points = scalePoints(snapshot.points, origin, rect);
    if (snapshot.strokes) {
      patch.strokes = snapshot.strokes.map((s) => ({ ...s, pts: scalePoints(s.pts, origin, rect) }));
    }
    if (snapshot.quads) patch.quads = snapshot.quads.map((q) => scaleQuad(q, origin, rect));
    if (snapshot.type === 'freetext') {
      // Dragging a side sets the width by hand; the text then wraps to it.
      if (handle.includes('w') || handle.includes('e')) patch.autoWidth = false;
      if (handle.includes('n') || handle.includes('s')) patch.autoHeight = false;
    }
    model.updateAnnots([annot.id], patch, { merge: this.pending.gesture });
  }

  _updateMarquee(point) {
    const { start, view } = this.pending;
    const rect = normRect(start.x, start.y, point.x, point.y);
    if (!this.pending.node) {
      this.pending.node = el('rect', { class: 'marquee preview' });
      view.draw.append(this.pending.node);
    }
    setRect(this.pending.node, rect);
    this.pending.rect = rect;
  }

  _finishMarquee(pending) {
    if (!pending.rect) return;
    const ids = model.onPage(pending.view.index)
      .filter((a) => intersects(a.rect, pending.rect))
      .map((a) => a.id);
    model.select(ids);
  }

  // -------------------------------------------------------------- ink

  _startInk(view, point, event) {
    const style = this.style;
    this.pending = {
      kind: 'ink', view, style,
      pts: [[point.x, point.y]],
      pressure: [normalisePressure(event)],
      lastTime: performance.now(),
      lastPoint: point,
      node: el('path', { class: 'preview', fill: 'none', stroke: style.stroke, 'stroke-width': style.width,
        'stroke-linecap': 'round', 'stroke-linejoin': 'round', opacity: style.opacity }),
    };
    view.draw.append(this.pending.node);
  }

  _extendInk(event, point) {
    const p = this.pending;
    const coalesced = event.getCoalescedEvents?.() ?? [];
    const events = coalesced.length ? coalesced : [event];
    for (const sub of events) {
      const local = sub === event ? point : this.viewer.toPageCoords(p.view, sub);
      const last = p.pts[p.pts.length - 1];
      if (Math.hypot(local.x - last[0], local.y - last[1]) < 0.4) continue;
      p.pts.push([local.x, local.y]);
      p.pressure.push(normalisePressure(sub, p));
    }
    const usePressure = getPref('pressure') && this.tool === 'pen';
    if (usePressure) {
      p.node.setAttribute('d', pressurePath(p.pts, p.pressure, p.style.width));
      p.node.setAttribute('fill', p.style.stroke);
      p.node.setAttribute('stroke', 'none');
    } else {
      p.node.setAttribute('d', strokePath(p.pts));
    }
  }

  _finishInk(pending) {
    if (pending.pts.length < 2) {
      // A tap leaves a dot — dotting an i should not need a wiggle.
      const [x, y] = pending.pts[0];
      pending.pts.push([x + 0.6, y + 0.6]);
      pending.pressure.push(pending.pressure[0] ?? 0.5);
    }
    const usePressure = getPref('pressure') && this.tool === 'pen';
    const pts = simplify(pending.pts, 0.3);
    const pressure = usePressure ? resample(pending.pressure, pending.pts.length, pts.length) : null;
    model.addAnnots([{
      type: 'ink',
      page: pending.view.index,
      rect: boundsOf(pts, pending.style.width),
      strokes: [{ pts, pressure }],
      style: pending.style,
      tool: this.tool,
      author: getPref('author') || '',
      flags: FLAGS(),
    }], { select: false });
    this._emit('edited');
  }

  // -------------------------------------------------------------- eraser

  _erase(view, point) {
    for (const annot of model.onPage(view.index)) {
      if (annot.type !== 'ink') continue;
      if (annot.flags?.locked) continue;
      const reach = 5 + (annot.style?.width || 2) / 2;
      for (const stroke of annot.strokes || []) {
        if (stroke.pts.some((p) => Math.hypot(p[0] - point.x, p[1] - point.y) < reach)) {
          this.pending.hits.add(annot.id);
          break;
        }
      }
    }
    for (const id of this.pending.hits) {
      const node = view.svg.querySelector(`[data-id="${id}"]`);
      if (node) node.setAttribute('opacity', '0.25');
    }
  }

  _finishErase(pending) {
    if (!pending.hits.size) return;
    model.removeAnnots([...pending.hits]);
    this._emit('edited');
  }

  // -------------------------------------------------------------- lasso

  _extendLasso(point) {
    const p = this.pending;
    p.pts.push([point.x, point.y]);
    if (!p.node) {
      p.node = el('path', { class: 'preview', fill: 'rgba(80,140,255,.12)', stroke: '#4d8dff',
        'stroke-width': 1, 'stroke-dasharray': '4 3' });
      p.view.draw.append(p.node);
    }
    p.node.setAttribute('d', `M ${p.pts.map((q) => q.join(' ')).join(' L ')} Z`);
  }

  _finishLasso(pending) {
    if (pending.pts.length < 3) return;
    const ids = model.onPage(pending.view.index)
      .filter((a) => pointInPolygon(centreOf(a.rect), pending.pts))
      .map((a) => a.id);
    this._emit('lassoed', { ids });
  }

  // -------------------------------------------------------------- shapes

  _updateShape(point, event) {
    const p = this.pending;
    const tool = this.tool;
    const isLine = tool === 'line' || tool === 'arrow';
    let end = point;
    if (event.shiftKey) end = constrain(p.start, point, isLine);
    p.current = end;
    const rect = normRect(p.start.x, p.start.y, end.x, end.y);
    p.rect = rect;

    if (!p.node) {
      const tag = tool === 'circle' ? 'ellipse' : isLine || tool === 'callout' ? 'line' : 'rect';
      const filled = tool === 'areaHighlight' || tool === 'redact';
      const text = tool === 'freetext';
      p.node = el(tag, {
        class: 'preview',
        fill: filled ? (p.style.fill || p.style.stroke) : text ? 'none' : (p.style.fill || 'none'),
        stroke: text ? '#1e5bb8' : p.style.stroke,
        'stroke-width': text ? 1 : (p.style.width || 1),
        'stroke-dasharray': text ? '4 3' : null,
        opacity: filled ? Math.min(0.5, p.style.opacity ?? 1) : (p.style.opacity ?? 1),
        'vector-effect': text ? 'non-scaling-stroke' : null,
      });
      p.view.draw.append(p.node);
    }
    if (isLine || tool === 'callout') {
      p.node.setAttribute('x1', p.start.x); p.node.setAttribute('y1', p.start.y);
      p.node.setAttribute('x2', end.x); p.node.setAttribute('y2', end.y);
    } else if (tool === 'circle') {
      p.node.setAttribute('cx', (rect[0] + rect[2]) / 2);
      p.node.setAttribute('cy', (rect[1] + rect[3]) / 2);
      p.node.setAttribute('rx', (rect[2] - rect[0]) / 2);
      p.node.setAttribute('ry', (rect[3] - rect[1]) / 2);
    } else {
      setRect(p.node, rect);
    }
  }

  _finishShape(pending, event) {
    const tool = this.tool;
    const rect = pending.rect;
    const dragged = !!pending.dragging && !!rect && (rect[2] - rect[0] > 3 || rect[3] - rect[1] > 3);
    const page = model.store.pages[pending.view.index] || { width: 595, height: 842 };
    const base = { page: pending.view.index, style: pending.style, author: getPref('author') || '', flags: FLAGS() };

    if (tool === 'freetext') {
      const size = pending.style.font?.size || 12;
      const lineHeight = size * LINE_HEIGHT;
      let box;
      let autoWidth = true;
      if (dragged && rect[2] - rect[0] > 12) {
        // A dragged box has the width the user drew; text wraps inside it.
        box = [rect[0], rect[1], rect[2], Math.max(rect[3], rect[1] + lineHeight + 4)];
        autoWidth = false;
      } else {
        // A click puts the first line where the pointer is, like a caret.
        const x = Math.max(2, Math.min(pending.start.x, page.width - size * 2));
        const y = Math.max(2, pending.start.y - lineHeight / 2 - 2);
        box = [x, y, x + size + 6, y + lineHeight + 4];
      }
      const [created] = model.addAnnots([{ ...base, type: 'freetext', rect: box, text: '', contents: '', autoWidth }]);
      this._emit('edit-text', { id: created.id, isNew: true });
      return;
    }

    if (tool === 'callout') {
      // Press on the thing being pointed at, release where the note goes.
      const size = pending.style.font?.size || 12;
      const lineHeight = size * LINE_HEIGHT;
      const tip = [pending.start.x, pending.start.y];
      const end = dragged ? pending.current : { x: pending.start.x + 70, y: pending.start.y - 50 };
      const toRight = end.x >= tip[0];
      const x = Math.max(2, Math.min(toRight ? end.x : end.x - 120, page.width - 60));
      const y = Math.max(2, end.y - lineHeight / 2 - 2);
      const box = [x, y, x + 120, y + lineHeight + 4];
      const midY = y + 2 + lineHeight / 2;
      const edge = toRight ? box[0] : box[2];
      const knee = [edge + (toRight ? -14 : 14), midY];
      const [created] = model.addAnnots([{
        // Grows to the right with its text; a box left of the tip keeps a set
        // width so the end of the leader line stays attached to it.
        ...base, type: 'freetext', rect: box, text: '', contents: '', autoWidth: toRight,
        callout: [tip, knee, [edge, midY]],
      }]);
      this._emit('edit-text', { id: created.id, isNew: true });
      this._emit('tool-done', { ids: [created.id] });
      return;
    }

    if (tool === 'stamp') {
      const index = pending.style.stampIndex ?? 0;
      if (index < 0) {
        // A custom wording has to be a text box: standard stamps can only say
        // what the spec says, so anything else would not survive saving.
        const size = pending.style.font?.size || 14;
        const x = dragged ? rect[0] : pending.start.x - 30;
        const y = dragged ? rect[1] : pending.start.y - size;
        const [created] = model.addAnnots([{
          ...base, type: 'freetext', rect: [x, y, x + 80, y + size * LINE_HEIGHT + 4],
          text: expandStamp(pending.style.stampText || '確認済'), tool: 'stamp', autoWidth: true,
          style: {
            ...pending.style, width: Math.max(1.5, pending.style.width || 2), fill: null,
            font: { ...pending.style.font, align: 'center', color: pending.style.stroke },
          },
        }]);
        created.contents = created.text;
        this._emit('refit', { ids: [created.id] });
        this._done([created.id]);
        return;
      }
      const box = dragged ? rect : [pending.start.x - 55, pending.start.y - 18, pending.start.x + 55, pending.start.y + 18];
      const [created] = model.addAnnots([{ ...base, type: 'stamp', rect: box, stampIndex: index }]);
      this._done([created.id]);
      return;
    }

    if (!dragged) {
      // A bare click with a shape tool: say what to do rather than nothing.
      this._emit('hint', { message: 'ドラッグして描いてください' });
      return;
    }

    let created;
    if (tool === 'line' || tool === 'arrow') {
      const points = [[pending.start.x, pending.start.y], [pending.current.x, pending.current.y]];
      [created] = model.addAnnots([{ ...base, type: 'line', points, rect: boundsOf(points, 10) }]);
    } else if (tool === 'redact') {
      [created] = model.addAnnots([{ ...base, type: 'redact', rect,
        quads: [[rect[0], rect[1], rect[2], rect[1], rect[0], rect[3], rect[2], rect[3]]] }], { select: false });
    } else {
      [created] = model.addAnnots([{ ...base, type: tool, rect }]);
    }
    this._done([created.id]);
    void event;
  }

  // -------------------------------------------------------------- polygons

  _addPolyPoint(view, point, event) {
    if (!this.poly || this.poly.view !== view) {
      this.cancelPoly();
      this.poly = { view, pts: [], style: this.style, node: null };
    }
    let p = point;
    if (event.shiftKey && this.poly.pts.length) {
      const last = this.poly.pts[this.poly.pts.length - 1];
      p = constrain({ x: last[0], y: last[1] }, point, true);
    }
    const last = this.poly.pts[this.poly.pts.length - 1];
    // The second press of a double-click lands on the same spot; skip it.
    if (last && Math.hypot(last[0] - p.x, last[1] - p.y) < 1.5) return;
    this.poly.pts.push([p.x, p.y]);
    this._drawPolyPreview();
    if (this.poly.pts.length === 1) this._emit('hint', { message: 'クリックで頂点を追加、ダブルクリックか Enter で確定、Esc で取り消し' });
  }

  _drawPolyPreview() {
    const poly = this.poly;
    if (!poly.node) {
      poly.node = el('path', { fill: 'none', stroke: poly.style.stroke,
        'stroke-width': poly.style.width || 1.5, 'stroke-dasharray': '4 3' });
      poly.view.draw.append(poly.node);
    }
    const closed = this.tool === 'polygon' && poly.pts.length > 2;
    const d = poly.style.cloudIntensity > 0 && poly.pts.length > 1
      ? cloudPath(poly.pts, poly.style.cloudIntensity, closed)
      : `M ${poly.pts.map((q) => q.join(' ')).join(' L ')}${closed ? ' Z' : ''}`;
    poly.node.setAttribute('d', d);
  }

  _finishPoly() {
    const poly = this.poly;
    if (!poly) return;
    this.poly = null;
    poly.node?.remove();
    const min = this.tool === 'polygon' ? 3 : 2;
    if (poly.pts.length < min) return;
    const [created] = model.addAnnots([{
      type: this.tool, page: poly.view.index, points: poly.pts,
      rect: boundsOf(poly.pts, poly.style.width), style: poly.style,
      author: getPref('author') || '', flags: FLAGS(),
    }]);
    this._done([created.id]);
  }

  cancelPoly() {
    if (this.poly) { this.poly.node?.remove(); this.poly = null; }
  }

  // -------------------------------------------------------------- measuring

  _addMeasurePoint(view, point, event) {
    const kind = MEASURE_KINDS[this.tool];
    if (!this.measuring || this.measuring.view !== view || this.measuring.kind !== kind) {
      this.cancelMeasure();
      this.measuring = { view, kind, pts: [], style: this.style, node: null, labelNode: null };
    }
    let p = point;
    if (event.shiftKey && this.measuring.pts.length) {
      const last = this.measuring.pts[this.measuring.pts.length - 1];
      p = constrain({ x: last[0], y: last[1] }, point, true);
    }
    const last = this.measuring.pts[this.measuring.pts.length - 1];
    if (last && Math.hypot(last[0] - p.x, last[1] - p.y) < 1.5) return;
    this.measuring.pts.push([p.x, p.y]);
    this._drawMeasurePreview();

    const needed = { distance: 2, angle: 3, radius: 2 }[kind];
    if (needed && this.measuring.pts.length >= needed) this.finishMeasure();
  }

  _drawMeasurePreview() {
    const m = this.measuring;
    if (!m.pts.length) return;
    const closed = m.kind === 'area' && m.pts.length > 2;
    if (!m.node) {
      m.node = el('path', {
        fill: 'none', stroke: m.style.stroke, 'stroke-width': m.style.width || 1.5,
        'stroke-dasharray': '5 3',
      });
      m.view.draw.append(m.node);
    }
    m.node.setAttribute('fill', closed ? `${m.style.stroke}22` : 'none');
    m.node.setAttribute('d', `M ${m.pts.map((q) => q.join(' ')).join(' L ')}${closed ? ' Z' : ''}`);

    if (m.pts.length >= 2) {
      const result = compute(m.kind, m.pts);
      const [lx, ly] = m.pts[m.pts.length - 1];
      if (!m.labelNode) {
        m.labelNode = el('text', {
          fill: m.style.stroke, 'font-size': 11, 'font-family': 'sans-serif',
          'paint-order': 'stroke', stroke: '#ffffff', 'stroke-width': 3,
        });
        m.view.draw.append(m.labelNode);
      }
      m.labelNode.setAttribute('x', lx + 6);
      m.labelNode.setAttribute('y', ly - 6);
      m.labelNode.textContent = result.label;
    }
  }

  /** Commit the measurement in progress; called on Enter or double-click too. */
  finishMeasure() {
    const m = this.measuring;
    if (!m) return;
    this.measuring = null;
    m.node?.remove();
    m.labelNode?.remove();
    const minimum = { area: 3, angle: 3 }[m.kind] || 2;
    if (m.pts.length < minimum) return;

    const result = compute(m.kind, m.pts);
    const closed = m.kind === 'area';
    model.addAnnots([{
      type: closed ? 'polygon' : 'polyline',
      page: m.view.index,
      points: m.pts,
      rect: boundsOf(m.pts, (m.style.width || 1.5) + 12),
      style: { ...m.style, fill: closed ? m.style.fill : null },
      measure: result,
      tool: m.kind,
      subject: this.subject || '',
      contents: result.label,
      author: getPref('author') || '',
      flags: FLAGS(),
    }], { select: false });
    this._emit('edited');
    this._emit('measured', result);
  }

  cancelMeasure() {
    if (!this.measuring) return;
    this.measuring.node?.remove();
    this.measuring.labelNode?.remove();
    this.measuring = null;
  }

  _addCount(view, point) {
    const style = this.style;
    const existing = model.store.annots.filter(
      (a) => a.tool === 'count' && (a.subject || '') === (this.subject || ''),
    );
    const number = existing.length + 1;
    const size = 9;
    model.addAnnots([{
      type: 'circle', page: view.index,
      rect: [point.x - size, point.y - size, point.x + size, point.y + size],
      style: { ...style, fill: style.fill || style.stroke },
      tool: 'count',
      subject: this.subject || '',
      // Sequence numbering: each placement is labelled as it is dropped.
      contents: `${this.subject || 'カウント'} ${number}`,
      label: String(number),
      author: getPref('author') || '',
      flags: FLAGS(),
    }], { select: false });
    this._emit('edited');
  }

  _addCalibrationPoint(view, point) {
    if (!this.calibrating || this.calibrating.view !== view) {
      this.calibrating?.node?.remove();
      this.calibrating = { view, pts: [], node: null };
    }
    this.calibrating.pts.push([point.x, point.y]);
    const c = this.calibrating;
    if (!c.node) {
      c.node = el('path', { fill: 'none', stroke: '#2f6df6', 'stroke-width': 1.5, 'stroke-dasharray': '5 3' });
      c.view.draw.append(c.node);
    }
    c.node.setAttribute('d', `M ${c.pts.map((q) => q.join(' ')).join(' L ')}`);
    if (c.pts.length === 2) {
      const pts = c.pts;
      c.node.remove();
      this.calibrating = null;
      this._emit('calibrated', { points: pts });
    }
  }

  // -------------------------------------------------------------- notes & marks

  _createNote(view, point) {
    const [created] = model.addAnnots([{
      type: 'note', page: view.index,
      rect: [point.x - 9, point.y - 9, point.x + 11, point.y + 11],
      contents: '', icon: 'Comment', style: this.style,
      author: getPref('author') || '',
      flags: FLAGS(),
    }]);
    this._emit('edit-text', { id: created.id, isNew: true });
    this._emit('tool-done', { ids: [created.id], keepEditing: true });
  }

  /** Tick, cross, ring or dot — the marks a paper form asks for. */
  _placeMark(view, point) {
    const style = this.style;
    const { x, y } = point;
    const base = { page: view.index, tool: 'mark', author: getPref('author') || '', flags: FLAGS() };
    const s = (style.markSize || 14) / 14;
    let annot;
    if (this.markKind === 'ring' || this.markKind === 'dot') {
      const r = (this.markKind === 'ring' ? 7.5 : 3.2) * s;
      annot = { ...base, type: 'circle', rect: [x - r, y - r, x + r, y + r],
        style: { ...style, fill: this.markKind === 'dot' ? style.stroke : null } };
    } else {
      const strokes = this.markKind === 'check'
        ? [[[x - 5.5 * s, y + 0.5 * s], [x - 1.5 * s, y + 4.8 * s], [x + 6 * s, y - 5 * s]]]
        : [[[x - 5 * s, y - 5 * s], [x + 5 * s, y + 5 * s]], [[x + 5 * s, y - 5 * s], [x - 5 * s, y + 5 * s]]];
      const all = strokes.flat();
      annot = { ...base, type: 'ink', rect: boundsOf(all, style.width || 1.8),
        strokes: strokes.map((pts) => ({ pts, pressure: null })), style: { ...style, fill: null } };
    }
    model.addAnnots([annot], { select: false });
    this._emit('edited');
  }

  // -------------------------------------------------------------- text markup

  /**
   * Turn the current text selection into a markup annotation.
   * With no argument it uses the active tool; the floating bar passes a kind.
   */
  markupSelection(kind = this.tool) {
    if (!MARKUP_TOOLS.has(kind) && kind !== 'redact') return false;
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return false;
    if (!selection.anchorNode?.parentElement?.closest('.text-layer')
      && !selection.focusNode?.parentElement?.closest('.text-layer')) return false;
    const quadsByPage = this._quadsFromSelection(selection);
    const quoted = selection.toString().trim();
    selection.removeAllRanges();
    if (!quadsByPage.size) return false;

    const style = styleFor(kind);
    const items = [];
    for (const [pageIndex, quads] of quadsByPage) {
      items.push({
        type: kind, page: pageIndex, quads,
        rect: boundsOfQuads(quads), style,
        subject: kind === 'redact' ? '' : quoted.slice(0, 200),
        author: getPref('author') || '',
        flags: FLAGS(),
      });
    }
    model.addAnnots(items, { select: false });
    this._emit('edited');
    return true;
  }

  _quadsFromSelection(selection) {
    const byPage = new Map();
    const s = this.viewer.scale;
    for (let i = 0; i < selection.rangeCount; i += 1) {
      for (const clientRect of selection.getRangeAt(i).getClientRects()) {
        if (clientRect.width < 0.5 || clientRect.height < 0.5) continue;
        const view = this._viewAtPoint(clientRect.left + clientRect.width / 2, clientRect.top + clientRect.height / 2);
        if (!view) continue;
        const box = view.wrap.getBoundingClientRect();
        // A rect as tall as the page is the layer itself, not a line of text.
        if (clientRect.height > box.height * 0.25 && clientRect.width > box.width * 0.6) continue;
        if (!byPage.has(view.index)) byPage.set(view.index, []);
        byPage.get(view.index).push([
          (clientRect.left - box.left) / s, (clientRect.top - box.top) / s,
          (clientRect.right - box.left) / s, (clientRect.bottom - box.top) / s,
        ]);
      }
    }
    // The browser reports one rectangle per text run, and they overlap. Left
    // as they are, a highlight would be darker wherever two runs meet.
    const out = new Map();
    for (const [index, rects] of byPage) {
      out.set(index, mergeLineRects(rects).map(([x0, y0, x1, y1]) => [x0, y0, x1, y0, x0, y1, x1, y1]));
    }
    return out;
  }

  _viewAtPoint(clientX, clientY) {
    for (const view of this.viewer.pageViews) {
      const box = view.wrap.getBoundingClientRect();
      if (clientX >= box.left && clientX <= box.right && clientY >= box.top && clientY <= box.bottom) {
        return view;
      }
    }
    return null;
  }
}

// ---------------------------------------------------------------- helpers

/** Join the per-run rectangles of a selection into one rectangle per line. */
function mergeLineRects(rects) {
  const sorted = [...rects].sort((a, b) => (a[1] + a[3]) / 2 - (b[1] + b[3]) / 2 || a[0] - b[0]);
  const lines = [];
  for (const rect of sorted) {
    const mid = (rect[1] + rect[3]) / 2;
    const line = lines.find((l) => mid > l.y0 && mid < l.y1
      && Math.min(l.y1, rect[3]) - Math.max(l.y0, rect[1]) > 0.5 * Math.min(l.y1 - l.y0, rect[3] - rect[1]));
    if (line) {
      line.items.push(rect);
    } else {
      lines.push({ y0: rect[1], y1: rect[3], items: [rect] });
    }
  }
  const out = [];
  for (const line of lines) {
    line.items.sort((a, b) => a[0] - b[0]);
    let current = null;
    for (const rect of line.items) {
      const gap = current ? rect[0] - current[2] : 0;
      if (current && gap < (current[3] - current[1]) * 0.9) {
        current[2] = Math.max(current[2], rect[2]);
        current[1] = Math.min(current[1], rect[1]);
        current[3] = Math.max(current[3], rect[3]);
      } else {
        if (current) out.push(current);
        current = [...rect];
      }
    }
    if (current) out.push(current);
  }
  return out;
}

/** Dynamic stamp: fill in who stamped it and when, at the moment of stamping. */
export function expandStamp(template) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return template
    .replace(/\{name\}/g, getPref('author') || '')
    .replace(/\{date\}/g, `${now.getFullYear()}/${pad(now.getMonth() + 1)}/${pad(now.getDate())}`)
    .replace(/\{time\}/g, `${pad(now.getHours())}:${pad(now.getMinutes())}`);
}

function normalisePressure(event, pending) {
  if (event.pointerType === 'pen' && event.pressure > 0) return event.pressure;
  if (event.pointerType === 'touch' && event.pressure > 0 && event.pressure !== 0.5) return event.pressure;
  // A mouse reports a flat 0.5. Derive a stand-in from stroke speed so the
  // line still tapers and mouse drawing feels like pen drawing.
  if (!pending) return 0.5;
  const now = performance.now();
  const dt = Math.max(1, now - pending.lastTime);
  const speed = Math.hypot(event.clientX - (pending.lastClientX ?? event.clientX),
    event.clientY - (pending.lastClientY ?? event.clientY)) / dt;
  pending.lastTime = now;
  pending.lastClientX = event.clientX;
  pending.lastClientY = event.clientY;
  const eased = Math.max(0.18, Math.min(1, 0.9 - speed * 0.22));
  const previous = pending.pressure[pending.pressure.length - 1] ?? 0.5;
  return previous * 0.65 + eased * 0.35;
}

function simplify(points, tolerance) {
  if (points.length < 3) return points;
  const out = [points[0]];
  for (let i = 1; i < points.length - 1; i += 1) {
    const [x, y] = points[i];
    const [px, py] = out[out.length - 1];
    if (Math.hypot(x - px, y - py) >= tolerance) out.push(points[i]);
  }
  out.push(points[points.length - 1]);
  return out;
}

function resample(values, fromLength, toLength) {
  if (!values || !values.length) return null;
  const out = [];
  for (let i = 0; i < toLength; i += 1) {
    const source = Math.round((i / Math.max(1, toLength - 1)) * (fromLength - 1));
    out.push(Number((values[Math.min(source, values.length - 1)] ?? 0.5).toFixed(3)));
  }
  return out;
}

export function boundsOf(points, pad = 0) {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
}

function boundsOfQuads(quads) {
  const pts = [];
  for (const q of quads) for (let i = 0; i < q.length; i += 2) pts.push([q[i], q[i + 1]]);
  return boundsOf(pts);
}

function normRect(x0, y0, x1, y1) {
  return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
}

function setRect(node, rect) {
  node.setAttribute('x', rect[0]);
  node.setAttribute('y', rect[1]);
  node.setAttribute('width', Math.max(0, rect[2] - rect[0]));
  node.setAttribute('height', Math.max(0, rect[3] - rect[1]));
}

function constrain(start, point, isLine) {
  const dx = point.x - start.x;
  const dy = point.y - start.y;
  if (!isLine) {
    const size = Math.max(Math.abs(dx), Math.abs(dy));
    return { x: start.x + Math.sign(dx) * size, y: start.y + Math.sign(dy) * size };
  }
  const step = Math.PI / 12; // 15 degree increments
  const angle = Math.round(Math.atan2(dy, dx) / step) * step;
  const length = Math.hypot(dx, dy);
  return { x: start.x + Math.cos(angle) * length, y: start.y + Math.sin(angle) * length };
}

export function translated(annot, dx, dy) {
  const patch = { rect: annot.rect.map((v, i) => v + (i % 2 ? dy : dx)) };
  if (annot.points) patch.points = annot.points.map(([x, y]) => [x + dx, y + dy]);
  if (annot.quads) patch.quads = annot.quads.map((q) => q.map((v, i) => v + (i % 2 ? dy : dx)));
  if (annot.strokes) {
    patch.strokes = annot.strokes.map((s) => ({ ...s, pts: s.pts.map(([x, y]) => [x + dx, y + dy]) }));
  }
  if (annot.callout) patch.callout = annot.callout.map(([x, y]) => [x + dx, y + dy]);
  return patch;
}

function scalePoints(points, from, to) {
  const sx = (to[2] - to[0]) / ((from[2] - from[0]) || 1);
  const sy = (to[3] - to[1]) / ((from[3] - from[1]) || 1);
  return points.map(([x, y]) => [to[0] + (x - from[0]) * sx, to[1] + (y - from[1]) * sy]);
}

function scaleQuad(quad, from, to) {
  const sx = (to[2] - to[0]) / ((from[2] - from[0]) || 1);
  const sy = (to[3] - to[1]) / ((from[3] - from[1]) || 1);
  return quad.map((v, i) => (i % 2 ? to[1] + (v - from[1]) * sy : to[0] + (v - from[0]) * sx));
}

function intersects(a, b) {
  return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
}

function centreOf(rect) {
  return [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2];
}

function pointInPolygon([x, y], polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
