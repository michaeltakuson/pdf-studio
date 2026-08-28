// Dynamic Default: whatever you used last becomes the default next time.
// The source material calls this the most under-used feature in every PDF
// tool, so here it is always on rather than an opt-in "set as default" menu.

const KEY = 'pdfstudio.defaults.v1';

const BASE = {
  highlight:     { stroke: '#ffe14d', opacity: 0.45 },
  underline:     { stroke: '#e0403a', width: 1.2 },
  squiggly:      { stroke: '#e0403a', width: 1.2 },
  strikeout:     { stroke: '#e0403a', width: 1.2 },
  areaHighlight: { stroke: '#ffe14d', fill: '#ffe14d', opacity: 0.35, width: 0 },
  // For FreeText, `stroke` mirrors the font colour: the PDF draws border,
  // callout line and text all in the /DA colour.
  freetext:      { stroke: '#1c1f26', fill: null, width: 0, opacity: 1,
                   font: { family: 'japan', size: 12, color: '#1c1f26', align: 'left' } },
  callout:       { stroke: '#2f6df6', fill: '#ffffff', width: 1, opacity: 1,
                   font: { family: 'japan', size: 12, color: '#2f6df6', align: 'left' } },
  note:          { stroke: '#ffd23d', opacity: 1 },
  pen:           { stroke: '#e0403a', width: 2, opacity: 1 },
  marker:        { stroke: '#ffe14d', width: 12, opacity: 0.4 },
  line:          { stroke: '#e0403a', width: 1.5, opacity: 1, lineEnds: ['none', 'openArrow'] },
  square:        { stroke: '#e0403a', fill: null, width: 1.5, opacity: 1, borderStyle: 'solid', cloudIntensity: 0 },
  circle:        { stroke: '#e0403a', fill: null, width: 1.5, opacity: 1 },
  polygon:       { stroke: '#e0403a', fill: null, width: 1.5, opacity: 1, cloudIntensity: 0 },
  polyline:      { stroke: '#e0403a', fill: null, width: 1.5, opacity: 1 },
  stamp:         { stroke: '#1b7f3b', opacity: 1, width: 2, stampIndex: 0, stampText: '確認済 {date}',
                   font: { family: 'japan', size: 14, color: '#1b7f3b', align: 'center' } },
  measureDistance: { stroke: '#2f6df6', width: 1.5, opacity: 1 },
  measurePerimeter: { stroke: '#2f6df6', width: 1.5, opacity: 1 },
  // The fill marks the region measured, so it has to stay see-through: an
  // opaque patch hides the very drawing being measured. PDF gives an
  // annotation one opacity for stroke and fill together, so this single value
  // is the compromise — light enough to read through, dark enough to see.
  measureArea:   { stroke: '#22b3a4', fill: '#22b3a4', width: 1.5, opacity: 0.35 },
  measureAngle:  { stroke: '#8b5cf6', width: 1.5, opacity: 1 },
  measureRadius: { stroke: '#8b5cf6', width: 1.5, opacity: 1 },
  count:         { stroke: '#e256a5', fill: '#e256a5', width: 1, opacity: 1 },
  calibrate:     { stroke: '#2f6df6', width: 1.5, opacity: 1 },
  redact:        { stroke: '#000000', fill: '#000000', opacity: 1,
                   font: { family: 'japan', size: 9, color: '#ffffff', align: 'left' } },
};

const FULL_STYLE = {
  stroke: '#e0403a',
  fill: null,
  opacity: 1,
  width: 1.5,
  dash: [],
  borderStyle: 'solid',
  cloudIntensity: 0,
  lineEnds: ['none', 'none'],
  font: { family: 'japan', size: 11, color: '#000000', align: 'left' },
  rotate: 0,
  blend: null,
};

let stored = {};
try {
  stored = JSON.parse(localStorage.getItem(KEY) || '{}');
} catch { stored = {}; }

export function styleFor(tool) {
  return structuredClone({ ...FULL_STYLE, ...(BASE[tool] || {}), ...(stored[tool] || {}) });
}

/** Remember a property the user just changed, so the next annotation inherits it. */
export function remember(tool, patch) {
  if (!BASE[tool]) return;
  stored[tool] = { ...(stored[tool] || {}), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(stored)); } catch { /* private mode */ }
}

export function resetTool(tool) {
  delete stored[tool];
  try { localStorage.setItem(KEY, JSON.stringify(stored)); } catch { /* ignore */ }
}

export const SWATCHES = [
  '#ffe14d', '#ffa53d', '#e0403a', '#e256a5',
  '#8b5cf6', '#2f6df6', '#22b3a4', '#3fb950',
  '#1c1f26', '#ffffff',
];

export const PREFS_KEY = 'pdfstudio.prefs.v1';

const prefDefaults = {
  theme: 'dark',
  author: '',
  penOnly: false,
  leftHanded: false,
  pressure: true,
  smoothing: 0.5,
  snapAngle: true,
  shapeRecognition: false,
};

let prefs;
try {
  prefs = { ...prefDefaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') };
} catch { prefs = { ...prefDefaults }; }

export function getPref(key) {
  return prefs[key];
}

export function setPref(key, value) {
  prefs[key] = value;
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
}
