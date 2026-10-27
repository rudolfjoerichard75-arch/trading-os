"""Independent M15 FVG/IFVG candidates and eligible-first setup selection.

Formation and inversion use completed bars. The latest M5 is an observed
snapshot: its rejection is provisional until close, like other live setups.
"""
import pandas as pd

GAP_MAX_AGE_MINUTES = 240
GAP_MIN_PIPS = 1.0
GAP_MIN_BODY_RATIO = 0.5
FAMILIES = ('BREAKOUT RETEST','FVG','IFVG','RANGE REVERSAL','LIQUIDITY SWEEP','PULLBACK')


def family(opportunity):
    return str(opportunity or '').removeprefix('BUY ').removeprefix('SELL ')


def gap_candidates(m15, m5, pip_size=0.1):
    if len(m15)<3 or len(m5)<2: return []
    a=m15.copy(); b=m5.copy()
    a['datetime']=pd.to_datetime(a.datetime,utc=True)
    b['datetime']=pd.to_datetime(b.datetime,utc=True)
    latest=b.iloc[-1]; at=latest.datetime
    # Only already-completed M15 bars can create a zone.
    a=a[a.datetime+pd.Timedelta(minutes=15)<=at].reset_index(drop=True)
    found=[]
    for i in range(max(2,len(a)-18),len(a)):
        left,middle,right=a.iloc[i-2],a.iloc[i-1],a.iloc[i]
        if middle.datetime-left.datetime!=pd.Timedelta(minutes=15) or right.datetime-middle.datetime!=pd.Timedelta(minutes=15): continue
        formed=right.datetime+pd.Timedelta(minutes=15)
        if at-formed>=pd.Timedelta(minutes=GAP_MAX_AGE_MINUTES): continue
        bullish=float(right.low)>float(left.high)
        bearish=float(right.high)<float(left.low)
        if not (bullish or bearish): continue
        lo,hi=(float(left.high),float(right.low)) if bullish else (float(right.high),float(left.low))
        if hi-lo < GAP_MIN_PIPS*pip_size-1e-9: continue
        body=float(middle.close-middle.open); span=float(middle.high-middle.low)
        if span<=0 or abs(body)/span<GAP_MIN_BODY_RATIO or (body<=0 if bullish else body>=0): continue
        post=b[b.datetime>=formed]
        if post.empty or post.iloc[0].datetime!=formed or (post.datetime.diff().dropna()!=pd.Timedelta(minutes=5)).any(): continue
        prior=post.iloc[:-1]
        breaks=prior[prior.close<lo] if bullish else prior[prior.close>hi]
        kind='FVG'; direction='BUY' if bullish else 'SELL'; inversion=None
        if not breaks.empty:
            inv=breaks.iloc[0]
            inversion=inv.datetime+pd.Timedelta(minutes=5)
            if at<inversion: continue
            kind='IFVG'; direction='SELL' if bullish else 'BUY'
            prior=prior[prior.datetime>=inversion]
            if ((prior.close>hi).any() if bullish else (prior.close<lo).any()): continue
        # First observed retest only; previous completed touches retire this phase.
        if ((prior.low<=hi)&(prior.high>=lo)).any(): continue
        touched=float(latest.low)<=hi and float(latest.high)>=lo
        rejected=(latest.close>=hi and latest.close>latest.open) if direction=='BUY' else (latest.close<=lo and latest.close<latest.open)
        if not touched or not rejected: continue
        found.append({'direction':direction,'opportunity':direction+' '+kind,'valid':True,
                      'reason':[f'M15 {kind} first retest + observed M5 rejection'],
                      'zone':{'kind':kind,'low':lo,'high':hi,'formed_at':formed.isoformat(),
                              'inverted_at':inversion.isoformat() if inversion is not None else None,
                              'id':f'{kind}:{formed.isoformat()}:{lo}:{hi}',
                              'formation_timeframe':'M15','retest_timeframe':'M5',
                              'retest_candle':at.isoformat(),'confirmation':'INTRABAR_OBSERVATION'}})
    # Newest zone first, at most one candidate per family/direction.
    unique={}
    for item in reversed(found): unique.setdefault(item['opportunity'],item)
    return list(unique.values())


