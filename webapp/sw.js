// Service worker: makes the app start fast the second time, and work with no
// connection at all once everything has been fetched once.
//
// Two kinds of file, two rules:
//
// - Things that never change at a given URL — the PDF engine, pdf.js, the
//   fonts, the versioned Pyodide runtime on the CDN — are served from the
//   cache when present. They are tens of megabytes; fetching them again on
//   every visit is what made start-up slow.
// - The app's own code and pages are fetched fresh whenever there is a
//   connection, and only fall back to the cached copy offline. A cache-first
//   rule here would pin people to an old version after an update.
//
// PDFs never pass through here: they are handled inside the page.

const SHELL = 'pdfstudio-shell-v1';
const FIXED = 'pdfstudio-fixed-v1';

const FIXED_HOSTS = new Set(['cdn.jsdelivr.net', 'tessdata.projectnaptha.com']);

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith('pdfstudio-') && n !== SHELL && n !== FIXED).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

function isFixed(url) {
  if (FIXED_HOSTS.has(url.hostname)) return true;
  return url.origin === self.location.origin && url.pathname.includes('/vendor/');
}

async function cacheFirst(request) {
  const cache = await caches.open(FIXED);
  const hit = await cache.match(request, { ignoreVary: true });
  if (hit) return hit;
  const response = await fetch(request);
  // Opaque responses (a <script> from the CDN) have status 0 and are fine to keep.
  if (response.ok || response.type === 'opaque') cache.put(request, response.clone()).catch(() => {});
  return response;
}

async function networkFirst(request) {
  const cache = await caches.open(SHELL);
  try {
    // 'no-cache' makes the browser check with the server instead of trusting
    // its own HTTP cache, so an update is picked up on the next load rather
    // than ten minutes later, half old and half new.
    const response = await fetch(request, { cache: 'no-cache' });
    if (response.ok) cache.put(request, response.clone()).catch(() => {});
    return response;
  } catch (err) {
    const hit = await cache.match(request, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
  if (url.pathname.includes('/api/')) return;
  if (request.headers.has('range')) return;
  if (isFixed(url)) {
    event.respondWith(cacheFirst(request).catch(() => fetch(request)));
  } else if (url.origin === self.location.origin) {
    event.respondWith(networkFirst(request));
  }
});
