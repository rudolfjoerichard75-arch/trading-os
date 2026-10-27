"""Historical replay of unmodified V3 decisions; paper fills, not broker fills."""
import argparse, hashlib, json
from collections import Counter
from pathlib import Path
import pandas as pd
import bosque_engine as e
import bosque_tracking as t
from bosque_signals import select_alert, mark_delivered, POLICY
from bosque_signal_audit import observe

def news_allowed(calendar, at):
    if calendar is None: return True
    if not t.stamp(calendar['coverage_start'])<=at<t.stamp(calendar['coverage_end']): raise ValueError('Historical news coverage missing')
    return not any(abs((t.stamp(x['time'])-at).total_seconds())<=1800 for x in calendar['events'] if str(x.get('impact','')).lower()=='high' and x.get('currency')=='USD')

def replay(frame,cost=None,calendar=None,start=None,end=None,alert_delay_seconds=1,evaluation_cache=None):
    if not 0 <= alert_delay_seconds < 300: raise ValueError('Alert delay must be 0..299 seconds')
    df=t.bars(frame); trades=[]; state={}; count=0; candidates=0
    blocked=Counter(); evaluated=0; unhealthy=0
    for i in range(len(df)):
        row=df.iloc[i:i+1]
        for trade in trades:
            if trade['result'] in ('PENDING','OPEN'): t.advance(trade,row)
            observe(trade,row)
        at=df.iloc[i].datetime+pd.Timedelta(minutes=5)
        if end is not None and at>=end: continue
        if i<359: continue
        window=df.iloc[max(0,i+1-e.OUTPUT_SIZE):i+1]
        from bosque_health import market_health
        if not market_health(window,at)['allowed']:
            if start is None or at>=start: unhealthy+=1
            continue
        news_ok=news_allowed(calendar,at)
        key=(at.isoformat(),news_ok)
        # This run-local cache is shared only by prefixes of the same validated
        # dataset/configuration. OOS delivery state still replays every candle.
        if evaluation_cache is not None and key in evaluation_cache:
            ev=evaluation_cache[key]
        else:
            ev=t.evaluate(window,at,news_ok)
            if evaluation_cache is not None: evaluation_cache[key]=ev
        if start is None or at>=start:
            evaluated+=1
            blocked.update(ev.get('blocked_by',[]))
        eligible=ev.get('active',False)
        candidates+=int(eligible and (start is None or at>=start))
        delivery=select_alert(state,ev.get('setup_valid',eligible),ev.get('direction'),eligible,df.iloc[i].datetime)
        if not delivery['allowed']: continue
        # Reporting splits must not rearm a previously delivered setup.
        mark_delivered(state,at.isoformat())
        if start is not None and at<start: continue
        p=ev['plan'];count+=1
        alert_at=at+pd.Timedelta(seconds=alert_delay_seconds)
        params={**t.configuration(),'entry_logic_version':e.CLOSED_ENTRY_LOGIC_VERSION,'entry_mode':'CLOSED_CANDLE','signal_policy':POLICY}
        item=t.new_trade({**ev,'id':delivery['id'],'policy':POLICY,'parameters':params},p,alert_at,cost)
        item['decision_at']=at.isoformat()
        observe(item,df.iloc[0:0],reconstructed=True)
        item['signal_path']['source']='HISTORICAL_REPLAY'
        trades.append(item)
    return {'entry_mode':'CLOSED_CANDLE_RESEARCH_ONLY','stats':t.stats(trades),'trades':trades,'eligible_signals':count,
            'gate_audit':{'evaluated_scans':evaluated,'unhealthy_scans':unhealthy,
                          'blocked_counts':dict(blocked),'counting':'Non-exclusive: one scan may fail several gates'},
            'eligible_scan_candidates':candidates,'repeated_candidates_suppressed':candidates-count}

def intrabar_unavailable():
    out={"status":"AWAITING_INTRABAR_REPLAY","version":t.VERSION,"parameters":t.configuration(),
         "results":{},"reason":"Final M5 OHLC cannot reconstruct intrabar signals. Timestamped snapshots or ticks required.",
         "last_updated":pd.Timestamp.now(tz="UTC").isoformat()}
    t.save(e.ENGINE_DIR/"backtest_results.json",out)
    return out

