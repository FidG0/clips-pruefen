// 26.11.2026: Service Worker, damit Chrome/Samsung die Seite als App installiert.
// Immer zuerst das Netz (Selbst-Update bleibt wie gehabt), Cache nur als Rückfall ohne Netz.
// Supabase, CDN und alles außer GET geht unverändert durch.
const CACHE = "clipwreck-v1";
const SCHALE = ["./", "index.html", "lernen.js", "manifest.webmanifest", "icons/clipwreck-192.png", "icons/clipwreck-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SCHALE)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== CACHE).map(n => caches.delete(n)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const r = e.request;
  if (r.method !== "GET" || new URL(r.url).origin !== location.origin) return;
  e.respondWith(
    fetch(r).then(antwort => {
      if (antwort.ok && !new URL(r.url).search) {
        const kopie = antwort.clone();
        caches.open(CACHE).then(c => c.put(r, kopie)).catch(() => {});
      }
      return antwort;
    }).catch(() => caches.match(r, { ignoreSearch: true }).then(t => t || caches.match("index.html")))
  );
});
