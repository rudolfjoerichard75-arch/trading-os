"""Bosque V5.2: V5 strategy, shared replay/live evaluator, paper journal.
No broker execution. Prices are provider OHLC, not executable broker quotes.
"""
import os, json, math, hashlib, tempfile, argparse, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent
DATA_DIR = Path(os.getenv('BOSQUE_DATA_DIR', str(ENGINE_DIR)))
DASHBOARD_FILE = REPO_DIR / 'dashboard_data.json'
JOURNAL_FILE = DATA_DIR / 'trade_journal.json'
NEWS_CACHE_FILE = DATA_DIR / 'news_cache.json'
BACKTEST_FILE = DATA_DIR / 'backtest_results.json'
VERSION = 'V5.2 REPAIRED'
SYMBOL = 'XAU/USD'
PIP_SIZE = 0.10
MIN_SCORE = 70
MIN_RISK_PIPS, MAX_RISK_PIPS = 25, 80
TP1_PIPS, MIN_TP2_PIPS, TP3_PIPS, MIN_RR = 60, 120, 180, 2.0
OUTPUT_SIZE = 500
MAX_HOLD_BARS = 72
COOLDOWN_MINUTES = 30
MY_TZ = timezone(timedelta(hours=8))
SESSIONS = [('ASIAN',7,15),('LONDON',15,20),('NEW YORK',20,24),('NEW YORK',0,1)]
# Fixed MY-time display buckets, not exchange opening hours or DST calendars.

def now_utc(): return datetime.now(timezone.utc)
def now_my(): return now_utc().astimezone(MY_TZ)
def pips(d): return abs(float(d)) / PIP_SIZE
def pp(x): return float(x) * PIP_SIZE
def rp(x): return round(float(x), 2)
def session(dt=None):
    hour=(dt or now_my()).astimezone(MY_TZ).hour
    return next((name for name,start,end in SESSIONS if start <= hour < end), 'OFF SESSION')
def preferred(s): return s in ('LONDON','NEW YORK')
def stamp(v): return pd.Timestamp(v).tz_convert('UTC')
def clean(v):
    if isinstance(v, dict): return {str(k):clean(x) for k,x in v.items()}
    if isinstance(v, (list,tuple)): return [clean(x) for x in v]
    if isinstance(v, (datetime,pd.Timestamp)): return v.isoformat()
    if isinstance(v,float) and not math.isfinite(v): return None
    if hasattr(v,'item'): return clean(v.item())
    return v

def save_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp')
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(clean(data),f,indent=2,allow_nan=False); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def load_json(path, default=None):
    if not Path(path).exists(): return {} if default is None else default
    # Fail visibly on corruption; never overwrite a corrupt journal as empty.
    with open(path) as f: return json.load(f)

def http_json(url, payload=None):
    body=None if payload is None else json.dumps(payload).encode()
    req=Request(url,data=body,headers={'User-Agent':'Bosque/5.2','Content-Type':'application/json'})
    with urlopen(req,timeout=25) as r: return json.load(r)

def validate_bars(df):
    needed=['datetime','open','high','low','close']
    if not set(needed)<=set(df.columns): raise ValueError('Required columns: '+','.join(needed))
    df=df[needed].copy()
    df['datetime']=pd.to_datetime(df.datetime,utc=True,errors='raise')
    for col in needed[1:]: df[col]=pd.to_numeric(df[col],errors='raise')
    if df.empty or df.isna().any().any(): raise ValueError('Empty or missing OHLC data')
    if not df[needed[1:]].map(math.isfinite).all().all(): raise ValueError('Non-finite OHLC')
    if (df[needed[1:]]<=0).any().any(): raise ValueError('Nonpositive price')
    if ((df.high<df[['open','close','low']].max(axis=1)) | (df.low>df[['open','close','high']].min(axis=1))).any(): raise ValueError('Invalid OHLC geometry')
    if df.datetime.duplicated().any(): raise ValueError('Duplicate timestamps')
    if ((df.datetime.dt.minute%5!=0)|(df.datetime.dt.second!=0)).any(): raise ValueError('Expected M5 opening timestamps')
    return df.sort_values('datetime').reset_index(drop=True)

