// Draws the annotation model into each page's SVG overlay.
// Everything here works in unscaled PDF points; the SVG viewBox does the zoom.

const NS = 'http://www.w3.org/2000/svg';

export function el(name, attrs = {}, children = []) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function quadRects(annot) {
  return (annot.quads || []).map((q) => {
    const xs = [q[0], q[2], q[4], q[6]];
    const ys = [q[1], q[3], q[5], q[7]];
    return {
      x: Math.min(...xs),
      y: Math.min(...ys),
      w: Math.max(...xs) - Math.min(...xs),
      h: Math.max(...ys) - Math.min(...ys),
    };
  });
}

/** Scalloped "cloud" border, the standard revision-marking notation. */
export function cloudPath(points, intensity = 1, close = true) {
  if (points.length < 2) return '';
  const radius = 3 + intensity * 2.5;
  const parts = [];
  const list = close ? [...points, points[0]] : points;
  parts.push(`M ${list[0][0]} ${list[0][1]}`);
  for (let i = 1; i < list.length; i += 1) {
    const [x0, y0] = list[i - 1];
    const [x1, y1] = list[i];
    const length = Math.hypot(x1 - x0, y1 - y0);
    const count = Math.max(1, Math.round(length / (radius * 1.7)));
    for (let s = 1; s <= count; s += 1) {
      const t = s / count;
      parts.push(`A ${radius} ${radius} 0 0 1 ${x0 + (x1 - x0) * t} ${y0 + (y1 - y0) * t}`);
    }
  }
  if (close) parts.push('Z');
  return parts.join(' ');
}

function rectPoints(r) {
  const [x0, y0, x1, y1] = r;
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

function dashArray(style) {
  if (style.borderStyle === 'dashed' || (style.dash && style.dash.length)) {
    return (style.dash && style.dash.length ? style.dash : [4, 3]).join(' ');
  }
  return null;
}

/** Smooth a stroke with a Catmull-Rom style curve so pen input looks natural. */
export function strokePath(points) {
  if (points.length < 2) return '';
  if (points.length === 2) return `M ${points[0][0]} ${points[0][1]} L ${points[1][0]} ${points[1][1]}`;
  const parts = [`M ${points[0][0]} ${points[0][1]}`];
  for (let i = 1; i < points.length - 1; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    parts.push(`Q ${x0} ${y0} ${(x0 + x1) / 2} ${(y0 + y1) / 2}`);
  }
  const last = points[points.length - 1];
  parts.push(`L ${last[0]} ${last[1]}`);
  return parts.join(' ');
}

/** Outline of a pressure-varying stroke, drawn as a filled shape. */
export function pressurePath(points, pressures, baseWidth) {
  if (points.length < 2) return '';
  const left = [];
  const right = [];
  for (let i = 0; i < points.length; i += 1) {
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(points.length - 1, i + 1)];
    let dx = next[0] - prev[0];
    let dy = next[1] - prev[1];
    const len = Math.hypot(dx, dy) || 1;
    dx /= len; dy /= len;
    const w = (baseWidth * (0.35 + 1.3 * (pressures?.[i] ?? 0.5))) / 2;
    left.push([points[i][0] - dy * w, points[i][1] + dx * w]);
    right.push([points[i][0] + dy * w, points[i][1] - dx * w]);
  }
  right.reverse();
  const seg = (list) => list.map((p, i) => `${i ? 'L' : ''} ${p[0]} ${p[1]}`).join(' ');
  return `M ${seg(left).slice(1)} L ${seg(right).slice(1)} Z`;
}

const ARROW_SIZE = 8;

