"""V3 support only: paper accounting, persistence and replay adapter.
Entry decisions are delegated unchanged to bosque_engine's V3 functions.
"""
import csv, json, math, os, hashlib
from pathlib import Path
import pandas as pd

from bosque_execution import MODEL, ZONE_MODEL, ENTRY_WAIT_MINUTES, assess, annotate

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
    if df.datetime.isna().any(): raise ValueError('Missing candle timestamp')
    for col in ['open','high','low','close']: df[col]=pd.to_numeric(df[col],errors='raise')
    if df[['open','high','low','close']].isna().any().any(): raise ValueError('Missing OHLC')
    if not df[['open','high','low','close']].map(math.isfinite).all().all(): raise ValueError('Nonfinite OHLC')
    if ((df.high<df[['open','close','low']].max(axis=1)) | (df.low>df[['open','close','high']].min(axis=1))).any(): raise ValueError('Invalid OHLC')
    if df.datetime.duplicated().any(): raise ValueError('Duplicate candle')
    return df.sort_values('datetime').reset_index(drop=True)

def evaluate(df, at, news_ok=True):
    import bosque_engine as e
    if len(df)<100: return {'active':False, 'blocked_by':['warmup']}
    m15=e.aggregate(df,15); h1=e.aggregate(df,60)
    if len(m15)<50 or len(h1)<30: return {'active':False, 'blocked_by':['warmup']}
    hs=e.analyze_structure(h1); setup=e.detect_m15_setup(hs,m15,df)
    conf=e.m5_confirmation(df,setup['direction']); vol=e.volatility_analysis(df)
    session=e.get_session(stamp(at).to_pydatetime().astimezone(e.MY_TZ))
    score=e.calculate_score(hs,setup,conf,session,vol)
    plan=e.create_trade_plan(setup['direction'],df,score,vol)
    gates={'score':score>=e.MIN_SCORE,'setup':bool(setup['valid']),
           'm5_confirmation':bool(conf['confirmed']),'plan':bool(plan.get('valid',False)),
           'rr':plan.get('rr_tp2',0)>=e.MIN_RR,'news':bool(news_ok)}
    active=all(gates.values())
    return {'active':active,'score':score,'plan':plan,'opportunity':setup['opportunity'],'direction':setup['direction'],'session':session,'timestamp':stamp(at).isoformat(),'setup_valid':setup['valid'],
            'gates':gates,'blocked_by':[k for k,v in gates.items() if not v],
            'plan_reason':plan.get('reason')}

def cost():
    raw=os.getenv('ROUND_TRIP_COST_PIPS','').strip()
    if not raw: return None
    n=float(raw)
    if not math.isfinite(n) or n<0: raise ValueError('Invalid ROUND_TRIP_COST_PIPS')
    return n

def new_trade(sig, plan, at, costs=None):
    params=dict(sig.get('parameters') or configuration())
    return {'strategy_version':VERSION,'signal_id':sig['id'],'learning_id':sig.get('learning_id'),'timestamp':stamp(at).isoformat(),
        'direction':sig['direction'],'opportunity':sig.get('opportunity'),'score':sig.get('score'),
        'session':sig.get('session'),'planned_entry':plan['entry'],'entry':None,'sl':plan['sl'],
        'tp1':plan['tp1'],'tp2':plan['tp2'],'tp3':plan['tp3'],'result':'PENDING','bars_held':0,
        'cost_pips':costs,'cost_status':'GROSS_ONLY_UNCONFIGURED' if costs is None else 'CONFIGURED',
        'tp1_hit':False,'tp2_hit':False,'tp3_hit':False,'result_pips':None,'result_r':None,'closed_at':None,
        'execution_model':params.get('execution_model',ZONE_MODEL),
        'entry_deadline':(stamp(at)+pd.Timedelta(minutes=params.get('entry_wait_minutes',ENTRY_WAIT_MINUTES))).isoformat(),
        'signal_policy':sig.get('policy') or 'LEGACY_PRICE_ID','parameters':params}

