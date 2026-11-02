/* Service worker: приложение открывается без сети и показывает пуш-уведомления. */
"use strict";

const CACHE = "ski-tickets-v2";
const SHELL = [
  "./",
  "index.html",
  "app.js",
  "style.css",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/badge-96.png",
  "icons/favicon-32.png",
];
const NETWORK_TIMEOUT = 6000;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) {
        if (key !== CACHE) await caches.delete(key);
      }
      await self.clients.claim();
    })()
  );
});

// Сначала сеть (цены должны быть свежими), без сети — последняя сохранённая копия.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  event.respondWith(networkFirst(request, url));
});

async function networkFirst(request, url) {
  const cache = await caches.open(CACHE);
  const key = url.origin + url.pathname;
  // no-cache: браузер спрашивает сервер, не изменился ли файл, а не берёт копию 10-минутной давности.
  const network = fetch(new Request(request, { cache: "no-cache" })).then((response) => {
    if (response.ok) cache.put(key, response.clone());
    return response;
  });
  network.catch(() => {}); // если сеть упадёт уже после ответа из кэша — это не ошибка
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), NETWORK_TIMEOUT));
  try {
    return await Promise.race([network, timeout]);
  } catch (err) {
    const cached = (await cache.match(key)) || (request.mode === "navigate" ? await cache.match(new URL("./", self.registration.scope).href) : null);
    if (cached) return offline(cached);
    return network;
  }
}

function offline(response) {
  const headers = new Headers(response.headers);
  headers.set("X-Offline", "1");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Билеты на горнолыжку";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      icon: "icons/icon-192.png",
      badge: "icons/badge-96.png",
      tag: data.tag || undefined,
      renotify: Boolean(data.tag),
      silent: Boolean(data.silent),
      lang: "ru",
      data: { url: data.url || "./" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "./", self.registration.scope).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (client.url.startsWith(self.registration.scope)) {
          await client.focus();
          if ("navigate" in client) await client.navigate(target);
          return;
        }
      }
      await self.clients.openWindow(target);
    })()
  );
});
