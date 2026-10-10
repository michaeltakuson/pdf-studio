// Pictures made inside the app: signatures and Japanese seals (hanko).
//
// Both end up as an ordinary image on the page, so they can be moved and
// resized like any other picture and survive in every PDF viewer.

import { customDialog, node } from './dialogs.js';
import { ensureFontLoaded, FONTS } from './textedit.js';

const SIG_KEY = 'pdfstudio.signatures.v1';

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(SIG_KEY) || '[]'); } catch { return []; }
}

function storeSaved(list) {
  try { localStorage.setItem(SIG_KEY, JSON.stringify(list.slice(0, 6))); } catch { /* storage full or blocked */ }
}

/** Crop a canvas to the pixels that are actually drawn on. */
function trim(canvas, pad = 6) {
  const context = canvas.getContext('2d');
  const { width, height } = canvas;
  const data = context.getImageData(0, 0, width, height).data;
  let x0 = width; let y0 = height; let x1 = -1; let y1 = -1;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (data[(y * width + x) * 4 + 3] > 8) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < 0) return null;
  x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad);
  x1 = Math.min(width - 1, x1 + pad); y1 = Math.min(height - 1, y1 + pad);
  const out = document.createElement('canvas');
  out.width = x1 - x0 + 1;
  out.height = y1 - y0 + 1;
  out.getContext('2d').drawImage(canvas, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

export function loadImage(source) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('画像を読み込めませんでした'));
    image.src = source;
  });
}

export function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('ファイルを読み込めませんでした'));
    reader.readAsDataURL(file);
  });
}

/**
 * Bring a picture into a form that is safe to store in a PDF: a PNG or JPEG
 * no larger than it needs to be. A phone photo is 12 megapixels; on an A4
 * page that is several times more than any printer can use.
 */
export async function normaliseImage(source, { maxSide = 2400, whiteToAlpha = false, forcePng = false } = {}) {
  const image = await loadImage(source);
  const ratio = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight, 1));
  const width = Math.max(1, Math.round((image.naturalWidth || 300) * ratio));
  const height = Math.max(1, Math.round((image.naturalHeight || 150) * ratio));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  context.drawImage(image, 0, 0, width, height);

  let transparent = false;
  if (whiteToAlpha) {
    const pixels = context.getImageData(0, 0, width, height);
    const d = pixels.data;
    for (let i = 0; i < d.length; i += 4) {
      const light = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000;
      // Paper white goes fully clear; the grey fringe of each stroke fades
      // out rather than leaving a hard pale edge.
      if (light > 235) d[i + 3] = 0;
      else if (light > 170) d[i + 3] = Math.round(d[i + 3] * ((235 - light) / 65));
    }
    context.putImageData(pixels, 0, 0);
    transparent = true;
  } else {
    const d = context.getImageData(0, 0, width, height).data;
    for (let i = 3; i < d.length; i += 4 * 97) { if (d[i] < 250) { transparent = true; break; } }
  }
  const isPhoto = !transparent && !forcePng && /^data:image\/jpe?g/i.test(String(source));
  const url = isPhoto ? canvas.toDataURL('image/jpeg', 0.9) : canvas.toDataURL('image/png');
  return { image: url, width, height };
}

// ---------------------------------------------------------------- signature