def run(args):
    if e.ENTRY_MODE == "INTRABAR": return intrabar_unavailable()
    frames=[]; hashes={}
    for name in args.csv:
        path=Path(name);hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        frame=pd.read_csv(path);dt=pd.to_datetime(frame.datetime)
        if dt.dt.tz is None:
            if not args.timezone: raise ValueError('Naive timestamps need --timezone')
            dt=dt.dt.tz_localize(args.timezone)
        frame['datetime']=dt.dt.tz_convert('UTC');frames.append(frame)
    df=t.bars(pd.concat(frames,ignore_index=True))
    if len(df)<400: raise ValueError('Need at least 400 M5 candles')
    costs=[args.spread_pips,args.slippage_pips,args.commission_pips]
    if any(not e.math.isfinite(x) or x<0 for x in costs): raise ValueError('Invalid costs')
    cost=None if getattr(args,'gross_only',False) else costs[0]+2*costs[1]+costs[2]
    calendar=t.load(args.news) if args.news else None
    if calendar is not None:
        if not isinstance(calendar.get('events'),list): raise ValueError('Historical events missing')
        if t.stamp(calendar['coverage_start'])>df.iloc[0].datetime or t.stamp(calendar['coverage_end'])<df.iloc[-1].datetime+pd.Timedelta(minutes=5): raise ValueError('Historical news must cover dataset')
    delay=getattr(args,'alert_delay_seconds',1)
    evaluation_cache={}
    split=t.stamp(args.split) if args.split else None
    if split is not None and not df.iloc[0].datetime<split<df.iloc[-1].datetime: raise ValueError('Split outside dataset')
    results={'full_sample':replay(df,cost,calendar,alert_delay_seconds=delay)} if split is None else {'development':replay(df[df.datetime+pd.Timedelta(minutes=5)<split],cost,calendar,alert_delay_seconds=delay,evaluation_cache=evaluation_cache),'out_of_sample':replay(df,cost,calendar,start=split,alert_delay_seconds=delay,evaluation_cache=evaluation_cache)}
    delta=df.datetime.diff().dt.total_seconds()/60
    out={'status':'COMPLETED','version':t.VERSION,'symbol':e.SYMBOL,'parameters':t.configuration(),'signal_policy':POLICY,'bars_used':len(df),'period_start':df.iloc[0].datetime.isoformat(),'period_end':df.iloc[-1].datetime.isoformat(),'source_sha256':hashes,'results':results,'last_updated':pd.Timestamp.now(tz='UTC').isoformat(),'news_mode':'HISTORICAL_CALENDAR' if calendar else 'WITHOUT_NEWS_NOT_LIVE_EQUIVALENT','cost_model':{'status':'GROSS_ONLY_UNCONFIGURED' if cost is None else 'CONFIGURED','round_trip_pips':cost},'data_quality':{'gaps':int((delta>5).sum())},'note':'Current V3 entry rules with complete aggregated bars. Decisions at M5 close; wait up to 30 minutes for a valid full M5 open inside the frozen zone; TP2 exit; SL first on ambiguous bars; max 72 bars. One alert per directional setup cycle; two closed M5 bars without setup or a direction change rearm alerts. Target path tracked separately after TP2; not additional paper wins. Historical replay is not exact wall-clock live execution.'}
    if any(x['stats']['unresolved'] for x in results.values()): out['status']='COMPLETED_WITH_DATA_GAPS'
    out['execution_timing']={'alert_delay_seconds':delay,'assumption':'Decision at M5 close; simulated alert after configured delay; first full M5 open at/after alert. 1 second models exclusion of the in-progress alert candle, not measured broker latency.'}
    out['limitations']=[]
    if cost is None: out['limitations'].append('Broker spread/slippage/commission not configured; results are gross.')
    if calendar is None: out['limitations'].append('No historical news; not live-equivalent.')
    elif calendar.get('source'):
        out['news_mode']='HISTORICAL_PROVIDER_SCHEDULE_NOT_POINT_IN_TIME'
        out['news_source']={k:calendar.get(k) for k in ('source','source_url','fetched_at','coverage_start','coverage_end','events_count')}
        out['limitations'].append('Provider schedule fetched after the fact; historical revisions and missing provider events cannot be reconstructed.')
    out['note']=out['note'].replace('fill next M5 open','fill next full M5 open after alert delay with risk/RR validation')
    out['execution_groups']={k: {'model':t.ZONE_MODEL,'stats':v['stats']} for k,v in results.items()}
    t.save(e.ENGINE_DIR/'backtest_results.json',out)
    print(json.dumps({k:v['stats'] for k,v in results.items()},indent=2));return out

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--csv',nargs='+',required=True);p.add_argument('--timezone');p.add_argument('--spread-pips',type=float,required=True);p.add_argument('--slippage-pips',type=float,required=True);p.add_argument('--commission-pips',type=float,default=0);p.add_argument('--split');p.add_argument('--gross-only',action='store_true');p.add_argument('--alert-delay-seconds',type=float,default=1)
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--news');mode.add_argument('--without-news',action='store_true')
    args=p.parse_args()
    try: run(args)
    except Exception as exc:
        t.save(e.ENGINE_DIR/'backtest_results.json',{'status':'FAILED','version':t.VERSION,'reason':type(exc).__name__+': '+str(exc),'last_updated':pd.Timestamp.now(tz='UTC').isoformat()});raise
if __name__=='__main__': main()
