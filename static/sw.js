self.addEventListener('install', e => {
  e.waitUntil(
    caches.open('ytgrab-cache-v2').then(cache => {
      return cache.addAll([
        '/',
        '/static/css/style.css',
        '/static/js/main.js'
      ]);
    })
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.map(key => {
          if (key !== 'ytgrab-cache-v2') {
            return caches.delete(key);
          }
        })
      );
    })
  );
});

self.addEventListener('fetch', e => {
  e.respondWith(
    fetch(e.request)
      .then(response => {
        if (response && response.status === 200 && e.request.method === 'GET') {
          const responseClone = response.clone();
          caches.open('ytgrab-cache-v2').then(cache => {
            cache.put(e.request, responseClone);
          });
        }
        return response;
      })
      .catch(() => {
        return caches.match(e.request);
      })
  );
});