def wait_for_zone(trade, r):
    """Conservative paper fills at full M5 opens only, within a frozen deadline.

    Returns True if this open can fill. Wick-only touches are observations,
    never invented fills. A pre-fill SL/TP2 touch retires the original plan.
    """
    from bosque_execution import entry_window
    deadline=stamp(trade['entry_deadline'])
    opened=r.datetime; closed=opened+pd.Timedelta(minutes=5)
    def cancel(reason, at):
        trade.update(result='NOT_ENTERED',exit_reason=reason,closed_at=at.isoformat())
        return False
    if opened >= deadline: return cancel('ENTRY_WINDOW_EXPIRED',deadline)
    zone=entry_window(trade)
    if zone['status']!='AVAILABLE': return cancel('ENTRY_ZONE_UNAVAILABLE',opened)
    entry=float(r.open); sign=1 if trade['direction']=='BUY' else -1
    trade.update(next_open=entry,execution_checked_at=opened.isoformat())
    annotate(trade)
    if sign*(entry-trade['sl'])<=0: return cancel('OPEN_BEYOND_SL',opened)
    if sign*(trade['tp2']-entry)<=0: return cancel('OPEN_BEYOND_TP2',opened)
    if trade['fill_audit']['status']=='PASS': return True
    # Close-time observations only; never inspect this candle before its close.
    trade['last_bar']=opened.isoformat()
    trade['entry_wait_bars']=trade.get('entry_wait_bars',0)+1
    if r.low <= zone['high'] and r.high >= zone['low']:
        trade['wick_zone_touch_observed']=True
    if closed >= deadline: return cancel('ENTRY_WINDOW_EXPIRED',deadline)
    if (r.low<=trade['sl'] if sign==1 else r.high>=trade['sl']):
        return cancel('SL_TOUCHED_BEFORE_FILL',closed)
    if (r.high>=trade['tp2'] if sign==1 else r.low<=trade['tp2']):
        return cancel('TP2_TOUCHED_BEFORE_FILL',closed)
    return False

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
            if trade.get('execution_model')==ZONE_MODEL:
                if not wait_for_zone(trade,r):
                    if trade['result']!='PENDING': return
                    last=r.datetime
                    continue
            entry=float(r.open)
            trade['next_open']=entry
            trade['execution_checked_at']=r.datetime.isoformat()
            annotate(trade)
            if trade.get('execution_model') == MODEL and trade['fill_audit']['status'] != 'PASS':
                trade.update(result='NOT_ENTERED',exit_reason=trade['fill_audit']['reasons'][0],closed_at=r.datetime.isoformat())
                return
            if sign*(entry-trade['sl'])<=0 or sign*(trade['tp2']-entry)<=0:
                reason='OPEN_BEYOND_SL' if sign*(entry-trade['sl'])<=0 else 'OPEN_BEYOND_TP2'
                trade.update(result='NOT_ENTERED',exit_reason=reason,closed_at=r.datetime.isoformat()); return
            trade.update(result='OPEN',entry=entry,filled_at=r.datetime.isoformat())
        trade['bars_held']+=1; trade['last_bar']=r.datetime.isoformat(); last=r.datetime
        slhit=r.low<=trade['sl'] if sign==1 else r.high>=trade['sl']
        tphit=r.high>=trade['tp2'] if sign==1 else r.low<=trade['tp2']
        # SL first when both touched: no invented intrabar ordering.
        if not slhit:
            for key in ('tp1','tp2','tp3'):
                if (r.high>=trade[key] if sign==1 else r.low<=trade[key]):
                    trade.setdefault('fill_targets',{}).setdefault(key,
                        {'bar':r.datetime.isoformat(),'order':'BEFORE_OBSERVED_SL'})
            trade['tp2_hit'] = trade.get('tp2_hit',False) or bool(tphit)
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
    closed=[t for t in trades if t.get('result') in ('WIN','LOSS','BE') and t.get('review_status')!='REVIEW_REQUIRED' and t.get('performance_group')!='HISTORICAL_RECONSTRUCTION']
    rs=[t['result_r'] for t in closed]; wins=sum(t['result']=='WIN' for t in closed)
    equity=peak=dd=0.; streak=max_streak=0
    for t in sorted(closed,key=lambda x:x['closed_at']):
        equity+=t['result_r']; peak=max(peak,equity); dd=max(dd,peak-equity)
        streak=streak+1 if t['result']=='LOSS' else 0; max_streak=max(max_streak,streak)
    loss=-sum(r for r in rs if r<0); avg=sum(rs)/len(rs) if rs else None
    return {'status':'PAPER SIMULATION','trades':len(closed),'wins':wins,'losses':sum(t['result']=='LOSS' for t in closed),'win_rate':round(wins/len(closed)*100,2) if closed else None,'average_r':avg,'expectancy_r':avg,'profit_factor':sum(r for r in rs if r>0)/loss if loss else None,'max_drawdown':round(dd,4),'max_consecutive_losses':max_streak,'total_pips':round(sum(t['result_pips'] for t in closed),3),'open_trades':sum(t['result'] in ('OPEN','PENDING') for t in trades),'unresolved':sum(t['result']=='DATA_GAP' for t in trades),'cancelled':sum(t['result'] in ('CANCELLED','NOT_ENTERED') and t.get('review_status')!='REVIEW_REQUIRED' for t in trades),'review_required':sum(t.get('review_status')=='REVIEW_REQUIRED' for t in trades),'reconstructed':sum(t.get('performance_group')=='HISTORICAL_RECONSTRUCTION' for t in trades),'cost_status':'NET_CONFIGURED' if closed and all(t.get('cost_pips') is not None for t in closed) else 'GROSS_OR_UNCONFIGURED'}

