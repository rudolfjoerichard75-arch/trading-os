"""Bounded historical data download for V3 replay. Does not send signals."""
import os, time
from types import SimpleNamespace
import pandas as pd
import requests
import bosque_engine as e
import bosque_tracking as t
import bosque_backtest as b

def download_history():
    key=os.getenv('TWELVEDATA_API_KEY','')
    if not key: raise ValueError('TWELVEDATA_API_KEY missing')
    start=t.stamp(os.environ['HISTORY_START_DATE']) if os.getenv('HISTORY_START_DATE','').strip() else None
    end=t.stamp(os.environ['HISTORY_END_DATE']) if os.getenv('HISTORY_END_DATE','').strip() else pd.Timestamp.now(tz='UTC')
    limit=int(os.getenv('HISTORY_MAX_CHUNKS','') or '20') if start is not None else 1
    if not 1<=limit<=100: raise ValueError('Chunk limit must be 1..100')
    if start is not None and start>=end: raise ValueError('History start must precede end')
    frames=[];cursor=end; complete=start is None
    for n in range(limit):
        if n: time.sleep(9)
        r=requests.get(e.TWELVEDATA_URL,params={'symbol':e.SYMBOL,'interval':'5min','outputsize':5000,'timezone':'UTC','end_date':cursor.strftime('%Y-%m-%d %H:%M:%S'),'apikey':key},timeout=30)
        if r.status_code!=200: raise RuntimeError('Historical provider HTTP '+str(r.status_code))
        raw=r.json()
        if not isinstance(raw,dict) or not raw.get('values'): raise RuntimeError('Historical provider returned no values; check quota/plan')
        frame=t.bars(pd.DataFrame(raw['values']));frames.append(frame)
        earliest=frame.datetime.min()
        if start is None or earliest<=start: complete=True;break
        next_cursor=earliest-pd.Timedelta(minutes=5)
        if next_cursor>=cursor: raise ValueError('Pagination did not advance')
        cursor=next_cursor
    if not complete: raise ValueError('Requested history incomplete; increase chunk limit')
    df=pd.concat(frames,ignore_index=True)
    duplicates=df[df.datetime.duplicated(keep=False)]
    if not duplicates.empty and (duplicates.groupby('datetime')[['open','high','low','close']].nunique()>1).any().any(): raise ValueError('Conflicting duplicate OHLC')
    df=t.bars(df.drop_duplicates('datetime'))
    cutoff=min(end,pd.Timestamp.now(tz='UTC'))
    df=df[df.datetime+pd.Timedelta(minutes=5)<=cutoff]
    if start is not None: df=df[df.datetime>=start]
    if len(df)<800: raise ValueError('Too few historical bars')
    return df.reset_index(drop=True),len(frames)

def main():
    if e.ENTRY_MODE == "INTRABAR":
        b.intrabar_unavailable()
        return
    try:
        df,requests_count=download_history();directory=e.REPO_DIR/'backtest_data';directory.mkdir(exist_ok=True)
        path=directory/'XAUUSD_M5_auto.csv';df.to_csv(path,index=False)
        cost=t.cost();news=os.getenv('HISTORICAL_NEWS_PATH','').strip() or None
        news_fetch={'status':'SUPPLIED_FILE' if news else 'NOT_ATTEMPTED'}
        if news is None:
            from bosque_news import historical_calendar
            try:
                calendar=historical_calendar(df.iloc[0].datetime,
                    df.iloc[-1].datetime+pd.Timedelta(minutes=5),requests.get,e.FINANCECALENDAR_URL)
                news_path=directory/'news_auto.json';t.save(news_path,calendar);news=str(news_path)
                news_fetch={'status':'PROVIDER_SCHEDULE','events':calendar['events_count'],
                            'requests':calendar['requests']}
            except Exception as exc:
                news_fetch={'status':'UNVERIFIED','error_type':type(exc).__name__,
                            'reason':'Provider historical coverage could not be verified; replay explicitly excludes news.'}
        args=SimpleNamespace(csv=[str(path)],timezone='UTC',spread_pips=cost or 0,slippage_pips=0,commission_pips=0,split=df.iloc[int(len(df)*.8)].datetime.isoformat(),news=news,gross_only=cost is None)
        out=b.run(args);out['automation']={'requests':requests_count,'split_rule':'CHRONOLOGICAL_80_20','history_bars':len(df),'news_fetch':news_fetch};t.save(e.ENGINE_DIR/'backtest_results.json',out)
    except Exception as exc:
        t.save(e.ENGINE_DIR/'backtest_results.json',{'status':'FAILED','version':t.VERSION,'reason':type(exc).__name__,'last_updated':pd.Timestamp.now(tz='UTC').isoformat()});print('Research failed: '+type(exc).__name__);raise SystemExit(1) from None
if __name__=='__main__': main()
