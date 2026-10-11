/* KOLI CI client — vanilla JavaScript + Leaflet. API is same-origin. */
const $ = id => document.getElementById(id);
const state = {
 page:'customer', map:null, trackingMap:null, trackingLayers:[], selection:'pickup',
 points:{pickup:null,dropoff:null}, markers:{},routeLine:null,
 riderToken:sessionStorage.getItem('koli_rider_token'),adminToken:sessionStorage.getItem('koli_admin_token'),
 riderMe:null, riderOrders:[], adminData:null, gpsWatch:null, gpsLastSent:0,
 chosenOrder:null, loading:false
};
const ABIDJAN=[5.3599,-4.0083];
const portal=location.pathname==='/rider'?'rider':location.pathname==='/admin'?'admin':'client';
document.documentElement.dataset.portal=portal;
if(portal==='rider'||portal==='admin'){
 document.querySelectorAll('.nav-item').forEach(b=>{if(b.dataset.page!==portal)b.hidden=true});
 $('webManifest').href='/static/manifest-'+portal+'.webmanifest';
 document.title=(portal==='rider'?'KOLI CI · Livreur':'KOLI CI · Gestionnaire');
}else{
 document.querySelectorAll('.nav-item').forEach(b=>{if(!['customer','tracking'].includes(b.dataset.page))b.hidden=true});
 $('mobileCustomerTabs').hidden=false;
}
if('serviceWorker' in navigator && window.isSecureContext)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});

const STATUSES={pending:'En attente',assigned:'Livreur attribué',to_pickup:'Vers le retrait',picked_up:'Colis récupéré',delivering:'En livraison',delivered:'Livré',cancelled:'Annulé'};
const NEXT={assigned:['to_pickup','Partir au retrait'],to_pickup:['picked_up','Colis récupéré'],picked_up:['delivering','Démarrer la livraison'],delivering:['delivered','Confirmer la livraison']};
const currency=n=>Number(n||0).toLocaleString('fr-FR');
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDate=s=>s?new Date(s).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'}):'—';
const clsStatus=s=>s==='delivered'?'done':s==='pending'?'waiting':'';
const stored=()=>{try{return JSON.parse(localStorage.getItem('koli_my_orders')||'[]')}catch{return []}};
function remember(order, key){let a=stored().filter(o=>o.id!==order.id);a.unshift({id:order.id,key,code:order.code,created_at:order.created_at});localStorage.setItem('koli_my_orders',JSON.stringify(a.slice(0,80)))}
function toast(msg,error=false){const el=$('toast');el.textContent=msg;el.classList.toggle('error',error);el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),4500)}
async function api(path,{method='GET',data,token}={}){
 const headers={};if(data!==undefined)headers['Content-Type']='application/json';if(token)headers.Authorization='Bearer '+token;
 let r;try{r=await fetch('/api'+path,{method,headers,body:data!==undefined?JSON.stringify(data):undefined,cache:'no-store'})}
 catch{throw Error('Connexion au serveur impossible')}
 let j;try{j=await r.json()}catch{j={detail:'Réponse du serveur invalide'}}
 if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:'Erreur HTTP '+r.status);
 return j;
}
function switchPage(p){
 state.page=p;document.querySelectorAll('.page').forEach(x=>x.classList.toggle('visible',x.id==='page-'+p));
 document.querySelectorAll('.nav-item, .mobile-customer-tabs button').forEach(x=>x.classList.toggle('active',x.dataset.page===p));
 $('breadcrumb').textContent=({customer:'Nouvelle livraison',tracking:'Mes livraisons',rider:'Espace livreur',admin:'Gestionnaire'})[p];
 document.querySelector('.sidebar').classList.remove('open');window.scrollTo({top:0,behavior:'smooth'});
 if(p==='customer')setTimeout(()=>state.map?.invalidateSize(),80);
 if(p==='tracking')renderHistory();
 if(p==='rider')initRiderPanel();
 if(p==='admin')initAdminPanel();
}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>switchPage(b.dataset.page)));
$('menuBtn').onclick=()=>document.querySelector('.sidebar').classList.toggle('open');