def add_diagnostics(dashboard):
    """Explanations only; never changes a gate, plan or entry decision."""
    import re
    import bosque_engine as e
    gates = dashboard.get('filters', {})
    plan = dashboard.get('plan', {})
    opp = dashboard.get('opportunity', {})
    direction = opp.get('direction') or dashboard.get('signal', {}).get('direction')
    limits = {'min_score': e.MIN_SCORE, 'min_risk_pips': e.MIN_RISK_PIPS,
              'max_risk_pips': e.MAX_RISK_PIPS, 'min_rr': e.MIN_RR}
    reasons = {}
    if gates.get('setup') is not True:
        reasons['setup'] = 'Tiada setup M15 V3 yang sah'
    if gates.get('score') is not True:
        value = opp.get('score', dashboard.get('signal', {}).get('score'))
        reasons['score'] = ('Skor belum tersedia' if value is None else
            f'Skor {value}/100 < minimum {e.MIN_SCORE}' if value < e.MIN_SCORE else
            'Score gate belum disahkan oleh engine')
    if gates.get('m5_confirmation') is not True:
        reasons['m5_confirmation'] = (f'BOS atau candle + momentum {direction} belum sah'
            if direction in ('BUY', 'SELL') else 'Menunggu arah setup M15')

    # V3 intentionally returns only a reason when risk is outside its range.
    # Read that existing result without recalculating or inventing a trade plan.
    risk_value = plan.get('risk_pips')
    plan_reason = str(plan.get('reason', ''))
    match = re.search(r'Risk ([0-9.]+) pips outside', plan_reason)
    if match:
        risk_value = float(match.group(1))
    if gates.get('risk') is not True:
        if risk_value is not None and risk_value > e.MAX_RISK_PIPS:
            reasons['risk'] = f'SL {risk_value:.1f} pips melebihi had {e.MAX_RISK_PIPS} pips'
        elif risk_value is not None and risk_value < e.MIN_RISK_PIPS:
            reasons['risk'] = f'SL {risk_value:.1f} pips bawah minimum {e.MIN_RISK_PIPS} pips'
        elif plan_reason:
            reasons['risk'] = plan_reason
        elif direction not in ('BUY', 'SELL'):
            reasons['risk'] = 'Belum dikira: tiada arah setup'
        elif dashboard.get('m5', {}).get('swing_low' if direction == 'BUY' else 'swing_high') is None:
            reasons['risk'] = 'Swing M5 untuk menentukan SL belum tersedia'
        else:
            reasons['risk'] = 'Trade plan belum sah'
    if gates.get('rr') is not True:
        rr = plan.get('rr_tp2')
        if plan_reason == 'TP2 RR below minimum':
            reasons['rr'] = f'RR TP2 di bawah minimum 1:{e.MIN_RR:g}'
        elif gates.get('risk') is not True:
            reasons['rr'] = 'Belum dikira: risk tidak lulus'
        elif isinstance(rr, (int, float)):
            reasons['rr'] = f'RR TP2 1:{rr:g} di bawah 1:{e.MIN_RR:g}'
        else:
            reasons['rr'] = 'RR TP2 belum tersedia'
    news = dashboard.get('news', {})
    if gates.get('news') is not True:
        reasons['news'] = (news.get('reason') or
            ('Data berita gagal disahkan; signal ditahan' if news.get('status') in ('ERROR', 'UNKNOWN') else
             'Berita USD high impact dalam tetingkap sekatan' if news.get('filter') == 'BLOCK' else
             'Status news belum tersedia'))
    if dashboard.get('data_health', {}).get('allowed') is False:
        reasons['data'] = dashboard['data_health'].get('reason', 'Data M5 belum disahkan')
    dashboard['gate_reasons'] = reasons
    dashboard['diagnostics'] = {
        'limits': limits,
        'risk_distance_pips': risk_value,
        'blocking_reasons': [reasons[k] for k in
            ['data', 'setup', 'score', 'm5_confirmation', 'risk', 'rr', 'news'] if k in reasons]
    }
    return dashboard

