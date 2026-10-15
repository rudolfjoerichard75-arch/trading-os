"""V3 support only: paper accounting, persistence and replay adapter.
Entry decisions are delegated unchanged to bosque_engine's V3 functions.
"""
import csv, json, math, os, hashlib
from pathlib import Path
import pandas as pd

VERSION = 'SCALPING V3'

def load(path, default=None):
    if not Path(path).exists(): return {} if default is None else default
    return json.loads(Path(path).read_text())

def save(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    temp.replace(path)

def stamp(value): return pd.to_datetime(value,utc=True)

def bars(frame):
    df=frame.copy(); df['datetime']=pd.to_datetime(df.datetime,utc=True,errors='raise')
    for col in ['open','high','low','close']: df[col]=pd.to_numeric(df[col],errors='raise')
    if df[['open','high','low','close']].isna().any().any(): raise ValueError('Missing OHLC')
    if not df[['open','high','low','close']].map(math.isfinite).all().all(): raise ValueError('Nonfinite OHLC')
    if ((df.high<df[['open','close','low']].max(axis=1)) | (df.low>df[['open','close','high']].min(axis=1))).any(): raise ValueError('Invalid OHLC')
    if df.datetime.duplicated().any(): raise ValueError('Duplicate candle')
    return df.sort_values('datetime').reset_index(drop=True)

def evaluate(df, at, news_ok=True):
    import bosque_engine as e
    if len(df)<100: return {'active':False}
    m15=e.aggregate(df,15); h1=e.aggregate(df,60)
    if len(m15)<50 or len(h1)<30: return {'active':False}
    hs=e.analyze_structure(h1); setup=e.detect_m15_setup(hs,m15,df)
    conf=e.m5_confirmation(df,setup['direction']); vol=e.volatility_analysis(df)
    session=e.get_session(stamp(at).to_pydatetime().astimezone(e.MY_TZ))
    score=e.calculate_score(hs,setup,conf,session,vol)
    plan=e.create_trade_plan(setup['direction'],df,score,vol)
    active=all([score>=e.MIN_SCORE,setup['valid'],conf['confirmed'],plan.get('valid',False),plan.get('rr_tp2',0)>=e.MIN_RR,news_ok])
    return {'active':active,'score':score,'plan':plan,'opportunity':setup['opportunity'],'direction':setup['direction'],'session':session,'timestamp':stamp(at).isoformat()}

def cost():
    raw=os.getenv('ROUND_TRIP_COST_PIPS','').strip()
    if not raw: return None
    n=float(raw)
    if not math.isfinite(n) or n<0: raise ValueError('Invalid ROUND_TRIP_COST_PIPS')
    return n

def new_trade(sig, plan, at, costs=None):
    return {'strategy_version':VERSION,'signal_id':sig['id'],'timestamp':stamp(at).isoformat(),
        'direction':sig['direction'],'opportunity':sig.get('opportunity'),'score':sig.get('score'),
        'session':sig.get('session'),'planned_entry':plan['entry'],'entry':None,'sl':plan['sl'],
        'tp1':plan['tp1'],'tp2':plan['tp2'],'tp3':plan['tp3'],'result':'PENDING','bars_held':0,
        'cost_pips':costs,'cost_status':'GROSS_ONLY_UNCONFIGURED' if costs is None else 'CONFIGURED',
        'tp1_hit':False,'tp3_hit':False,'result_pips':None,'result_r':None,'closed_at':None}

def advance(trade, df):
    if trade['result'] not in ('PENDING','OPEN'): return
    start=stamp(trade['timestamp']); last=stamp(trade['last_bar']) if trade.get('last_bar') else None
    rows=df[df.datetime>=start] if last is None else df[df.datetime>last]
    sign=1 if trade['direction']=='BUY' else -1
    for r in rows.itertuples(index=False):
        expected=last+pd.Timedelta(minutes=5) if last is not None else start.ceil('5min')
        if r.datetime>expected:
            trade.update(result='DATA_GAP',exit_reason='MISSING_BARS',closed_at=r.datetime.isoformat()); return
        if trade['result']=='PENDING':
            entry=float(r.open)
            if sign*(entry-trade['sl'])<=0 or sign*(trade['tp2']-entry)<=0:
                trade.update(result='CANCELLED',exit_reason='NEXT_OPEN_OUTSIDE_PLAN',closed_at=r.datetime.isoformat()); return
            trade.update(result='OPEN',entry=entry,filled_at=r.datetime.isoformat())
        trade['bars_held']+=1; trade['last_bar']=r.datetime.isoformat(); last=r.datetime
        slhit=r.low<=trade['sl'] if sign==1 else r.high>=trade['sl']
        tphit=r.high>=trade['tp2'] if sign==1 else r.low<=trade['tp2']
        # SL first when both touched: no invented intrabar ordering.
        if not slhit:
            trade['tp1_hit'] |= bool(r.high>=trade['tp1'] if sign==1 else r.low<=trade['tp1'])
            trade['tp3_hit'] |= bool(r.high>=trade['tp3'] if sign==1 else r.low<=trade['tp3'])
        exitp=None; reason=None
        if slhit:
            exitp=min(trade['sl'],float(r.open)) if sign==1 else max(trade['sl'],float(r.open))
            reason='SL_AMBIGUOUS' if tphit else 'SL'
        elif tphit: exitp=trade['tp2']; reason='TP2'
        elif trade['bars_held']>=72: exitp=float(r.close); reason='TIME_EXIT'
        if exitp is not None:
            net=sign*(exitp-trade['entry'])/.10-(trade['cost_pips'] or 0)
            risk=abs(trade['entry']-trade['sl'])/.10
            trade.update(result='WIN' if net>0 else 'LOSS' if net<0 else 'BE',result_pips=round(net,3),result_r=round(net/risk,4),exit_price=exitp,exit_reason=reason,closed_at=(r.datetime+pd.Timedelta(minutes=5)).isoformat()); return

def stats(trades):
    closed=[t for t in trades if t.get('result') in ('WIN','LOSS','BE')]
    rs=[t['result_r'] for t in closed]; wins=sum(t['result']=='WIN' for t in closed)
    equity=peak=dd=0.; streak=max_streak=0
    for t in sorted(closed,key=lambda x:x['closed_at']):
        equity+=t['result_r']; peak=max(peak,equity); dd=max(dd,peak-equity)
        streak=streak+1 if t['result']=='LOSS' else 0; max_streak=max(max_streak,streak)
    loss=-sum(r for r in rs if r<0); avg=sum(rs)/len(rs) if rs else None
    return {'status':'PAPER SIMULATION','trades':len(closed),'wins':wins,'losses':sum(t['result']=='LOSS' for t in closed),'win_rate':round(wins/len(closed)*100,2) if closed else None,'average_r':avg,'expectancy_r':avg,'profit_factor':sum(r for r in rs if r>0)/loss if loss else None,'max_drawdown':round(dd,4),'max_consecutive_losses':max_streak,'total_pips':round(sum(t['result_pips'] for t in closed),3),'open_trades':sum(t['result'] in ('OPEN','PENDING') for t in trades),'unresolved':sum(t['result']=='DATA_GAP' for t in trades),'cancelled':sum(t['result']=='CANCELLED' for t in trades),'cost_status':'NET_CONFIGURED' if closed and all(t.get('cost_pips') is not None for t in closed) else 'GROSS_OR_UNCONFIGURED'}

def sync(dashboard, frame, sent=None):
    import bosque_engine as e
    directory=e.ENGINE_DIR; at=dashboard['engine']['timestamp']; df=bars(frame)
    path=directory/'trade_journal.json'; journal=load(path,{'trades':[]})
    journal.setdefault('trades',[]); current=[t for t in journal['trades'] if t.get('strategy_version')==VERSION]
    for t in current: advance(t,df)
    sig=dashboard['signal']; plan=dashboard['plan']
    if sig.get('active') and not any(t.get('signal_id')==sig['id'] for t in current):
        t=new_trade({**sig,'session':dashboard['session']},plan,at,cost()); t['telegram_status']='SENT' if sent is True else 'FAILED' if sent is False else 'NOT_SENT_DUPLICATE';journal['trades'].append(t);current.append(t)
    journal.update(version=VERSION,last_updated=at); save(path,journal)
    summary=stats(current); forward={**summary,'version':VERSION,'last_updated':at,'execution':'Next available M5 open after signal observed; TP2 exit, SL-first ambiguity, 72 bars maximum; paper only.'}
    save(directory/'forward_test.json',forward)
    dashboard['journal']={**summary,'status':'ACTIVE','file':'engine/trade_journal.json','recorded_signals':len(current)}
    dashboard['journal_recent']=current[-30:][::-1]; dashboard['forward_test']=forward; dashboard['expectancy']=summary
    bt=load(directory/'backtest_results.json')
    dashboard['backtest']=bt if bt.get('version')==VERSION else {'status':'AWAITING_V3_REPLAY','version':VERSION,'previous_version':bt.get('version')}
    dashboard['sop']['session_filter']='PREFERRED' if dashboard['filters']['session'] else 'NON-PREFERRED'
    dashboard['sop']['fresh_zone']='NOT TRACKED'
    dashboard['filters']['session_blocking']=False
    dashboard['services']={'journal':{'status':'ACTIVE'},'telegram':{'status':'SENT' if sent is True else 'FAILED' if sent is False else 'NOT_ATTEMPTED','checked_at':at,'delivery_tested':sent is True}}
    save(directory/'telegram_health.json',dashboard['services']['telegram'])
    # Persist observed news state; not a fabricated historical calendar.
    news=dashboard.get('news',{}); save(directory/'news_cache.json',{'observed_at':at,'news':news})
    archive=directory/'news_archive'/('v3-'+stamp(at).strftime('%Y-%m-%d')+'.json')
    observations=load(archive,{'version':VERSION,'observations':[]})
    if not observations['observations'] or observations['observations'][-1]['news']!=news:
        observations['observations'].append({'observed_at':at,'news':news});save(archive,observations)
    fields=['signal_id','timestamp','direction','opportunity','score','entry','planned_entry','sl','tp1','tp2','tp3','result','result_pips','result_r','closed_at','exit_reason','cost_status','telegram_status']
    target=e.REPO_DIR/'paper_journal.csv';tmp=target.with_suffix('.tmp')
    with tmp.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(journal['trades'])
    tmp.replace(target)
    return dashboard
