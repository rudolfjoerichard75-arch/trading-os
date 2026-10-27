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
  assert.equal(el('learningStatus').innerText,'BELUM AKTIF');
  d.learning={status:'COLLECTING',captured_today:4,candidates_today:3,rejected_today:2,gross_research_rows:1,net_research_rows:0};
  render(d);
  assert.equal(el('learningStatus').innerText,'MENGUMPUL DATA');
  assert.equal(el('learningCounts').innerText,'4 / 3 / 2');
  assert.equal(el('learningLabels').innerText,'1 / 0');
  assert.match(el('learningNote').innerText,/belum ada model AI terlatih/);

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
  const journal=payload();
  journal.signal.delivery={status:'DUPLICATE_SUPPRESSED'};
  journal.expectancy={trades:1,cancelled:4,unresolved:0};
  journal.signal_audit={tp3_observed_before_sl:0,tp3_after_sl:4,ambiguous_tp3_sl:0};
  journal.journal_recent=[{timestamp:'2026-10-01T00:00:30Z',direction:'BUY',opportunity:'BUY PULLBACK',planned_entry:100,entry:null,sl:95,tp1:106,tp2:110,tp3:115,result:'CANCELLED',exit_reason:'NEXT_OPEN_OUTSIDE_PLAN',execution_audit:{reason:'OPEN_BEYOND_SL',next_open:94},signal_path:{status:'TP3_AFTER_SL',source:'RECONSTRUCTED_PROVIDER_HISTORY',targets:{tp3:{bar:'2026-10-01T00:30:00Z',order:'AFTER_SL'}}}}];
  journal.recorded_signal=journal.journal_recent[0];render(journal);
  assert.match(el('signalDelivery').innerText,/Alert berulang ditahan/);
  assert.equal(el('signalOutcomes').innerText,'0 / 4 / 0');
  assert.equal(el('journalUnfilled').innerText,'4 / 0');
  assert.match(el('journalRecent').innerText,/NO ENTRY \(Open seterusnya sudah melepasi SL\)/);
  assert.match(el('journalRecent').innerText,/TP3 kemudian, selepas SL/);
  assert.match(el('journalRecent').innerText,/MYT/);
  assert.match(el('recordedPlan').innerText,/Entry 100.00.*SL 95.00/);
  const guarded=payload();
  guarded.expectancy={historical_fill_exceptions:3,execution_groups:{GUARDED_NEXT_M5_OPEN_TP2_V2:{trades:0,cancelled:2,win_rate:null}}};
  guarded.journal_recent=[{timestamp:'2026-10-01T00:00:30Z',direction:'BUY',planned_entry:100,sl:95,tp1:106,tp2:110,tp3:115,result:'NOT_ENTERED',exit_reason:'FILL_RR_BELOW_MIN',fill_audit:{status:'OUTSIDE_LIMITS',risk_pips:70,rr_tp2:1.14,reasons:['FILL_RR_BELOW_MIN'],enforced:true}}];
  render(guarded);
  assert.equal(el('guardedCounts').innerText,'0 / 2');
  assert.equal(el('guardedWinrate').innerText,'NO CLOSED TRADES');
  assert.match(el('journalRecent').innerText,/NO ENTRY.*RR TP2 pada harga fill/);
  assert.match(el('journalRecent').innerText,/Risk fill: 70.00 pips/);
  guarded.expectancy.win_rate=90;
  guarded.expectancy.current_version={trades:0,open_trades:1,win_rate:null};
  render(guarded);
  assert.equal(el('journalWinrate').innerText,'NO CLOSED TRADES');
  assert.equal(el('journalCounts').innerText,'0 / 1');
  assert.equal(el('guardedCounts').innerText,'0 / 2');
  guarded.signal={active:true,delivery:{status:'DELIVERY_UNKNOWN'}};
  guarded.data_health={allowed:false,status:'BLOCKED',reason:'Candle M5 lama'};
  render(guarded);
  assert.match(el('finalSignal').innerText,/^WAIT/);
  assert.equal(el('engineStatus').innerText,'DATA BLOCKED');
  assert.match(el('signalDelivery').innerText,/Penghantaran Telegram belum disahkan/);
  c.fetch=async()=>({ok:true,json:async()=>({...payload(),signal:{active:true},data_health:{allowed:true,last_candle_close:'2000-01-01T00:00:00Z'}})});
  await vm.runInContext('runDashboard()',c);
  assert.match(el('finalSignal').innerText,/^WAIT \(Candle M5 lebih 10 minit/);
  // A stale snapshot and a failed refresh cannot leave a live BUY/SELL on screen.
  c.fetch=async()=>({ok:true,json:async()=>({...payload(),signal:{active:true},engine:{timestamp:'2000-01-01T00:00:00Z'}})});
  await vm.runInContext('runDashboard()',c);assert.match(el('finalSignal').innerText,/^WAIT \(Data engine lebih 15 minit/);
  const intrabar={...payload(),engine:{timestamp:new Date().toISOString(),entry_mode:'INTRABAR'},signal:{active:true},data_health:{allowed:true},entry_timing:{current_candle_close:new Date(Date.now()+60000).toISOString()}};
  c.fetch=async()=>({ok:true,json:async()=>intrabar});
  await vm.runInContext('runDashboard()',c);
  assert.match(el('entryTimingNote').innerText,/M5 INTRABAR/);
  assert.equal(el('finalSignal').innerText,'SELL');
  intrabar.entry_timing.current_candle_close=new Date(Date.now()-1000).toISOString();
  await vm.runInContext('runDashboard()',c);
  assert.match(el('finalSignal').innerText,/WAIT.*Snapshot intrabar tamat/);
  const focused=payload();focused.strategy_focus={enabled:true,allowed:false,reason:'Fokus hanya OFF SESSION'};
  focused.filters.strategy_focus=false;render(focused);
  assert.match(el('strategyFocusNote').innerText,/SELL BREAKOUT RETEST/);
  assert.match(el('finalSignal').innerText,/OFF SESSION/);
  const multi=payload();multi.strategy_focus={mode:'PREFERENCE',enabled:false};
  multi.setup_candidates=[{opportunity:'BUY FVG',score:80,eligible:true,selected:true,zone:{low:100,high:102}}];
  multi.strategy_performance={'BUY FVG':{trades:0,open_trades:1}};
  render(multi);
  assert.match(el('strategyFocusNote').innerText,/semua sesi/);
  assert.match(el('setupCandidates').innerText,/DIPILIH.*BUY FVG.*100.00–102.00/);
  assert.match(el('setupPerformance').innerText,/BUY FVG: 0 selesai.*1 pending\/open.*Belum ada hasil/);
  c.fetch=async()=>{throw new Error('offline')};
  await vm.runInContext('runDashboard()',c);assert.match(el('finalSignal').innerText,/^WAIT \(Data engine gagal dimuat/);
  for(const id of ['validSetup','scoreGate','m5Gate','riskGate','rrGate'])assert.equal(el(id).innerText,'WAIT (Data engine gagal dimuat)');
  assert.match(el('newsFilter').innerText,/^BLOCK \(Status news terkini/);
  assert.equal(el('learningStatus').innerText,'STATUS BELUM DISAHKAN');
  console.log('V3 dashboard reasons, news evidence and stale/error checks passed');
})().catch(err=>{console.error(err);process.exitCode=1});
