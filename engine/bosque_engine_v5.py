import os, json, math, hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd

# =========================================================
# BOSQUE FOREX AI - SCALPING ENGINE V4
# H1 -> M15 -> M5 | Journal | Forward Test | Backtest
# One Twelve Data request per scan + lightweight economic-calendar request.
# News filter is fail-safe and blocks USD high-impact windows for XAU/USD.
# Session is informational only and NEVER blocks a signal.
# =========================================================

ENGINE_DIR = Path(__file__).resolve().parent
REPO_DIR = ENGINE_DIR.parent
DASHBOARD_FILE = REPO_DIR / 'dashboard_data.json'
STATE_FILE = ENGINE_DIR / 'state.json'
JOURNAL_FILE = ENGINE_DIR / 'trade_journal.json'
BACKTEST_FILE = ENGINE_DIR / 'backtest_results.json'
FORWARD_FILE = ENGINE_DIR / 'forward_test.json'

TWELVEDATA_URL = 'https://api.twelvedata.com/time_series'
NEWS_URL = os.getenv('NEWS_CALENDAR_URL', 'https://nfs.faireconomy.media/ff_calendar_thisweek.json')
API_KEY = os.getenv('TWELVEDATA_API_KEY', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
SYMBOL = 'XAU/USD'
INTERVAL = '5min'
OUTPUT_SIZE = 500

MIN_SCORE = 70
PIP_SIZE = 0.10
MIN_RISK_PIPS = 25
MAX_RISK_PIPS = 80
TP1_PIPS = 60
MIN_TP2_PIPS = 120
TP3_PIPS = 180
MIN_RR = 2.0

BACKTEST_ENABLED = True
BACKTEST_MIN_BARS = 150
BACKTEST_MAX_HOLD_BARS = 72
BACKTEST_ONE_TRADE_AT_A_TIME = True
FORWARD_MAX_HOLD_BARS = 72
NEWS_BLOCK_BEFORE_MIN = 30
NEWS_BLOCK_AFTER_MIN = 20
NEWS_CURRENCIES = {'USD'}
NEWS_IMPACTS = {'HIGH'}

MY_TZ = timezone(timedelta(hours=8))
SESSIONS = [('ASIAN',7,15),('LONDON',15,20),('NEW YORK',20,23),('NEW YORK',0,1)]


def now_my(): return datetime.now(timezone.utc).astimezone(MY_TZ)

def clean(v):
    if isinstance(v, dict): return {str(k): clean(x) for k,x in v.items()}
    if isinstance(v, list): return [clean(x) for x in v]
    if isinstance(v, pd.Timestamp): return v.isoformat()
    if isinstance(v, datetime): return v.isoformat()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
    return v

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with open(tmp,'w',encoding='utf-8') as f: json.dump(clean(data),f,ensure_ascii=False,indent=2)
    tmp.replace(path)

def load_json(path):
    if not path.exists(): return {}
    try:
        with open(path,'r',encoding='utf-8') as f: return json.load(f)
    except Exception: return {}

def pips(d): return abs(float(d))/PIP_SIZE
def pp(x): return float(x)*PIP_SIZE
def rp(x): return round(float(x),2)
def session(dt=None):
    dt=dt or now_my()
    for n,s,e in SESSIONS:
        if s <= dt.hour < e: return n
    return 'OFF SESSION'
def preferred(s): return s in ('LONDON','NEW YORK')

# ---------------- DATA ----------------
def fetch_m5():
    if not API_KEY: raise RuntimeError('TWELVEDATA_API_KEY missing')
    r=requests.get(TWELVEDATA_URL,params={'symbol':SYMBOL,'interval':INTERVAL,'outputsize':OUTPUT_SIZE,'apikey':API_KEY,'format':'JSON'},timeout=30)
    r.raise_for_status(); data=r.json()
    if 'values' not in data: raise RuntimeError(f'Twelve Data error: {data}')
    df=pd.DataFrame(data['values'])
    if df.empty: raise RuntimeError('No market data returned')
    df['datetime']=pd.to_datetime(df['datetime'],utc=True)
    for c in ('open','high','low','close'): df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.dropna(subset=['datetime','open','high','low','close']).sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)
    return remove_incomplete(df)

