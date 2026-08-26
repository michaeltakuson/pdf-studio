// pdf.js integration: page rendering, zoom, lazy canvas rendering, text layer.
//
// Model coordinates equal pdf.js viewport coordinates at scale 1 — the page as
// the reader sees it, rotation included. The backend converts to and from
// PyMuPDF's authored frame at the API boundary (see backend/common.py), so
// nothing here needs to know about /Rotate. Each page's SVG overlay carries
// viewBox="0 0 w h" while being sized to w*scale, so annotations are drawn in
// raw PDF points and the browser handles every zoom level for free.

import * as pdfjsLib from '/vendor/pdfjs/build/pdf.mjs';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/build/pdf.worker.mjs';

const VENDOR = '/vendor/pdfjs/';

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
    window.addEventListener('resize', () => {
      if (this.zoomMode.startsWith('fit')) this.setZoom(this.zoomMode);
    });
  }

  async load(url) {
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
      url,
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
    this.setZoom(this.zoomMode);
    for (const view of this.pageViews) this._observer.observe(view.wrap);
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

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'annot-layer');
    svg.setAttribute('viewBox', `0 0 ${base.width} ${base.height}`);
    svg.setAttribute('preserveAspectRatio', 'none');

    const draw = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    draw.setAttribute('class', 'draw-layer idle');
    draw.setAttribute('viewBox', `0 0 ${base.width} ${base.height}`);
    draw.setAttribute('preserveAspectRatio', 'none');

    wrap.append(canvas, textLayer, svg, draw);
    this.container.append(wrap);

    return {
      index,
      page,
      wrap,
      canvas,
      textLayer,
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
      // Annotations are drawn by our own overlay, which is editable. Letting
      // pdf.js paint them too would double every mark on the page.
      view.renderTask = view.page.render({
        canvasContext: context,
        viewport,
        canvas: view.canvas,
        annotationMode: pdfjsLib.AnnotationMode.DISABLE,
      });
      try {
        await view.renderTask.promise;
      } catch (err) {
        if (err?.name !== 'RenderingCancelledException') throw err;
        return;
      }
      await this._renderText(view, scale);
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

  setZoom(mode) {
    this.zoomMode = String(mode);
    const first = this.pageViews[0];
    if (!first) return;

    if (this.zoomMode === 'fit-width' || this.zoomMode === 'fit-page') {
      const padding = 56;
      const available = this.stage.clientWidth - padding;
      let scale = available / first.width;
      if (this.zoomMode === 'fit-page') {
        scale = Math.min(scale, (this.stage.clientHeight - padding) / first.height);
      }
      this.scale = Math.max(0.1, Math.min(8, scale));
    } else {
      this.scale = Math.max(0.1, Math.min(8, parseFloat(this.zoomMode) || 1));
    }

    for (const view of this.pageViews) {
      this._layoutPage(view);
      view.rendered = false;
    }
    for (const view of this._visibleViews()) this._renderPage(view);
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

  _visibleViews() {
    const top = this.stage.scrollTop - 400;
    const bottom = top + this.stage.clientHeight + 800;
    return this.pageViews.filter((view) => {
      const y = view.wrap.offsetTop;
      return y + view.wrap.offsetHeight >= top && y <= bottom;
    });
  }

  _updateCurrentPage() {
    const mid = this.stage.scrollTop + this.stage.clientHeight / 3;
    let best = 0;
    for (const view of this.pageViews) {
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

  setTextLayerActive(active) {
    for (const view of this.pageViews) {
      view.textLayer.classList.toggle('inactive', !active);
    }
  }

  setCursor(name) {
    for (const view of this.pageViews) view.wrap.dataset.cursor = name || '';
  }

  setDrawActive(active) {
    for (const view of this.pageViews) view.draw.classList.toggle('idle', !active);
  }
}
