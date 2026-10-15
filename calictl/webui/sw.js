// Service worker for the shared web UI (R_UI_PWA_OFFLINE): the app shell opens instantly and offline.
// Registered by app.js on secure origins only (the tailnet HTTPS fronts of buspi and the satellite);
// plain http on the LAN never runs it. Served at /sw.js by calictl (static file) and by the ESP32
// (pwa_assets_gen.h). It caches the SHELL only -- the page at / and the same-origin files it links --
// stale-while-revalidate: answer from the cache, refetch in the background (the server's no-cache +
// ETag makes that a cheap 304), and when the ETag changed (a deploy / an OTA) store the new copy
// and tell the page, which offers a reload. /api/* is never cached here: app.js keeps the last
// /api/state in localStorage itself. No push, no background sync, no precache manifest.

// Bump when this file's caching scheme changes: activate deletes every other cache.
const CACHE = "oc-shell-v1";

/** @param {string} html @returns {string[]} the root-relative src/href paths a shell page links */
const linked = (html) => [...html.matchAll(/(?:src|href)="(\/[^"/][^"]*)"/g)].map((m) => m[1]);

self.addEventListener("install", (ev) => {
  ev.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const res = await fetch("/", { cache: "no-cache" });
    if (!res.ok) return;
    await cache.put("/", res.clone());
    // the satellite inlines its scripts (only the manifest + icons are linked); buspi links them all
    // -- one missing file must not fail the install. One at a time: the satellite's single-connection
    // core has a listen backlog of 2, a burst would reset the page's own requests.
    for (const p of linked(await res.text())) await cache.add(p).catch(() => {});
  })());
  self.skipWaiting();
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
});

let chain = Promise.resolve();   // background revalidations, one at a time (see install)

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // the satellite serves the same bundle at / (station mode) and /app (its manifest's start_url)
  const key = url.pathname === "/app" ? "/" : url.pathname;
  ev.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(key);
    if (!hit) return fetch(req);   // not shell (e.g. the satellite's /device recovery page): straight through
    chain = chain.then(async () => {
      const res = await fetch(req, { cache: "no-cache" });
      if (!res.ok || res.headers.get("ETag") === hit.headers.get("ETag")) return;
      await cache.put(key, res);
      const c = await self.clients.get(ev.resultingClientId || ev.clientId);
      if (c) c.postMessage("shell-updated");
    }).catch(() => {});   // offline: the cached copy is all there is
    ev.waitUntil(chain);
    return hit;
  })());
});