function makePin(type){return L.divIcon({className:'',html:`<div class="marker-pin ${type==='dropoff'?'end':type==='rider'?'rider':''}"></div>`,iconSize:[28,28],iconAnchor:[14,14]})}
function initMap(){
 if(!window.L){$('mapFallback').textContent='Carte indisponible. Une connexion Internet est nécessaire pour les fonds de carte.';toast('Impossible de charger Leaflet — vérifiez votre connexion',true);return;}
 state.map=L.map('map',{zoomControl:false}).setView(ABIDJAN,12);
 L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(state.map);
 L.control.zoom({position:'bottomright'}).addTo(state.map);
 $('mapFallback').style.display='none';
 state.map.on('click',e=>{const t=state.selection;setPoint(t,{lat:e.latlng.lat,lng:e.latlng.lng,label:`Position GPS · ${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`});showMapToast((t==='pickup'?'Départ':'Arrivée')+' positionné · déplacez le repère pour ajuster');if(t==='pickup'&&!state.points.dropoff)selectMapTarget('dropoff')});
 selectMapTarget('pickup');
}
function showMapToast(t){const a=$('mapToast');a.textContent=t;a.hidden=false;clearTimeout(showMapToast.t);showMapToast.t=setTimeout(()=>a.hidden=true,2800)}
function selectMapTarget(t){state.selection=t;document.querySelectorAll('.pin-select').forEach(b=>b.classList.toggle('active',b.dataset.mapTarget===t));$('selectionHint').textContent='✳ Sélection de '+(t==='pickup'?'départ':'destination')+' : touchez la carte pour placer le repère.';showMapToast('Touchez la carte pour choisir : '+(t==='pickup'?'départ':'destination'))}
document.querySelectorAll('.pin-select').forEach(b=>b.onclick=()=>selectMapTarget(b.dataset.mapTarget));
function setPoint(type,p){
 state.points[type]=p;$(type+'Input').value=p.label;
 const map=state.map;if(map){
  if(state.markers[type]){state.markers[type].setLatLng([p.lat,p.lng])}
  else {const mark=L.marker([p.lat,p.lng],{icon:makePin(type),draggable:true}).addTo(map);mark.on('dragend',()=>{const c=mark.getLatLng();setPoint(type,{lat:c.lat,lng:c.lng,label:`Position GPS · ${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}`})});state.markers[type]=mark;}
  map.panTo([p.lat,p.lng]);
 }
 renderEstimate();drawRoute();
}
function distanceKm(a,b){const R=6371, rad=x=>x*Math.PI/180,dlat=rad(b.lat-a.lat),dlng=rad(b.lng-a.lng);const h=Math.sin(dlat/2)**2+Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dlng/2)**2;return R*2*Math.atan2(Math.sqrt(h),Math.sqrt(1-h))}
function getQuote(){const a=state.points.pickup,b=state.points.dropoff;if(!a||!b)return null;const km=distanceKm(a,b);return {km,price:Math.max(1000,Math.ceil((800+km*300)/100)*100)}}
function renderEstimate(){const q=getQuote();$('estimatePrice').innerHTML=q?`${currency(q.price)} <small>FCFA</small>`:'— <small>FCFA</small>';$('estimateDistance').textContent=q?`${q.km.toFixed(1)} km à vol d’oiseau · estimation pilote`:'Sélectionnez vos adresses'}
async function drawRoute(){const map=state.map,a=state.points.pickup,b=state.points.dropoff;if(!map)return;
 if(state.routeLine){map.removeLayer(state.routeLine);state.routeLine=null}if(!a||!b)return;
 const fallback=()=>{state.routeLine=L.polyline([[a.lat,a.lng],[b.lat,b.lng]],{color:'#fa7553',weight:4,dashArray:'7 8',opacity:.9}).addTo(map)};
 fallback();const fit=L.latLngBounds([[a.lat,a.lng],[b.lat,b.lng]]);map.fitBounds(fit.pad(.25),{maxZoom:15});
 // Public OSRM demo route: optional visual aid; fees are based on server-side geodesic distance.
 const routeKey=`${a.lat},${a.lng},${b.lat},${b.lng}`;drawRoute.latest=routeKey;
 try{const controller=new AbortController();setTimeout(()=>controller.abort(),4500);const url=`https://router.project-osrm.org/route/v1/driving/${a.lng},${a.lat};${b.lng},${b.lat}?overview=full&geometries=geojson`;
 const resp=await fetch(url,{signal:controller.signal});const j=await resp.json();if(drawRoute.latest!==routeKey||!j.routes?.[0])return;
 map.removeLayer(state.routeLine);state.routeLine=L.geoJSON(j.routes[0].geometry,{style:{color:'#f67b5a',weight:5,opacity:.95}}).addTo(map);
 }catch{/* Straight-line route remains visible when OSRM is offline */}
}
$('geoBtn').onclick=()=>{
 if(!navigator.geolocation){toast('GPS non disponible sur cet appareil',true);return}
 navigator.geolocation.getCurrentPosition(p=>{const {latitude:lat,longitude:lng}=p.coords;setPoint(state.selection,{lat,lng,label:'Ma position GPS · '+lat.toFixed(5)+', '+lng.toFixed(5)});state.map?.setView([lat,lng],15)},err=>toast('Localisation indisponible : '+err.message,true),{enableHighAccuracy:true,timeout:13000});
};
// Search with Nominatim. Debounced client-side (public geocoder is for demo use only).
for(const type of ['pickup','dropoff']){
 const input=$(type+'Input'),out=$(type+'Suggestions');let timer,searchSeq=0;
 input.addEventListener('input',()=>{
  state.points[type]=null;renderEstimate();if(state.markers[type]){state.map?.removeLayer(state.markers[type]);delete state.markers[type]};
  clearTimeout(timer);out.innerHTML='';const query=input.value.trim();if(query.length<3){searchSeq++;return;}
  timer=setTimeout(async()=>{const seq=++searchSeq;
   try{const res=await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=4&countrycodes=ci&q='+encodeURIComponent(query));const data=await res.json();if(seq!==searchSeq)return;
    out.replaceChildren(...data.map(item=>{const b=document.createElement('button');b.type='button';b.textContent=item.display_name;b.onclick=()=>{out.innerHTML='';selectMapTarget(type);setPoint(type,{lat:Number(item.lat),lng:Number(item.lon),label:item.display_name})};return b}));
   }catch{out.innerHTML='';}
  },950);
 });
}
$('orderBtn').onclick=async()=>{
 if(state.loading)return;
 const a=state.points.pickup,b=state.points.dropoff;if(!a||!b){toast('Choisissez les deux adresses sur la carte',true);return}
 const customer_name=$('customerName').value.trim(),customer_phone=$('customerPhone').value.trim();if(customer_name.length<2||customer_phone.length<5){toast('Indiquez votre nom et votre numéro de téléphone',true);return}
 state.loading=true;$('orderBtn').disabled=true;$('orderBtn').textContent='Enregistrement de la commande...';
 try{const j=await api('/orders',{method:'POST',data:{pickup:a,dropoff:b,customer_name,customer_phone,notes:$('orderNotes').value.trim()}});remember(j.order,j.tracking_token);toast('Commande '+j.order.code+' créée avec succès !');state.chosenOrder=j.order.id;switchPage('tracking')}
 catch(e){toast(e.message,true)}finally{state.loading=false;$('orderBtn').disabled=false;$('orderBtn').innerHTML='Confirmer la livraison <span>↗</span>'}
};

