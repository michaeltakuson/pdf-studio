// The PDF engine, off the main thread.
//
// Python (Pyodide) and PyMuPDF run in this worker. Loading them takes tens of
// seconds of compilation, and rewriting a large document takes seconds more;
// on the page's own thread either would freeze scrolling, typing and every
// button for that long. Here they cost the user nothing: a PDF can be opened
// and read while the engine is still starting, and the page stays responsive
// while a 50 MB file is being saved.
//
// The page talks to it with {id, action, payload} messages and gets back
// {id, result}; see bridge.js.

const PYODIDE_VERSION = '0.28.0';
const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const WHEEL_NAME = 'pymupdf-1.28.2-cp313-abi3-pyodide_2025_0_wasm32.whl';
const PY_MODULES = [
  '__init__', 'common', 'textap', 'annots', 'content', 'pages', 'export',
  'forms', 'measure', 'compare', 'signing', 'accessibility', 'session', 'bridge',
];
const BASE = new URL('../', self.location.href);

let pyodide = null;
let engine = null;
const loadedFonts = new Set();
let fontTools = null;

const status = (text) => self.postMessage({ type: 'status', text });

async function boot() {
  const started = performance.now();
  const lap = (label) => console.info(`[boot] ${label}: ${Math.round(performance.now() - started)}ms`);
  status('実行環境を読み込んでいます…');

  // Everything that can be fetched while the runtime compiles is started now.
  const wheelUrl = new URL(`vendor/pymupdf-wasm/${WHEEL_NAME}`, BASE);
  const wheelWarm = fetch(wheelUrl).then((r) => r.arrayBuffer()).catch(() => null);
  const sources = Promise.all(PY_MODULES.map(async (name) => {
    const response = await fetch(new URL(`py/pdfstudio/${name}.py`, BASE));
    if (!response.ok) throw new Error(`${name}.py を取得できませんでした (${response.status})`);
    return [name, await response.text()];
  }));

  try {
    importScripts(`${PYODIDE_CDN}pyodide.js`);
  } catch {
    throw new Error('実行環境を取得できませんでした（インターネット接続を確認してください）');
  }
  pyodide = await self.loadPyodide({ indexURL: PYODIDE_CDN });
  lap('runtime');

  status('PDF エンジンを読み込んでいます…');
  // The wheel is loaded directly. Going through the package installer cost
  // several seconds of dependency resolution for a package with no
  // dependencies. The warm-up fetch above has already put it in the cache.
  await wheelWarm;
  await pyodide.loadPackage(wheelUrl.href, { messageCallback: () => {}, checkIntegrity: false });
  lap('engine unpacked');

  status('もうすぐ準備ができます…');
  pyodide.FS.mkdirTree('/home/pyodide/pdfstudio');
  for (const [name, text] of await sources) {
    pyodide.FS.writeFile(`/home/pyodide/pdfstudio/${name}.py`, text);
  }
  pyodide.runPython(`
import sys
if '/home/pyodide' not in sys.path:
    sys.path.insert(0, '/home/pyodide')
import pdfstudio.bridge
`);
  engine = pyodide.pyimport('pdfstudio.bridge');
  lap('ready');
}

/**
 * Hand the PDF writer the font files it is about to embed, and the subsetter
 * that cuts them down to the characters used.
 *
 * Both are fetched only when text is first saved — most sessions that only
 * read or highlight never pay for them — and the cache makes every later use
 * instant. If either fails (offline, say) saving still works: the writer
 * falls back to a standard Japanese font reference.
 */
async function ensureFonts(files) {
  const wanted = (files || []).filter((name) => /^[\w.-]+\.ttf$/.test(name) && !loadedFonts.has(name));
  if (!wanted.length) return;
  try {
    pyodide.FS.mkdirTree('/fonts');
    await Promise.all(wanted.map(async (name) => {
      const response = await fetch(new URL(`vendor/fonts/${name}`, BASE));
      if (!response.ok) throw new Error(`${name}: ${response.status}`);
      pyodide.FS.writeFile(`/fonts/${name}`, new Uint8Array(await response.arrayBuffer()));
      loadedFonts.add(name);
    }));
    fontTools = fontTools || pyodide.loadPackage('fonttools', { messageCallback: () => {} });
    await fontTools;
  } catch (err) {
    console.warn('フォントの準備に失敗しました（標準フォントで保存します）', err);
  }
}

function call(action, payload) {
  const pyPayload = pyodide.toPy(payload || {});
  let pyResult;
  try {
    pyResult = engine.dispatch(action, pyPayload);
    return pyResult.toJs({ dict_converter: Object.fromEntries });
  } finally {
    pyPayload.destroy();
    if (pyResult && typeof pyResult.destroy === 'function') pyResult.destroy();
  }
}

const ready = boot().then(
  () => { self.postMessage({ type: 'ready' }); },
  (err) => { self.postMessage({ type: 'failed', message: String(err?.message || err) }); throw err; },
);

// One request at a time, in order: the engine holds a single document state.
let queue = Promise.resolve();

self.addEventListener('message', (event) => {
  const { id, action, payload } = event.data || {};
  if (id === undefined) return;
  queue = queue.then(async () => {
    let result;
    try {
      await ready;
      if (Array.isArray(payload?.annots)) await ensureFonts(payload.fonts);
      result = call(action, payload);
    } catch (err) {
      result = { status: 500, json: { detail: String(err?.message || err) } };
    }
    const transfer = [];
    if (result && result.data !== undefined && result.data !== null) {
      // A copy that owns its buffer, so it can be handed over without copying again.
      const bytes = result.data instanceof Uint8Array ? result.data.slice() : new Uint8Array(result.data);
      result = { ...result, data: bytes };
      transfer.push(bytes.buffer);
    }
    self.postMessage({ id, result }, transfer);
  });
});
