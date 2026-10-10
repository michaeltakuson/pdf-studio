// pdf.js integration: page rendering, zoom, lazy canvas rendering, text layer.
//
// Model coordinates equal pdf.js viewport coordinates at scale 1 — the page as
// the reader sees it, rotation included. The backend converts to and from
// PyMuPDF's authored frame at the API boundary (see backend/common.py), so
// nothing here needs to know about /Rotate. Each page's SVG overlay carries
// viewBox="0 0 w h" while being sized to w*scale, so annotations are drawn in
// raw PDF points and the browser handles every zoom level for free.

import * as pdfjsLib from '../vendor/pdfjs/build/pdf.mjs';

// Computed from this module's own URL rather than root-relative, so it keeps
// working when the site is served from a sub-path (e.g. GitHub Pages project
// sites at username.github.io/repo/).
const VENDOR = new URL('../vendor/pdfjs/', import.meta.url).href;

pdfjsLib.GlobalWorkerOptions.workerSrc = `${VENDOR}build/pdf.worker.mjs`;

export class Viewer extends EventTarget {
  constructor(container, stage) {
    super();
    this.container = container;
    this.stage = stage;
    this.pdf = null;
    this.loadingTask = null;
    this.scale = 1;
    // Reading is the first thing anyone does, so open at a readable width.
    this.zoomMode = 'fit-width';
    this.pageViews = [];
    this.currentPage = 0;
    this._renderQueue = new Map();

    this._observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const view = this.pageViews[Number(entry.target.dataset.page)];
          if (!view) continue;
          if (entry.isIntersecting) this._renderPage(view);
        }
        this._updateCurrentPage();
      },
      { root: stage, rootMargin: '400px 0px' },
    );

    stage.addEventListener('scroll', () => this._updateCurrentPage(), { passive: true });
    // Ctrl+wheel (and a trackpad pinch, which browsers report the same way)
    // zooms the document. Left alone it would zoom the whole interface.
    stage.addEventListener('wheel', (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      if (!this.pageViews.length) return;
      const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0022));
      this.zoomAt(this.scale * factor, event.clientX, event.clientY);
    }, { passive: false });
    window.addEventListener('resize', () => {
      if (this.zoomMode.startsWith('fit')) this.setZoom(this.zoomMode);
    });
  }

  async load(url, { keepPosition = false } = {}) {
    const anchor = keepPosition && this.pageViews.length ? this.position() : null;
    // pdf.js picks its own network stream for a `url` source — sometimes
    // XMLHttpRequest, depending on internal checks the bridge's fetch shim
    // cannot see or influence — so a "/api/..." source never reliably reaches
    // window.fetch. Fetching the bytes here instead, through this module's
    // own ordinary fetch() call (which the shim does intercept), and handing
    // pdf.js the resulting bytes via `data` sidesteps pdf.js's URL handling
    // entirely: there is no longer a network decision for it to make.
    let data;
    if (url instanceof Uint8Array) {
      // Bytes straight from a file the user picked: shown at once, before
      // the editing engine has even finished loading.
      data = url;
    } else {
      const response = await fetch(new URL(url, window.location.href).href);
      if (!response.ok) throw new Error(`PDFの取得に失敗しました (status ${response.status})`);
      data = new Uint8Array(await response.arrayBuffer());
    }

    if (this.loadingTask) {
      // Tear down through the loading task: it owns the worker port, and the
      // document proxy itself has no destroy in this pdf.js version.
      await this.loadingTask.destroy();
      this.loadingTask = null;
      this.pdf = null;
    }
    // Stop watching the previous document's pages before discarding them.
    this._observer.disconnect();
    this.container.textContent = '';
    this.pageViews = [];

    const task = pdfjsLib.getDocument({
      data,
      cMapUrl: `${VENDOR}cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${VENDOR}standard_fonts/`,
      wasmUrl: `${VENDOR}wasm/`,
      iccUrl: `${VENDOR}iccs/`,
    });
    this.loadingTask = task;
    this.pdf = await task.promise;

    for (let i = 0; i < this.pdf.numPages; i += 1) {
      const page = await this.pdf.getPage(i + 1);
      this.pageViews.push(this._createPageView(page, i));
    }
    // Lay the pages out before watching them. Until they have a height they all
    // sit at the same point, so every page would count as on-screen and a long
    // document would render every page at once.
    this.setZoom(this.zoomMode, { keep: false });
    this.setMode(this.mode || 'select');
    this.setCursor(this.cursor || '');
    for (const view of this.pageViews) this._observer.observe(view.wrap);
    // Reloading after a page operation must not throw the reader back to
    // page one of a two-hundred-page document.
    if (anchor) this.restore(anchor);
    else this.stage.scrollTop = 0;
    this.currentPage = -1;
    this._updateCurrentPage();
    this.dispatchEvent(new CustomEvent('loaded'));
  }

  _createPageView(page, index) {
    const base = page.getViewport({ scale: 1 });

    const wrap = document.createElement('div');
    wrap.className = 'page-wrap';
    wrap.dataset.page = String(index);

    const canvas = document.createElement('canvas');
    canvas.className = 'page-canvas';

    const textLayer = document.createElement('div');
    textLayer.className = 'text-layer';

    const links = document.createElement('div');
    links.className = 'link-layer';

    // Form fields are real inputs laid over the page, filled in where they are.
    const forms = document.createElement('div');
    forms.className = 'form-layer';

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'annot-layer');
    svg.setAttribute('viewBox', `0 0 ${base.width} ${base.height}`);
    svg.setAttribute('preserveAspectRatio', 'none');

    const draw = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    draw.setAttribute('class', 'draw-layer');
    draw.setAttribute('viewBox', `0 0 ${base.width} ${base.height}`);
    draw.setAttribute('preserveAspectRatio', 'none');

    wrap.append(canvas, textLayer, links, forms, svg, draw);
    this.container.append(wrap);

    return {
      index,
      page,
      wrap,
      canvas,
      textLayer,
      links,
      forms,
      svg,
      draw,
      width: base.width,
      height: base.height,
      rotation: ((page.rotate % 360) + 360) % 360,
      rendered: false,
      renderTask: null,
    };
  }

  _layoutPage(view) {
    const w = view.width * this.scale;
    const h = view.height * this.scale;
    view.wrap.style.width = `${w}px`;
    view.wrap.style.height = `${h}px`;
    // Stretch the bitmap already there until the sharp one is ready.
    view.canvas.style.width = `${w}px`;
    view.canvas.style.height = `${h}px`;
    view.wrap.style.setProperty('--scale-factor', String(this.scale));
    view.wrap.style.setProperty('--total-scale-factor', String(this.scale));
    // pdf.js sizes its text layer with round(down, …, var(--scale-round-x)).
    // Leaving those undefined makes the declaration invalid, the layer falls
    // back to filling its parent, and every percentage-positioned span lands in
    // the wrong place — visibly so on a rotated page.
    view.wrap.style.setProperty('--scale-round-x', '1px');
    view.wrap.style.setProperty('--scale-round-y', '1px');
    for (const el of [view.svg, view.draw]) {
      el.setAttribute('width', String(w));
      el.setAttribute('height', String(h));
    }
    // The text layer is laid out in the page's own orientation, so it has to be
    // rotated into place over the rendered canvas.
    view.textLayer.style.transformOrigin = '0 0';
    view.textLayer.style.transform = {
      90: `translate(${w}px, 0) rotate(90deg)`,
      180: `translate(${w}px, ${h}px) rotate(180deg)`,
      270: `translate(0, ${h}px) rotate(270deg)`,
    }[view.rotation] || 'none';
  }

  async _renderPage(view) {
    if (view.rendered && view.renderedScale === this.scale) return;
    if (this._renderQueue.has(view.index)) return;

    const scale = this.scale;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const viewport = view.page.getViewport({ scale: scale * dpr });

    const job = (async () => {
      view.canvas.width = Math.floor(viewport.width);
      view.canvas.height = Math.floor(viewport.height);
      view.canvas.style.width = `${view.width * scale}px`;
      view.canvas.style.height = `${view.height * scale}px`;

      if (view.renderTask) {
        try { view.renderTask.cancel(); } catch { /* already done */ }
      }
      const context = view.canvas.getContext('2d', { alpha: false });
      // The file handed to pdf.js has had the editable markup taken out (the
      // overlay draws that), so what is left to paint here is everything the
      // overlay does not handle: form fields, attachments, media.
      view.renderTask = view.page.render({
        canvasContext: context,
        viewport,
        canvas: view.canvas,
        annotationMode: pdfjsLib.AnnotationMode.ENABLE,
      });
      try {
        await view.renderTask.promise;
      } catch (err) {
        if (err?.name !== 'RenderingCancelledException') throw err;
        return;
      }
      await this._renderText(view, scale);
      this._renderLinks(view);
      view.rendered = true;
      view.renderedScale = scale;
    })().finally(() => this._renderQueue.delete(view.index));

    this._renderQueue.set(view.index, job);
    return job;
  }

  async _renderText(view, scale) {
    if (view.textScale === scale && view.textLayer.childElementCount) return;
    view.textLayer.textContent = '';
    const layer = new pdfjsLib.TextLayer({
      textContentSource: view.page.streamTextContent(),
      container: view.textLayer,
      viewport: view.page.getViewport({ scale }),
    });
    await layer.render();
    view.textScale = scale;
  }

  /** Clickable areas for the document's own links (contents pages, URLs). */
  async _renderLinks(view) {
    if (view.linksDone) return;
    view.linksDone = true;
    let annotations = [];
    try { annotations = await view.page.getAnnotations({ intent: 'display' }); } catch { return; }
    const viewport = view.page.getViewport({ scale: 1 });
    for (const item of annotations) {
      if (item.subtype !== 'Link' || !item.rect) continue;
      if (!item.url && !item.dest && !item.unsafeUrl) continue;
      // PDF user space to the page as shown (the viewport matrix carries the
      // flip to a top-left origin and any page rotation).
      const [a, b, c, d, e, f] = viewport.transform;
      const point = (x, y) => [a * x + c * y + e, b * x + d * y + f];
      const [x0, y0] = point(item.rect[0], item.rect[1]);
      const [x1, y1] = point(item.rect[2], item.rect[3]);
      const node = document.createElement('a');
      node.className = 'pdf-link';
      node.style.left = `${(Math.min(x0, x1) / view.width) * 100}%`;
      node.style.top = `${(Math.min(y0, y1) / view.height) * 100}%`;
      node.style.width = `${(Math.abs(x1 - x0) / view.width) * 100}%`;
      node.style.height = `${(Math.abs(y1 - y0) / view.height) * 100}%`;
      const url = item.url || item.unsafeUrl;
      if (url && /^(https?:|mailto:)/i.test(url)) {
        node.href = url;
        node.target = '_blank';
        node.rel = 'noopener noreferrer';
        node.title = url;
      } else if (item.dest) {
        node.href = '#';
        node.title = 'リンク先へ移動';
        node.addEventListener('click', (event) => { event.preventDefault(); this.goToDestination(item.dest); });
      } else {
        continue;
      }
      node.addEventListener('pointerdown', (event) => event.stopPropagation());
      view.links.append(node);
    }
  }

  async goToDestination(dest) {
    try {
      const explicit = typeof dest === 'string' ? await this.pdf.getDestination(dest) : dest;
      if (!explicit) return;
      const index = typeof explicit[0] === 'object' ? await this.pdf.getPageIndex(explicit[0]) : Number(explicit[0]);
      this.scrollToPage(index);
    } catch { /* a broken link in the file: nothing to go to */ }
  }

  /** Where the reader is: which page, and how far down it. */
  position() {
    const top = this.stage.scrollTop;
    let view = this.pageViews[0];
    for (const candidate of this.pageViews) {
      if (candidate.wrap.offsetTop <= top + 1) view = candidate; else break;
    }
    if (!view) return null;
    return { page: view.index, offset: (top - view.wrap.offsetTop) / Math.max(1, view.wrap.offsetHeight) };
  }

  restore(anchor) {
    const view = this.pageViews[Math.min(anchor.page, this.pageViews.length - 1)];
    if (!view) return;
    this.stage.scrollTop = view.wrap.offsetTop + anchor.offset * view.wrap.offsetHeight;
  }

  /** Zoom to an exact scale, keeping the point under the pointer where it is. */
  zoomAt(scale, clientX, clientY) {
    const next = Math.max(0.2, Math.min(8, scale));
    if (Math.abs(next - this.scale) < 0.001) return;
    const box = this.stage.getBoundingClientRect();
    const px = clientX - box.left;
    const py = clientY - box.top;
    const view = this.viewFromPoint(clientX, clientY) || this.pageViews[this.currentPage] || this.pageViews[0];
    const wrapBox = view.wrap.getBoundingClientRect();
    const fx = (clientX - wrapBox.left) / wrapBox.width;
    const fy = (clientY - wrapBox.top) / wrapBox.height;
    this.setZoom(String(Number(next.toFixed(3))), { keep: false });
    const after = view.wrap.getBoundingClientRect();
    this.stage.scrollLeft += after.left + fx * after.width - box.left - px;
    this.stage.scrollTop += after.top + fy * after.height - box.top - py;
  }

  viewFromPoint(clientX, clientY) {
    for (const view of this.pageViews) {
      const box = view.wrap.getBoundingClientRect();
      if (clientY >= box.top && clientY <= box.bottom && clientX >= box.left && clientX <= box.right) return view;
    }
    return null;
  }

  setZoom(mode, { keep = true } = {}) {
    this.zoomMode = String(mode);
    const first = this.pageViews[0];
    if (!first) return;
    const anchor = keep ? this.position() : null;

    if (this.zoomMode === 'fit-width' || this.zoomMode === 'fit-page') {
      const padding = 52;
      // Fit the page being read, not page one: a document can mix portrait
      // and landscape pages.
      const target = this.pageViews[this.currentPage] || first;
      // Side by side, each page gets half the width (less the gap between them).
      const columns = this.spread ? 2 : 1;
      const available = (this.stage.clientWidth - padding - (columns - 1) * 16) / columns;
      let scale = available / target.width;
      if (this.zoomMode === 'fit-page') {
        scale = Math.min(scale, (this.stage.clientHeight - padding) / target.height);
      }
      this.scale = Math.max(0.1, Math.min(8, scale));
    } else {
      this.scale = Math.max(0.1, Math.min(8, parseFloat(this.zoomMode) || 1));
    }

    for (const view of this.pageViews) {
      this._layoutPage(view);
      view.rendered = false;
    }
    if (this.spread) {
      // Exactly two pages per row at any zoom: the row is as wide as the two
      // widest pages, and the stage scrolls sideways when that is too much.
      const widest = Math.max(...this.pageViews.map((view) => view.width)) * this.scale;
      this.container.style.width = `${Math.ceil(widest * 2 + 16)}px`;
    } else {
      this.container.style.width = '';
    }
    if (anchor) this.restore(anchor);
    clearTimeout(this._zoomTimer);
    // Rendering is the slow part; wait for a wheel or pinch to settle.
    this._zoomTimer = setTimeout(() => {
      for (const view of this._visibleViews()) this._renderPage(view);
    }, 60);
    this.dispatchEvent(new CustomEvent('zoom', { detail: { scale: this.scale } }));
  }

  nudgeZoom(direction) {
    const steps = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, 6];
    const current = this.scale;
    const next = direction > 0
      ? steps.find((s) => s > current + 0.001) ?? steps[steps.length - 1]
      : [...steps].reverse().find((s) => s < current - 0.001) ?? steps[0];
    this.setZoom(String(next));
  }

  /** The pages on or near the screen. */
  visibleViews() {
    return this._visibleViews();
  }

  _visibleViews() {
    const top = this.stage.scrollTop - 400;
    const bottom = top + this.stage.clientHeight + 800;
    return this.pageViews.filter((view) => {
      // A page that is not laid out (hidden in slideshow mode) reports a
      // position of zero and would otherwise always count as on screen.
      if (!view.wrap.offsetParent) return false;
      const y = view.wrap.offsetTop;
      return y + view.wrap.offsetHeight >= top && y <= bottom;
    });
  }

  _updateCurrentPage() {
    const mid = this.stage.scrollTop + this.stage.clientHeight / 3;
    let best = 0;
    for (const view of this.pageViews) {
      if (!view.wrap.offsetParent) continue;   // hidden (slideshow shows one page)
      if (view.wrap.offsetTop <= mid) best = view.index;
      else break;
    }
    if (best !== this.currentPage) {
      this.currentPage = best;
      this.dispatchEvent(new CustomEvent('page', { detail: { page: best } }));
    }
  }

  scrollToPage(index, y = null) {
    const view = this.pageViews[index];
    if (!view) return;
    const offset = y === null ? 0 : Math.max(0, y * this.scale - 80);
    const top = Math.max(0, view.wrap.offsetTop + offset - 16);
    const from = this.stage.scrollTop;
    if (Math.abs(from - top) < 1) return;

    const gentle = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.stage.scrollTo({ top, behavior: gentle ? 'smooth' : 'auto' });
    // Not every browser honours smooth scrolling. If nothing has moved by the
    // time an animation would clearly be under way, jump — a bookmark that does
    // nothing when clicked is worse than one that arrives abruptly.
    if (!gentle) return;
    setTimeout(() => {
      if (Math.abs(this.stage.scrollTop - from) < 1) this.stage.scrollTop = top;
    }, 150);
  }

  /** Convert a pointer event into unscaled PDF page coordinates. */
  toPageCoords(view, event) {
    const rect = view.wrap.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / this.scale,
      y: (event.clientY - rect.top) / this.scale,
    };
  }

  viewFromEvent(event) {
    const wrap = event.target.closest?.('.page-wrap');
    return wrap ? this.pageViews[Number(wrap.dataset.page)] : null;
  }

  /** What a press on the page means right now; CSS does the hit-testing. */
  setMode(mode) {
    this.mode = mode;
    this.container.dataset.mode = mode;
  }

  setCursor(name) {
    this.cursor = name || '';
    for (const view of this.pageViews) view.wrap.dataset.cursor = this.cursor;
  }

  /** Show pages two abreast, like an open book. */
  setSpread(on) {
    this.spread = !!on;
    this.container.classList.toggle('spread', this.spread);
    if (this.pageViews.length) this.setZoom(this.zoomMode.startsWith('fit') ? this.zoomMode : 'fit-width');
  }

  setInvert(on) {
    this.container.classList.toggle('invert', !!on);
  }
}