async function renderHistory(){const history=stored();$('orderCount').textContent=history.length+' commande'+(history.length>1?'s':'');
 $('orderHistory').innerHTML=history.length?history.map(x=>`<button class="history-item ${state.chosenOrder===x.id?'selected':''}" data-track-id="${x.id}"><div class="history-item-head"><strong>${escapeHtml(x.code)}</strong><span class="status-pill">Voir le suivi →</span></div><p>${escapeHtml(fmtDate(x.created_at))}</p></button>`).join(''):'<div class="empty">Aucune livraison enregistrée. Créez une commande pour commencer.</div>';
 document.querySelectorAll('[data-track-id]').forEach(b=>b.onclick=()=>openOrder(Number(b.dataset.trackId)));
 if(!state.chosenOrder&&history.length)state.chosenOrder=history[0].id;
 if(state.chosenOrder&&history.some(x=>x.id===state.chosenOrder))await openOrder(state.chosenOrder);
}
async function openOrder(id){state.chosenOrder=id;const x=stored().find(x=>x.id===id);if(!x)return;
 try{const o=await api('/orders/'+id+'/track?key='+encodeURIComponent(x.key));if(state.chosenOrder!==id)return;
 document.querySelectorAll('[data-track-id]').forEach(b=>b.classList.toggle('selected',Number(b.dataset.trackId)===id));
 $('orderDetail').innerHTML=`<div class="detail-head"><div><strong>${escapeHtml(o.code)}</strong><small>${fmtDate(o.created_at)}</small></div><span class="status-pill ${clsStatus(o.status)}">${escapeHtml(STATUSES[o.status]||o.status)}</span></div>
 <div class="address-pair"><div><small>● DÉPART</small>${escapeHtml(o.pickup_label)}</div><div><small>◆ ARRIVÉE</small>${escapeHtml(o.dropoff_label)}</div></div>
 <p style="font-size:12px;margin:16px 0 2px"><b>Livreur :</b> ${escapeHtml(o.rider?.name||'Attribution en cours')} ${o.rider?.last_location_at?`<small>· ${o.rider.gps_live?'GPS récent':'Dernier GPS (hors ligne)'} : ${fmtDate(o.rider.last_location_at)}</small>`:''}</p>
 <h3 style="font-size:15px;margin:20px 0">Progression de la livraison</h3><ul class="timeline">${o.events.map(e=>`<li><strong>${escapeHtml(e.note)}</strong><small>${fmtDate(e.created_at)}</small></li>`).join('')}</ul>
 <div class="detail-fee"><span>Estimation de livraison</span><strong>${currency(o.price_xof)} FCFA</strong></div><button class="outline-button" id="copyTracking" style="margin-top:18px">Copier le lien de suivi ↗</button>`;
 $('copyTracking').onclick=async()=>{const url=location.origin+location.pathname+'?order='+id+'&key='+encodeURIComponent(x.key);try{await navigator.clipboard.writeText(url);toast('Lien de suivi copié')}catch{prompt('Copiez ce lien de suivi',url)}};
 $('trackMapWrap').hidden=false;renderTrackingMap(o);
 }catch(e){toast(e.message,true)}
}
function renderTrackingMap(o){if(!window.L)return;
 if(!state.trackingMap){state.trackingMap=L.map('trackingMap',{zoomControl:true}).setView(ABIDJAN,12);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(state.trackingMap)}
 state.trackingLayers.forEach(x=>state.trackingMap.removeLayer(x));state.trackingLayers=[];
 const points=[[o.pickup_lat,o.pickup_lng],[o.dropoff_lat,o.dropoff_lng]];
 let a=L.marker(points[0],{icon:makePin('pickup')}).addTo(state.trackingMap).bindPopup('Retrait');
 let b=L.marker(points[1],{icon:makePin('dropoff')}).addTo(state.trackingMap).bindPopup('Livraison');state.trackingLayers.push(a,b);
 const line=L.polyline(points,{color:'#ef7553',weight:3,dashArray:'6 8'}).addTo(state.trackingMap);state.trackingLayers.push(line);
 if(o.rider?.lat!=null&&o.rider?.lng!=null){const pos=[o.rider.lat,o.rider.lng];const r=L.marker(pos,{icon:makePin('rider')}).addTo(state.trackingMap).bindPopup('Livreur · '+escapeHtml(o.rider.name));state.trackingLayers.push(r);points.push(pos)}
 state.trackingMap.fitBounds(points,{padding:[55,55],maxZoom:15});setTimeout(()=>state.trackingMap.invalidateSize(),80);
}
// Rider section
function riderHeaders(){return state.riderToken}
async function initRiderPanel(){const logged=!!state.riderToken;$('riderLogin').hidden=logged;$('riderDashboard').hidden=!logged;if(logged)try{await refreshRider()}catch(e){if(/401|session|connexion/i.test(e.message))logoutRider(false);else toast(e.message,true)}}
$('riderLoginBtn').onclick=async()=>{try{const j=await api('/login',{method:'POST',data:{username:$('riderUsername').value,password:$('riderPassword').value}});if(j.user.role!=='rider')throw Error('Ce compte n’est pas un compte livreur');state.riderToken=j.token;sessionStorage.setItem('koli_rider_token',j.token);$('riderPassword').value='';await initRiderPanel();toast('Bienvenue '+j.user.name)}catch(e){toast(e.message,true)}};
async function refreshRider(){if(!state.riderToken)return;const [me,orders]=await Promise.all([api('/me',{token:riderHeaders()}),api('/rider/orders',{token:riderHeaders()})]);state.riderMe=me;state.riderOrders=orders;
 $('riderName').textContent=me.name;$('riderOnlineLabel').textContent=me.online?'En ligne':'Hors ligne';$('riderOnlineLabel').style.color=me.online?'#20a17b':'';$('onlineBtn').textContent=me.online?'Passer hors ligne':'Passer en ligne';
 $('riderLocationLabel').textContent=me.lat==null?'En attente':(me.lat.toFixed(4)+', '+me.lng.toFixed(4));
 $('riderTaskCount').textContent=orders.length+' mission(s)';
 $('riderOrderList').innerHTML=orders.length?orders.map(o=>`<div class="task"><div class="detail-head"><h3>${escapeHtml(o.code)}</h3><span class="status-pill ${clsStatus(o.status)}">${escapeHtml(STATUSES[o.status]||o.status)}</span></div><p><b>① Retrait</b> · ${escapeHtml(o.pickup_label)}</p><p><b>② Livraison</b> · ${escapeHtml(o.dropoff_label)}</p><p><b>Destinataire</b> · ${escapeHtml(o.customer_name)} · <a href="tel:${escapeHtml(o.customer_phone)}">${escapeHtml(o.customer_phone)}</a></p><p><b>Distance directe</b> ${o.distance_km} km · <b>Tarif estimé</b> ${currency(o.price_xof)} FCFA</p>${o.notes?`<p><b>Instructions</b> : ${escapeHtml(o.notes)}</p>`:''}${NEXT[o.status]?`<div class="task-actions"><button class="primary-button" data-advance="${o.id}" data-next="${NEXT[o.status][0]}">${NEXT[o.status][1]} <span>→</span></button><button class="outline-button" data-maporder="${o.id}">Itinéraire ↗</button></div>`:''}</div>`).join(''):'<div class="empty">Aucune commande. Mettez-vous en ligne et activez le GPS pour recevoir une course.</div>';
 document.querySelectorAll('[data-advance]').forEach(b=>b.onclick=async()=>{try{await api('/rider/orders/'+b.dataset.advance+'/status',{method:'PATCH',data:{status:b.dataset.next},token:riderHeaders()});toast('Statut mis à jour');await refreshRider()}catch(e){toast(e.message,true)}});
 document.querySelectorAll('[data-maporder]').forEach(b=>b.onclick=()=>{const o=orders.find(x=>x.id===Number(b.dataset.maporder));const start=o.status==='assigned'||o.status==='to_pickup'?[o.pickup_lat,o.pickup_lng]:[o.dropoff_lat,o.dropoff_lng];const origin=me.lat!=null?[me.lat,me.lng]:start;const url=`https://www.google.com/maps/dir/?api=1&origin=${origin.join(',')}&destination=${start.join(',')}&travelmode=driving`;window.open(url,'_blank','noopener,noreferrer')});
 if(me.online)beginGpsWatch();else stopGpsWatch();
}
$('onlineBtn').onclick=async()=>{try{const online=!state.riderMe.online;await api('/rider/online',{method:'POST',data:{online},token:riderHeaders()});toast(online?'Vous êtes en ligne. GPS requis pour le dispatch.':'Vous êtes hors ligne.');await refreshRider()}catch(e){toast(e.message,true)}};
async function postGps(lat,lng){if(!state.riderToken)return;await api('/rider/location',{method:'POST',data:{lat,lng},token:riderHeaders()})}
$('riderLocateBtn').onclick=()=>{if(!navigator.geolocation){toast('Géolocalisation non supportée',true);return}navigator.geolocation.getCurrentPosition(async p=>{try{await postGps(p.coords.latitude,p.coords.longitude);toast('Position GPS mise à jour');await refreshRider()}catch(e){toast(e.message,true)}},e=>toast('GPS : '+e.message,true),{enableHighAccuracy:true,timeout:15000})};
$('demoLocationBtn').onclick=async()=>{try{await postGps(5.3978,-3.9842);toast('Position de test Angré enregistrée');await refreshRider()}catch(e){toast(e.message,true)}};
function beginGpsWatch(){if(state.gpsWatch!==null||!navigator.geolocation)return;
 state.gpsWatch=navigator.geolocation.watchPosition(async p=>{if(Date.now()-state.gpsLastSent<6500)return;state.gpsLastSent=Date.now();try{await postGps(p.coords.latitude,p.coords.longitude)}catch{}},()=>{},{enableHighAccuracy:true,maximumAge:5000,timeout:20000});
}
function stopGpsWatch(){if(state.gpsWatch!==null&&navigator.geolocation){navigator.geolocation.clearWatch(state.gpsWatch);state.gpsWatch=null}}
async function logoutRider(send=true){if(send&&state.riderToken){try{await api('/logout',{method:'POST',token:riderHeaders()})}catch{}}stopGpsWatch();state.riderToken=null;sessionStorage.removeItem('koli_rider_token');$('riderLogin').hidden=false;$('riderDashboard').hidden=true}
$('riderLogoutBtn').onclick=()=>logoutRider();
// Admin
async function initAdminPanel(){const logged=!!state.adminToken;$('adminLogin').hidden=logged;$('adminDashboard').hidden=!logged;if(logged)try{await refreshAdmin()}catch(e){if(/session|connexion/i.test(e.message))logoutAdmin(false);else toast(e.message,true)}}
$('adminLoginBtn').onclick=async()=>{try{const j=await api('/login',{method:'POST',data:{username:$('adminUsername').value,password:$('adminPassword').value}});if(j.user.role!=='admin')throw Error('Compte gestionnaire requis');state.adminToken=j.token;sessionStorage.setItem('koli_admin_token',j.token);$('adminPassword').value='';await initAdminPanel()}catch(e){toast(e.message,true)}};
async function refreshAdmin(){const j=await api('/admin/dashboard',{token:state.adminToken});state.adminData=j;
 $('riderCount').textContent=j.riders.length+' livreur(s)';const active=j.orders.filter(x=>!['delivered','cancelled'].includes(x.status)).length,delivered=j.orders.filter(x=>x.status==='delivered').length;
 $('adminStats').innerHTML=`<div class="stat-card"><small>TOTAL COMMANDES</small><strong>${j.orders.length}</strong><small>Dernières 100 commandes</small></div><div class="stat-card"><small>EN COURS</small><strong>${active}</strong><small>Non terminées</small></div><div class="stat-card"><small>LIVRÉES</small><strong>${delivered}</strong><small>Terminées</small></div>`;
 $('adminRiders').innerHTML=j.riders.length?j.riders.map(r=>`<div class="rider-chip"><div><strong>${escapeHtml(r.name)}</strong><small>@${escapeHtml(r.username)} · ${r.last_location_at?'GPS '+fmtDate(r.last_location_at):'GPS absent'}</small></div><em class="${r.online?'on':''}">${r.online?'● En ligne':'○ Hors ligne'}</em></div>`).join(''):'<div class="empty">Aucun livreur enregistré.</div>';
 $('adminOrders').innerHTML=j.orders.length?j.orders.map(o=>`<div class="admin-order-card"><div class="admin-order-head"><strong>${escapeHtml(o.code)}</strong><span class="status-pill ${clsStatus(o.status)}">${escapeHtml(STATUSES[o.status]||o.status)}</span></div><p>De : ${escapeHtml(o.pickup_label)}<br/>Vers : ${escapeHtml(o.dropoff_label)}<br/>Client : ${escapeHtml(o.customer_name)} · ${escapeHtml(o.customer_phone)}<br/>Livreur : ${escapeHtml(o.rider?.name||'Non attribué')} · ${currency(o.price_xof)} FCFA</p>${o.status==='pending'?`<div class="assign-bar"><select data-rider-select="${o.id}"><option value="">Choisir un livreur</option>${j.riders.map(r=>`<option value="${r.id}">${escapeHtml(r.name)}${r.online?' (en ligne)':''}</option>`).join('')}</select><button class="outline-button tiny" data-assign="${o.id}">Attribuer</button></div>`:''}</div>`).join(''):'<div class="empty">Aucune commande encore.</div>';
 document.querySelectorAll('[data-assign]').forEach(b=>b.onclick=async()=>{const rider_id=Number(document.querySelector(`[data-rider-select="${b.dataset.assign}"]`).value);if(!rider_id){toast('Choisissez un livreur',true);return}try{await api('/admin/orders/'+b.dataset.assign+'/assign',{method:'POST',token:state.adminToken,data:{rider_id}});toast('Livreur attribué');await refreshAdmin()}catch(e){toast(e.message,true)}});
}
$('adminRefreshBtn').onclick=()=>refreshAdmin().catch(e=>toast(e.message,true));
$('addRiderBtn').onclick=async()=>{const data={name:$('newRiderName').value.trim(),username:$('newRiderUsername').value.trim(),password:$('newRiderPassword').value,phone:$('newRiderPhone').value.trim()};if(data.password.length<8){toast('Mot de passe : 8 caractères minimum',true);return}try{const r=await api('/admin/riders',{method:'POST',token:state.adminToken,data});toast('Compte créé pour '+r.name);['newRiderName','newRiderUsername','newRiderPassword','newRiderPhone'].forEach(x=>$(x).value='');await refreshAdmin()}catch(e){toast(e.message,true)}};
async function logoutAdmin(send=true){if(send&&state.adminToken){try{await api('/logout',{method:'POST',token:state.adminToken})}catch{}}state.adminToken=null;sessionStorage.removeItem('koli_admin_token');$('adminLogin').hidden=false;$('adminDashboard').hidden=true}
$('adminLogoutBtn').onclick=()=>logoutAdmin();

initMap();
if(location.pathname==='/rider')switchPage('rider');
else if(location.pathname==='/admin')switchPage('admin');
else if(location.pathname==='/client')switchPage('customer');
const shared=new URLSearchParams(location.search);if(shared.has('order')&&shared.has('key')){const id=Number(shared.get('order')),key=shared.get('key');if(Number.isInteger(id)&&id>0&&key?.length>15){remember({id,code:'Commande partagée',created_at:new Date().toISOString()},key);state.chosenOrder=id;switchPage('tracking')}}
setInterval(()=>{if(state.page==='tracking'&&state.chosenOrder&&document.visibilityState==='visible')openOrder(state.chosenOrder);if(state.page==='rider'&&state.riderToken&&document.visibilityState==='visible')refreshRider().catch(()=>{});if(state.page==='admin'&&state.adminToken&&document.visibilityState==='visible')refreshAdmin().catch(()=>{})},5500);

