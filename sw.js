// Service worker mínimo de Nova.
// Objetivo único: que la app se pueda instalar y que el cascarón (HTML/manifest)
// cargue aunque la conexión esté mala. Los datos siempre viven en el servidor,
// así que /api/* nunca se cachea: mejor mostrar el estado de error de la app
// que datos viejos disfrazados de reales.

const CACHE = "nova-shell-v2"; // subir este número invalida cualquier caché vieja al desplegar
const SHELL = ["/", "/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Los datos nunca se cachean: siempre van a la red.
  if (url.pathname.startsWith("/api/")) return;

  // Cascarón: red primero, con caché como respaldo si no hay conexión.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
