/**
 * The seam between the page and the PDF engine.
 *
 * The rest of the app talks to the engine the way it would talk to a server:
 * `fetch('/api/...')`. Nothing is ever sent anywhere. This file replaces
 * `window.fetch` with a router that recognises those calls, turns each into
 * an (action, payload) message for the engine — Python and PyMuPDF compiled
 * to WebAssembly, running in a worker (engine-worker.js) — and wraps the
 * answer back into a real `Response`. Everything else passes through to the
 * real fetch untouched.
 *
 * Because the engine lives in a worker, the page never waits on it: a PDF can
 * be shown the moment it is picked, while the engine is still starting, and
 * the interface stays live while a large file is being written.
 */

function setBootStatus(text) {
  const node = document.getElementById('pyodideBootStatus');
  if (node) node.textContent = text;
}

const realFetch = window.fetch.bind(window);
let resolveReady;
let rejectReady;
const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
ready.catch(() => {});

// Installed before anything else on the page runs, so no module can capture
// the native fetch first.
installFetchShim();

const worker = new Worker(new URL('./engine-worker.js', import.meta.url));
const waiting = new Map();
let nextId = 1;

worker.addEventListener('message', (event) => {
  const message = event.data || {};
  if (message.type === 'status') { setBootStatus(message.text); return; }
  if (message.type === 'ready') {
    setBootStatus('準備完了');
    window.pdfStudioReady = true;
    resolveReady();
    document.getElementById('pyodideBoot')?.classList.add('done');
    document.dispatchEvent(new CustomEvent('pdfstudio:ready'));
    return;
  }
  if (message.type === 'failed') { engineFailed(message.message); return; }
  const resolve = waiting.get(message.id);
  if (resolve) { waiting.delete(message.id); resolve(message.result); }
});
worker.addEventListener('error', (event) => engineFailed(event.message || 'エンジンを起動できませんでした'));

function engineFailed(detail) {
  console.error(detail);
  const node = document.getElementById('pyodideBootStatus');
  if (node) { node.textContent = `読み込めませんでした: ${detail}`; node.classList.add('error'); }
  document.getElementById('pyodideBoot')?.classList.add('failed');
  rejectReady(new Error(detail));
  // Anything already waiting on the engine is answered rather than left hanging.
  for (const [id, resolve] of waiting) {
    waiting.delete(id);
    resolve({ status: 503, json: { detail: `編集機能を読み込めませんでした（${detail}）` } });
  }
  document.dispatchEvent(new CustomEvent('pdfstudio:failed', { detail }));
}

/** Ask the engine to do something; resolves with its {status, json|data, ...}. */
function callEngine(action, payload) {
  return new Promise((resolve) => {
    const id = nextId;
    nextId += 1;
    waiting.set(id, resolve);
    // File contents are handed over, not copied: a 50 MB PDF would otherwise
    // exist twice for a moment.
    const transfer = [];
    if (payload?.data instanceof Uint8Array) transfer.push(payload.data.buffer);
    for (const file of payload?.files || []) if (file.data instanceof Uint8Array) transfer.push(file.data.buffer);
    worker.postMessage({ id, action, payload }, transfer);
  });
}

function resultToResponse(result) {
  const status = result.status ?? 200;
  const headers = new Headers();
  if (result.filename) {
    headers.set('Content-Disposition', `attachment; filename*=UTF-8''${encodeURIComponent(result.filename)}`);
  }
  if (result.data !== undefined && result.data !== null) {
    headers.set('Content-Type', result.mediaType || 'application/octet-stream');
    const bytes = result.data instanceof Uint8Array ? result.data : new Uint8Array(result.data);
    return new Response(bytes, { status, headers });
  }
  headers.set('Content-Type', 'application/json');
  return new Response(JSON.stringify(result.json ?? {}), { status, headers });
}

// ==================================================================== fetch routing

async function fileBytes(file) {
  return new Uint8Array(await file.arrayBuffer());
}

/** Parse whatever body shape the call used into a plain payload object. */
async function readPayload(init, extra) {
  const body = init && init.body;
  if (!body) return { ...extra };
  if (typeof body === 'string') {
    try { return { ...JSON.parse(body), ...extra }; } catch { return { ...extra }; }
  }
  if (body instanceof FormData) {
    const out = { ...extra };
    const files = body.getAll('files');
    if (files.length) out.files = await Promise.all(files.map(async (f) => ({
      filename: f.name, data: await fileBytes(f),
    })));
    const file = body.get('file');
    if (file) { out.name = file.name; out.data = await fileBytes(file); }
    const password = body.get('password');
    if (password != null) out.password = password;
    return out;
  }
  return { ...extra };
}

