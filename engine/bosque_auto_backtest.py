"""Bounded automatic research replay. Uses existing Twelve Data secret; no invented data."""
import os, json, hashlib
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlencode
import pandas as pd
import bosque_engine as e
import bosque_backtest as b


def download_history():
    key=os.getenv('TWELVEDATA_API_KEY')
    if not key: raise RuntimeError('TWELVEDATA_API_KEY missing')
    payload=e.http_json('https://api.twelvedata.com/time_series?'+urlencode({
        'symbol':e.SYMBOL,'interval':'5min','outputsize':5000,'timezone':'UTC','apikey':key}))
    if not isinstance(payload,dict) or not payload.get('values'):
        raise RuntimeError('Historical provider returned no data; check plan/quota')
    bars=e.closed_bars(e.validate_bars(pd.DataFrame(payload['values'])),e.now_utc())
    if len(bars)<800: raise RuntimeError('Historical response too short for warmup and holdout')
    return bars


def main():
    try:
        bars=download_history()
        directory=e.REPO_DIR/'backtest_data'; directory.mkdir(exist_ok=True)
        path=directory/'XAUUSD_M5_auto.csv'; bars.to_csv(path,index=False)
        # Fixed 80/20 chronological split, no tuning or candidate search.
        split=bars.iloc[int(len(bars)*.8)].datetime.isoformat()
        raw=os.getenv('ROUND_TRIP_COST_PIPS','').strip()
        cost=float(raw) if raw else None
        if cost is not None and (not e.math.isfinite(cost) or cost<0): raise ValueError('Invalid ROUND_TRIP_COST_PIPS')
        args=SimpleNamespace(csv=[str(path)],timezone='UTC',spread_pips=cost or 0.,slippage_pips=0.,commission_pips=0.,news=None,split=split,gross_only=cost is None)
        out=b.run(args)
        out['automation']={'source':'Twelve Data XAU/USD M5','requests':1,'requested_bars':5000,'received_closed_bars':len(bars),'split_rule':'FIXED_CHRONOLOGICAL_80_20','cost_input':'ROUND_TRIP_COST_PIPS as aggregate' if cost is not None else 'UNCONFIGURED_GROSS_ONLY','historical_news':'UNAVAILABLE — current weekly feed is not a historical calendar'}
        e.save_json(e.BACKTEST_FILE,out)
    except Exception as exc:
        # Avoid leaking request URLs/API keys in workflow logs.
        status={'status':'FAILED','reason':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__,'last_updated':e.now_utc().isoformat()}
        e.save_json(e.BACKTEST_FILE,status); print(json.dumps(status)); raise SystemExit(1)

if __name__=='__main__': main()