/** Ask for a signature. Resolves to {image, width, height} or null. */
export function signatureDialog({ defaultName = '' } = {}) {
  let mode = 'draw';
  let pad; let padContext; let drew = false;
  let typed; let typedCanvas; let typedFont = 'klee';
  let pictureResult = null;
  let whiteToAlpha = true; let pictureSource = null;
  let colour = '#12305e';
  let saveForLater = true;
  let finishDialog = () => {};

  const body = node('div', {});
  const saved = loadSaved();
  const panes = {};

  const renderSaved = () => {
    const holder = body.querySelector('.sig-saved') || node('div', { class: 'sig-saved' });
    holder.textContent = '';
    for (const [index, item] of loadSaved().entries()) {
      const chip = node('div', { class: 'sig-chip', title: 'この署名を使う' }, [
        node('img', { src: item.image, alt: '保存した署名' }),
        node('button', {
          class: 'x', text: '×', title: 'この署名を削除',
          onclick: (e) => {
            e.stopPropagation();
            const list = loadSaved();
            list.splice(index, 1);
            storeSaved(list);
            renderSaved();
          },
        }),
      ]);
      chip.addEventListener('click', () => finishDialog(item));
      holder.append(chip);
    }
    return holder;
  };

  const drawTyped = async () => {
    const name = typed.value.trim();
    const context = typedCanvas.getContext('2d');
    context.clearRect(0, 0, typedCanvas.width, typedCanvas.height);
    if (!name) return;
    await ensureFontLoaded({ family: typedFont, size: 96 }, name);
    const family = `"${FONTS[typedFont].css}", "Yu Mincho", serif`;
    let size = 110;
    context.font = `${size}px ${family}`;
    const width = context.measureText(name).width;
    if (width > typedCanvas.width - 40) size = Math.floor(size * (typedCanvas.width - 40) / width);
    context.font = `${size}px ${family}`;
    context.fillStyle = colour;
    context.textBaseline = 'middle';
    context.textAlign = 'center';
    context.fillText(name, typedCanvas.width / 2, typedCanvas.height / 2);
  };

  const build = (finish) => {
    finishDialog = finish;
    if (saved.length) {
      body.append(node('div', { class: 'muted', text: '保存した署名（クリックでそのまま使えます）' }), renderSaved());
    }
    const seg = node('div', { class: 'seg', style: 'margin-bottom:10px' });
    const buttons = {};
    const show = (next) => {
      mode = next;
      for (const [key, pane] of Object.entries(panes)) pane.hidden = key !== next;
      for (const [key, button] of Object.entries(buttons)) button.classList.toggle('active', key === next);
      if (next === 'type') { typed.focus(); drawTyped(); }
    };
    for (const [key, label] of [['draw', '手書き'], ['type', '名前を入力'], ['image', '画像を使う']]) {
      buttons[key] = node('button', { text: label, onclick: () => show(key) });
      seg.append(buttons[key]);
    }
    body.append(seg);

    // --- draw
    pad = node('canvas', { class: 'sig-pad', width: 1200, height: 380 });
    padContext = pad.getContext('2d');
    padContext.lineCap = 'round';
    padContext.lineJoin = 'round';
    let last = null;
    const at = (event) => {
      const box = pad.getBoundingClientRect();
      return {
        x: ((event.clientX - box.left) / box.width) * pad.width,
        y: ((event.clientY - box.top) / box.height) * pad.height,
        p: event.pointerType === 'pen' && event.pressure > 0 ? event.pressure : 0.5,
      };
    };
    pad.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      pad.setPointerCapture(event.pointerId);
      last = at(event);
      drew = true;
      padContext.fillStyle = colour;
      padContext.beginPath();
      padContext.arc(last.x, last.y, 2.2, 0, Math.PI * 2);
      padContext.fill();
    });
    pad.addEventListener('pointermove', (event) => {
      if (!last) return;
      for (const sub of (event.getCoalescedEvents?.() || [event])) {
        const next = at(sub);
        padContext.strokeStyle = colour;
        padContext.lineWidth = 2.5 + next.p * 5;
        padContext.beginPath();
        padContext.moveTo(last.x, last.y);
        padContext.lineTo(next.x, next.y);
        padContext.stroke();
        last = next;
      }
    });
    const lift = () => { last = null; };
    pad.addEventListener('pointerup', lift);
    pad.addEventListener('pointercancel', lift);
    panes.draw = node('div', {}, [
      pad,
      node('div', { class: 'prop-row', style: 'margin-top:6px' }, [
        node('span', { class: 'muted', text: 'マウス・指・ペンでここに書いてください' }),
        node('button', {
          class: 'btn small', text: '書き直す', style: 'flex:none',
          onclick: () => { padContext.clearRect(0, 0, pad.width, pad.height); drew = false; },
        }),
      ]),
    ]);

    // --- type
    typed = node('input', { class: 'input', value: defaultName, placeholder: '氏名を入力', style: 'width:100%', oninput: drawTyped });
    typedCanvas = node('canvas', { width: 1000, height: 300 });
    const fontPick = node('select', { class: 'select', onchange: (e) => { typedFont = e.target.value; drawTyped(); } });
    for (const key of ['klee', 'yomogi', 'mincho', 'maru', 'gothic']) {
      fontPick.append(node('option', { value: key, text: FONTS[key].label }));
    }
    panes.type = node('div', { hidden: '' }, [
      node('div', { class: 'prop-row' }, [typed, fontPick]),
      node('div', { class: 'sig-preview' }, typedCanvas),
    ]);

    // --- image
    const preview = node('div', { class: 'sig-preview' }, node('span', { class: 'muted', text: '紙に書いた署名や印影の写真・スキャン画像を選んでください' }));
    const refreshPicture = async () => {
      if (!pictureSource) return;
      try {
        pictureResult = await normaliseImage(pictureSource, { maxSide: 1400, whiteToAlpha, forcePng: true });
        preview.textContent = '';
        preview.append(node('img', { src: pictureResult.image, alt: '' }));
      } catch (err) {
        preview.textContent = err.message;
      }
    };
    const picker = node('input', {
      type: 'file', accept: 'image/*',
      onchange: async (e) => {
        if (!e.target.files[0]) return;
        pictureSource = await fileToDataUrl(e.target.files[0]);
        refreshPicture();
      },
    });
    panes.image = node('div', { hidden: '' }, [
      node('div', { class: 'prop-row' }, picker),
      preview,
      node('div', { class: 'prop-row', style: 'margin-top:6px' }, [
        node('label', { text: '白い背景を透明にする' }),
        node('input', { type: 'checkbox', checked: true, style: 'flex:none', onchange: (e) => { whiteToAlpha = e.target.checked; refreshPicture(); } }),
      ]),
    ]);

    body.append(panes.draw, panes.type, panes.image);
    body.append(node('div', { class: 'prop-row', style: 'margin-top:10px' }, [
      node('label', { text: 'インクの色' }),
      node('div', { class: 'seg', style: 'flex:none' }, ['#12305e', '#000000', '#c62828'].map((value, index) => node('button', {
        text: ['紺', '黒', '赤'][index], class: value === colour ? 'active' : '',
        onclick: (e) => {
          colour = value;
          for (const other of e.target.parentElement.children) other.classList.toggle('active', other === e.target);
          drawTyped();
        },
      }))),
      node('label', { text: '次回のために保存', style: 'margin-left:auto' }),
      node('input', { type: 'checkbox', checked: true, style: 'flex:none', onchange: (e) => { saveForLater = e.target.checked; } }),
    ]));
    show('draw');
    return body;
  };

  return customDialog({
    title: '署名を入れる',
    intro: '見た目としての署名です（電子証明書による署名ではありません）。入れたあと、ページ上で位置と大きさを調整できます。',
    confirmLabel: 'この署名を使う',
    wide: true,
    build,
    onConfirm: () => {
      let result = null;
      if (mode === 'draw') {
        const cropped = drew ? trim(pad, 10) : null;
        if (cropped) result = { image: cropped.toDataURL('image/png'), width: cropped.width, height: cropped.height };
      } else if (mode === 'type') {
        const cropped = trim(typedCanvas, 8);
        if (cropped) result = { image: cropped.toDataURL('image/png'), width: cropped.width, height: cropped.height };
      } else {
        result = pictureResult;
      }
      if (!result) return false;
      if (saveForLater) {
        const list = loadSaved().filter((item) => item.image !== result.image);
        list.unshift(result);
        storeSaved(list);
      }
      return result;
    },
  });
}

