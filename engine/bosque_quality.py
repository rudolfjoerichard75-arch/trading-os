"""V5.3 candidate: causal trend/zone/retest gates. Profitability is unvalidated."""
import pandas as pd


def range_quality(df):
    x=df.tail(20)
    width=float(x.high.max()-x.low.min())
    path=float(x.close.diff().abs().sum())
    efficiency=abs(float(x.close.iloc[-1]-x.close.iloc[0]))/path if path else 0.
    recent=float((x.high-x.low).tail(5).mean())
    prior=float((x.high-x.low).head(15).mean())
    compression=recent/prior if prior else 1.
    return {'is_range':bool(efficiency<.3 and compression<=1 and width>0),
            'efficiency':efficiency,'compression':compression}


def fresh_retest(df, direction):
    """Three-bar FVG; only first subsequent touch, within 16 closed M15 bars.

    Formation candle is never a retest. A close through the far edge or a
    previous touching bar invalidates the candidate. Current close must reject
    the near edge. This defines FVG only, not an order-block heuristic.
    """
    for i in range(len(df)-2,max(1,len(df)-18),-1):
        a,c=df.iloc[i-2],df.iloc[i]
        lo,hi=(float(a.high),float(c.low)) if direction=='BUY' else (float(c.high),float(a.low))
        if hi<=lo: continue
        previous=df.iloc[i+1:-1]
        if ((previous.low<=hi)&(previous.high>=lo)).any(): continue
        if ((previous.close<lo) if direction=='BUY' else (previous.close>hi)).any(): continue
        last=df.iloc[-1]
        touch=float(last.low)<=hi and float(last.high)>=lo
        rejection=float(last.close)>hi if direction=='BUY' else float(last.close)<lo
        if touch and rejection:
            return {'valid':True,'type':'FVG_FIRST_RETEST','low':lo,'high':hi,
                    'formed_at':c.datetime.isoformat(),'age_bars':len(df)-1-i,'previous_touches':0}
    return {'valid':False,'type':'FVG_FIRST_RETEST'}


def net_rr(plan, cost, pip_size=.10):
    if not plan.get('valid') or cost is None: return None
    risk=abs(plan['entry']-plan['sl'])/pip_size
    reward=abs(plan['tp2']-plan['entry'])/pip_size
    return (reward-cost)/(risk+cost) if risk+cost>0 else None


def evaluate_quality(df, e):
    w=df.tail(e.OUTPUT_SIZE).reset_index(drop=True)
    m15,h1,h4=e.aggregate(w,15),e.aggregate(w,60),e.aggregate(w,240)
    if len(h4)<30 or len(h1)<30 or len(m15)<50:
        return {'active':False,'reason':'WARMUP: 30 complete H4 candles required','gates':{'history':False}}
    h=e.structure(h1); four=e.structure(h4)
    d='BUY' if h['direction']=='BULLISH' else 'SELL' if h['direction']=='BEARISH' else None
    aligned=d is not None and h['direction']==four['direction']
    setup=e.m15_setup(h,m15); setup['direction']=d
    zone=fresh_retest(m15,d) if d else {'valid':False}
    expected='BULLISH' if d=='BUY' else 'BEARISH'
    conf=e.m5_confirm(w,d)
    conf['confirmed']=bool(d and conf['bos']==expected+' BOS' and conf['candle'])
    conf['reason']='M5 BOS + candle + momentum' if conf['confirmed'] else 'Waiting for M5 BOS + candle + momentum'
    li=setup['liquidity']; sweep=li['sell_side_sweep'] if d=='BUY' else li['buy_side_sweep']
    m15_bos=setup['structure']['bos']==expected+' BOS'
    setup['valid']=bool(d and zone['valid'] and (sweep or m15_bos))
    setup['opportunity']=(d+' FVG RETEST') if d else 'NO ALIGNED TREND'
    setup['range']=range_quality(m15)
    setup['fresh_zone']=zone
    at=w.iloc[-1].datetime+pd.Timedelta(minutes=5)
    se=e.session(at); vol=e.volatility(w); pl=e.plan(d,w,vol)
    pd_ok=bool(d and setup['pd']['zone']==('DISCOUNT' if d=='BUY' else 'PREMIUM'))
    gates={'h4_h1':aligned,'pd_zone':pd_ok,'fresh_zone':zone['valid'],
           'm15_structure':bool(d and (sweep or m15_bos)),'m5_confirmation':conf['confirmed'],
           'session':e.preferred(se),'risk':bool(pl.get('valid')),
           'rr':pl.get('rr_tp2',0)>=e.MIN_RR,'trending':not setup['range']['is_range']}
    weights={'h4_h1':20,'pd_zone':10,'fresh_zone':15,'m15_structure':15,
             'm5_confirmation':20,'session':5,'risk':5,'rr':5,'trending':5}
    sc=sum(weights[k] for k,v in gates.items() if v)
    active=all(gates.values()) and sc>=e.MIN_SCORE
    return {'active':active,'reason':'VALID' if active else 'Waiting: '+', '.join(k for k,v in gates.items() if not v),
            'h1':h,'h4':four,'m15':setup,'m5':conf,'score':sc,'plan':pl,'volatility':vol,
            'gates':gates,'session':se,'decision_time':at.isoformat(),'candle_time':w.iloc[-1].datetime.isoformat()}