function arrowHead(kind, tip, from, style) {
  if (!kind || kind === 'none') return null;
  const angle = Math.atan2(tip[1] - from[1], tip[0] - from[0]);
  const size = ARROW_SIZE + style.width;
  const common = { fill: style.stroke, stroke: style.stroke, 'stroke-width': style.width };
  const pt = (dist, spread) => [
    tip[0] - Math.cos(angle - spread) * dist,
    tip[1] - Math.sin(angle - spread) * dist,
  ];
  switch (kind) {
    case 'openArrow': {
      const a = pt(size, 0.45); const b = pt(size, -0.45);
      return el('path', { d: `M ${a[0]} ${a[1]} L ${tip[0]} ${tip[1]} L ${b[0]} ${b[1]}`, fill: 'none', stroke: style.stroke, 'stroke-width': style.width, 'stroke-linecap': 'round' });
    }
    case 'closedArrow': {
      const a = pt(size, 0.42); const b = pt(size, -0.42);
      return el('path', { d: `M ${tip[0]} ${tip[1]} L ${a[0]} ${a[1]} L ${b[0]} ${b[1]} Z`, ...common });
    }
    case 'rOpenArrow': {
      const a = [tip[0] + Math.cos(angle - 0.45) * size, tip[1] + Math.sin(angle - 0.45) * size];
      const b = [tip[0] + Math.cos(angle + 0.45) * size, tip[1] + Math.sin(angle + 0.45) * size];
      return el('path', { d: `M ${a[0]} ${a[1]} L ${tip[0]} ${tip[1]} L ${b[0]} ${b[1]}`, fill: 'none', stroke: style.stroke, 'stroke-width': style.width });
    }
    case 'rClosedArrow': {
      const a = [tip[0] + Math.cos(angle - 0.42) * size, tip[1] + Math.sin(angle - 0.42) * size];
      const b = [tip[0] + Math.cos(angle + 0.42) * size, tip[1] + Math.sin(angle + 0.42) * size];
      return el('path', { d: `M ${tip[0]} ${tip[1]} L ${a[0]} ${a[1]} L ${b[0]} ${b[1]} Z`, ...common });
    }
    case 'circle':
      return el('circle', { cx: tip[0], cy: tip[1], r: size / 2, ...common });
    case 'square':
      return el('rect', { x: tip[0] - size / 2, y: tip[1] - size / 2, width: size, height: size, ...common });
    case 'diamond':
      return el('path', { d: `M ${tip[0]} ${tip[1] - size / 2} L ${tip[0] + size / 2} ${tip[1]} L ${tip[0]} ${tip[1] + size / 2} L ${tip[0] - size / 2} ${tip[1]} Z`, ...common });
    case 'slash': {
      const a = [tip[0] + Math.cos(angle + 1.05) * size / 2, tip[1] + Math.sin(angle + 1.05) * size / 2];
      const b = [tip[0] - Math.cos(angle + 1.05) * size / 2, tip[1] - Math.sin(angle + 1.05) * size / 2];
      return el('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: style.stroke, 'stroke-width': style.width });
    }
    case 'butt': {
      const a = [tip[0] + Math.cos(angle + Math.PI / 2) * size / 2, tip[1] + Math.sin(angle + Math.PI / 2) * size / 2];
      const b = [tip[0] - Math.cos(angle + Math.PI / 2) * size / 2, tip[1] - Math.sin(angle + Math.PI / 2) * size / 2];
      return el('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: style.stroke, 'stroke-width': style.width });
    }
    default:
      return null;
  }
}

const NOTE_ICON_PATH = 'M2 2 h18 a2 2 0 0 1 2 2 v11 a2 2 0 0 1 -2 2 h-9 l-5 5 v-5 h-4 a2 2 0 0 1 -2 -2 v-11 a2 2 0 0 1 2 -2 z';

// The wording the PDF spec fixes for each standard stamp. Viewers draw these,
// so the app must show the same words rather than a label of its own.
export const STANDARD_STAMPS = [
  'APPROVED', 'AS IS', 'CONFIDENTIAL', 'DEPARTMENTAL', 'EXPERIMENTAL',
  'EXPIRED', 'FINAL', 'FOR COMMENT', 'FOR PUBLIC RELEASE', 'NOT APPROVED',
  'NOT FOR PUBLIC RELEASE', 'SOLD', 'TOP SECRET', 'DRAFT',
];

export function renderAnnot(annot) {
  const style = annot.style || {};
  const group = el('g', {
    'data-id': annot.id,
    class: 'annot hit',
    opacity: annot.flags?.hidden ? 0.15 : 1,
  });

  const strokeAttrs = {
    stroke: style.stroke || '#000',
    'stroke-width': style.width ?? 1,
    'stroke-dasharray': dashArray(style),
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    fill: 'none',
    opacity: style.opacity ?? 1,
  };

  switch (annot.type) {
    case 'highlight':
      for (const r of quadRects(annot)) {
        group.append(el('rect', {
          x: r.x, y: r.y, width: r.w, height: r.h,
          fill: style.stroke, opacity: style.opacity ?? 0.45,
          style: 'mix-blend-mode:multiply',
        }));
      }
      break;

    case 'areaHighlight':
      group.append(el('rect', {
        x: annot.rect[0], y: annot.rect[1],
        width: annot.rect[2] - annot.rect[0], height: annot.rect[3] - annot.rect[1],
        fill: style.fill || style.stroke, opacity: style.opacity ?? 0.35,
        style: 'mix-blend-mode:multiply',
      }));
      break;

    case 'underline':
      for (const r of quadRects(annot)) {
        group.append(el('line', {
          x1: r.x, y1: r.y + r.h - 1, x2: r.x + r.w, y2: r.y + r.h - 1, ...strokeAttrs,
        }));
      }
      break;

    case 'strikeout':
      for (const r of quadRects(annot)) {
        group.append(el('line', {
          x1: r.x, y1: r.y + r.h / 2, x2: r.x + r.w, y2: r.y + r.h / 2, ...strokeAttrs,
        }));
      }
      break;

    case 'squiggly':
      for (const r of quadRects(annot)) {
        const amp = 1.6;
        const step = 3;
        const baseY = r.y + r.h - amp;
        let d = `M ${r.x} ${baseY}`;
        for (let x = r.x; x < r.x + r.w; x += step) {
          d += ` Q ${x + step / 2} ${baseY + (((x - r.x) / step) % 2 ? amp : -amp) * 2} ${Math.min(x + step, r.x + r.w)} ${baseY}`;
        }
        group.append(el('path', { d, ...strokeAttrs }));
      }
      break;

    case 'square': {
      const [x0, y0, x1, y1] = annot.rect;
      if (style.cloudIntensity > 0) {
        group.append(el('path', {
          d: cloudPath(rectPoints(annot.rect), style.cloudIntensity),
          ...strokeAttrs, fill: style.fill || 'none',
        }));
      } else {
        group.append(el('rect', {
          x: x0, y: y0, width: x1 - x0, height: y1 - y0,
          ...strokeAttrs, fill: style.fill || 'none',
        }));
      }
      break;
    }

    case 'circle': {
      const [x0, y0, x1, y1] = annot.rect;
      group.append(el('ellipse', {
        cx: (x0 + x1) / 2, cy: (y0 + y1) / 2,
        rx: Math.abs(x1 - x0) / 2, ry: Math.abs(y1 - y0) / 2,
        ...strokeAttrs, fill: style.fill || 'none',
      }));
      break;
    }

    case 'line': {
      const pts = annot.points || [];
      if (pts.length >= 2) {
        group.append(el('line', {
          x1: pts[0][0], y1: pts[0][1], x2: pts[1][0], y2: pts[1][1], ...strokeAttrs,
        }));
        const ends = style.lineEnds || ['none', 'none'];
        const head1 = arrowHead(ends[0], pts[0], pts[1], style);
        const head2 = arrowHead(ends[1], pts[1], pts[0], style);
        if (head1) group.append(head1);
        if (head2) group.append(head2);
      }
      break;
    }

    case 'polygon':
    case 'polyline': {
      const pts = annot.points || [];
      if (pts.length >= 2) {
        if (style.cloudIntensity > 0) {
          group.append(el('path', {
            d: cloudPath(pts, style.cloudIntensity, annot.type === 'polygon'),
            ...strokeAttrs, fill: annot.type === 'polygon' ? (style.fill || 'none') : 'none',
          }));
        } else {
          group.append(el(annot.type === 'polygon' ? 'polygon' : 'polyline', {
            points: pts.map((p) => p.join(',')).join(' '),
            ...strokeAttrs, fill: annot.type === 'polygon' ? (style.fill || 'none') : 'none',
          }));
        }
      }
      break;
    }

    case 'ink':
      for (const stroke of annot.strokes || []) {
        const pts = stroke.pts || [];
        if (pts.length < 2) continue;
        if (stroke.pressure && stroke.pressure.length === pts.length) {
          group.append(el('path', {
            d: pressurePath(pts, stroke.pressure, style.width ?? 2),
            fill: style.stroke, opacity: style.opacity ?? 1,
            style: (style.opacity ?? 1) < 1 ? 'mix-blend-mode:multiply' : null,
          }));
        } else {
          group.append(el('path', {
            d: strokePath(pts),
            ...strokeAttrs,
            'stroke-width': style.width ?? 2,
            style: (style.opacity ?? 1) < 1 ? 'mix-blend-mode:multiply' : null,
          }));
        }
      }
      break;

    case 'freetext': {
      const [x0, y0, x1, y1] = annot.rect;
      const font = style.font || {};
      // Viewers draw a FreeText's border and callout line in its text colour
      // (the /DA colour), so drawing them any other way here would show the
      // user something the saved file will not reproduce.
      const ink = font.color || '#000000';
      if (annot.callout && annot.callout.length >= 2) {
        group.append(el('polyline', {
          points: annot.callout.map((p) => p.join(',')).join(' '),
          fill: 'none', stroke: ink, 'stroke-width': style.width || 1,
        }));
        const head = arrowHead('openArrow', annot.callout[0], annot.callout[1], {
          stroke: ink, width: style.width || 1,
        });
        if (head) group.append(head);
      }
      if (style.fill || style.width) {
        group.append(el('rect', {
          x: x0, y: y0, width: x1 - x0, height: y1 - y0,
          fill: style.fill || 'none',
          stroke: style.width ? ink : 'none',
          'stroke-width': style.width || 0,
          'stroke-dasharray': dashArray(style),
          opacity: style.opacity ?? 1,
        }));
      }
      const fo = el('foreignObject', {
        x: x0 + 2, y: y0 + 1, width: Math.max(4, x1 - x0 - 4), height: Math.max(4, y1 - y0 - 2),
      });
      const div = document.createElement('div');
      div.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
      div.style.cssText = `font-family:"Yu Gothic UI","Hiragino Sans",sans-serif;font-size:${font.size || 12}px;line-height:1.25;color:${font.color || '#000'};text-align:${font.align || 'left'};white-space:pre-wrap;word-break:break-word;overflow:hidden;`;
      div.textContent = annot.text || annot.contents || '';
      fo.append(div);
      group.append(fo);
      break;
    }

    case 'note': {
      const [x0, y0] = annot.rect;
      const icon = el('g', { transform: `translate(${x0},${y0}) scale(0.85)` });
      icon.append(el('path', {
        d: NOTE_ICON_PATH,
        fill: style.stroke || '#ffd23d',
        stroke: '#0006',
        'stroke-width': 0.8,
      }));
      group.append(icon);
      break;
    }

    case 'stamp': {
      // Standard stamps carry fixed spec-defined wording; showing anything
      // else here would not match what the saved PDF displays.
      const [x0, y0, x1, y1] = annot.rect;
      const label = STANDARD_STAMPS[annot.stampIndex ?? 0] || 'APPROVED';
      group.append(el('rect', {
        x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: 4,
        fill: 'none', stroke: style.stroke, 'stroke-width': Math.max(1.5, style.width || 2),
        opacity: style.opacity ?? 1,
      }));
      const text = el('text', {
        x: (x0 + x1) / 2, y: (y0 + y1) / 2,
        'text-anchor': 'middle', 'dominant-baseline': 'central',
        fill: style.stroke,
        'font-size': Math.min((y1 - y0) * 0.55, ((x1 - x0) * 1.5) / Math.max(4, label.length)),
        'font-family': 'Georgia, serif', 'font-weight': 700,
        opacity: style.opacity ?? 1,
      });
      text.textContent = label;
      group.append(text);
      break;
    }

    case 'redact': {
      const rects = annot.quads?.length ? quadRects(annot) : [{
        x: annot.rect[0], y: annot.rect[1],
        w: annot.rect[2] - annot.rect[0], h: annot.rect[3] - annot.rect[1],
      }];
      for (const r of rects) {
        group.append(el('rect', {
          x: r.x, y: r.y, width: r.w, height: r.h,
          fill: style.fill || '#000', stroke: '#e0403a', 'stroke-width': 1,
          'stroke-dasharray': '3 2',
        }));
      }
      if (annot.overlayText) {
        const r = rects[0];
        const text = el('text', {
          x: r.x + 3, y: r.y + r.h / 2, 'dominant-baseline': 'central',
          fill: style.font?.color || '#fff', 'font-size': style.font?.size || 9,
        });
        text.textContent = annot.overlayText;
        group.append(text);
      }
      break;
    }

    case 'caret':
      group.append(el('path', {
        d: `M ${annot.rect[0]} ${annot.rect[3]} l 4 -8 l 4 8 Z`,
        fill: style.stroke, opacity: style.opacity ?? 1,
      }));
      break;

    default:
      break;
  }

  // Measurements and counts carry their value on the page, the way a marked-up
  // drawing does — the number is the point of the mark.
  if (annot.measure) {
    const anchor = (annot.points || [])[annot.points.length - 1] || [annot.rect[0], annot.rect[1]];
    const label = el('text', {
      x: anchor[0] + 6, y: anchor[1] - 6,
      fill: style.stroke, 'font-size': 10.5, 'font-family': 'sans-serif',
      'paint-order': 'stroke', stroke: '#ffffff', 'stroke-width': 3,
      'stroke-linejoin': 'round',
    });
    label.textContent = annot.measure.label;
    group.append(label);
  } else if (annot.tool === 'count' && annot.label) {
    const [x0, y0, x1, y1] = annot.rect;
    const number = el('text', {
      x: (x0 + x1) / 2, y: (y0 + y1) / 2,
      'text-anchor': 'middle', 'dominant-baseline': 'central',
      fill: '#ffffff', 'font-size': Math.min(11, (y1 - y0) * 0.75),
      'font-family': 'sans-serif', 'font-weight': 700,
    });
    number.textContent = annot.label;
    group.append(number);
  }

  // A transparent hit target keeps thin strokes clickable.
  const [hx0, hy0, hx1, hy1] = annot.rect || [0, 0, 0, 0];
  group.prepend(el('rect', {
    x: hx0 - 3, y: hy0 - 3, width: Math.max(6, hx1 - hx0 + 6), height: Math.max(6, hy1 - hy0 + 6),
    fill: 'transparent', class: 'hit-area',
  }));

  return group;
}

// A note is a fixed-size icon: resizing it means nothing, so it gets an
// outline and no handles.
const FIXED_SIZE = new Set(['note', 'caret']);

export function selectionOverlay(annot) {
  const [x0, y0, x1, y1] = annot.rect;
  const group = el('g', { class: 'selection', 'data-id': annot.id });
  group.append(el('rect', {
    class: 'sel-box', x: x0 - 2, y: y0 - 2, width: x1 - x0 + 4, height: y1 - y0 + 4,
  }));
  if (FIXED_SIZE.has(annot.type)) return group;
  const handles = [
    ['nw', x0, y0], ['n', (x0 + x1) / 2, y0], ['ne', x1, y0],
    ['e', x1, (y0 + y1) / 2], ['se', x1, y1], ['s', (x0 + x1) / 2, y1],
    ['sw', x0, y1], ['w', x0, (y0 + y1) / 2],
  ];
  for (const [name, hx, hy] of handles) {
    group.append(el('rect', {
      class: 'handle hit', 'data-handle': name, 'data-id': annot.id,
      x: hx - 3.5, y: hy - 3.5, width: 7, height: 7, rx: 1.5,
    }));
  }
  return group;
}

export function renderPage(view, annots, selectedIds) {
  view.svg.textContent = '';
  const selected = new Set(selectedIds);
  for (const annot of annots) view.svg.append(renderAnnot(annot));
  for (const annot of annots) {
    if (selected.has(annot.id)) view.svg.append(selectionOverlay(annot));
  }
}
