/**
 * The whole reason webapp/ exists: it makes the app run with no server at
 * all. Everything the original app.js used to POST to FastAPI is instead
 * answered here, inside the same tab, by a Python interpreter compiled to
 * WebAssembly (Pyodide) running PyMuPDF's wasm build.
 *
 * The trick is that app.js is not aware of any of this — it still calls
 * `fetch('/api/...')` exactly as it did against the real server. This file
 * replaces `window.fetch` with a router that recognises those `/api/` calls,
 * translates them into a `dispatch(action, payload)` call into Python, and
 * wraps the answer back into a real `Response` object. Everything else
 * (fonts, pdf.js's own asset fetches) is passed through to the real fetch
 * untouched.
 *
 * The one thing a fetch shim cannot intercept is a full page navigation —
 * `window.location.href = downloadUrl` — so the download button in app.js is
 * the one call site that was changed to call `window.pdfStudioDownload()`
 * (defined at the bottom of this file) instead.
 */

const PYODIDE_VERSION = '0.28.0';
const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const WHEEL_NAME = 'pymupdf-1.28.2-cp313-abi3-pyodide_2025_0_wasm32.whl';
const PY_MODULES = [
  '__init__', 'common', 'annots', 'content', 'pages', 'export',
  'forms', 'measure', 'compare', 'signing', 'accessibility', 'session', 'bridge',
];

function setBootStatus(text) {
  const node = document.getElementById('pyodideBootStatus');
  if (node) node.textContent = text;
}

function bootFailed(err) {
  console.error(err);
  const node = document.getElementById('pyodideBootStatus');
  if (node) {
    node.textContent = `起動に失敗しました: ${err.message || err}`;
    node.classList.add('error');
  }
}

let pdfstudioBridge = null;
let pyodide = null;
const realFetch = window.fetch.bind(window);
let resolveReady;
const ready = new Promise((resolve) => { resolveReady = resolve; });

// Installed synchronously, before anything else on the page runs an `import`.
// pdf.js captures a reference to `fetch` when its own module is first
// evaluated, which happens as soon as app.js's module graph is instantiated
// — long before Pyodide has finished booting. If the shim were installed
// only at the end of boot(), that early capture would already hold the
// native fetch and every /api/doc/{id}/file request would go straight to
// the real network (and 404, since there is no server) instead of here.
installFetchShim();

async function boot() {
  setBootStatus('Python 実行環境を読み込んでいます…');
  const script = document.createElement('script');
  script.src = `${PYODIDE_CDN}pyodide.js`;
  await new Promise((resolve, reject) => {
    script.onload = resolve;
    script.onerror = () => reject(new Error('pyodide.js を取得できませんでした'));
    document.head.append(script);
  });

  pyodide = await loadPyodide({ indexURL: PYODIDE_CDN });

  setBootStatus('PDF エンジン（PyMuPDF）を読み込んでいます…');
  await pyodide.loadPackage('micropip');
  const micropip = pyodide.pyimport('micropip');

  const wheelUrl = new URL(`../vendor/pymupdf-wasm/${WHEEL_NAME}`, import.meta.url);
  const wheelResponse = await realFetch(wheelUrl);
  if (!wheelResponse.ok) throw new Error(`PyMuPDF wasm を取得できませんでした (${wheelResponse.status})`);
  const wheelBytes = new Uint8Array(await wheelResponse.arrayBuffer());
  pyodide.FS.writeFile(`/${WHEEL_NAME}`, wheelBytes);
  await micropip.install(`emfs:/${WHEEL_NAME}`);

  setBootStatus('アプリ本体を読み込んでいます…');
  pyodide.FS.mkdirTree('/home/pyodide/pdfstudio');
  await Promise.all(PY_MODULES.map(async (name) => {
    const url = new URL(`../py/pdfstudio/${name}.py`, import.meta.url);
    const response = await realFetch(url);
    if (!response.ok) throw new Error(`${name}.py を取得できませんでした (${response.status})`);
    const text = await response.text();
    pyodide.FS.writeFile(`/home/pyodide/pdfstudio/${name}.py`, text);
  }));
  pyodide.runPython(`
import sys
if '/home/pyodide' not in sys.path:
    sys.path.insert(0, '/home/pyodide')
import importlib
import pdfstudio.bridge
importlib.reload(pdfstudio.bridge)
`);
  pdfstudioBridge = pyodide.pyimport('pdfstudio.bridge');

  setBootStatus('準備完了');
  resolveReady();
  document.dispatchEvent(new CustomEvent('pdfstudio:ready'));
  const overlay = document.getElementById('pyodideBoot');
  if (overlay) overlay.classList.add('done');
}

// ==================================================================== bridge call

function callBridge(action, payload) {
  const pyPayload = pyodide.toPy(payload || {});
  let pyResult;
  try {
    pyResult = pdfstudioBridge.dispatch(action, pyPayload);
    return pyResult.toJs({ dict_converter: Object.fromEntries });
  } finally {
    pyPayload.destroy();
    if (pyResult && typeof pyResult.destroy === 'function') pyResult.destroy();
  }
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
    if (file) { out.name = file.name; out.data = await fileBytes(file); out._file = file; }
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
  if (parts[0] !== 'doc' || parts.length < 2) return null;

  const docId = parts[1];
  const rest = parts.slice(2);
  const base = { docId };

  if (rest.length === 0 && method === 'GET') return { action: 'describe', payload: base };
  if (rest[0] === 'file' && method === 'GET') return { action: 'file', payload: base };
  if (rest[0] === 'download' && method === 'GET') return { action: 'download', payload: base };
  if (rest[0] === 'annots' && method === 'POST') {
    return { action: 'annots.save', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'search' && method === 'POST') {
    return { action: 'search', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'flatten' && method === 'POST') {
    return { action: 'flatten', payload: await readPayload(init, base) };
  }
  if (rest[0] === 'clear-annots' && method === 'POST') {
    return { action: 'clear-annots', payload: base };
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
    return { action: 'optimise', payload: base };
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
      const matched = await route(url, init);
      if (matched) {
        await ready;
        const result = callBridge(matched.action, matched.payload);
        return resultToResponse(result);
      }
      return new Response(JSON.stringify({ detail: `未対応の操作です: ${url.pathname}` }), {
        status: 404, headers: { 'Content-Type': 'application/json' },
      });
    }
    return realFetch(input, init);
  };
}

// ==================================================================== download

/** The one call the fetch shim cannot reach: a full-page navigation. */
window.pdfStudioDownload = function pdfStudioDownload(docId) {
  const result = callBridge('download', { docId });
  const bytes = result.data instanceof Uint8Array ? result.data : new Uint8Array(result.data || []);
  const blob = new Blob([bytes], { type: result.mediaType || 'application/pdf' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = result.filename || 'document.pdf';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
};

boot().catch(bootFailed);