def remove_incomplete(df):
    if df.empty: return df
    last=df.iloc[-1]['datetime']
    if last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc)-last).total_seconds() < 300: return df.iloc[:-1].copy()
    return df

def aggregate(df, mins):
    x=df.copy().set_index(pd.to_datetime(df['datetime'],utc=True))
    x=x.resample(f'{mins}min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna().reset_index().rename(columns={'index':'datetime'})
    return x

# ---------------- STRUCTURE ----------------
def swings(df,left=2,right=2):
    hs=[]; ls=[]
    if len(df)>=left+right+1:
        for i in range(left,len(df)-right):
            h=float(df.iloc[i].high); l=float(df.iloc[i].low)
            if h>float(df.iloc[i-left:i].high.max()) and h>=float(df.iloc[i+1:i+right+1].high.max()): hs.append((i,h))
            if l<float(df.iloc[i-left:i].low.min()) and l<=float(df.iloc[i+1:i+right+1].low.min()): ls.append((i,l))
    return hs,ls

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
        if st['bos']=='BULLISH BOS': d='BUY'; opp='BUY BREAKOUT RETEST'; why.append('M15 bullish BOS')
        elif st['bos']=='BEARISH BOS': d='SELL'; opp='SELL BREAKOUT RETEST'; why.append('M15 bearish BOS')
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
    if h1['direction'] in ('BULLISH','BEARISH'):z+=15
    if h1['bos']:z+=5
    if s['valid']:z+=10
    if s['liquidity'].get('buy_side_sweep') or s['liquidity'].get('sell_side_sweep'):z+=10
    if s['structure'].get('bos'):z+=10
    if m5['confirmed']:z+=15
    if m5['bos']:z+=10
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

# ---------------- NEWS ----------------
def parse_news_time(value):
    if not value:
        return None
    try:
        dt=pd.to_datetime(value, utc=True, errors='coerce')
        if pd.isna(dt):
            return None
        return dt.to_pydatetime().astimezone(MY_TZ)
    except Exception:
        return None

def fetch_news(now=None):
    """Fetch a lightweight economic calendar and evaluate USD high-impact events.

    This is intentionally separate from Twelve Data so the market-data quota remains
    one Twelve Data request per scan. Unknown calendar failures fail closed.
    """
    now=now or now_my()
    try:
        r=requests.get(NEWS_URL, timeout=15, headers={'User-Agent':'BosqueForexAI/5.0'})
        r.raise_for_status()
        payload=r.json()
        events=payload if isinstance(payload,list) else payload.get('events',[]) if isinstance(payload,dict) else []
        candidates=[]
        for ev in events:
            if not isinstance(ev,dict):
                continue
            currency=str(ev.get('country',ev.get('currency',''))).upper().strip()
            impact=str(ev.get('impact','')).upper().strip()
            if currency not in NEWS_CURRENCIES or impact not in NEWS_IMPACTS:
                continue
            dt=parse_news_time(ev.get('date',ev.get('datetime',ev.get('time'))))
            if dt is None:
                continue
            minutes=(dt-now).total_seconds()/60.0
            if -NEWS_BLOCK_AFTER_MIN <= minutes <= NEWS_BLOCK_BEFORE_MIN:
                candidates.append({
                    'title':str(ev.get('title',ev.get('event','USD HIGH IMPACT NEWS'))),
                    'currency':currency,
                    'impact':impact,
                    'datetime':dt.isoformat(),
                    'minutes_to_news':round(minutes,1),
                })
        candidates.sort(key=lambda x: abs(x['minutes_to_news']))
        if candidates:
            e=candidates[0]
            return {'ok':False,'status':'BLOCK','high_impact':'HIGH','minutes':e['minutes_to_news'],'event':e['title'],'currency':e['currency'],'event_time':e['datetime'],'source':NEWS_URL,'reason':'USD high-impact event inside block window','events':candidates[:5]}
        return {'ok':True,'status':'PASS','high_impact':'CLEAR','minutes':None,'event':None,'currency':'USD','event_time':None,'source':NEWS_URL,'reason':'No USD high-impact event inside block window','events':[]}
    except Exception as exc:
        return {'ok':False,'status':'BLOCK','high_impact':'UNKNOWN','minutes':None,'event':None,'currency':'USD','event_time':None,'source':NEWS_URL,'reason':f'News calendar unavailable: {type(exc).__name__}','events':[]}

# ---------------- IDS / TELEGRAM ----------------
def signal_id(d,opp,ct,e,sl,tp):
    return hashlib.sha256(f'{d}|{opp}|{ct}|{round(e,2)}|{round(sl,2)}|{round(tp,2)}'.encode()).hexdigest()[:16]

def telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',json={'chat_id':TELEGRAM_CHAT_ID,'text':msg},timeout=20)
        return r.ok
    except Exception:return False

def format_msg(d,opp,s,p,se,pd,v,n):
    return (f'👑 BOSQUE FOREX AI\n\n🚨 {d} {opp}\n⭐ Score: {s}/100\n\n'
            f'📍 Entry: {p["entry"]}\n🛑 SL: {p["sl"]}\n🎯 TP1: {p["tp1"]} ({p["tp1_pips"]} pips)\n'
            f'🎯 TP2: {p["tp2"]} ({p["tp2_pips"]} pips)\n🎯 TP3: {p["tp3"]} ({p["tp3_pips"]} pips)\n\n'
            f'💰 Risk: {p["risk_pips"]} pips\n📊 RR TP2: 1:{p["rr_tp2"]}\n🌍 Session: {se}\n⭐ Preferred: {"YES" if preferred(se) else "NO"}\n'
            f'📐 PD: {pd["zone"]}\n🌡 ATR: {v.get("atr_pips")} pips\n📰 News: {n["status"]}\n\n⚠️ Manual confirmation required.')

# ---------------- JOURNAL / OUTCOMES ----------------
def load_journal():
    j=load_json(JOURNAL_FILE)
    if not j:j={'version':'2.0','symbol':SYMBOL,'trades':[]}
    j.setdefault('trades',[]); return j

def add_journal(sig,setup,conf,pl,scorev,se,nr,scan_time,candle_time):
    if not sig.get('active'):return
    j=load_journal()
    if any(t.get('signal_id')==sig['id'] for t in j['trades']):return
    j['trades'].append({'signal_id':sig['id'],'timestamp':scan_time.isoformat(),'signal_candle_time':candle_time.isoformat(),'symbol':SYMBOL,'direction':sig['direction'],'opportunity':sig['opportunity'],'score':scorev,'session':se,'preferred_session':preferred(se),'news_status':nr['status'],'entry':pl.get('entry'),'sl':pl.get('sl'),'tp1':pl.get('tp1'),'tp2':pl.get('tp2'),'tp3':pl.get('tp3'),'risk_pips':pl.get('risk_pips'),'tp1_pips':pl.get('tp1_pips'),'tp2_pips':pl.get('tp2_pips'),'tp3_pips':pl.get('tp3_pips'),'rr_tp2':pl.get('rr_tp2'),'result':'OPEN','result_pips':None,'result_r':None,'closed_at':None,'exit_price':None,'exit_reason':None,'bars_held':None,'setup_reason':setup.get('reason',[]),'m5_confirmation':conf.get('reason')})
    save_json(JOURNAL_FILE,j)

def resolve_trade(t,df,maxbars=72):
    if t.get('result')!='OPEN':return t
    try:
        d=t['direction']; e=float(t['entry']); sl=float(t['sl']); tp=float(t['tp2']); st=pd.to_datetime(t['signal_candle_time'],utc=True)
    except Exception:return t
    x=df[pd.to_datetime(df.datetime,utc=True)>st].head(maxbars).copy()
    for _,r in x.iterrows():
        hi=float(r.high); lo=float(r.low); ct=pd.to_datetime(r.datetime,utc=True)
        if d=='BUY': slhit=lo<=sl; tphit=hi>=tp; rpnl=pips(tp-e); rloss=pips(e-sl)
        else: slhit=hi>=sl; tphit=lo<=tp; rpnl=pips(e-tp); rloss=pips(sl-e)
        result=None; exitp=None; reason=None
        if slhit: result='LOSS'; exitp=sl; reason='SL' if not tphit else 'SL_AND_TP_SAME_CANDLE_SL_FIRST'
        elif tphit: result='WIN'; exitp=tp; reason='TP2'
        if result:
            t.update(result=result,result_pips=round(-rloss if result=='LOSS' else rpnl,1),result_r=-1.0 if result=='LOSS' else round(rpnl/max(rloss,.0001),3),closed_at=ct.isoformat(),exit_price=exitp,exit_reason=reason)
            t['bars_held']=int((pd.to_datetime(df.datetime,utc=True)>st)&(pd.to_datetime(df.datetime,utc=True)<=ct)).sum(); return t
    if len(x)>=maxbars:
        r=x.iloc[-1]; close=float(r.close); move=pips(close-e) if d=='BUY' else pips(e-close); risk=pips(e-sl) if d=='BUY' else pips(sl-e); result='BE' if abs(move)<5 else 'WIN' if move>0 else 'LOSS'; t.update(result=result,result_pips=round(move,1),result_r=round(move/max(risk,.0001),3),closed_at=pd.to_datetime(r.datetime,utc=True).isoformat(),exit_price=rp(close),exit_reason='TIME_EXIT',bars_held=maxbars)
    return t

def update_outcomes(df):
    j=load_journal(); changed=False
    for i,t in enumerate(j['trades']):
        old=json.dumps(t,sort_keys=True); j['trades'][i]=resolve_trade(t,df,FORWARD_MAX_HOLD_BARS); changed |= old!=json.dumps(j['trades'][i],sort_keys=True)
    if changed:save_json(JOURNAL_FILE,j)
    return j

def stats(trades):
    c=[t for t in trades if t.get('result') in ('WIN','LOSS','BE')]; w=sum(t.get('result')=='WIN' for t in c); l=sum(t.get('result')=='LOSS' for t in c); b=sum(t.get('result')=='BE' for t in c); rs=[float(t.get('result_r')) for t in c if t.get('result_r') is not None]; gp=sum(x for x in rs if x>0); gl=abs(sum(x for x in rs if x<0)); return {'trades':len(c),'wins':w,'losses':l,'breakeven':b,'win_rate':round(w/len(c)*100,2) if c else None,'total_pips':round(sum(float(t.get('result_pips') or 0) for t in c),1),'average_r':round(sum(rs)/len(rs),3) if rs else None,'profit_factor':round(gp/gl,3) if gl else None}

def forward(df):
    j=update_outcomes(df); s=stats(j['trades']); out={'status':'FORWARD TESTING','symbol':SYMBOL,**s,'open_trades':sum(t.get('result')=='OPEN' for t in j['trades']),'last_updated':now_my().isoformat()}; save_json(FORWARD_FILE,out); return out

# ---------------- ROLLING BACKTEST ----------------
def signal_at(df,i):
    if i<BACKTEST_MIN_BARS:return None
    w=df.iloc[:i+1].copy(); m15=aggregate(w,15); h1=aggregate(w,60)
    if len(m15)<50 or len(h1)<30:return None
    h=structure(h1); s=m15_setup(h,m15); c=m5_confirm(w,s['direction']); dt=pd.to_datetime(w.iloc[-1].datetime,utc=True).to_pydatetime().astimezone(MY_TZ); se=session(dt); v=volatility(w); sc=score(h,s,c,se,v); pl=plan(s['direction'],w,v)
    if not (s['valid'] and c['confirmed'] and pl.get('valid') and sc>=MIN_SCORE):return None
    ct=pd.to_datetime(w.iloc[-1].datetime,utc=True); return {'signal_id':signal_id(s['direction'],s['opportunity'],ct.isoformat(),pl['entry'],pl['sl'],pl['tp2']),'candle_time':ct.isoformat(),'direction':s['direction'],'opportunity':s['opportunity'],'score':sc,'session':se,'entry':pl['entry'],'sl':pl['sl'],'tp2':pl['tp2'],'risk_pips':pl['risk_pips']}

def backtest(df):
    if not BACKTEST_ENABLED:return {'status':'DISABLED','symbol':SYMBOL}
    if len(df)<BACKTEST_MIN_BARS+30:return {'status':'NOT ENOUGH DATA','symbol':SYMBOL,'bars_used':len(df)}
    trades=[]; i=BACKTEST_MIN_BARS
    while i<len(df)-1:
        s=signal_at(df,i)
        if not s:i+=1;continue
        t={'signal_id':s['signal_id'],'signal_candle_time':s['candle_time'],'direction':s['direction'],'entry':s['entry'],'sl':s['sl'],'tp2':s['tp2'],'result':'OPEN'}
        t=resolve_trade(t,df.iloc[i+1:].copy(),BACKTEST_MAX_HOLD_BARS); t.update({k:s[k] for k in ('signal_id','candle_time','direction','opportunity','score','session','entry','sl','tp2','risk_pips')});
        if t.get('result')!='OPEN':trades.append(t)
        if BACKTEST_ONE_TRADE_AT_A_TIME and t.get('closed_at'):
            close=pd.to_datetime(t['closed_at'],utc=True); inds=df.index[pd.to_datetime(df.datetime,utc=True)<=close]; i=(int(inds[-1])+1 if len(inds) else i+1); continue
        i+=1
    out={'status':'COMPLETED','symbol':SYMBOL,'timeframe':'M5','bars_used':len(df),'period_start':df.iloc[0].datetime.isoformat(),'period_end':df.iloc[-1].datetime.isoformat(),'strategy':'SCALPING V4 H1-M15-M5','min_score':MIN_SCORE,'min_rr':MIN_RR,'pip_size':PIP_SIZE,'stats':stats(trades),'trades':trades,'note':'Rolling replay of the already-fetched M5 dataset. No extra Twelve Data request.','last_updated':now_my().isoformat()}; save_json(BACKTEST_FILE,out); return out

# ---------------- MAIN ----------------
def main():
    ts=now_my(); m5=fetch_m5()
    if len(m5)<100:raise RuntimeError('Not enough M5 candles')
    fw=forward(m5); bt=backtest(m5)
    m15=aggregate(m5,15); h1=aggregate(m5,60)
    if len(m15)<50 or len(h1)<30:raise RuntimeError('Not enough MTF data')
    h=structure(h1); ms=structure(m15); m5s=structure(m5); rg=range_info(h1); mode='TRENDING' if h['direction'] in ('BULLISH','BEARISH') else 'RANGING' if rg['is_range'] else 'NEUTRAL'
    pdv=pdzone(m15); li=liquidity(m15); vol=volatility(m5); se=session(ts); s=m15_setup(h,m15); c=m5_confirm(m5,s['direction']); sc=score(h,s,c,se,vol); pl=plan(s['direction'],m5,vol)
    nr=fetch_news(ts)
    gates={'score':sc>=MIN_SCORE,'setup':s['valid'],'m5_confirmation':c['confirmed'],'risk':pl.get('valid',False),'rr':pl.get('rr_tp2',0)>=MIN_RR,'news':nr['ok'],'session':preferred(se),'session_blocking':False}
    valid=all(gates[k] for k in ('score','setup','m5_confirmation','risk','rr','news'))
    ct=pd.to_datetime(m5.iloc[-1].datetime,utc=True); sig={'active':False,'id':None,'direction':s['direction'],'opportunity':s['opportunity'],'score':sc,'timestamp':ts.isoformat(),'candle_time':ct.isoformat()}
    state=load_json(STATE_FILE)
    if valid:
        sid=signal_id(s['direction'],s['opportunity'],ct.isoformat(),pl['entry'],pl['sl'],pl['tp2']); sig.update(active=True,id=sid,entry=pl['entry'],sl=pl['sl'],tp1=pl['tp1'],tp2=pl['tp2'],tp3=pl['tp3'])
        if sid!=state.get('last_signal_id'):
            if telegram(format_msg(s['direction'],s['opportunity'],sc,pl,se,pdv,vol,nr)):
                state.update(last_signal_id=sid,last_signal_sent=ts.isoformat()); save_json(STATE_FILE,state)
    add_journal(sig,s,c,pl,sc,se,nr,ts,ct)
    fw=forward(m5)
    j=load_journal(); js=stats(j['trades'])
    dashboard={'engine':{'name':'BOSQUE FOREX AI','version':'SCALPING V5 NEWS-AUDITED','symbol':SYMBOL,'timeframe':'H1 → M15 → M5','timestamp':ts.isoformat(),'data_requests_this_scan':1},'latest_price':rp(m5.iloc[-1].close),'session':se,'market_mode':mode,'regime':{'type':mode,'h1_direction':h['direction'],'range':rg},'volatility':vol,'pd':pdv,'liquidity':{**li,'pdh':None,'pdl':None,'asia_high':None,'asia_low':None,'session_high':None,'session_low':None},'news':{'status':nr['status'],'high_impact':nr['high_impact'],'minutes_to_news':nr['minutes'],'event':nr.get('event'),'currency':nr.get('currency'),'event_time':nr.get('event_time'),'reason':nr.get('reason'),'source':nr.get('source'),'events':nr.get('events',[]),'filter':nr['status']},'opportunity':{'type':s['opportunity'],'direction':s['direction'],'valid':s['valid'],'score':sc},'signal':sig,'plan':pl,'potential':{'tp1_pips':pl.get('tp1_pips'),'tp2_pips':pl.get('tp2_pips'),'tp3_pips':pl.get('tp3_pips')},'h1':{**h,'condition':mode},'m15':{**ms,'setup':s},'m5':{**m5s,'confirmation':c},'filters':gates,'confirmations':{'h1_bias':h['direction'],'m15_setup':s['opportunity'],'m5_confirmation':c['reason'],'liquidity':li['description'],'pd_zone':pdv['zone']},'risk_engine':{'status':'VALID' if gates['risk'] else 'INVALID','risk_pips':pl.get('risk_pips'),'risk_level':pl.get('risk_level'),'min_risk_pips':MIN_RISK_PIPS,'max_risk_pips':MAX_RISK_PIPS,'daily_loss_limit':'NOT CONFIGURED','consecutive_loss_limit':'NOT CONFIGURED'},'invalidation':{'status':'VALID' if valid else 'WAIT','conditions':['M5 confirmation required','Risk must remain valid','RR TP2 must remain >= 1:2','Avoid invalidation after structure failure']},'sop':{'news_filter':nr['status'],'session_filter':'PREFERRED' if preferred(se) else 'NON-PREFERRED','session_blocking':'NO','risk':pl.get('risk_level','UNKNOWN'),'fresh_zone':'YES' if s['valid'] else 'NO','m15_setup':'YES' if gates['setup'] else 'NO','m5_confirmation':'YES' if gates['m5_confirmation'] else 'NO'},'journal':{'status':'ACTIVE','file':JOURNAL_FILE.name,'open_trades':sum(t.get('result')=='OPEN' for t in j['trades']),**js},'forward_test':fw,'backtest':{'status':bt.get('status'),'file':BACKTEST_FILE.name,'bars_used':bt.get('bars_used'),'period_start':bt.get('period_start'),'period_end':bt.get('period_end'),'stats':bt.get('stats',{}),'note':bt.get('note')}}
    save_json(DASHBOARD_FILE,dashboard); print(json.dumps(clean(dashboard),indent=2))

if __name__=='__main__': main()
