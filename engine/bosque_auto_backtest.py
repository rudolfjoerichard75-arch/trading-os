"""Download bounded real M5 history and replay the shared V5.2 engine.

The default scheduled run remains a small recent-data smoke run. A longer
research window is enabled explicitly with HISTORY_START_DATE and is fetched
in provider-sized chunks. No candles or news events are invented locally.
"""
import os, json, time
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlencode
import pandas as pd
import bosque_engine as e
import bosque_backtest as b

CHUNK_SIZE = 5000


def _env_int(name, default, minimum=1, maximum=1000):
    raw=os.getenv(name,'').strip()
    if not raw:
        return default
    try:
        value=int(raw)
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer') from exc
    if value<minimum or value>maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


def _timestamp_env(name):
    raw=os.getenv(name,'').strip()
    if not raw:
        return None
    value=pd.Timestamp(raw)
    if value.tzinfo is None:
        raise ValueError(f'{name} must include a timezone, e.g. 2025-01-01T00:00:00Z')
    return value.tz_convert('UTC')


def _provider_date(value):
    return value.strftime('%Y-%m-%d %H:%M:%S')


def _download_chunk(key, end=None, start=None, outputsize=CHUNK_SIZE):
    params={'symbol':e.SYMBOL,'interval':'5min','outputsize':outputsize,'timezone':'UTC','apikey':key}
    if end is not None: params['end_date']=_provider_date(end)
    if start is not None: params['start_date']=_provider_date(start)
    payload=e.http_json('https://api.twelvedata.com/time_series?'+urlencode(params))
    if not isinstance(payload,dict) or not payload.get('values'):
        raise RuntimeError('Historical provider returned no data; check plan/quota')
    return payload['values']


def download_history(return_meta=False):
    """Return validated closed M5 bars plus fetch metadata.

    With no HISTORY_START_DATE this makes one request, matching the regular
    weekday job. With a start date it walks backwards by the earliest returned
    candle until the requested range is covered or the chunk limit is reached.
    """
    key=os.getenv('TWELVEDATA_API_KEY')
    if not key: raise RuntimeError('TWELVEDATA_API_KEY missing')
    requested_start=_timestamp_env('HISTORY_START_DATE')
    requested_end=_timestamp_env('HISTORY_END_DATE') or e.now_utc()
    if requested_start is not None and requested_start>=requested_end:
        raise ValueError('HISTORY_START_DATE must be earlier than HISTORY_END_DATE')
    chunks=_env_int('HISTORY_MAX_CHUNKS',1 if requested_start is None else 20,1,1000)
    outputsize=_env_int('HISTORY_CHUNK_SIZE',CHUNK_SIZE,100,CHUNK_SIZE)

    raw=[]; cursor_end=requested_end; requests=0; reached_start=requested_start is None
    while requests<chunks and not reached_start:
        if requests: time.sleep(9)  # bound API burst rate; no automatic paid upgrades
        values=_download_chunk(key,end=cursor_end,outputsize=outputsize)
        raw.extend(values); requests+=1
        frame=e.validate_bars(pd.DataFrame(values))
        earliest=frame.datetime.min()
        if requested_start is not None and earliest<=requested_start:
            reached_start=True
            break
        next_end=earliest-pd.Timedelta(minutes=5)
        if next_end>=cursor_end:
            raise RuntimeError('Historical provider did not move the pagination cursor')
        cursor_end=next_end

    if not raw:
        raw=_download_chunk(key,end=requested_end,outputsize=outputsize)
        requests=1
    # Some providers treat end_date as inclusive. De-duplicate only after
    # parsing the provider timestamps, then run the strict OHLC validator.
    combined=pd.DataFrame(raw)
    combined['datetime']=pd.to_datetime(combined['datetime'],utc=True,errors='raise')
    duplicates=combined[combined.datetime.duplicated(keep=False)]
    if not duplicates.empty and (duplicates.groupby('datetime')[['open','high','low','close']].nunique()>1).any().any():
        raise RuntimeError('Conflicting historical candles across pages')
    combined=combined.drop_duplicates('datetime',keep='first').reset_index(drop=True)
    bars=e.validate_bars(combined)
    bars=e.closed_bars(bars,e.now_utc())
    if requested_start is not None:
        bars=bars[bars.datetime>=requested_start].reset_index(drop=True)
        if not reached_start:
            raise RuntimeError(f'Historical range not fully downloaded; increase HISTORY_MAX_CHUNKS (received {len(bars)} bars)')
    bars=bars[bars.datetime<requested_end].reset_index(drop=True)
    if len(bars)<800: raise RuntimeError('Historical response too short for warmup and holdout')
    metadata={
        'source':'Twelve Data XAU/USD M5','requests':requests,
        'requested_bars_per_request':outputsize,
        'requested_start':requested_start.isoformat() if requested_start is not None else None,
        'requested_end':requested_end.isoformat(),
        'coverage_complete':bool(requested_start is None or reached_start),
    }
    return (bars,metadata) if return_meta else bars


def main():
    try:
        bars,download_meta=download_history(return_meta=True)
        directory=e.REPO_DIR/'backtest_data'; directory.mkdir(exist_ok=True)
        path=directory/'XAUUSD_M5_auto.csv'; bars.to_csv(path,index=False)
        # Fixed 80/20 chronological split, no tuning or candidate search.
        split=bars.iloc[int(len(bars)*.8)].datetime.isoformat()
        raw=os.getenv('ROUND_TRIP_COST_PIPS','').strip()
        cost=float(raw) if raw else None
        if cost is not None and (not e.math.isfinite(cost) or cost<0): raise ValueError('Invalid ROUND_TRIP_COST_PIPS')
        news_path=os.getenv('HISTORICAL_NEWS_PATH','').strip() or None
        if news_path and not Path(news_path).exists(): raise RuntimeError(f'HISTORICAL_NEWS_PATH not found: {news_path}')
        folds=_env_int('WALK_FORWARD_FOLDS',4,2,12) if os.getenv('WALK_FORWARD_FOLDS','').strip() else 0
        args=SimpleNamespace(csv=[str(path)],timezone='UTC',spread_pips=cost or 0.,slippage_pips=0.,commission_pips=0.,news=news_path,split=split,gross_only=cost is None,walk_forward_folds=folds)
        out=b.run(args)
        out['automation']={**download_meta,'received_closed_bars':len(bars),'split_rule':'FIXED_CHRONOLOGICAL_80_20','walk_forward_folds':folds,'cost_input':'ROUND_TRIP_COST_PIPS as aggregate' if cost is not None else 'UNCONFIGURED_GROSS_ONLY','historical_news':'HISTORICAL_CALENDAR' if news_path else 'UNAVAILABLE — current weekly feed is not a historical calendar'}
        e.save_json(e.BACKTEST_FILE,out)
    except Exception as exc:
        # Avoid leaking request URLs/API keys in workflow logs.
        status={'status':'FAILED','reason':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__,'last_updated':e.now_utc().isoformat()}
        e.save_json(e.BACKTEST_FILE,status); print(json.dumps(status)); raise SystemExit(1)

if __name__=='__main__': main()
