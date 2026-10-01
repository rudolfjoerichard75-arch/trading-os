"""Explicit historical replay using the V5.2 live strategy and paper execution model."""
import argparse, hashlib, json
from pathlib import Path
import pandas as pd
import bosque_engine as e


def replay(df, cost, calendar=None, start=None, end=None):
    trades=[]; eligible=0
    for i,row in enumerate(df.itertuples(index=False)):
        # Resolve existing positions before deciding at this bar's close.
        for t in trades[-1:]: e.process_bar(t,row)
        at=row.datetime+pd.Timedelta(minutes=5)
        if (start is not None and at<start) or (end is not None and at>=end): continue
        if not e.entry_allowed(trades,at): continue
        if calendar is not None and not e.news_at(calendar,at)['ok']: continue
        ev=e.evaluate(df.iloc[max(0,i+1-e.OUTPUT_SIZE):i+1])
        if ev['active']:
            eligible+=1; trades.append(e.new_trade(ev,cost))
    return {'stats':e.stats(trades),'trades':trades,'eligible_signals':eligible}


def run(args):
    frames=[]; hashes={}
    for name in args.csv:
        path=Path(name); hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        frame=pd.read_csv(path)
        # Naive CSV timestamps must be explicitly localized; never guess broker timezone.
        parsed=pd.to_datetime(frame['datetime'])
        if parsed.dt.tz is None:
            if not args.timezone: raise ValueError('Naive timestamps require --timezone, e.g. UTC')
            parsed=parsed.dt.tz_localize(args.timezone,ambiguous='raise',nonexistent='raise')
        frame['datetime']=parsed.dt.tz_convert('UTC'); frames.append(frame)
    df=e.validate_bars(pd.concat(frames,ignore_index=True))
    if len(df)<400: raise ValueError('At least 400 M5 candles required')
    cost=None if getattr(args,'gross_only',False) else args.spread_pips+2*args.slippage_pips+args.commission_pips
    if any(not e.math.isfinite(x) or x<0 for x in [args.spread_pips,args.slippage_pips,args.commission_pips]): raise ValueError('Costs must be finite and nonnegative')
    calendar=None
    if args.news:
        calendar=e.load_json(args.news)
        calendar['events']=e.normalize_events(calendar['events'])
        if e.stamp(calendar['coverage_start'])>df.iloc[0].datetime or e.stamp(calendar['coverage_end'])<=df.iloc[-1].datetime+pd.Timedelta(minutes=5): raise ValueError('Historical calendar must cover full dataset')
    split=e.stamp(args.split) if args.split else None
    if split is not None and not df.iloc[0].datetime<split<df.iloc[-1].datetime: raise ValueError('Split must be inside dataset')
    if split is None:
        results={'full_sample':replay(df,cost,calendar)}
    else:
        # Independent runs; past bars warm up OOS but no development position crosses the split.
        results={'development':replay(df[df.datetime+pd.Timedelta(minutes=5)<split],cost,calendar),
                 'out_of_sample':replay(df,cost,calendar,start=split)}
    out={'status':'INCOMPLETE_DATA' if any(v['stats']['unresolved'] for v in results.values()) else 'COMPLETED','version':e.VERSION,'symbol':e.SYMBOL,'timeframe':'M5',
         'bars_used':len(df),'period_start':df.iloc[0].datetime.isoformat(),'period_end':df.iloc[-1].datetime.isoformat(),
         'source_sha256':hashes,'cost_model':{'status':'GROSS_ONLY_UNCONFIGURED' if cost is None else 'CONFIGURED','spread_pips':args.spread_pips,'slippage_per_side_pips':args.slippage_pips,'commission_round_trip_pips':args.commission_pips},
         'news_mode':'HISTORICAL_CALENDAR' if calendar else 'WITHOUT_NEWS_NOT_LIVE_EQUIVALENT',
         'development_period':'Before '+str(split) if split is not None else 'Full sample; no holdout',
         'out_of_sample':'From '+str(split) if split is not None else 'NOT SEPARATED',
         'forward':'Separate live paper journal; not historical proof',
         'results':results,'last_updated':e.now_utc().isoformat(),
         'note':'No optimization. Data gaps unresolved and block further entries. OPEN/PENDING are excluded from winrate. Costs charged once on exit. OHLC ambiguity uses SL first.'}
    e.save_json(e.BACKTEST_FILE,out)
    for name,result in results.items():
        e.save_json(e.DATA_DIR/('backtest_'+name+'_trades.json'),result['trades'])
    # The live engine publishes this summary on its next scan.
    print(json.dumps(e.clean({k:v['stats'] for k,v in results.items()}),indent=2))
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv',nargs='+',required=True)
    p.add_argument('--timezone',help='Timezone for naive CSV dates; timestamps are candle OPEN times')
    p.add_argument('--spread-pips',type=float,required=True)
    p.add_argument('--slippage-pips',type=float,required=True,help='Per side')
    p.add_argument('--commission-pips',type=float,default=0,help='Round trip equivalent in project pips')
    p.add_argument('--split',help='Predeclared holdout boundary, e.g. 2025-01-01T00:00:00Z')
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--news',help='JSON envelope: coverage_start, coverage_end, events (offset-aware dates)')
    mode.add_argument('--without-news',action='store_true',help='Explicitly run a research baseline without historical news')
    args=p.parse_args()
    try: run(args)
    except Exception as exc:
        e.save_json(e.BACKTEST_FILE,{'status':'FAILED','reason':str(exc),'last_updated':e.now_utc().isoformat()})
        raise

if __name__=='__main__': main()