// ---------------------------------------------------------------- hanko

const SEAL_PRESETS = ['済', '承認', '確認済', '至急', '社外秘', '重要', '写', '回覧', '検'];

async function drawSeal(canvas, spec) {
  const context = canvas.getContext('2d');
  const size = canvas.width;
  context.clearRect(0, 0, size, canvas.height);
  const colour = spec.colour || '#d7261e';
  const family = spec.kind === 'date' ? 'gothic' : 'mincho';
  await ensureFontLoaded({ family, size: 64 }, `${spec.name}${spec.top || ''}${spec.text || ''}0123456789.`);
  const face = `"${FONTS[family].css}", "Yu Mincho", serif`;
  context.strokeStyle = colour;
  context.fillStyle = colour;
  context.textAlign = 'center';
  context.textBaseline = 'middle';

  if (spec.kind === 'box') {
    const text = (spec.text || '済').slice(0, 8);
    const fontSize = 150;
    context.font = `700 ${fontSize}px ${face}`;
    const width = Math.max(fontSize * 1.2, context.measureText(text).width + 90);
    canvas.width = Math.ceil(width + 24);
    canvas.height = 250;
    const c2 = canvas.getContext('2d');
    c2.strokeStyle = colour; c2.fillStyle = colour;
    c2.lineWidth = 12;
    c2.textAlign = 'center'; c2.textBaseline = 'middle';
    c2.font = `700 ${fontSize}px ${face}`;
    c2.beginPath();
    c2.roundRect(12, 12, canvas.width - 24, canvas.height - 24, 14);
    c2.stroke();
    c2.fillText(text, canvas.width / 2, canvas.height / 2 + 6);
    return;
  }

  const centre = size / 2;
  const radius = size / 2 - 14;
  context.lineWidth = size * 0.035;
  context.beginPath();
  context.arc(centre, centre, radius, 0, Math.PI * 2);
  context.stroke();

  if (spec.kind === 'date') {
    // A date seal: who (top), when (middle band), name (bottom).
    const band = radius * 0.36;
    context.lineWidth = size * 0.018;
    for (const y of [centre - band, centre + band]) {
      const half = Math.sqrt(radius * radius - (y - centre) * (y - centre));
      context.beginPath();
      context.moveTo(centre - half, y);
      context.lineTo(centre + half, y);
      context.stroke();
    }
    const fit = (text, y, max, base) => {
      let fontSize = base;
      context.font = `700 ${fontSize}px ${face}`;
      const width = context.measureText(text).width;
      if (width > max) fontSize = Math.floor(fontSize * max / width);
      context.font = `700 ${fontSize}px ${face}`;
      context.fillText(text, centre, y);
    };
    fit(spec.top || '', centre - band - radius * 0.3, radius * 1.15, radius * 0.34);
    fit(spec.date || '', centre + radius * 0.02, radius * 1.75, radius * 0.38);
    fit(spec.name || '', centre + band + radius * 0.3, radius * 1.15, radius * 0.38);
    return;
  }

  // A name seal: characters run top to bottom; four go in two columns,
  // read from the right.
  const chars = [...(spec.name || '印')].slice(0, 4);
  const count = chars.length;
  if (count === 4) {
    const fontSize = radius * 0.72;
    context.font = `700 ${fontSize}px ${face}`;
    const dx = radius * 0.4; const dy = radius * 0.4;
    const spots = [[dx, -dy], [dx, dy], [-dx, -dy], [-dx, dy]];
    chars.forEach((ch, i) => context.fillText(ch, centre + spots[i][0], centre + spots[i][1] + fontSize * 0.04));
    return;
  }
  const fontSize = radius * (count === 1 ? 1.35 : count === 2 ? 0.86 : 0.6);
  context.font = `700 ${fontSize}px ${face}`;
  const step = count === 1 ? 0 : (radius * 1.62 - fontSize) / (count - 1);
  const first = centre - (step * (count - 1)) / 2;
  chars.forEach((ch, i) => context.fillText(ch, centre, first + i * step + fontSize * 0.04));
}

