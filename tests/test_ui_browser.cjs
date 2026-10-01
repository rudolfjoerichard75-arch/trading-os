// Optional local visual/interaction check: node tests/test_ui_browser.cjs
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const fixture={engine:{version:'V5.3 CANDIDATE',timestamp:new Date().toISOString(),status:'WAIT'},signal:{active:false},session:'LONDON',latest_price:2300,opportunity:{score:65,type:'Waiting for M5 confirmation'},news:{status:'CLEAR',events:[]},filters:{h4_h1:true,fresh_zone:false},journal:{trades:0},services:{telegram_health:{status:'BOT_AND_CHAT_ACCESS_VERIFIED'}}};
  await page.route('**/*',async route=>{
   const url=route.request().url();
   if(url.includes('dashboard_data.json'))return route.fulfill({json:fixture});
   if(url.includes('backtest_results.json'))return route.fulfill({json:{status:'COMPLETED',version:'V5.2 REPAIRED',results:{development:{stats:{trades:7,win_rate:28.57}}}}});
   if(url==='http://bosque.test/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../index.html'),'utf8')});
   return route.abort();
  });
  await page.goto('http://bosque.test/');
  await page.waitForFunction(()=>document.getElementById('healthBadge').innerText.includes('UPDATED'));
  assert.equal(await page.locator('#entry').innerText(),'—');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:'/tmp/bosque-mobile.png',fullPage:true});
  for(const id of ['journalDialog','researchDialog','statusDialog']){
   await page.locator('[data-open="'+id+'"]').click();
   assert.equal(await page.locator('#'+id).evaluate(el=>el.open),true);
   if(id==='researchDialog')assert.match(await page.locator('#researchNotice').innerText(),/Previous strategy/);
   await page.keyboard.press('Escape');
   assert.equal(await page.locator('#'+id).evaluate(el=>el.open),false);
   await page.locator('[data-open="'+id+'"]').click();
   await page.locator('[data-close="'+id+'"]').click();
  }
  await page.evaluate(()=>render({engine:{timestamp:'2020-01-01',status:'WAIT'},signal:{active:true,direction:'BUY'},news:{status:'CLEAR'}}));
  assert.equal(await page.locator('#finalSignal').innerText(),'WAIT');
  assert.equal(await page.locator('#newsStatus').innerText(),'NEWS STALE');
  await page.setViewportSize({width:1440,height:1000});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('Browser: mobile width, dialog open/close/Escape, old-version research, stale blocking PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
