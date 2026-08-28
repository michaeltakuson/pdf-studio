// Pointer handling for every annotation tool.
//
// Pen support is deliberate: pressure and tilt come from Pointer Events, palm
// contact is filtered out once a stylus has been seen, and two-finger gestures
// are left alone so the page still scrolls while a drawing tool is active.

import { el, strokePath, pressurePath, cloudPath } from './render.js';
import * as model from './model.js';
import { styleFor, getPref } from './defaults.js';
import { compute, MEASURE_KINDS } from './measure.js';

const MARKUP_TOOLS = new Set(['highlight', 'underline', 'squiggly', 'strikeout']);
const DRAG_SHAPES = new Set(['square', 'circle', 'line', 'areaHighlight', 'redact', 'stamp']);
const POLY_TOOLS = new Set(['polygon', 'polyline']);
const INK_TOOLS = new Set(['pen', 'marker']);
// Distance and angle are click-to-click; area closes a polygon like the
// polygon tool does.
const MEASURE_POINT_TOOLS = new Set([
  'measureDistance', 'measureArea', 'measureAngle', 'measurePerimeter', 'measureRadius',
]);

export class ToolController extends EventTarget {
  constructor(viewer) {
    super();
    this.viewer = viewer;
    this.tool = 'select';
    this.pending = null;
    this.sawPen = false;
    this._activePointers = new Set();

    const stage = viewer.stage;
    stage.addEventListener('pointerdown', (e) => this._onDown(e));
    stage.addEventListener('pointermove', (e) => this._onMove(e));
    stage.addEventListener('pointerup', (e) => this._onUp(e));
    stage.addEventListener('pointercancel', (e) => this._onUp(e));
    stage.addEventListener('dblclick', (e) => this._onDoubleClick(e));
    stage.addEventListener('mouseup', () => {
      if (MARKUP_TOOLS.has(this.tool)) setTimeout(() => this._commitTextMarkup(), 0);
    });
  }

  setTool(tool) {
    this._cancelPending();
    // Picking a drawing tool means "I am about to draw", so the property bar
    // should switch to that tool's defaults instead of staying on a selection.
    if (tool !== 'select' && tool !== 'pan' && model.store.selection.length) {
      model.select([]);
    }
    this.tool = tool;
    const textActive = MARKUP_TOOLS.has(tool) || tool === 'select';
    this.viewer.setTextLayerActive(textActive);
    this.viewer.setDrawActive(tool !== 'select');
    this.viewer.setCursor(
      tool === 'pan' ? 'grab'
        : INK_TOOLS.has(tool) || tool === 'eraser' ? 'pen'
          : MARKUP_TOOLS.has(tool) ? 'text'
            : tool === 'select' ? '' : 'cross',
    );
    this.dispatchEvent(new CustomEvent('tool', { detail: { tool } }));
  }

  get style() {
    return styleFor(this.tool);
  }

  // -------------------------------------------------------------- pointers

  _shouldIgnore(event) {
    if (event.pointerType === 'touch') {
      // Palm rejection: once a stylus is in use, ignore skin contact entirely.
      if (this.sawPen || getPref('penOnly')) return true;
      // Two fingers means the user wants to scroll, not draw.
      if (this._activePointers.size > 1) return true;
    }
    if (event.pointerType !== 'pen' && getPref('penOnly') && INK_TOOLS.has(this.tool)) return true;
    return false;
  }

