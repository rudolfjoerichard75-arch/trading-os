"""One bounded diagnostic comparing old omitted-timezone input with explicit UTC."""
import pandas as pd


def infer_clock_offset(result, frame):
    """Infer only from several unique exact-price matches, not a guessed zone."""
    import bosque_tracking as t
    if result.get('status')!='COMPLETED' or result.get('clock_offset_evidence'):
        return result
    current=t.bars(frame);matches=[]
    for sample in result.get('signal_open_comparisons',[]):
        found=current[(current.open-float(sample['legacy_open'])).abs()<1e-8]
        if len(found)!=1: continue
        when=found.iloc[0].datetime
        minutes=(t.stamp(sample['bar'])-when).total_seconds()/60
        matches.append({'signal_id':sample['signal_id'],'legacy_label':sample['bar'],
                        'matching_utc_bar':when.isoformat(),'offset_minutes':minutes})
    offsets={x['offset_minutes'] for x in matches}
    if len(matches)>=3 and len(offsets)==1 and abs(matches[0]['offset_minutes'])<1440:
        result['legacy_clock_offset_minutes']=matches[0]['offset_minutes']
        result['clock_offset_evidence']={'method':'UNIQUE_EXACT_OPEN_PRICE_MATCHES',
                                         'matches':matches,'count':len(matches)}
    return result


def audit_once(directory, frame, at):
    import bosque_engine as e
    import bosque_tracking as t
    path=directory/'market_data_audit.json'
    if path.exists():
        result=infer_clock_offset(t.load(path),frame)
        t.save(path,result)
        return result
    if frame.attrs.get('requested_timezone') != 'UTC': return None
    result={'checked_at':at,'status':'CHECKING','requests':1,
            'utc_meta':frame.attrs.get('provider_meta',{}),
            'note':'One-time comparison of the legacy request (timezone omitted) and explicit UTC; no change to trade results.'}
    try:
        response=e.requests.get(e.TWELVEDATA_URL,params={
            'symbol':e.SYMBOL,'interval':e.INTERVAL,'outputsize':e.OUTPUT_SIZE,
            'apikey':e.API_KEY,'format':'JSON'},timeout=30)
        response.raise_for_status();data=response.json()
        keys=['symbol','interval','currency_base','currency_quote','exchange','exchange_timezone','type']
        result['legacy_meta']={k:data.get('meta',{}).get(k) for k in keys}
        legacy=t.bars(pd.DataFrame(data['values']))
        legacy=e.remove_incomplete_candle(legacy)
        current=t.bars(frame)
        joined=current.merge(legacy,on='datetime',suffixes=('_utc','_legacy'))
        changed=pd.Series(False,index=joined.index)
        for col in ['open','high','low','close']:
            changed |= (joined[col+'_utc']-joined[col+'_legacy']).abs()>1e-6
        result.update(status='COMPLETED',overlapping_bars=len(joined),different_bars=int(changed.sum()),
                      utc_last_candle=current.iloc[-1].datetime.isoformat(),
                      legacy_last_candle=legacy.iloc[-1].datetime.isoformat())
        records=t.load(directory/'trade_journal.json',{'trades':[]})['trades']
        samples=[]
        for trade in records:
            at_bar=t.stamp(trade['timestamp']).ceil('5min')
            match=joined[joined.datetime==at_bar]
            if match.empty: continue
            r=match.iloc[0]
            samples.append({'signal_id':trade['signal_id'],'bar':at_bar.isoformat(),
                            'utc_open':float(r.open_utc),'legacy_open':float(r.open_legacy)})
        result['signal_open_comparisons']=samples
    except Exception as exc:
        # Never persist a request URL or API key from exception text.
        result.update(status='ERROR',reason=type(exc).__name__)
    infer_clock_offset(result,frame)
    t.save(path,result)
    return result
