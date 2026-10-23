/* Subscription data stays on this device until the owner copies it into GitHub Secrets. */
(async () => {
  const el = id => document.getElementById(id);
  const status = text => { el('pushStatus').textContent = text; };
  const encode = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  const decode = s => Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-s.length%4)%4)), c=>c.charCodeAt(0));
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (ios && !window.matchMedia('(display-mode: standalone)').matches && !navigator.standalone) {
    status('Tambah ke Home Screen dahulu melalui menu Share, kemudian buka ikon Bosque dan aktifkan notifikasi.');
    el('preparePush').disabled=true; return;
  }
  if (!isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    status('Web Push tidak disokong dalam paparan ini. Gunakan app Home Screen pada iPhone yang menyokong push.');
    el('preparePush').disabled=true; return;
  }
  let reg, key, config={};
  const showSubscription = sub => {
    el('pushSecrets').hidden=false;
    el('pushSubscription').value=JSON.stringify([sub.toJSON()]);
    el('localPushTest').disabled=false;
    el('disablePush').disabled=false;
  };
  try {
    await navigator.serviceWorker.register('./sw.js', {scope:'./', updateViaCache:'none'});
    reg=await navigator.serviceWorker.ready;
    const response=await fetch('./push-config.json?t='+Date.now(), {cache:'no-store'});
    if(response.ok) config=await response.json();
    key=config.public_key || null;
    const existing=await reg.pushManager.getSubscription();
    if(existing) {
      showSubscription(existing);
      // Existing browser permission alone is not proof of server registration.
      status('Telefon berdaftar pada pelayar. Pastikan PUSH_SUBSCRIPTIONS disimpan di GitHub; penerimaan ujian server mengesahkan laluan push.');
      el('preparePush').disabled=true;
    } else status(key ? 'Kunci server tersedia. Tekan Aktifkan notifikasi, kemudian simpan pendaftaran telefon di GitHub Secrets.' : 'Push server belum dikonfigurasi. Pemilik: tekan Sediakan kunci pemilik untuk setup sekali.');
    el('enablePush').disabled=!key;
    if(key) el('preparePush').disabled=true;
  } catch(e) {status('Persediaan gagal. Muat semula app dan cuba lagi.'); return;}
  el('preparePush').onclick=async()=>{
    try {
      el('preparePush').disabled=true;
      const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
      key=encode(await crypto.subtle.exportKey('raw',pair.publicKey));
      // PKCS8 DER accepted by py_vapid; private material is never persisted by the app.
      el('pushPrivateKey').value=encode(await crypto.subtle.exportKey('pkcs8',pair.privateKey));
      el('pushSecrets').hidden=false;el('privateKeyLabel').hidden=false;
      el('enablePush').disabled=false;
      status('Salin PUSH_VAPID_PRIVATE_KEY ke GitHub Secrets sebelum tutup app. Kemudian tekan Aktifkan notifikasi.');
    } catch(e){el('preparePush').disabled=false;status('Kunci gagal dijana. Cuba lagi.');}
  };
  el('enablePush').onclick=async()=>{
    el('enablePush').disabled=true;
    try {
      const permission=await Notification.requestPermission();
      if(permission!=='granted') throw new Error('Izin notifikasi tidak diberi. Semak Settings > Notifications untuk Bosque.');
      let sub=await reg.pushManager.getSubscription();
      if(!sub) sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:decode(key)});
      showSubscription(sub);
      status('Izin telefon diberi. Simpan PUSH_SUBSCRIPTIONS di GitHub Secrets; server belum disahkan sehingga notifikasi ujian server diterima.');
    } catch(e) {status(e.message || 'Pendaftaran gagal.');}
    finally {el('enablePush').disabled=false;}
  };
  el('localPushTest').onclick=async()=>{
    try {await reg.showNotification('Bosque Forex AI — Ujian telefon',{body:'Paparan telefon berfungsi. Ini ujian tempatan, bukan penghantaran server.',icon:'./assets/brand/crown-192.png',tag:'bosque-local-test'});}
    catch(e){status('Ujian paparan gagal. Semak izin notifikasi.');}
  };
  el('disablePush').onclick=async()=>{
    try {
      const sub=await reg.pushManager.getSubscription();
      if(sub && !await sub.unsubscribe()) throw new Error();
      el('pushSubscription').value='';el('localPushTest').disabled=true;el('disablePush').disabled=true;
      status('Notifikasi telefon ini dimatikan. Padam atau kemas kini PUSH_SUBSCRIPTIONS di GitHub Secrets.');
    }catch(e){status('Gagal mematikan notifikasi. Cuba lagi.');}
  };
})();