  _onDown(event) {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    this._activePointers.add(event.pointerId);
    if (event.pointerType === 'pen') this.sawPen = true;
    if (this._shouldIgnore(event)) return;

    const view = this.viewer.viewFromEvent(event);
    if (!view) return;
    const point = this.viewer.toPageCoords(view, event);

    if (this.tool === 'pan') {
      this.pending = { kind: 'pan', startX: event.clientX, startY: event.clientY,
        scrollLeft: this.viewer.stage.scrollLeft, scrollTop: this.viewer.stage.scrollTop };
      this.viewer.stage.setPointerCapture?.(event.pointerId);
      return;
    }

    if (this.tool === 'select') { this._startSelect(view, point, event); return; }
    if (MARKUP_TOOLS.has(this.tool)) return;
    if (this.tool === 'note') { this._createNote(view, point); return; }
    if (this.tool === 'count') { this._addCount(view, point); return; }
    if (this.tool === 'calibrate') { this._addCalibrationPoint(view, point); return; }
    if (MEASURE_POINT_TOOLS.has(this.tool)) { this._addMeasurePoint(view, point, event); return; }
    if (POLY_TOOLS.has(this.tool)) { this._addPolyPoint(view, point, event); return; }

    event.preventDefault();
    // Capture keeps a fast stroke from escaping the page element. It throws if
    // the pointer has already been lifted, which a quick tap can do.
    try { view.draw.setPointerCapture(event.pointerId); } catch { /* pointer gone */ }

    if (INK_TOOLS.has(this.tool)) { this._startInk(view, point, event); return; }
    if (this.tool === 'eraser') { this.pending = { kind: 'erase', view, hits: new Set() }; this._erase(view, point); return; }
    if (this.tool === 'lasso') { this.pending = { kind: 'lasso', view, pts: [[point.x, point.y]], node: null }; return; }
    if (DRAG_SHAPES.has(this.tool)) { this._startShape(view, point); return; }
    if (this.tool === 'freetext' || this.tool === 'callout') { this._startShape(view, point); return; }
  }