def closed_bars(df, asof):
    return df[df.datetime+pd.Timedelta(minutes=5)<=pd.Timestamp(asof)].reset_index(drop=True)

def aggregate(df, mins):
    x=df.set_index('datetime')
    out=x.resample(f'{mins}min',origin='start_day').agg({'open':'first','high':'max','low':'min','close':'last'})
    counts=x.close.resample(f'{mins}min',origin='start_day').count()
    return out[counts==mins//5].dropna().reset_index()

def swings(df,left=2,right=2):
    # Confirmed pivots only; right-side bars are already in the available prefix.
    h=df.high.reset_index(drop=True); l=df.low.reset_index(drop=True)
    lh=h.shift(1).rolling(left).max(); ll=l.shift(1).rolling(left).min()
    rh=h.iloc[::-1].shift(1).rolling(right).max().iloc[::-1]
    rl=l.iloc[::-1].shift(1).rolling(right).min().iloc[::-1]
    hm=(h>lh)&(h>=rh); lm=(l<ll)&(l<=rl)
    return [(int(i),float(h[i])) for i in h.index[hm]], [(int(i),float(l[i])) for i in l.index[lm]]


def structure(df):
    hs,ls=swings(df); direction='RANGE'; bos=None
    if len(hs)>=2 and len(ls)>=2:
        if hs[-1][1]>hs[-2][1] and ls[-1][1]>ls[-2][1]: direction='BULLISH'
        elif hs[-1][1]<hs[-2][1] and ls[-1][1]<ls[-2][1]: direction='BEARISH'
    c=float(df.iloc[-1].close)
    if hs and c>hs[-1][1]: bos='BULLISH BOS'
    if ls and c<ls[-1][1]: bos='BEARISH BOS'
    return {'direction':direction,'bos':bos,'swing_high':hs[-1][1] if hs else None,'swing_low':ls[-1][1] if ls else None}

def pdzone(df,lookback=20):
    x=df.tail(lookback); hi=float(x.high.max()); lo=float(x.low.min()); eq=(hi+lo)/2; c=float(x.iloc[-1].close)
    return {'zone':'PREMIUM' if c>eq else 'DISCOUNT' if c<eq else 'EQUILIBRIUM','equilibrium':rp(eq),'high':rp(hi),'low':rp(lo)}

def range_info(df,lookback=20):
    if len(df)<lookback: return {'is_range':False,'high':None,'low':None,'width':None,'location':None}
    x=df.tail(lookback); hi=float(x.high.max()); lo=float(x.low.min()); c=float(x.iloc[-1].close); w=hi-lo; loc=(c-lo)/w if w else .5
    return {'is_range':bool(.2<=loc<=.8 or w<c*.01),'high':hi,'low':lo,'width':w,'location':loc}

def liquidity(df):
    hs,ls=swings(df); r={'buy_side_sweep':False,'sell_side_sweep':False,'swept_level':None,'description':'NONE'}
    c=float(df.iloc[-1].close); h=float(df.iloc[-1].high); l=float(df.iloc[-1].low)
    if hs and h>hs[-1][1] and c<hs[-1][1]: r.update(buy_side_sweep=True,swept_level=hs[-1][1],description='BUY-SIDE LIQUIDITY SWEPT')
    if ls and l<ls[-1][1] and c>ls[-1][1]: r.update(sell_side_sweep=True,swept_level=ls[-1][1],description='SELL-SIDE LIQUIDITY SWEPT')
    return r

def momentum(df,n=6):
    if len(df)<n+1: return {'direction':'NEUTRAL','strength':0}
    a=df.close.tail(n+1).tolist(); up=sum(a[i]>a[i-1] for i in range(1,len(a))); dn=sum(a[i]<a[i-1] for i in range(1,len(a)))
    return {'direction':'BULLISH','strength':up} if up>=4 else {'direction':'BEARISH','strength':dn} if dn>=4 else {'direction':'NEUTRAL','strength':max(up,dn)}

def candle(df):
    c=df.iloc[-1]; body=abs(float(c.close)-float(c.open)); rng=float(c.high)-float(c.low)
    if rng<=0:return {'bullish':False,'bearish':False}
    q=body/rng
    return {'bullish':bool(c.close>c.open and q>=.55),'bearish':bool(c.close<c.open and q>=.55)}

def atr(df,n=14):
    if len(df)<n+1:return None
    pc=df.close.shift(1); tr=pd.concat([df.high-df.low,abs(df.high-pc),abs(df.low-pc)],axis=1).max(axis=1); x=tr.rolling(n).mean().iloc[-1]
    return None if pd.isna(x) else float(x)

def volatility(df):
    a=atr(df)
    if a is None:return {'atr':None,'atr_pips':None,'condition':'UNKNOWN'}
    ap=pips(a); return {'atr':rp(a),'atr_pips':round(ap,1),'condition':'LOW' if ap<25 else 'NORMAL' if ap<=70 else 'HIGH'}

# ---------------- SETUP ----------------
def m15_setup(h1,m15):
    st=structure(m15); pd= pdzone(m15); li=liquidity(m15); rg=range_info(m15); d=None; opp='NO VALID SETUP'; why=[]
    if rg['is_range'] and rg['location'] is not None:
        if li['sell_side_sweep'] and rg['location']<=.25: d='BUY'; opp='BUY RANGE REVERSAL'; why.append('M15 range low + sell-side sweep')
        elif li['buy_side_sweep'] and rg['location']>=.75: d='SELL'; opp='SELL RANGE REVERSAL'; why.append('M15 range high + buy-side sweep')
    if d is None:
        if li['sell_side_sweep']: d='BUY'; opp='BUY LIQUIDITY SWEEP'; why.append('M15 sell-side liquidity sweep')
        elif li['buy_side_sweep']: d='SELL'; opp='SELL LIQUIDITY SWEEP'; why.append('M15 buy-side liquidity sweep')
    if d is None:
        if h1['direction']=='BULLISH' and pd['zone']=='DISCOUNT': d='BUY'; opp='BUY PULLBACK'; why.append('H1 bullish + M15 discount')
        elif h1['direction']=='BEARISH' and pd['zone']=='PREMIUM': d='SELL'; opp='SELL PULLBACK'; why.append('H1 bearish + M15 premium')
    if d is None:
        if st['bos']=='BULLISH BOS': d='BUY'; opp='BUY BREAKOUT'; why.append('M15 bullish BOS')
        elif st['bos']=='BEARISH BOS': d='SELL'; opp='SELL BREAKOUT'; why.append('M15 bearish BOS')
    return {'direction':d,'opportunity':opp,'valid':d is not None,'reason':why,'pd':pd,'liquidity':li,'structure':st,'range':rg}

def m5_confirm(df,d):
    st=structure(df); mo=momentum(df); ca=candle(df); bos=st['bos']
    if d=='BUY':
        b=bos=='BULLISH BOS'; c=ca['bullish'] and mo['direction']=='BULLISH'
        return {'confirmed':b or c,'bos':bos,'candle':c,'momentum':mo['direction'],'reason':'M5 bullish BOS' if b else 'M5 bullish candle + momentum' if c else 'NO CONFIRMATION'}
    if d=='SELL':
        b=bos=='BEARISH BOS'; c=ca['bearish'] and mo['direction']=='BEARISH'
        return {'confirmed':b or c,'bos':bos,'candle':c,'momentum':mo['direction'],'reason':'M5 bearish BOS' if b else 'M5 bearish candle + momentum' if c else 'NO CONFIRMATION'}
    return {'confirmed':False,'bos':bos,'candle':False,'momentum':mo['direction'],'reason':'NO DIRECTION'}

def score(h1,s,m5,sess,vol):
    z=0
    expected='BULLISH' if s['direction']=='BUY' else 'BEARISH'
    if h1['direction']==expected:z+=15
    if h1['bos']==expected+' BOS':z+=5
    if s['valid']:z+=10
    if s['liquidity'].get('buy_side_sweep') or s['liquidity'].get('sell_side_sweep'):z+=10
    if s['structure'].get('bos')==expected+' BOS':z+=10
    if m5['confirmed']:z+=15
    if m5['bos']==expected+' BOS':z+=10
    if m5['candle']:z+=10
    if (s['direction']=='BUY' and s['pd']['zone']=='DISCOUNT') or (s['direction']=='SELL' and s['pd']['zone']=='PREMIUM'):z+=5
    if preferred(sess):z+=5
    if vol['condition']=='NORMAL':z+=5
    return min(z,100)

def plan(d,df,vol):
    if not d:return {'valid':False,'reason':'NO DIRECTION'}
    e=float(df.iloc[-1].close); st=structure(df); a=vol.get('atr'); buf=max(.2,float(a)*.2) if a else .2
    if d=='BUY':
        if st['swing_low'] is None:return {'valid':False,'reason':'NO SWING LOW'}
        sl=float(st['swing_low'])-buf
    else:
        if st['swing_high'] is None:return {'valid':False,'reason':'NO SWING HIGH'}
        sl=float(st['swing_high'])+buf
    if d=='BUY' and sl>=e:return {'valid':False,'reason':'INVALID BUY PLAN: SL must be below entry'}
    if d=='SELL' and sl<=e:return {'valid':False,'reason':'INVALID SELL PLAN: SL must be above entry'}
    risk=pips(e-sl)
    if risk<MIN_RISK_PIPS or risk>MAX_RISK_PIPS:return {'valid':False,'reason':f'Risk {risk:.1f} pips outside {MIN_RISK_PIPS}-{MAX_RISK_PIPS}'}
    t2=max(MIN_TP2_PIPS,risk*2); t3=max(TP3_PIPS,risk*3)
    t1p=pp(TP1_PIPS); t2p=pp(t2); t3p=pp(t3)
    t1=e+t1p if d=='BUY' else e-t1p; t2x=e+t2p if d=='BUY' else e-t2p; t3x=e+t3p if d=='BUY' else e-t3p
    return {'valid':True,'direction':d,'entry':rp(e),'sl':rp(sl),'risk_pips':round(risk,1),'tp1':rp(t1),'tp2':rp(t2x),'tp3':rp(t3x),'tp1_pips':TP1_PIPS,'tp2_pips':round(t2,1),'tp3_pips':round(t3,1),'rr_tp1':round(TP1_PIPS/risk,2),'rr_tp2':round(t2/risk,2),'rr_tp3':round(t3/risk,2),'risk_level':'LOW' if risk<=40 else 'MEDIUM'}


# Shared signal evaluator: closed bars only, bounded identically in live/replay.
def evaluate(df):
    w=df.tail(OUTPUT_SIZE).reset_index(drop=True)
    m15,h1=aggregate(w,15),aggregate(w,60)
    if len(h1)<30 or len(m15)<50: return {'active':False,'reason':'WARMUP'}
    h=structure(h1); setup=m15_setup(h,m15); conf=m5_confirm(w,setup['direction'])
    at=w.iloc[-1].datetime+pd.Timedelta(minutes=5)
    se=session(at); vol=volatility(w); sc=score(h,setup,conf,se,vol)
    pl=plan(setup['direction'],w,vol)
    active=bool(setup['valid'] and conf['confirmed'] and pl.get('valid') and sc>=MIN_SCORE and pl.get('rr_tp2',0)>=MIN_RR)
    return {'active':active,'reason':'VALID' if active else 'SETUP/SCORE/RISK NOT MET',
            'h1':h,'m15':setup,'m5':conf,'score':sc,'plan':pl,'volatility':vol,
            'session':se,'decision_time':at.isoformat(),'candle_time':w.iloc[-1].datetime.isoformat()}

# Calendar contracts include explicit coverage; an empty calendar must never imply safety.
def normalize_events(raw):
    if not isinstance(raw,list) or not raw: raise ValueError('Calendar must be a nonempty list')
    events=[]
    for ev in raw:
        if not isinstance(ev,dict): raise ValueError('Invalid event')
        currency=str(ev.get('country',ev.get('currency',''))).upper().strip()
        impact=str(ev.get('impact','')).upper().strip()
        if not currency or not impact: raise ValueError('Missing currency/impact')
        if currency!='USD' or impact!='HIGH': continue
        value=ev.get('date',ev.get('datetime'))
        dt=pd.Timestamp(value)
        if pd.isna(dt) or dt.tzinfo is None: raise ValueError('USD HIGH event requires timezone-aware date')
        events.append({'date':dt.tz_convert('UTC').isoformat(),'currency':'USD','impact':'HIGH','title':str(ev.get('title','USD HIGH'))})
    return sorted(events,key=lambda e:e['date'])

def news_at(calendar, at):
    at=pd.Timestamp(at)
    if not calendar or not (stamp(calendar['coverage_start'])<=at<stamp(calendar['coverage_end'])):
        return {'ok':False,'status':'UNKNOWN','reason':'CALENDAR COVERAGE MISSING','events':[]}
    upcoming=[]
    for event in calendar['events']:
        minutes=(stamp(event['date'])-at).total_seconds()/60
        if -20<=minutes<=30:
            return {'ok':False,'status':'BLOCK','reason':event['title'],'event':event['title'],'minutes':round(minutes,1),'event_time':event['date'],'events':[event]}
        if minutes>30: upcoming.append(event)
    return {'ok':True,'status':'CLEAR','reason':'No USD HIGH event in block window','events':upcoming[:5]}

def fetch_news(at):
    cache=load_json(NEWS_CACHE_FILE)
    current=pd.Timestamp(at).tz_convert('UTC')
    try:
        age=(current-stamp(cache['fetched_at'])).total_seconds()/60 if cache else None
        if age is not None and 0<=age<=30 and news_at(cache,current)['status']!='UNKNOWN':
            return {**news_at(cache,current),'source':'FOREX FACTORY','feed_status':'CACHE','fetched_at':cache['fetched_at']}
        raw=http_json('https://nfs.faireconomy.media/ff_calendar_thisweek.json')
        events=normalize_events(raw)
        # Require date evidence from this feed week (Sunday-Saturday), not just a fresh download.
        ny=current.tz_convert('America/New_York'); start=ny.normalize()-pd.Timedelta(days=(ny.weekday()+1)%7)
        end=start+pd.DateOffset(days=7)
        dates=[pd.Timestamp(e.get('date')) for e in raw if e.get('date')]
        if not dates or any(d.tzinfo is None or pd.isna(d) for d in dates): raise ValueError('Unverifiable calendar dates')
        if not all(start<=d<end for d in dates): raise ValueError('Calendar belongs to another week')
        cache={'fetched_at':current.isoformat(),'coverage_start':start.isoformat(),'coverage_end':end.isoformat(),'events':events}
        save_json(NEWS_CACHE_FILE,cache)
        return {**news_at(cache,current),'source':'FOREX FACTORY','feed_status':'LIVE','fetched_at':cache['fetched_at']}
    except Exception as exc:
        return {'ok':False,'status':'UNKNOWN','feed_status':'ERROR','reason':'Calendar unavailable or invalid: '+type(exc).__name__,'events':[]}

# Paper journal: next available bar open is the simulated entry, not a historical close fill.
def new_trade(ev, cost):
    p=ev['plan']; sid=hashlib.sha256((ev['candle_time']+'|'+p['direction']).encode()).hexdigest()[:20]
    return {'signal_id':sid,'strategy_version':VERSION,'mode':'PAPER','result':'PENDING',
            'timestamp':ev['decision_time'],'signal_candle_time':ev['candle_time'],
            'opportunity':ev['m15']['opportunity'],'score':ev['score'],'session':ev['session'],
            **p,'planned_entry':p['entry'],'round_trip_cost_pips':cost,
            'cost_status':'CONFIGURED' if cost is not None else 'UNCONFIGURED_GROSS_ONLY',
            'bars_held':0,'last_bar':None,'tp1_hit':False,'execution_model':'NEXT_M5_OPEN; TP2 FULL EXIT; SL FIRST ON AMBIGUOUS BAR'}

def finish(t, price, at, reason):
    sign=1 if t['direction']=='BUY' else -1
    gross=sign*(price-t['entry'])/PIP_SIZE
    net=gross-(t['round_trip_cost_pips'] or 0)
    t.update(result='WIN' if net>1e-8 else 'LOSS' if net < -1e-8 else 'BE',
             gross_pips=round(gross,4),result_pips=round(net,4),result_r=round(net/t['risk_pips'],6),
             exit_price=price,closed_at=pd.Timestamp(at).isoformat(),exit_reason=reason)

def process_bar(t, row):
    if t['result'] not in ('PENDING','OPEN'): return
    at=pd.Timestamp(row.datetime)
    if at<stamp(t['timestamp']): return
    if t.get('last_bar') and at<=stamp(t['last_bar']): return
    if t['result']=='PENDING':
        if at>stamp(t['timestamp']):
            t.update(result='EXPIRED',exit_reason='MISSED_NEXT_ENTRY_BAR'); return
        entry=float(row.open); sign=1 if t['direction']=='BUY' else -1
        risk=sign*(entry-t['sl'])/PIP_SIZE; reward=sign*(t['tp2']-entry)/PIP_SIZE
        if risk<MIN_RISK_PIPS or risk>MAX_RISK_PIPS or reward/risk<MIN_RR:
            t.update(result='CANCELLED',exit_reason='ENTRY_GAP_INVALIDATES_PLAN'); return
        t.update(result='OPEN',entry=entry,risk_pips=risk,filled_at=at.isoformat())
    elif t.get('last_bar') and at-stamp(t['last_bar'])!=pd.Timedelta(minutes=5):
        # Do not infer outcomes across unavailable bars, including session gaps.
        t.update(result='DATA_GAP',exit_reason='UNOBSERVED_PRICE_PATH'); return
    t['last_bar']=at.isoformat(); t['bars_held']+=1
    buy=t['direction']=='BUY'; hi=float(row.high); lo=float(row.low); op=float(row.open)
    slhit=lo<=t['sl'] if buy else hi>=t['sl']; tphit=hi>=t['tp2'] if buy else lo<=t['tp2']
    if slhit:
        stop=min(op,t['sl']) if buy else max(op,t['sl'])
        finish(t,stop,at+pd.Timedelta(minutes=5),'SL_AMBIGUOUS' if tphit else 'SL'); return
    if (hi>=t['tp1'] if buy else lo<=t['tp1']): t['tp1_hit']=True
    if tphit: finish(t,t['tp2'],at+pd.Timedelta(minutes=5),'TP2'); return
    if t['bars_held']>=MAX_HOLD_BARS: finish(t,float(row.close),at+pd.Timedelta(minutes=5),'TIME_EXIT')

def advance(trades, df):
    for t in trades:
        if t.get('strategy_version')!=VERSION and t.get('result') in ('OPEN','PENDING'):
            t.update(result='LEGACY_UNRESOLVED',exit_reason='PRE_REPAIR_EXECUTION_UNKNOWN'); continue
        if t['result'] in ('OPEN','PENDING'):
            for row in df.itertuples(index=False):
                process_bar(t,row)
                if t['result'] not in ('OPEN','PENDING'): break

def entry_allowed(trades, at):
    if any(t['result'] in ('PENDING','OPEN','DATA_GAP','LEGACY_UNRESOLVED') for t in trades): return False
    # Unresolved data gaps require investigation, not silently assuming flat.
    if trades and (pd.Timestamp(at)-stamp(trades[-1]['timestamp'])).total_seconds()<COOLDOWN_MINUTES*60: return False
    day=pd.Timestamp(at).tz_convert(MY_TZ).date()
    closed=[t for t in trades if t.get('closed_at') and stamp(t['closed_at']).tz_convert(MY_TZ).date()==day and t.get('result_r') is not None]
    if sum(t['result_r'] for t in closed)<=-2: return False
    if len(closed)>=3 and all(t['result']=='LOSS' for t in closed[-3:]): return False
    return True

def stats(trades):
    c=sorted([t for t in trades if t.get('result') in ('WIN','LOSS','BE') and t.get('strategy_version')==VERSION],key=lambda t:t['closed_at'])
    rs=[t['result_r'] for t in c]; n=len(c); wins=sum(t['result']=='WIN' for t in c)
    eq=peak=dd=streak=max_streak=0
    for r in rs:
        eq+=r; peak=max(peak,eq); dd=max(dd,peak-eq); streak=streak+1 if r<0 else 0; max_streak=max(max_streak,streak)
    gain=sum(max(r,0) for r in rs); loss=-sum(min(r,0) for r in rs)
    # Wilson interval describes sampling uncertainty; does not certify independence.
    interval=None
    if n:
        z=1.96; p=wins/n; center=(p+z*z/(2*n))/(1+z*z/n)
        half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
        interval=[round(100*(center-half),2),round(100*(center+half),2)]
    return {'status':'PAPER SIMULATION','trades':n,'wins':wins,'losses':sum(t['result']=='LOSS' for t in c),
            'win_rate':round(100*wins/n,2) if n else None,'win_rate_95_interval':interval,
            'average_r':sum(rs)/n if n else None,'expectancy_r':sum(rs)/n if n else None,
            'profit_factor':gain/loss if loss else None,'max_drawdown':dd,'max_consecutive_losses':max_streak,
            'total_pips':sum(t['result_pips'] for t in c),'open_trades':sum(t['result'] in ('OPEN','PENDING') for t in trades),
            'unresolved':sum(t['result'] in ('DATA_GAP','LEGACY_UNRESOLVED') for t in trades),
            'cost_status':'NET_CONFIGURED' if c and all(t.get('round_trip_cost_pips') is not None for t in c) else 'GROSS_OR_MIXED_NOT_NET_VALIDATED'}

def telegram(text):
    token=os.getenv('TELEGRAM_BOT_TOKEN'); chat=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat: return 'NOT_CONFIGURED'
    try:
        reply=http_json('https://api.telegram.org/bot'+token+'/sendMessage',{'chat_id':chat,'text':text})
        return 'SENT' if reply.get('ok') is True else 'FAILED'
    except Exception: return 'FAILED'

def run_live():
    from urllib.parse import urlencode
    at=now_utc(); key=os.getenv('TWELVEDATA_API_KEY')
    if not key: raise RuntimeError('TWELVEDATA_API_KEY missing')
    data=http_json('https://api.twelvedata.com/time_series?'+urlencode({'symbol':SYMBOL,'interval':'5min','outputsize':OUTPUT_SIZE,'apikey':key,'timezone':'UTC'}))
    if 'values' not in data: raise RuntimeError('Market provider returned no candles')
    bars=closed_bars(validate_bars(pd.DataFrame(data['values'])),at)
    if bars.empty: raise RuntimeError('No closed M5 candles')
    age=(pd.Timestamp(at)-(bars.iloc[-1].datetime+pd.Timedelta(minutes=5))).total_seconds()/60
    if age<0 or age>10: raise RuntimeError('STALE market candles')
    old=load_json(DASHBOARD_FILE)
    journal=load_json(JOURNAL_FILE,old.get('_journal_store',{'trades':[]}))
    trades=journal['trades']; advance(trades,bars)
    save_json(JOURNAL_FILE,journal)
    ev=evaluate(bars); news=fetch_news(at); news.update(filter=news['status'],high_impact='HIGH' if news['status']=='BLOCK' else news['status'],minutes_to_news=news.get('minutes')); pl=ev.get('plan',{}); setup=ev.get('m15',{})
    ct=bars.iloc[-1].datetime.isoformat(); processed=journal.get('last_processed_candle')==ct
    allowed=entry_allowed(trades,ev.get('decision_time',at))
    # First scan of a bar only; do not retroactively enter after the news window expires.
    valid=bool(ev['active'] and news['ok'] and allowed and not processed and age<=2)
    sig={'active':False,'direction':pl.get('direction'),'candle_time':ct}
    if valid:
        cost=os.getenv('ROUND_TRIP_COST_PIPS'); cost=float(cost) if cost else None
        if cost is not None and (not math.isfinite(cost) or cost<0): raise ValueError('Invalid trading cost')
        t=new_trade(ev,cost); t['news_status']=news['status']; t['observed_at']=at.isoformat()
        trades.append(t); sig={**t,'active':True,'id':t['signal_id']}
        # Persist intent before outbound notification. Failed/uncertain sends are visible, not retried blindly.
        t['telegram_status']='PENDING'; journal['last_processed_candle']=ct; save_json(JOURNAL_FILE,journal)
        t['telegram_status']=telegram(f"BOSQUE {VERSION}\n{t['direction']} {t['opportunity']}\nReference entry {t['planned_entry']} | SL {t['sl']} | TP2 {t['tp2']}\nScore {ev['score']} | PAPER signal, manual execution\nID {t['signal_id']}")
    journal['last_processed_candle']=ct; journal['version']=VERSION; save_json(JOURNAL_FILE,journal)
    st=stats(trades)
    bt=load_json(BACKTEST_FILE,{'status':'NOT RUN','reason':'Historical CSV required'})
    dashboard={'engine':{'version':VERSION,'timestamp':at.isoformat(),'status':'SIGNAL' if valid else 'WAIT','symbol':SYMBOL},
        'latest_price':float(bars.iloc[-1].close),'session':ev.get('session',session()),'signal':sig,'plan':pl,
        'opportunity':{'type':setup.get('opportunity','WARMUP'),'direction':pl.get('direction'),'score':ev.get('score',0),'valid':valid},
        'regime':{'type':ev.get('h1',{}).get('direction','UNKNOWN')},'volatility':ev.get('volatility',{}),
        'pd':setup.get('pd',{}),'liquidity':setup.get('liquidity',{}),'news':news,
        'h1':ev.get('h1',{}),'m15':{**setup.get('structure',{}),'setup':setup},'m5':{'confirmation':ev.get('m5',{})},
        'potential':{k:pl.get(k) for k in ['tp1_pips','tp2_pips','tp3_pips']},
        'filters':{'news':news['ok'],'score':ev.get('score',0)>=MIN_SCORE,'risk':pl.get('valid',False),'rr':pl.get('rr_tp2',0)>=MIN_RR,'session_blocking':False,'entry_policy':allowed,'setup':bool(setup.get('valid')),'m5_confirmation':bool(ev.get('m5',{}).get('confirmed')),'session':preferred(ev.get('session',''))},
        'risk_engine':{'status':'AVAILABLE' if allowed else 'BLOCKED','daily_loss_limit':'2R PAPER','consecutive_loss_limit':'3/day PAPER','risk_pips':pl.get('risk_pips'),'risk_level':pl.get('risk_level'),'min_risk_pips':MIN_RISK_PIPS,'max_risk_pips':MAX_RISK_PIPS},
        'sop':{'fresh_zone':'NOT IMPLEMENTED','news_filter':news['status'],'session_blocking':'NO','session_filter':'INFORMATIONAL','risk':pl.get('risk_level'),'m15_setup':str(setup.get('valid',False)),'m5_confirmation':str(ev.get('m5',{}).get('confirmed',False))},
        'confirmations':{'h1_bias':ev.get('h1',{}).get('direction'),'m15_setup':setup.get('opportunity'),'m5_confirmation':ev.get('m5',{}).get('reason')},
        'invalidation':{'status':'SIGNAL' if valid else 'WAIT','conditions':[ev.get('reason','UNKNOWN'),news['reason'],'Paper entry policy: '+('AVAILABLE' if allowed else 'BLOCKED'),'Late signals blocked: '+str(age>2)]},
        'journal':st,'expectancy':st,'forward_test':st,'backtest':{**bt,'results':{k:{'stats':v.get('stats',{})} for k,v in bt.get('results',{}).items()}},'_journal_store':journal,
        'limitations':['Paper OHLC simulation; not MT5 executions','TP1 observation only; full exit TP2','Intrabar order unknown; SL first','Session names use fixed MY-time buckets'],
        'data_health':{'last_candle':ct,'age_minutes':age,'late_signal_blocked':age>2},
        'telegram':{'last_status':trades[-1].get('telegram_status') if trades else 'NO_SIGNAL'}}
    from bosque_services import refresh
    dashboard=refresh(dashboard,journal,at)
    save_json(DASHBOARD_FILE,dashboard)
    print(json.dumps({'status':dashboard['engine']['status'],'version':VERSION,'journal_trades':len(trades),'news':news['status']}))

def main():
    try: run_live()
    except Exception as exc:
        # Never leave an old active signal looking current after failure.
        try: previous=load_json(DASHBOARD_FILE)
        except Exception: previous={}
        save_json(DASHBOARD_FILE,{**previous,'engine':{'version':VERSION,'timestamp':now_utc().isoformat(),'status':'ERROR'},'signal':{'active':False},'error':type(exc).__name__+': '+str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__})
        print('Engine failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1)

if __name__=='__main__': main()
