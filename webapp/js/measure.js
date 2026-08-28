// Measuring and counting, in the browser.
//
// The same maths runs on the server for take-off totals; here it runs live so
// the value follows the cursor while the user is still drawing.

const UNITS = { pt: 25.4 / 72, mm: 1, cm: 10, m: 1000, in: 25.4, ft: 304.8 };
const AREA_UNITS = { mm: 'mm²', cm: 'cm²', m: 'm²', in: 'in²', ft: 'ft²', pt: 'pt²' };

const KEY = 'pdfstudio.scale.v1';

const DEFAULT_SCALE = { pagePoints: 1, realLength: 1, unit: 'mm' };

let scale;
try {
  scale = { ...DEFAULT_SCALE, ...JSON.parse(localStorage.getItem(KEY) || '{}') };
} catch { scale = { ...DEFAULT_SCALE }; }

export function getScale() {
  return { ...scale };
}

export function setScale(next) {
  scale = { ...scale, ...next };
  try { localStorage.setItem(KEY, JSON.stringify(scale)); } catch { /* private mode */ }
}

/** Is the scale still the untouched 1pt = 1mm placeholder? */
export function isCalibrated() {
  return !(scale.pagePoints === 1 && scale.realLength === 1);
}

export function factor() {
  return scale.pagePoints > 0 ? scale.realLength / scale.pagePoints : 1;
}

export function calibrateFrom(p1, p2, realLength, unit) {
  const points = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
  setScale({ pagePoints: points, realLength, unit });
  return getScale();
}

function pathLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  }
  return total;
}

function polygonArea(points) {
  if (points.length < 3) return 0;
  let total = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    total += x0 * y1 - x1 * y0;
  }
  return Math.abs(total) / 2;
}

function angleAt(points) {
  if (points.length < 3) return 0;
  const [[ax, ay], [bx, by], [cx, cy]] = points;
  const first = Math.atan2(ay - by, ax - bx);
  const second = Math.atan2(cy - by, cx - bx);
  const degrees = Math.abs(first - second) * 180 / Math.PI;
  return degrees > 180 ? 360 - degrees : degrees;
}

export function compute(kind, points, { depth = 0, precision = 2 } = {}) {
  const f = factor();
  let value;
  if (kind === 'distance') value = pathLength(points) * f;
  else if (kind === 'perimeter') value = pathLength([...points, points[0]]) * f;
  else if (kind === 'area') value = polygonArea(points) * f * f;
  else if (kind === 'volume') value = polygonArea(points) * f * f * depth;
  else if (kind === 'angle') value = angleAt(points);
  else if (kind === 'radius') value = pathLength(points.slice(0, 2)) * f;
  else value = 0;
  return {
    kind,
    value: Number(value.toFixed(6)),
    unit: scale.unit,
    label: format(value, kind, precision),
  };
}

export function format(value, kind, precision = 2) {
  if (kind === 'angle') return `${value.toFixed(precision)}°`;
  if (kind === 'area') return `${value.toFixed(precision)} ${AREA_UNITS[scale.unit] || `${scale.unit}²`}`;
  if (kind === 'volume') return `${value.toFixed(precision)} ${scale.unit}³`;
  return `${value.toFixed(precision)} ${scale.unit}`;
}

export function convert(value, from, to) {
  return value * (UNITS[from] || 1) / (UNITS[to] || 1);
}

export const UNIT_LABELS = {
  mm: 'ミリメートル (mm)',
  cm: 'センチメートル (cm)',
  m: 'メートル (m)',
  in: 'インチ (in)',
  ft: 'フィート (ft)',
  pt: 'ポイント (pt)',
};

export const KIND_LABELS = {
  distance: '距離', perimeter: '周囲長', area: '面積',
  angle: '角度', radius: '半径', volume: '体積', count: 'カウント',
};

/** The unit a total should carry — angles are degrees, counts have none. */
function unitFor(kind, unit) {
  if (kind === 'count') return '';
  if (kind === 'angle') return '°';
  if (kind === 'area') return AREA_UNITS[unit] || `${unit}²`;
  if (kind === 'volume') return `${unit}³`;
  return unit;
}

/** Group measurements and counts the way the server does, for the live panel. */
export function summarise(items) {
  const groups = new Map();
  for (const item of items) {
    const isCount = item.tool === 'count';
    if (!item.measure && !isCount) continue;
    const kind = isCount ? 'count' : item.measure.kind;
    const key = item.subject || KIND_LABELS[kind] || kind;
    if (!groups.has(key)) {
      groups.set(key, {
        label: key,
        colour: item.style?.stroke || '#888888',
        kind,
        unit: unitFor(kind, item.measure?.unit),
        // Adding up angles is meaningless; only the count is shown for them.
        summable: kind !== 'count' && kind !== 'angle',
        count: 0,
        total: 0,
        pages: new Set(),
      });
    }
    const group = groups.get(key);
    group.count += 1;
    group.pages.add(item.page + 1);
    if (item.measure) group.total += Number(item.measure.value) || 0;
  }
  return [...groups.values()]
    .map((g) => ({ ...g, total: Number(g.total.toFixed(4)), pages: [...g.pages].sort((a, b) => a - b) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'ja'));
}

export const MEASURE_KINDS = {
  measureDistance: 'distance',
  measurePerimeter: 'perimeter',
  measureArea: 'area',
  measureAngle: 'angle',
  measureRadius: 'radius',
};
