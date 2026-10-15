"""Historical replay of unmodified V3 decisions; paper fills, not broker fills."""
import argparse, hashlib, json
from pathlib import Path
import pandas as pd
import bosque_engine as e
import bosque_tracking as t

def news_allowed(calendar, at):
    if calendar is None: return True
    if not t.stamp(calendar['coverage_start'])<=at<t.stamp(calendar['coverage_end']): raise ValueError('Historical news coverage missing')
    return not any(abs((t.stamp(x['time'])-at).total_seconds())<=1800 for x in calendar['events'] if str(x.get('impact','')).lower()=='high' and x.get('currency')=='USD')

def replay(frame,cost=None,calendar=None,start=None,end=None):
    df=t.bars(frame); trades=[]; last_id=None; count=0
    for i in range(len(df)):
        row=df.iloc[i:i+1]
        for trade in trades:
            if trade['result'] in ('PENDING','OPEN'): t.advance(trade,row)
        at=df.iloc[i].datetime+pd.Timedelta(minutes=5)
        if (start is not None and at<start) or (end is not None and at>=end): continue
        if i<359: continue
        window=df.iloc[max(0,i+1-e.OUTPUT_SIZE):i+1]
        ev=t.evaluate(window,at,news_allowed(calendar,at))
        if not ev.get('active'): continue
        p=ev['plan'];sid=e.make_signal_id(ev['direction'],ev['opportunity'],p['entry'],p['sl'],p['tp2'])
        if sid == last_id: continue
        last_id=sid;count+=1
        trades.append(t.new_trade({**ev,'id':sid},p,at,cost))
    return {'stats':t.stats(trades),'trades':trades,'eligible_signals':count}

def run(args):
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
    split=t.stamp(args.split) if args.split else None
    if split is not None and not df.iloc[0].datetime<split<df.iloc[-1].datetime: raise ValueError('Split outside dataset')
    results={'full_sample':replay(df,cost,calendar)} if split is None else {'development':replay(df[df.datetime+pd.Timedelta(minutes=5)<split],cost,calendar),'out_of_sample':replay(df,cost,calendar,start=split)}
    delta=df.datetime.diff().dt.total_seconds()/60
    out={'status':'COMPLETED','version':t.VERSION,'symbol':e.SYMBOL,'bars_used':len(df),'period_start':df.iloc[0].datetime.isoformat(),'period_end':df.iloc[-1].datetime.isoformat(),'source_sha256':hashes,'results':results,'last_updated':pd.Timestamp.now(tz='UTC').isoformat(),'news_mode':'HISTORICAL_CALENDAR' if calendar else 'WITHOUT_NEWS_NOT_LIVE_EQUIVALENT','cost_model':{'status':'GROSS_ONLY_UNCONFIGURED' if cost is None else 'CONFIGURED','round_trip_pips':cost},'data_quality':{'gaps':int((delta>5).sum())},'note':'Unchanged V3 entry rules including partial aggregated bars. Decisions at M5 close; fill next M5 open; TP2 exit; SL first on ambiguous bars; max 72 bars. Overlapping signals allowed as in V3. Historical replay is not exact wall-clock live execution.'}
    if any(x['stats']['unresolved'] for x in results.values()): out['status']='COMPLETED_WITH_DATA_GAPS'
    t.save(e.ENGINE_DIR/'backtest_results.json',out)
    print(json.dumps({k:v['stats'] for k,v in results.items()},indent=2));return out

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--csv',nargs='+',required=True);p.add_argument('--timezone');p.add_argument('--spread-pips',type=float,required=True);p.add_argument('--slippage-pips',type=float,required=True);p.add_argument('--commission-pips',type=float,default=0);p.add_argument('--split');p.add_argument('--gross-only',action='store_true')
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--news');mode.add_argument('--without-news',action='store_true')
    args=p.parse_args()
    try: run(args)
    except Exception as exc:
        t.save(e.ENGINE_DIR/'backtest_results.json',{'status':'FAILED','version':t.VERSION,'reason':type(exc).__name__+': '+str(exc),'last_updated':pd.Timestamp.now(tz='UTC').isoformat()});raise
if __name__=='__main__': main()