def configuration():
    import bosque_engine as e
    from bosque_signals import POLICY
    from bosque_execution import ENTRY_ZONE_POLICY, ENTRY_ZONE_MAX_PIPS
    return {'min_risk_pips':e.MIN_RISK_PIPS,'max_risk_pips':e.MAX_RISK_PIPS,
            'min_score':e.MIN_SCORE,'min_rr':e.MIN_RR,'signal_policy':POLICY,
            'execution_model':ZONE_MODEL,'entry_logic_version':e.ENTRY_LOGIC_VERSION,
            'entry_wait_minutes':ENTRY_WAIT_MINUTES,
            'range_max_efficiency':e.RANGE_MAX_EFFICIENCY,
            'range_max_slope_ratio':e.RANGE_MAX_SLOPE_RATIO,
            'range_max_atr_width':e.RANGE_MAX_ATR_WIDTH,
            'entry_zone_policy':ENTRY_ZONE_POLICY,'entry_zone_max_pips':ENTRY_ZONE_MAX_PIPS}

def version_performance(trades):
    current_config=configuration()
    groups={}
    for item in trades:
        params=item.get('parameters') or {}
        key=params.get('entry_logic_version','UNVERSIONED')+' / '+item.get('execution_model','UNSPECIFIED')
        groups.setdefault(key,[]).append(item)
    return {'current_version':stats([item for item in trades if item.get('parameters')==current_config
                                    and item.get('execution_model')==current_config['execution_model']]),
            'current_parameters':current_config,
            'version_groups':{key:stats(items) for key,items in groups.items()}}


