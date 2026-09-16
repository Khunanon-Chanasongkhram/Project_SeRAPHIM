/* SeRAPHIM service worker.
 *
 * Floods take the network down. The app shell and the latest snapshots are cached on
 * first visit, so if the network goes the map still opens with the last data it had,
 * clearly labelled as old rather than blank.
 */
const CACHE = "seraphim-v1";
const SHELL = ["./index.html", "./fish.html"];
// Snapshots are cached too, so a failed origin degrades to last-known data rather
// than a blank page. During a flood, yesterday's water levels clearly labelled as
// old beat nothing at all.
const DATA = /\/(index\.json|stations-[a-z]{2}\.geojson|areas-[a-z]{2}\.json|stations\.geojson|meta\.json|tide\.json|areas\.json|fishing\.json|provinces\.geojson|quakes\.geojson|fires\.geojson|validation\.json|events\.geojson|terrain\.geojson)$/;

self.addEventListener("install", (e) => {
  // Cache what we can; a single failed asset must not abort the whole install and
  // leave the user with no offline shell at all.
  e.waitUntil(caches.open(CACHE)
    .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const { request } = e;
  if (request.method !== "GET") return;               // never cache submissions
  const url = new URL(request.url);
  if (url.pathname.includes("/api/")) return;         // API is always live

  // The page itself must revalidate every time. GitHub Pages serves it with
  // `cache-control: max-age=600`, so without this a deploy is invisible for ten
  // minutes even on a fast connection, and the obvious conclusion is that the deploy
  // failed. Assets keep the normal cache; only the document is forced to check.
  const isDocument = request.mode === "navigate"
    || (request.destination === "document")
    || url.pathname.endsWith(".html")
    || url.pathname.endsWith("/");
  const network = isDocument
    ? fetch(request.url, { cache: "no-cache", credentials: "same-origin" })
    : fetch(request);

  // Network-first so a connected user sees fresh data, cache as the fallback.
  // Snapshots are explicitly included: they are the difference between a degraded
  // map and no map.
  e.respondWith(
    network
      .then((res) => {
        if (res.ok && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(request, copy));
        }
        return res;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match("./index.html")))
  );
});
