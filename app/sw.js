/* Service Worker: App offline verfügbar, Kartenkacheln zwischenspeichern, Daten immer frisch versuchen. */
const VERSION = "wingst-v22";
const SHELL = ["./", "index.html", "style.css", "app.js", "spots.js", "model.js", "ensemble.js", "analyse.html", "analyse.js", "charts.js", "analyse.json", "modelle.html", "spotguide.html", "spotguide.js", "spotinfo.js", "spot_directions.json", "model/manifest.json", "manifest.webmanifest",
  "icons/icon-192.png", "icons/icon-512.png"];
const TILE_HOSTS = ["tiles.openfreemap.org", "tiles.maps.eox.at", "unpkg.com", "fonts.gstatic.com", "fonts.googleapis.com"];
const MAX_TILES = 1500;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k !== VERSION && k !== "wingst-tiles").map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

async function trimTiles(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_TILES; i++) await cache.delete(keys[i]);
}

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // Kartenkacheln, Bibliothek, Schriften: aus dem Speicher, im Hintergrund erneuern
  if (TILE_HOSTS.includes(url.hostname)) {
    e.respondWith(caches.open("wingst-tiles").then(async c => {
      const hit = await c.match(e.request);
      const net = fetch(e.request).then(r => { if (r.ok) { c.put(e.request, r.clone()); trimTiles(c); } return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  // eigene Dateien: Netz zuerst (damit Updates sofort ankommen), offline aus dem Speicher
  if (url.origin === self.location.origin) {
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) caches.open(VERSION).then(c => c.put(e.request, r.clone()));
      return r;
    }).catch(() => caches.match(e.request)));
  }
  // Wetter-APIs: immer Netz (die App speichert die letzte Vorhersage selbst)
});
