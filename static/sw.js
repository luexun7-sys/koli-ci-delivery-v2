// Minimal app-shell caching. NEVER cache /api requests or tracking-token URLs.
const CACHE='koli-static-v2';
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/static/style.css','/static/app.js','/static/icon.svg']))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('koli-static-')&&k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',e=>{
 const url=new URL(e.request.url);
 if(e.request.method!=='GET'||url.origin!==self.location.origin||!url.pathname.startsWith('/static/')||url.pathname.endsWith('.webmanifest'))return;
 e.respondWith(fetch(e.request).then(res=>{if(res.ok){const clone=res.clone();caches.open(CACHE).then(c=>c.put(e.request,clone))}return res}).catch(()=>caches.match(e.request)));
});

