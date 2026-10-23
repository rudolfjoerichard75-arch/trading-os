// Push only: never cache trading snapshots or intercept dashboard fetches.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('push', event => {
  let payload={};
  try {payload=event.data ? event.data.json() : {};} catch (_) {}
  event.waitUntil(self.registration.showNotification(payload.title || 'Bosque Forex AI', {
    body:payload.body || 'Kemas kini tersedia. Buka Bosque untuk semakan.',
    icon:'./assets/brand/crown-192.png',badge:'./assets/brand/crown-192.png',
    tag:payload.id || 'bosque-update', data:{url:self.registration.scope}
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async()=>{
    const url=self.registration.scope;
    for(const client of await self.clients.matchAll({type:'window',includeUncontrolled:true})) {
      if(client.url.startsWith(url) && 'focus' in client) return client.focus();
    }
    return self.clients.openWindow(url);
  })());
});
