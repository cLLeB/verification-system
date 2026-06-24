/* Minimal service worker: cache the app shell for instant loads / offline UI.
   API calls (/api, /v1, /admin) are always network — never cache identity ops. */
const CACHE = 'faceverify-v7';
const SHELL = ['/', '/static/app.css?v=13', '/static/device.js?v=1', '/static/app.js?v=14',
               '/static/offline.html', '/static/icon-192.png', '/static/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || /^\/(api|v1|admin)\//.test(url.pathname)) return; // never cache APIs
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(e.request.mode === 'navigate' ? '/static/offline.html' : '/')))
  );
});