  _onMove(event) {
    const pending = this.pending;
    if (!pending) return;
    if (this._shouldIgnore(event)) return;

    if (pending.kind === 'pan') {
      this.viewer.stage.scrollLeft = pending.scrollLeft - (event.clientX - pending.startX);
      this.viewer.stage.scrollTop = pending.scrollTop - (event.clientY - pending.startY);
      return;
    }

    const view = pending.view;
    if (!view) return;
    const point = this.viewer.toPageCoords(view, event);

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

  _onUp(event) {
    this._activePointers.delete(event.pointerId);
    const pending = this.pending;
    if (!pending) return;
    this.pending = null;

    switch (pending.kind) {
      case 'ink': this._finishInk(pending); break;
      case 'erase': this._finishErase(pending); break;
      case 'lasso': this._finishLasso(pending); break;
      case 'shape': this._finishShape(pending); break;
      case 'marquee': this._finishMarquee(pending); break;
      case 'move': case 'resize': this.dispatchEvent(new CustomEvent('edited')); break;
      default: break;
    }
    if (pending.node) pending.node.remove();
    if (pending.view) pending.view.draw.textContent = '';
  }

  _onDoubleClick(event) {
    if (this.measuring) { this.finishMeasure(); return; }
    if (POLY_TOOLS.has(this.tool) && this.poly) { this._finishPoly(); return; }
    if (this.tool !== 'select') return;
    const target = event.target.closest?.('[data-id]');
    if (!target) return;
    const annot = model.byId(target.dataset.id);
    if (annot && (annot.type === 'freetext' || annot.type === 'note')) {
      this.dispatchEvent(new CustomEvent('edit-text', { detail: { id: annot.id } }));
    }
  }

  _cancelPending() {
    if (this.pending?.view) this.pending.view.draw.textContent = '';
    this.pending = null;
    if (this.poly) { this.poly.view.draw.textContent = ''; this.poly = null; }
    this.cancelMeasure();
    if (this.calibrating) { this.calibrating.view.draw.textContent = ''; this.calibrating = null; }
  }

  // -------------------------------------------------------------- selection

  _startSelect(view, point, event) {
    const handle = event.target.closest?.('[data-handle]');
    if (handle) {
      const annot = model.byId(handle.dataset.id);
      if (!annot) return;
      this.pending = { kind: 'resize', view, annot, handle: handle.dataset.handle,
        origin: [...annot.rect], start: point, gesture: model.uid() };
      return;
    }
    const hit = event.target.closest?.('.annot');
    if (hit) {
      const id = hit.dataset.id;
      if (!model.store.selection.includes(id)) {
        model.select([id], { additive: event.shiftKey });
      }
      const annots = model.store.selection.map(model.byId).filter(Boolean);
      if (annots.some((a) => a.flags?.locked)) return;
      this.pending = { kind: 'move', view, start: point, gesture: model.uid(),
        origins: annots.map((a) => ({ id: a.id, snapshot: structuredClone(a) })) };
      return;
    }
    model.select([]);
    this.pending = { kind: 'marquee', view, start: point, node: null };
  }

  _updateMove(point) {
    const dx = point.x - this.pending.start.x;
    const dy = point.y - this.pending.start.y;
    for (const { id, snapshot } of this.pending.origins) {
      model.updateAnnots([id], translated(snapshot, dx, dy), { merge: this.pending.gesture });
    }
  }

  _updateResize(point, event) {
    const { annot, handle, origin, start } = this.pending;
    let [x0, y0, x1, y1] = origin;
    const dx = point.x - start.x;
    const dy = point.y - start.y;
    if (handle.includes('w')) x0 += dx;
    if (handle.includes('e')) x1 += dx;
    if (handle.includes('n')) y0 += dy;
    if (handle.includes('s')) y1 += dy;
    if (event.shiftKey && (x1 - x0) && (y1 - y0)) {
      const ratio = (origin[2] - origin[0]) / (origin[3] - origin[1]);
      y1 = y0 + (x1 - x0) / ratio;
    }
    const rect = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    const patch = { rect };
    if (annot.points) patch.points = scalePoints(annot.points, origin, rect);
    if (annot.strokes) {
      patch.strokes = annot.strokes.map((s) => ({ ...s, pts: scalePoints(s.pts, origin, rect) }));
    }
    if (annot.quads) patch.quads = annot.quads.map((q) => scaleQuad(q, origin, rect));
    model.updateAnnots([annot.id], patch, { merge: this.pending.gesture });
  }

  _updateMarquee(point) {
    const { start, view } = this.pending;
    const rect = normRect(start.x, start.y, point.x, point.y);
    if (!this.pending.node) {
      this.pending.node = el('rect', { class: 'sel-box' });
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
      node: el('path', { fill: 'none', stroke: style.stroke, 'stroke-width': style.width,
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
    if (pending.pts.length < 2) return;
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
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    }], { select: false });
    this.dispatchEvent(new CustomEvent('edited'));
  }

  // -------------------------------------------------------------- eraser

  _erase(view, point) {
    for (const annot of model.onPage(view.index)) {
      if (annot.type !== 'ink') continue;
      if (annot.flags?.locked) continue;
      for (const stroke of annot.strokes || []) {
        if (stroke.pts.some((p) => Math.hypot(p[0] - point.x, p[1] - point.y) < 6)) {
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
    this.dispatchEvent(new CustomEvent('edited'));
  }

  // -------------------------------------------------------------- lasso

  _extendLasso(point) {
    const p = this.pending;
    p.pts.push([point.x, point.y]);
    if (!p.node) {
      p.node = el('path', { fill: 'rgba(80,140,255,.12)', stroke: '#4d8dff',
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
    model.select(ids);
  }

  // -------------------------------------------------------------- shapes

  _startShape(view, point) {
    const style = this.style;
    this.pending = { kind: 'shape', view, style, start: point, current: point, node: null };
  }

  _updateShape(point, event) {
    const p = this.pending;
    let end = point;
    if (event.shiftKey) end = constrain(p.start, point, this.tool === 'line');
    p.current = end;
    const rect = normRect(p.start.x, p.start.y, end.x, end.y);
    p.rect = rect;

    if (!p.node) {
      const tag = this.tool === 'circle' ? 'ellipse' : this.tool === 'line' ? 'line' : 'rect';
      p.node = el(tag, {
        fill: this.tool === 'areaHighlight' || this.tool === 'redact'
          ? (p.style.fill || p.style.stroke) : (p.style.fill || 'none'),
        stroke: p.style.stroke,
        'stroke-width': p.style.width || 1,
        opacity: p.style.opacity ?? 1,
      });
      p.view.draw.append(p.node);
    }
    if (this.tool === 'line') {
      p.node.setAttribute('x1', p.start.x); p.node.setAttribute('y1', p.start.y);
      p.node.setAttribute('x2', end.x); p.node.setAttribute('y2', end.y);
    } else if (this.tool === 'circle') {
      p.node.setAttribute('cx', (rect[0] + rect[2]) / 2);
      p.node.setAttribute('cy', (rect[1] + rect[3]) / 2);
      p.node.setAttribute('rx', (rect[2] - rect[0]) / 2);
      p.node.setAttribute('ry', (rect[3] - rect[1]) / 2);
    } else {
      setRect(p.node, rect);
    }
  }

  _finishShape(pending) {
    const rect = pending.rect;
    const tool = this.tool;
    if (!rect) return;
    const tiny = rect[2] - rect[0] < 3 && rect[3] - rect[1] < 3;

    const base = {
      page: pending.view.index,
      style: pending.style,
      author: getPref('author') || '',
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    };

    if (tool === 'freetext' || tool === 'callout') {
      const box = tiny ? [rect[0], rect[1], rect[0] + 160, rect[1] + 24] : rect;
      const annot = {
        ...base, type: 'freetext', rect: box, text: '',
        callout: tool === 'callout'
          ? [[box[0] - 60, box[3] + 40], [box[0] - 25, box[3] + 15], [box[0], box[3]]]
          : null,
      };
      const [created] = model.addAnnots([annot]);
      this.dispatchEvent(new CustomEvent('edit-text', { detail: { id: created.id, isNew: true } }));
      return;
    }
    if (tiny) return;

    if (tool === 'line') {
      model.addAnnots([{ ...base, type: 'line',
        points: [[pending.start.x, pending.start.y], [pending.current.x, pending.current.y]],
        rect: boundsOf([[pending.start.x, pending.start.y], [pending.current.x, pending.current.y]], 10) }]);
    } else if (tool === 'stamp') {
      const index = pending.style.stampIndex ?? 0;
      if (index >= 0) {
        model.addAnnots([{ ...base, type: 'stamp', rect, stampIndex: index }]);
      } else {
        // A custom wording has to be a FreeText: standard stamps can only say
        // what the spec says, so anything else would not survive saving.
        model.addAnnots([{
          ...base, type: 'freetext', rect,
          text: expandStamp(pending.style.stampText || '確認済'),
          tool: 'stamp',
          style: {
            ...pending.style,
            width: Math.max(1.5, pending.style.width || 2),
            fill: null,
            font: { ...pending.style.font, align: 'center', color: pending.style.stroke },
          },
        }]);
      }
    } else if (tool === 'redact') {
      model.addAnnots([{ ...base, type: 'redact', rect,
        quads: [[rect[0], rect[1], rect[2], rect[1], rect[0], rect[3], rect[2], rect[3]]] }]);
    } else {
      model.addAnnots([{ ...base, type: tool, rect }]);
    }
    this.dispatchEvent(new CustomEvent('edited'));
  }

  // -------------------------------------------------------------- polygons

  _addPolyPoint(view, point, event) {
    if (!this.poly || this.poly.view !== view) {
      this.poly = { view, pts: [], style: this.style, node: null };
    }
    let p = point;
    if (event.shiftKey && this.poly.pts.length) {
      const last = this.poly.pts[this.poly.pts.length - 1];
      p = constrain({ x: last[0], y: last[1] }, point, true);
    }
    this.poly.pts.push([p.x, p.y]);
    this._drawPolyPreview();
  }

  _drawPolyPreview() {
    const poly = this.poly;
    if (!poly.node) {
      poly.node = el('path', { fill: 'none', stroke: poly.style.stroke,
        'stroke-width': poly.style.width || 1.5, 'stroke-dasharray': '4 3' });
      poly.view.draw.append(poly.node);
    }
    const closed = this.tool === 'polygon' && poly.pts.length > 2;
    const d = poly.style.cloudIntensity > 0
      ? cloudPath(poly.pts, poly.style.cloudIntensity, closed)
      : `M ${poly.pts.map((q) => q.join(' ')).join(' L ')}${closed ? ' Z' : ''}`;
    poly.node.setAttribute('d', d);
  }

  _finishPoly() {
    const poly = this.poly;
    this.poly = null;
    poly.view.draw.textContent = '';
    const min = this.tool === 'polygon' ? 3 : 2;
    if (poly.pts.length < min) return;
    model.addAnnots([{
      type: this.tool, page: poly.view.index, points: poly.pts,
      rect: boundsOf(poly.pts, poly.style.width), style: poly.style,
      author: getPref('author') || '',
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    }]);
    this.dispatchEvent(new CustomEvent('edited'));
  }

  cancelPoly() {
    if (this.poly) { this.poly.view.draw.textContent = ''; this.poly = null; }
  }

  // -------------------------------------------------------------- measuring

  _addMeasurePoint(view, point, event) {
    const kind = MEASURE_KINDS[this.tool];
    if (!this.measuring || this.measuring.view !== view || this.measuring.kind !== kind) {
      this.measuring = { view, kind, pts: [], style: this.style, node: null, labelNode: null };
    }
    let p = point;
    if (event.shiftKey && this.measuring.pts.length) {
      const last = this.measuring.pts[this.measuring.pts.length - 1];
      p = constrain({ x: last[0], y: last[1] }, point, true);
    }
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
        fill: closed ? `${m.style.stroke}22` : 'none',
        stroke: m.style.stroke, 'stroke-width': m.style.width || 1.5,
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
    m.view.draw.textContent = '';
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
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    }]);
    this.dispatchEvent(new CustomEvent('edited'));
    this.dispatchEvent(new CustomEvent('measured', { detail: result }));
  }

  cancelMeasure() {
    if (!this.measuring) return;
    this.measuring.view.draw.textContent = '';
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
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    }], { select: false });
    this.dispatchEvent(new CustomEvent('edited'));
  }

  _addCalibrationPoint(view, point) {
    if (!this.calibrating || this.calibrating.view !== view) {
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
      c.view.draw.textContent = '';
      this.calibrating = null;
      this.dispatchEvent(new CustomEvent('calibrated', { detail: { points: pts } }));
    }
  }

  // -------------------------------------------------------------- notes

  _createNote(view, point) {
    const [created] = model.addAnnots([{
      type: 'note', page: view.index,
      rect: [point.x, point.y, point.x + 20, point.y + 20],
      contents: '', icon: 'Comment', style: this.style,
      author: getPref('author') || '',
      flags: { print: true, locked: false, readOnly: false, hidden: false },
    }]);
    this.dispatchEvent(new CustomEvent('edit-text', { detail: { id: created.id, isNew: true } }));
  }

  // -------------------------------------------------------------- text markup

  _commitTextMarkup() {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const quadsByPage = this._quadsFromSelection(selection);
    selection.removeAllRanges();
    if (!quadsByPage.size) return;

    const style = this.style;
    const items = [];
    for (const [pageIndex, quads] of quadsByPage) {
      items.push({
        type: this.tool, page: pageIndex, quads,
        rect: boundsOfQuads(quads), style,
        author: getPref('author') || '',
        flags: { print: true, locked: false, readOnly: false, hidden: false },
      });
    }
    model.addAnnots(items, { select: false });
    this.dispatchEvent(new CustomEvent('edited'));
  }

  _quadsFromSelection(selection) {
    const byPage = new Map();
    for (let i = 0; i < selection.rangeCount; i += 1) {
      for (const clientRect of selection.getRangeAt(i).getClientRects()) {
        if (clientRect.width < 0.5 || clientRect.height < 0.5) continue;
        const view = this._viewAtPoint(clientRect.left + 1, clientRect.top + 1);
        if (!view) continue;
        const box = view.wrap.getBoundingClientRect();
        const s = this.viewer.scale;
        const x0 = (clientRect.left - box.left) / s;
        const y0 = (clientRect.top - box.top) / s;
        const x1 = (clientRect.right - box.left) / s;
        const y1 = (clientRect.bottom - box.top) / s;
        if (!byPage.has(view.index)) byPage.set(view.index, []);
        byPage.get(view.index).push([x0, y0, x1, y0, x0, y1, x1, y1]);
      }
    }
    return byPage;
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

/** Dynamic stamp: fill in who stamped it and when, at the moment of stamping. */
function expandStamp(template) {
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
  const last = pending.pts[pending.pts.length - 1] || [event.clientX, event.clientY];
  const speed = Math.hypot(event.clientX - (pending.lastClientX ?? event.clientX),
    event.clientY - (pending.lastClientY ?? event.clientY)) / dt;
  pending.lastTime = now;
  pending.lastClientX = event.clientX;
  pending.lastClientY = event.clientY;
  void last;
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

function translated(annot, dx, dy) {
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