def sync(dashboard, frame, sent=None):
    import bosque_engine as e
    dashboard['engine']['entry_logic_version'] = e.ENTRY_LOGIC_VERSION
    add_diagnostics(dashboard)
    from bosque_execution import entry_window
    dashboard["entry_window"] = entry_window(dashboard.get("plan", {}))
    directory=e.ENGINE_DIR; at=dashboard['engine']['timestamp']; df=bars(frame)
    from bosque_feed_audit import audit_once
    dashboard['market_data_audit']=audit_once(directory,frame,at)
    path=directory/'trade_journal.json'; journal=load(path,{'trades':[]})
    journal.setdefault('trades',[]); current=[t for t in journal['trades'] if t.get('strategy_version')==VERSION]
    from bosque_signal_audit import observe, audit_unfilled, review_legacy_execution, resolve_legacy_timezone, summary as signal_summary
    for t in current:
        advance(t,df)
        observe(t,df,reconstructed='signal_path' not in t)
        audit_unfilled(t,df)
        review_legacy_execution(t,df,at)
        resolve_legacy_timezone(t,df,dashboard.get('market_data_audit'),at)
        annotate(t)
    sig=dashboard['signal']; plan=dashboard['plan']
    state=load(directory/'state.json')
    acknowledged=state.get('delivered_alerts', [])
    if sig.get('active') and sent is True and not any(x['signal']['id']==sig['id'] for x in acknowledged):
        acknowledged.append({'signal':{**sig,'policy':sig.get('delivery',{}).get('policy')},
                             'plan':plan,'session':dashboard['session'],
                             'timestamp':sig.get('acknowledged_at') or at})
    for item in acknowledged:
        saved_sig=item['signal']
        if any(t.get('signal_id')==saved_sig['id'] for t in current): continue
        t=new_trade({**saved_sig,'session':item['session']},item['plan'],item['timestamp'],cost())
        t['telegram_status']='SENT';t['telegram_receipt']=item.get('receipt')
        t['source_candle']=item.get('source_candle')
        advance(t,df);observe(t,df);annotate(t)
        journal['trades'].append(t);current.append(t)
    # A bounded snapshot makes future fill/target audits reproducible, using the
    # same provider and UTC candles as the live engine (no extra API request).
    snapshot=directory/'market_snapshot.csv';tmp=snapshot.with_suffix('.tmp')
    df[['datetime','open','high','low','close']].to_csv(tmp,index=False);tmp.replace(snapshot)
    for item in current:
        item["entry_window"] = entry_window(item)
        item["signal_monitor"] = {"status": (item.get("signal_path") or {}).get("status", "UNAVAILABLE"),
                                  "basis": "PRICE_LEVEL_OBSERVATION", "paper_result": item.get("result"),
                                  "broker_position": "UNKNOWN"}
    journal.update(version=VERSION,last_updated=at); save(path,journal)
    from bosque_learning import safe_sync
    dashboard['learning'] = safe_sync(directory, current, dashboard, df)
    # Journal is durable before acknowledging the outbox as consumed.
    if state.get('delivered_alerts'):
        state['delivered_alerts']=[]
        save(directory/'state.json',state)
    summary=stats(current)
    groups={model:stats([x for x in current if (x.get('execution_model') or 'UNSPECIFIED')==model])
            for model in sorted({(x.get('execution_model') or 'UNSPECIFIED') for x in current})}
    summary['execution_groups']=groups
    summary.update(version_performance(current))
    summary['historical_fill_exceptions']=sum(x.get('fill_audit',{}).get('status')=='OUTSIDE_LIMITS' and not x.get('fill_audit',{}).get('enforced') for x in current)
    forward={**summary,'version':VERSION,'last_updated':at,'execution':'New paper trades wait up to 30 minutes after alert for a full M5 open inside the frozen zone with risk 20–80 pips and RR TP2 >= 2. Wick-only touches are not fills. Pre-fill SL/TP2 touches invalidate the plan. TP2 exit. Earlier models keep original rules.', 'signal_policy':'DIRECTION_SETUP_CYCLE_V1'}
    save(directory/'forward_test.json',forward)
    dashboard['journal']={**summary,'status':'ACTIVE','file':'engine/trade_journal.json','recorded_signals':len(current)}
    dashboard['journal_recent']=current[-30:][::-1]; dashboard['signal_audit']=signal_summary(current); dashboard['recorded_signal']=current[-1] if current else None; dashboard['forward_test']=forward; dashboard['expectancy']=summary
    bt=load(directory/'backtest_results.json')
    if bt.get('version')==VERSION and bt.get('status','').startswith('COMPLETED') and bt.get('parameters')!=configuration():
        bt={**bt,'status':'OUTDATED_SETTINGS','results':{},'reason':'Awaiting replay with current risk range and signal-cycle policy; previous results retained in backtest JSON.'}
    dashboard['backtest']=bt if bt.get('version')==VERSION else {'status':'AWAITING_V3_REPLAY','version':VERSION,'previous_version':bt.get('version')}
    dashboard['sop']['session_filter']='PREFERRED' if dashboard['filters']['session'] else 'NON-PREFERRED'
    dashboard['sop']['fresh_zone']='NOT TRACKED'
    dashboard['filters']['session_blocking']=False
    dashboard['services']={'journal':{'status':'ACTIVE'},'telegram':{'status':'SENT' if sent is True else 'FAILED' if sent is False else sig.get('delivery',{}).get('status','NOT_ATTEMPTED'),'checked_at':at,'delivery_tested':sent is True}}
    dashboard['services']['telegram']['uncertain_deliveries']=state.get('uncertain_deliveries',[])
    cycle=state.get('signal_cycle') or {}
    if cycle.get('sent_at'): dashboard['services']['telegram']['last_confirmed_at']=cycle['sent_at']
    save(directory/'telegram_health.json',dashboard['services']['telegram'])
    dashboard['services']['engine']=dashboard.get('data_health', {'status':'NOT_CHECKED'})
    save(directory/'engine_health.json',dashboard['services']['engine'])
    # Collect only; Telegram sends happen after the workflow persists a durable
    # reservation. Backtest/new_trade/advance never send notifications.
    from bosque_trade_alerts import collect, FILENAME
    dashboard['services']['trade_alerts']=collect(current,directory/FILENAME,at,
        dashboard.get('data_health',{}).get('allowed') is True)
    save(directory/'telegram_health.json', {**dashboard['services']['telegram'],
        'trade_alerts':dashboard['services']['trade_alerts']})
    # Persist observed news state; not a fabricated historical calendar.
    news=dashboard.get('news',{}); save(directory/'news_cache.json',{'observed_at':at,'news':news})
    archive=directory/'news_archive'/('v3-'+stamp(at).strftime('%Y-%m-%d')+'.json')
    observations=load(archive,{'version':VERSION,'observations':[]})
    if not observations['observations'] or observations['observations'][-1]['news']!=news:
        observations['observations'].append({'observed_at':at,'news':news});save(archive,observations)
    fields=['signal_id','timestamp','direction','opportunity','score','entry','planned_entry','sl','tp1','tp2','tp3','result','result_pips','result_r','closed_at','exit_reason','cost_status','telegram_status','next_open','execution_checked_at','signal_policy','signal_path','execution_audit','parameters','review_status','review_reason','historical_replay','original_execution','timezone_repair','performance_group','execution_model','fill_audit','entry_deadline','entry_wait_bars','wick_zone_touch_observed','filled_at']
    target=e.REPO_DIR/'paper_journal.csv';tmp=target.with_suffix('.tmp')
    with tmp.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for item in journal['trades']:
            record=dict(item)
            for key in ['signal_path','execution_audit','parameters','historical_replay','original_execution','timezone_repair','fill_audit']:
                if key in record: record[key]=json.dumps(record[key],ensure_ascii=False,allow_nan=False)
            writer.writerow(record)
    tmp.replace(target)
    return dashboard