/** Ask for a seal. Resolves to {image, width, height, points} or null. */
export function hankoDialog({ defaultName = '' } = {}) {
  const today = new Date();
  const pad2 = (n) => String(n).padStart(2, '0');
  const spec = {
    kind: 'name',
    name: (defaultName || '').replace(/\s+/g, '').slice(0, 4),
    top: '',
    date: `${today.getFullYear()}.${pad2(today.getMonth() + 1)}.${pad2(today.getDate())}`,
    text: '済',
    colour: '#d7261e',
  };
  const canvas = node('canvas', { width: 420, height: 420 });
  const redraw = async () => {
    canvas.width = 420; canvas.height = 420;
    await drawSeal(canvas, spec);
  };

  const build = () => {
    const rows = node('div', { class: 'dialog-form' });
    const fields = {};
    const row = (key, label, control) => {
      fields[key] = node('div', { class: 'prop-row' }, [node('label', { text: label }), control]);
      rows.append(fields[key]);
    };
    const text = (key, placeholder) => node('input', {
      class: 'input', value: spec[key], placeholder,
      oninput: (e) => { spec[key] = e.target.value; redraw(); },
    });
    const kind = node('select', { class: 'select' });
    for (const [value, label] of [['name', '認印（丸・名前）'], ['date', '日付印（データ印）'], ['box', '角印（済・承認など）']]) {
      kind.append(node('option', { value, text: label }));
    }
    const sync = () => {
      fields.name.hidden = spec.kind === 'box';
      fields.top.hidden = spec.kind !== 'date';
      fields.date.hidden = spec.kind !== 'date';
      fields.text.hidden = spec.kind !== 'box';
      fields.preset.hidden = spec.kind !== 'box';
      redraw();
    };
    kind.addEventListener('change', () => { spec.kind = kind.value; sync(); });
    row('kind', '種類', kind);
    row('name', '名前', text('name', '例: 山田'));
    row('top', '上段（部署など）', text('top', '例: 営業部'));
    row('date', '日付', text('date', '2026.10.10'));
    const textInput = text('text', '例: 済');
    row('text', '文言', textInput);
    const preset = node('div', { style: 'display:flex;gap:4px;flex-wrap:wrap' }, SEAL_PRESETS.map((word) => node('button', {
      class: 'btn small', text: word,
      onclick: () => { spec.text = word; textInput.value = word; redraw(); },
    })));
    row('preset', 'よく使う文言', preset);
    row('colour', '色', node('div', { class: 'seg', style: 'flex:none' }, [['#d7261e', '朱'], ['#1c1f26', '黒'], ['#1e5bb8', '青']].map(([value, label]) => node('button', {
      text: label, class: value === spec.colour ? 'active' : '',
      onclick: (e) => {
        spec.colour = value;
        for (const other of e.target.parentElement.children) other.classList.toggle('active', other === e.target);
        redraw();
      },
    }))));
    sync();
    return node('div', {}, [rows, node('div', { class: 'sig-preview', style: 'height:170px' }, canvas)]);
  };

  return customDialog({
    title: 'はんこを作る',
    intro: '作ったはんこは画像としてページに置かれ、位置と大きさを調整できます。',
    confirmLabel: 'ページに置く',
    build,
    onConfirm: async () => {
      await redraw();
      const cropped = trim(canvas, 4);
      if (!cropped) return false;
      // Real seals are small: about 12 mm across for a name seal.
      const height = spec.kind === 'box' ? 26 : spec.kind === 'date' ? 42 : 34;
      return {
        image: cropped.toDataURL('image/png'), width: cropped.width, height: cropped.height,
        points: [height * (cropped.width / cropped.height), height],
      };
    },
  });
}