async function route(url, init) {
  const method = (init && init.method) || 'GET';
  const path = url.pathname.replace(/^\/api\/?/, '');
  const parts = path.split('/').filter(Boolean);
  const query = url.searchParams;

  if (parts[0] === 'ocr' && parts[1] === 'status') {
    return { status: 200, json: { installed: false, japanese: false } };
  }
  if (parts[0] === 'measure') {
    if (parts.length === 1 && method === 'POST') {
      return { action: 'measure.compute', payload: await readPayload(init) };
    }
    if (parts[1] === 'calibrate' && method === 'POST') {
      return { action: 'measure.calibrate', payload: await readPayload(init) };
    }
  }
  if (parts[0] === 'open' && method === 'POST') {
    const payload = await readPayload(init);
    return { action: 'open', payload };
  }
  if (parts[0] === 'new' && method === 'POST') {
    return { action: 'new', payload: await readPayload(init) };
  }
  if (parts[0] === 'from-images' && method === 'POST') {
    return { action: 'from-images', payload: await readPayload(init) };
  }
  if (parts[0] !== 'doc' || parts.length < 2) return null;

  let docId = parts[1];
  if (docId === 'preview') {
    // The document is on screen but the engine has not opened it yet: wait
    // for that, then carry on with the id the engine gave it.
    await window.pdfStudioAdopted;
    docId = window.pdfStudioRealId;
  }
  const rest = parts.slice(2);
  const base = { docId };

  if (rest.length === 0 && method === 'GET') return { action: 'describe', payload: base };
  if (rest[0] === 'file' && method === 'GET') return { action: 'file', payload: base };
  if (rest[0] === 'download' && method === 'GET') return { action: 'download', payload: base };
  if (rest[0] === 'download' && method === 'POST') {
    return { action: 'download', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'annots' && method === 'POST') {
    return { action: 'annots.save', payload: await readPayload(init, base) };
  }
  const SIMPLE = {
    undo: 'undo', compress: 'compress', nup: 'nup', split: 'split', images: 'images',
    'extract-ranges': 'pages.extract-ranges', 'ocr-apply': 'ocr.apply',
    outline: 'outline.set', metadata: 'metadata.set', snapshot: 'snapshot', 'outline-auto': 'outline.auto', 'fit-paper': 'fit-paper',
  };
  if (rest.length === 1 && SIMPLE[rest[0]] && method === 'POST') {
    return { action: SIMPLE[rest[0]], payload: await readPayload(init, base) };
  }
  if (rest[0] === 'plain-text' && method === 'GET') return { action: 'text', payload: base };
  if (rest[0] === 'stats' && method === 'GET') return { action: 'stats', payload: base };
  if (rest[0] === 'page-text' && rest[1] !== undefined && method === 'GET') {
    return { action: 'page.text', payload: { ...base, page: Number(rest[1]) } };
  }
  if (rest[0] === 'search' && method === 'POST') {
    return { action: 'search', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'flatten' && method === 'POST') {
    return { action: 'flatten', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'clear-annots' && method === 'POST') {
    return { action: 'clear-annots', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'export' && rest[1] && method === 'POST') {
    return { action: 'export', payload: await readPayload(init, { ...base, fmt: rest[1] }) };
  }
  if (rest[0] === 'import-xfdf' && method === 'POST') {
    const form = init.body;
    const file = form.get('file');
    const xml = file ? await file.text() : '';
    return { action: 'import-xfdf', payload: { ...base, xml } };
  }
  if (rest[0] === 'pages' && rest[1] === 'extract' && method === 'POST') {
    return { action: 'pages.extract', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'pages' && rest[1] && method === 'POST') {
    return { action: 'pages.action', payload: await readPayload(init, { ...base, action: rest[1] }) };
  }
  if (rest[0] === 'merge' && method === 'POST') {
    const payload = await readPayload(init, base);
    if (query.get('at') != null) payload.at = Number(query.get('at'));
    return { action: 'merge', payload };
  }
  if (rest[0] === 'stamp-pages' && method === 'POST') {
    return { action: 'stamp-pages', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'redact' && rest[1] === 'apply' && method === 'POST') {
    return { action: 'redact.apply', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'redact' && rest[1] === 'search' && method === 'POST') {
    return { action: 'redact.search', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'scrub' && method === 'POST') {
    return { action: 'scrub', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'optimise' && method === 'POST') {
    return { action: 'optimise', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'protect' && method === 'POST') {
    return { action: 'protect', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'text' && rest[1] === 'replace' && method === 'POST') {
    return { action: 'text.replace', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'text' && rest[1] === 'search-replace' && method === 'POST') {
    return { action: 'text.search-replace', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'text' && rest[1] !== undefined && method === 'GET') {
    return { action: 'text.blocks', payload: { ...base, page: Number(rest[1]) } };
  }
  if (rest[0] === 'image' && method === 'POST') {
    const payload = await readPayload(init, base);
    payload.pageIndex = Number(query.get('page_index') || 0);
    payload.rect = (query.get('rect') || '').split(',').map(Number);
    return { action: 'image.insert', payload };
  }
  if (rest[0] === 'takeoff' && method === 'POST') {
    const payload = await readPayload(init, base);
    return { action: payload.csv ? 'takeoff.csv' : 'takeoff', payload };
  }
  if (rest[0] === 'fields') {
    if (rest.length === 1 && method === 'GET') return { action: 'fields.list', payload: base };
    if (rest.length === 1 && method === 'POST') {
      return { action: 'fields.add', payload: await readPayload(init, base) };
    }
    if (rest[1] === 'fill' && method === 'POST') {
      return { action: 'fields.fill', payload: await readPayload(init, base) };
    }
    if (rest[1] === 'detect' && method === 'POST') {
      return { action: 'fields.detect', payload: await readPayload(init, base) };
    }
    if (rest[1] === 'export' && rest[2] && method === 'POST') {
      return { action: 'fields.export', payload: { ...base, fmt: rest[2] } };
    }
    if (rest[1] === 'import' && method === 'POST') {
      const form = init.body;
      const file = form.get('file');
      const text = file ? await file.text() : '';
      return { action: 'fields.import', payload: { ...base, text } };
    }
    if (rest[1] === 'collate' && method === 'POST') {
      return { action: 'fields.collate', payload: await readPayload(init, base) };
    }
    if (rest[1] !== undefined && method === 'PATCH') {
      return { action: 'fields.patch', payload: await readPayload(init, { ...base, xref: Number(rest[1]) }) };
    }
    if (rest[1] !== undefined && method === 'DELETE') {
      return { action: 'fields.delete', payload: { ...base, xref: Number(rest[1]) } };
    }
  }
  if (rest[0] === 'compare' && method === 'POST') {
    const payload = await readPayload(init, base);
    payload.author = query.get('author') || '';
    const overlay = query.get('mode') === 'overlay';
    return { action: overlay ? 'compare.overlay' : 'compare.diff', payload };
  }
  if (rest[0] === 'signatures' && method === 'GET') {
    return { action: 'signatures.state', payload: base };
  }
  if (rest[0] === 'sign' && method === 'POST') {
    return { action: 'sign', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'accessibility') {
    if (rest.length === 1 && method === 'GET') return { action: 'accessibility.audit', payload: base };
    if (rest[1] === 'autotag' && method === 'POST') {
      return { action: 'accessibility.autotag', payload: await readPayload(init, base) };
    }
    if (rest[1] === 'alt' && method === 'POST') {
      return { action: 'accessibility.alt', payload: await readPayload(init, base) };
    }
    if (rest[1] === 'order' && rest[2] !== undefined && method === 'GET') {
      return { action: 'accessibility.order', payload: { ...base, pageIndex: Number(rest[2]) } };
    }
  }
  if (rest[0] === 'close' && method === 'POST') {
    return { action: 'close', payload: base };
  }
  return null;
}

function installFetchShim() {
  window.fetch = async function pdfStudioFetch(input, init = {}) {
    const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
    if (url.pathname.startsWith('/api/')) {
      let matched;
      try {
        matched = await route(url, init);
      } catch (err) {
        return resultToResponse({ status: 503, json: { detail: `この文書を編集用に開けませんでした（${err.message || err}）` } });
      }
      if (!matched) {
        return new Response(JSON.stringify({ detail: `未対応の操作です: ${url.pathname}` }), {
          status: 404, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (matched.status) return resultToResponse(matched);   // answered without the engine
      try {
        await ready;
      } catch (err) {
        return resultToResponse({ status: 503, json: { detail: `編集機能を読み込めませんでした（${err.message}）` } });
      }
      return resultToResponse(await callEngine(matched.action, matched.payload));
    }
    return realFetch(input, init);
  };
}