def candidates(h1,m15,m5):
    import bosque_engine as e
    base=e.detect_m15_setup(h1,m15,m5)
    result=[base] if base['valid'] else []
    common={key:base.get(key) for key in ('pd','liquidity','structure','range')}
    # Enumerate legacy families independently as well, so a failing range or
    # sweep candidate cannot hide a valid pullback/retest/gap setup.
    liq=common.get('liquidity') or {}; pd_zone=common.get('pd') or {}; rng=common.get('range') or {}
    for direction,trend,sweep,zone in [('BUY','BULLISH','sell_side_sweep','DISCOUNT'),('SELL','BEARISH','buy_side_sweep','PREMIUM')]:
        if liq.get(sweep):
            result.append({**common,'direction':direction,'opportunity':direction+' LIQUIDITY SWEEP',
                           'valid':True,'reason':['M15 directional liquidity sweep']})
            edge=rng.get('location',0.5)<=0.25 if direction=='BUY' else rng.get('location',0.5)>=0.75
            if rng.get('is_range') and edge:
                result.append({**common,'direction':direction,'opportunity':direction+' RANGE REVERSAL',
                               'valid':True,'reason':['M15 range edge + directional liquidity sweep']})
        if h1.get('direction')==trend and pd_zone.get('zone')==zone:
            result.append({**common,'direction':direction,'opportunity':direction+' PULLBACK',
                           'valid':True,'reason':['H1 direction + M15 premium/discount']})
    retest=e.breakout_retest(m15,m5)
    if retest:
        result.append({**common,'direction':retest['direction'],
                       'opportunity':retest['direction']+' BREAKOUT RETEST','valid':True,
                       'reason':['Closed M15 break + observed M5 retest rejection'],
                       'zone':{'kind':'BREAKOUT RETEST',**retest}})
    result.extend({**common,**item} for item in gap_candidates(m15,m5,e.PIP_SIZE))
    unique={}
    for item in result: unique[item['opportunity']]=item
    return list(unique.values()) or [base]


def select(h1,m15,m5,session,volatility):
    import bosque_engine as e
    evaluated=[]
    for setup in candidates(h1,m15,m5):
        conf=e.m5_confirmation(m5,setup['direction'])
        score=e.calculate_score(h1,setup,conf,session,volatility)
        plan=e.create_trade_plan(setup['direction'],m5,score,volatility)
        zone=setup.get('zone') or {}
        # Preserve the usual risk model, but a gap stop must invalidate its zone.
        if plan.get('valid') and zone.get('kind') in ('FVG','IFVG'):
            outside=plan['sl']<zone['low'] if setup['direction']=='BUY' else plan['sl']>zone['high']
            if not outside: plan={**plan,'valid':False,'reason':'SL must be outside the FVG/IFVG zone'}
        gates={'setup':bool(setup['valid']),'m5_confirmation':bool(conf['confirmed']),
               'score':score>=e.MIN_SCORE,'risk':bool(plan.get('valid')),'rr':plan.get('rr_tp2',0)>=e.MIN_RR}
        eligible=all(gates.values())
        evaluated.append({'setup':setup,'confirmation':conf,'score':score,'plan':plan,'eligible':eligible,
                          'blocked_by':[key for key,value in gates.items() if not value]})
    # Priority never lets a failing retest hide a valid alternative.
    pool=[item for item in evaluated if item['eligible']] or evaluated
    selected=sorted(pool,key=lambda item:(family(item['setup']['opportunity'])!='BREAKOUT RETEST',-item['score']))[0]
    return {**selected,'candidates':evaluated}
