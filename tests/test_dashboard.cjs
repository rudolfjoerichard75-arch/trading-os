const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('index.html','utf8');
const elements=new Map();
const node=(text='')=>({
  _text:String(text), children:[], style:{}, classList:{add(){},remove(){}},
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('')},
  set textContent(value){this._text=String(value);this.children=[]},
  get innerText(){return this.textContent},set innerText(value){this.textContent=value},
  set innerHTML(value){this.textContent=value},
  replaceChildren(...children){this._text='';this.children=children},
  appendChild(child){this.children.push(child)}
});
const el=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id)};
const c={document:{getElementById:el,createElement:()=>node(),createTextNode:node},window:{addEventListener(){}},console:{error(){}},Date,setInterval(){}};
vm.createContext(c);
for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))if(m[1].includes('function render('))vm.runInContext(m[1],c);
const payload=()=>({engine:{timestamp:new Date().toISOString()},opportunity:{direction:'SELL',score:85},signal:{active:false},plan:{valid:false,reason:'Risk 143.0 pips outside 20-80'},filters:{setup:true,score:true,m5_confirmation:true,risk:false,rr:false,news:true},news:{status:'CLEAR',filter:'PASS',high_impact:false,source:'FinanceCalendar',evidence:{checked_at:new Date().toISOString(),events_received:4,usd_high_events:2}},session:'ASIAN'});
function render(data){c.payload=data;vm.runInContext('render(payload)',c)}
(async()=>{
  const d=payload();render(d);
  assert.match(el('finalSignal').innerText,/^WAIT \(SL 143\.0 pips/);
  assert.equal(el('riskGate').innerText,'WAIT (SL 143.0 pips melebihi had 80 pips)');
  assert.equal(el('rrGate').innerText,'WAIT (Belum dikira: risk tidak lulus)');
  assert.equal(el('validSetup').innerText,'PASS');
  assert.equal(el('engineStatus').innerText,'SCANNING');
  assert.equal(el('newsFilter').innerText,'PASS');
  assert.equal(el('sessionFilter').innerText,'NON-PREFERRED');
  assert.match(el('newsEvidence').innerText,/4 acara diterima; 2 USD high impact/);
  d.signal.active=true;render(d);assert.equal(el('finalSignal').innerText,'SELL');
  d.signal.active=false;d.filters={};d.opportunity={score:50};d.plan={};render(d);
  for(const id of ['validSetup','scoreGate','m5Gate','riskGate','rrGate','planDirection'])assert.match(el(id).innerText,/^WAIT \(.+\)$/);
  assert.match(el('scoreGate').innerText,/50\/100 < minimum 70/);
  d.news={filter:'BLOCK',status:'ERROR'};render(d);assert.match(el('newsFilter').innerText,/^BLOCK \(Data berita gagal disahkan/);
  // Server reasons are rendered literally, never as executable HTML.
  d.gate_reasons={risk:'<img src=x onerror=alert(1)>'};render(d);
  assert.equal(el('riskGate').children[1].textContent,' (<img src=x onerror=alert(1)>)');
  d.filters.risk=true;d.filters.rr=true;render(d);assert.equal(el('riskGate').innerText,'PASS');assert.equal(el('rrGate').innerText,'PASS');
  // A stale snapshot and a failed refresh cannot leave a live BUY/SELL on screen.
  c.fetch=async()=>({ok:true,json:async()=>({...payload(),signal:{active:true},engine:{timestamp:'2000-01-01T00:00:00Z'}})});
  await vm.runInContext('runDashboard()',c);assert.match(el('finalSignal').innerText,/^WAIT \(Data engine lebih 15 minit/);
  c.fetch=async()=>{throw new Error('offline')};
  await vm.runInContext('runDashboard()',c);assert.match(el('finalSignal').innerText,/^WAIT \(Data engine gagal dimuat/);
  for(const id of ['validSetup','scoreGate','m5Gate','riskGate','rrGate'])assert.equal(el(id).innerText,'WAIT (Data engine gagal dimuat)');
  assert.match(el('newsFilter').innerText,/^BLOCK \(Status news terkini/);
  console.log('V3 dashboard reasons, news evidence and stale/error checks passed');
})().catch(err=>{console.error(err);process.exitCode=1});
