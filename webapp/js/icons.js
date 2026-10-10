// Line icons, drawn for this app on a 24-unit grid.
//
// Unicode symbols and emoji were used before; they render differently on
// every machine (and some not at all), which made the toolbar look broken.

const P = {
  // files
  open: '<path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M3 11h18"/>',
  save: '<path d="M5 3h11l4 4v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 3v6h7V3M7 21v-7h10v7"/>',
  saveas: '<path d="M5 3h11l4 4v6M11 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1"/><path d="M8 3v6h7V3"/><path d="m14 20 1-3.5 5-5 2.5 2.5-5 5z"/>',
  print: '<path d="M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path d="M7 14h10v7H7z"/>',
  newdoc: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5M12 11v6M9 14h6"/>',
  export: '<path d="M12 3v12M8 7l4-4 4 4"/><path d="M4 14v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  download: '<path d="M12 3v12M8 11l4 4 4-4"/><path d="M4 17v2a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
  // history & clipboard
  undo: '<path d="M8 5 4 9l4 4"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  redo: '<path d="m16 5 4 4-4 4"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
  paste: '<path d="M9 4H6a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1h-3"/><rect x="9" y="2.5" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h6"/>',
  cut: '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8 16.5 18 4M16 16.5 6 4"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="1"/><path d="M16 8V4a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h3"/>',
  duplicate: '<rect x="8" y="8" width="12" height="13" rx="1"/><path d="M16 8V4a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h3M14 11.5v6M11 14.5h6"/>',
  trash: '<path d="M4 7h16M10 7V4h4v3M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6"/>',
  // pointer tools
  select: '<path d="m5 3 14 8-6 1.5L10 19z"/>',
  pan: '<path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11M11 10V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v8a7 7 0 0 1-7 7c-2.5 0-4-1-5.5-3.5L3 14a1.5 1.5 0 0 1 2.5-1.5L8 15"/>',
  // text
  text: '<rect x="3" y="4" width="18" height="16" rx="1.5" stroke-dasharray="3 2.5"/><path d="M8 9V8h8v1M12 8v8M10.5 16h3"/>',
  edittext: '<path d="M4 6V4h10v2M9 4v12M7 16h4"/><path d="m13 20 1-3.5 5.5-5.5 2.5 2.5-5.5 5.5z"/>',
  callout: '<rect x="8" y="3" width="13" height="9" rx="1"/><path d="M8 9 3 20M3 20l1-4M3 20l3.5-2M11 7h7"/>',
  note: '<path d="M4 4h16v12H10l-5 4v-4H4z"/><path d="M8 8h8M8 12h5"/>',
  bold: '<path d="M7 4h6a4 4 0 0 1 0 8H7zM7 12h7a4 4 0 0 1 0 8H7z" stroke-width="2.4"/>',
  alignleft: '<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>',
  aligncenter: '<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>',
  alignright: '<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>',
  fontcolor: '<path d="m6 16 6-13 6 13M8.3 11h7.4"/>',
  fill: '<path d="m4 13 8-8 7 7-7 7a1.4 1.4 0 0 1-2 0l-6-6z"/><path d="M12 5 9.5 2.5M20 16s2 2.2 2 3.5a2 2 0 0 1-4 0c0-1.3 2-3.5 2-3.5z"/>',
  stroke: '<path d="m4 20 1-4L16 5l3 3L8 19z"/><path d="m14 7 3 3"/>',
  // markup
  highlight: '<path d="m9 14 7-9 4 3-7 9z"/><path d="m9 14-2 4 4-1M4 21h16"/>',
  underline: '<path d="M7 4v7a5 5 0 0 0 10 0V4M5 20h14"/>',
  strikeout: '<path d="M4 12h16M16 7a4 3 0 0 0-4-3c-2.5 0-4 1.3-4 3 0 1 .5 1.8 1.5 2.4M8 16.5c.3 2 2 3.5 4 3.5 2.4 0 4-1.3 4-3.2"/>',
  squiggly: '<path d="M7 4v6a5 5 0 0 0 10 0V4"/><path d="M4 20q2-3 4 0t4 0 4 0 4 0"/>',
  areahighlight: '<rect x="4" y="5" width="16" height="14" rx="1" stroke-dasharray="3 2.5"/><path d="M8 10h8M8 14h8" stroke-width="3" opacity=".45"/>',
  // drawing
  pen: '<path d="m4 20 1.2-4.5L16 4.7l3.3 3.3L8.5 18.8z"/><path d="m14 6.7 3.3 3.3"/>',
  marker: '<path d="m5 15 9-11 5 4-9 11z"/><path d="m5 15-1 5 5-1"/>',
  eraser: '<path d="m4 15 9-10 7 6-8 9H8z"/><path d="m9 9 7 6M4 20h16"/>',
  lasso: '<ellipse cx="12" cy="10" rx="8" ry="6" stroke-dasharray="3 2.5"/><path d="M8 15c-1 2 0 4 2 4s2-2 0-2"/>',
  // shapes
  line: '<path d="M5 19 19 5"/>',
  arrow: '<path d="M5 19 19 5M11 5h8v8"/>',
  square: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
  circle: '<ellipse cx="12" cy="12" rx="8.5" ry="7"/>',
  polygon: '<path d="m12 3 8.5 6.5-3.2 10.5H6.7L3.5 9.5z"/>',
  polyline: '<path d="m3 17 5-9 5 7 4-10 4 6"/>',
  cloud: '<path d="M7 18a4 4 0 0 1-.6-8 5 5 0 0 1 9.6-1.3A4.5 4.5 0 0 1 17 18z"/>',
  // inserts
  image: '<rect x="3" y="5" width="18" height="14" rx="1.5"/><circle cx="8.5" cy="10" r="1.6"/><path d="m4 17 5-4.5 3.5 3 3-2.5L20 17"/>',
  signature: '<path d="M3 17c2-6 4-10 5.5-10S9 12 8 15s1 2 3-1 2.500-3 3-1-1 3 1 3 3-2 6-2"/><path d="M3 21h18"/>',
  hanko: '<circle cx="12" cy="12" r="8.5"/><path d="M12 6.5v11M8.5 9h7M8.5 12.500h7M9 16h6"/>',
  stamp: '<path d="M9 3h6l-1 8h5v4H5v-4h5z"/><path d="M5 20h14"/>',
  date: '<rect x="3.500" y="5" width="17" height="15.500" rx="1.5"/><path d="M3.500 10h17M8 3v4M16 3v4M8 14h2M12 14h2M8 17h2"/>',
  check: '<path d="m5 13 4.500 4.500L19 7"/>',
  cross: '<path d="M6 6l12 12M18 6 6 18"/>',
  dot: '<circle cx="12" cy="12" r="5" fill="currentColor"/>',
  ring: '<circle cx="12" cy="12" r="7"/>',
  // pages
  rotatecw: '<path d="M20 11a8 8 0 1 0-2.500 6.500"/><path d="M20 4v7h-7"/>',
  rotateccw: '<path d="M4 11a8 8 0 1 1 2.500 6.500"/><path d="M4 4v7h7"/>',
  pagedelete: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5M9.500 12l5 5M14.500 12l-5 5"/>',
  pagecopy: '<path d="M10 7h6l3 3v10a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/><path d="M9 17H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h6l3 3"/>',
  pageblank: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  pageextract: '<path d="M13 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h5"/><path d="M13 3v5h5v3M13 17h8M18 14l3 3-3 3"/>',
  merge: '<path d="M4 4h6v7H4zM14 4h6v7h-6zM7 11v3a3 3 0 0 0 3 3h4a3 3 0 0 0 3-3v-3M12 17v4M9.500 18.500 12 21l2.500-2.500"/>',
  split: '<path d="M12 3v18" stroke-dasharray="3 2.5"/><path d="M9 6H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h4M15 6h4a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-4"/>',
  margins: '<rect x="3" y="3" width="18" height="18" rx="1"/><rect x="7" y="7" width="8" height="10" stroke-dasharray="2.500 2"/><path d="M17.500 9v6"/>',
  nup: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 4v16M3 12h18"/>',
  moveup: '<path d="M12 20V5M6 11l6-6 6 6"/>',
  movedown: '<path d="M12 4v15M6 13l6 6 6-6"/>',
  crop: '<path d="M7 2v15h15M2 7h15v15"/>',
  headerfooter: '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M7 6.500h10M7 17.500h10" stroke-width="2.200"/>',
  watermark: '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="m8 16 8-8M8 11l3-3M13 16l3-3" opacity=".7"/>',
  number: '<path d="M9 4 7 20M17 4l-2 16M4 9h16M3 15h16"/>',
  // review
  comments: '<path d="M4 4h13v9h-7l-4 3v-3H4z"/><path d="M20 9v8h-2v3l-4-3h-3"/>',
  redact: '<rect x="3" y="8" width="18" height="8" rx="1" fill="currentColor"/><path d="M5 4h14M5 20h9"/>',
  redactapply: '<rect x="3" y="5" width="12" height="6" rx="1" fill="currentColor"/><path d="m13 17 3 3 5-6"/><path d="M4 15h6M4 19h5"/>',
  searchredact: '<circle cx="10" cy="10" r="6"/><path d="m14.500 14.500 5.500 5.500"/><path d="M7 10h6" stroke-width="3"/>',
  scrub: '<path d="M12 3 4 6v6c0 5 3.500 8 8 9 4.500-1 8-4 8-9V6z"/><path d="m9 12 2.500 2.500L15.500 10"/>',
  compare: '<rect x="3" y="4" width="7.500" height="16" rx="1"/><rect x="13.500" y="4" width="7.500" height="16" rx="1"/><path d="M5.500 9h2.500M5.500 13h2.500M16 9h2.500M16 13h2.500"/>',
  flatten: '<path d="m12 3 9 5-9 5-9-5zM3 13l9 5 9-5"/>',
  // view
  zoomin: '<circle cx="10.500" cy="10.500" r="6.500"/><path d="m15.500 15.500 5 5M10.500 7.500v6M7.500 10.500h6"/>',
  zoomout: '<circle cx="10.500" cy="10.500" r="6.500"/><path d="m15.500 15.500 5 5M7.500 10.500h6"/>',
  fitwidth: '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M8 12h8M8 12l2-2M8 12l2 2M16 12l-2-2M16 12l-2 2"/>',
  fitpage: '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M12 7v10M12 7l-2 2M12 7l2 2M12 17l-2-2M12 17l2-2"/>',
  actual: '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M7.500 10v5M7 10.500l.5-.5M12 11h.01M12 14.500h.01M15.500 10v5M15 10.500l.5-.5"/>',
  thumbs: '<rect x="3" y="4" width="6" height="16" rx="1"/><rect x="12" y="4" width="9" height="16" rx="1"/>',
  sidepane: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M15 4v16"/>',
  theme: '<path d="M20 14A8.500 8.500 0 1 1 10 4a7 7 0 0 0 10 10z"/>',
  invert: '<circle cx="12" cy="12" r="8.500"/><path d="M12 3.500v17a8.500 8.500 0 0 0 0-17z" fill="currentColor"/>',
  fullscreen: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  speak: '<path d="M4 9h3l5-4v14l-5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.500 6.500a8 8 0 0 1 0 11"/>',
  search: '<circle cx="10.500" cy="10.500" r="6.500"/><path d="m15.500 15.500 5 5"/>',
  replace: '<path d="M4 7h9a4 4 0 0 1 0 8h-2"/><path d="m7 4-3 3 3 3M14 12l-3 3 3 3"/>',
  // tools
  form: '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M7.500 8h9M7.500 12h9"/><rect x="7.500" y="15" width="5" height="3"/>',
  ruler: '<path d="m3 16 13-13 5 5L8 21z"/><path d="m7 12 2 2M10 9l2 2M13 6l2 2"/>',
  area: '<path d="M4 20V8l8-4 8 6v10z" stroke-dasharray="3 2.500"/>',
  angle: '<path d="M20 20H4L16 5"/><path d="M11 20a7 7 0 0 0-2.200-5"/>',
  count: '<circle cx="12" cy="12" r="8.500"/><path d="M10.500 9 12.500 8v8M10.500 16h4"/>',
  scale: '<path d="M3 17h18M3 17v-3M21 17v-3M9 17v-2M15 17v-2M12 17v-3M6 8h12M6 8l2-2M6 8l2 2M18 8l-2-2M18 8l-2 2"/>',
  ocr: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M8 9v6M8 9h3M8 12h2M14 9v6M14 9h1.500a1.500 1.500 0 0 1 0 3H14l2.500 3"/>',
  lock: '<rect x="5" y="10.500" width="14" height="10" rx="1.500"/><path d="M8 10.500V7.500a4 4 0 0 1 8 0v3"/>',
  compress: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M12 9v3M12 18v-3M10 11l2 2 2-2M10 16l2-2 2 2"/>',
  accessibility: '<circle cx="12" cy="5" r="2"/><path d="M5 9h14M12 9v6M12 15l-3.500 6M12 15l3.500 6"/>',
  order: '<path d="M5 6h3M5 12h3M5 18h3M11 6h8M11 12h8M11 18h8"/>',
  bookmark: '<path d="M7 3h10a1 1 0 0 1 1 1v17l-6-4-6 4V4a1 1 0 0 1 1-1z"/>',
  caret: '<path d="m6 9 6 6 6-6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  more: '<circle cx="5" cy="12" r="1.300" fill="currentColor"/><circle cx="12" cy="12" r="1.300" fill="currentColor"/><circle cx="19" cy="12" r="1.300" fill="currentColor"/>',
  prev: '<path d="m14 6-6 6 6 6"/>',
  next: '<path d="m10 6 6 6-6 6"/>',
  first: '<path d="m17 6-6 6 6 6M7 6v12"/>',
  last: '<path d="m7 6 6 6-6 6M17 6v12"/>',
  width: '<path d="M4 6h16" stroke-width="1"/><path d="M4 11h16" stroke-width="2.200"/><path d="M4 17.500h16" stroke-width="3.600"/>',
  dash: '<path d="M4 7h16"/><path d="M4 12h16" stroke-dasharray="4 3"/><path d="M4 17h16" stroke-dasharray="1.500 3"/>',
  opacity: '<circle cx="12" cy="12" r="8.500"/><path d="M12 3.500v17M12 8h7M12 12h8.500M12 16h7" opacity=".6"/>',
  text2: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
};

/** An icon as an SVG string. */
export function iconSvg(name, size = 20) {
  const body = P[name] || P.more;
  return `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/** An icon as a detached element, ready to append. */
export function icon(name, size = 20) {
  const holder = document.createElement('span');
  holder.className = 'ic-wrap';
  holder.innerHTML = iconSvg(name, size);
  return holder;
}

export function hasIcon(name) {
  return Object.prototype.hasOwnProperty.call(P, name);
}
