"""Replay persistent POI zones as alternate signal generators, no Telegram."""
import hashlib, json, sys
from collections import Counter
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'engine'))
import bosque_engine as e
import bosque_tracking as t
import bosque_poi_zones as z
from bosque_health import market_health

def simulate(frame):
    df=t.bars(frame); zones=[]; trades=[]; seen=set(); last_m15=None
    diagnostics=Counter()
    for i in range(len(df)):
        row=df.iloc[i]; at=row.datetime+pd.Timedelta(minutes=5)
        for tr in trades:
            t.advance(tr,df.iloc[i:i+1])
        window=df.iloc[max(0,i+1-e.OUTPUT_SIZE):i+1]
        ready=[]
        for zone in zones:
            if z.advance_zone(zone,row): ready.append(zone)
        m15=e.aggregate(window,15)
        if len(m15) and m15.iloc[-1].datetime != last_m15:
            last_m15=m15.iloc[-1].datetime
            for zone in z.form_zones(m15):
                if zone['id'] not in seen:
                    seen.add(zone['id']); zones.append(zone)
        if i<359 or not ready: continue
        if not market_health(window,at)['allowed']:
            diagnostics['data_health_rejection']+=1; continue
        h1=e.aggregate(window,60)
        if len(h1)<30: continue
        bias=e.analyze_structure(h1)['direction']
        for kind in ('OB','FVG'):
            eligible=[q for q in ready if q['type']==kind and
                      bias==('BULLISH' if q['direction']=='BUY' else 'BEARISH')]
            if not eligible:
                if any(q['type']==kind for q in ready):diagnostics[kind+'_bias_rejection']+=1
                continue
            zone=max(eligible,key=lambda q:q['formed_at'])
            direction=zone['direction']
            conf=e.m5_confirmation(window,direction)
            if not conf['confirmed']:
                diagnostics[kind+'_confirmation_rejection']+=1;continue
            vol=e.volatility_analysis(window)
            # score argument is unused by V3's plan builder. No invented score.
            plan=e.create_trade_plan(direction,window,None,vol)
            if not plan.get('valid') or plan.get('rr_tp2',0)<e.MIN_RR:
                diagnostics[kind+'_plan_rejection']+=1;continue
            # Prevent entries whose V3 stop sits inside the originating POI.
            if (direction=='BUY' and plan['sl']>=zone['low']) or (direction=='SELL' and plan['sl']<=zone['high']):
                diagnostics[kind+'_stop_inside_zone']+=1;continue
            sig={'id':zone['id'],'direction':direction,'opportunity':kind+' ZONE RETEST',
                 'score':None,'session':e.get_session(at.to_pydatetime().astimezone(e.MY_TZ)),
                 'policy':z.POLICY}
            tr=t.new_trade(sig,plan,at+pd.Timedelta(seconds=1))
            tr.update(decision_at=at.isoformat(),research_strategy=z.POLICY,poi=dict(zone),confirmation=conf)
            trades.append(tr);zone['status']='SIGNALLED'
            # Same-type zones touched on this candle are consumed to avoid
            # repeated overlapping signals from one reaction.
            for other in eligible:
                if other is not zone:other['status']='SUPERSEDED'
    return trades,zones,dict(diagnostics)

def metrics(trades):
    out={}
    for cost in (0,5,10):
        sample=json.loads(json.dumps(trades))
        for tr in sample:
            if tr['result'] in ('WIN','LOSS','BE'):
                tr['result_pips']-=cost
                tr['result_r']=tr['result_pips']/(abs(tr['entry']-tr['sl'])/e.PIP_SIZE)
                tr['result']='WIN' if tr['result_pips']>0 else 'LOSS' if tr['result_pips']<0 else 'BE'
                tr['cost_pips']=cost
        out[str(cost)]=t.stats(sample)
    return {'signals':len(trades),'cost_sensitivity_pips':out}

def main():
    source=ROOT/'research/poi/input.csv'; df=t.bars(pd.read_csv(source))
    baseline=json.loads((ROOT/'research/poi/results.json').read_text())
    print('Replay persistent zones: '+str(len(df))+' M5 bars',flush=True)
    trades,zones,diagnostics=simulate(df)
    out={'policy':z.POLICY,'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
         'bars':len(df),'start':df.datetime.min().isoformat(),'end':df.datetime.max().isoformat(),
         'parameters':t.configuration(),'zone_rules':{'expiry_hours':4,'confirm_after_touch_minutes':15,
         'displacement_body_multiple':1.5,'median_body_lookback':10,'ob_prior_break_lookback':5,
         'ob_opposite_candle_lookback':3,'bias':'H1 structure at confirmation','news':'NOT_APPLIED'},
         'metrics':{'V3':metrics(baseline['trades']),**{kind:metrics([tr for tr in trades if tr['poi']['type']==kind]) for kind in ('OB','FVG')}},
         'zone_counts':dict(Counter(q['type'] for q in zones)),
         'zone_status':{kind:dict(Counter(q['status'] for q in zones if q['type']==kind)) for kind in ('OB','FVG')},
         'diagnostics':diagnostics,'zones':zones,'trades':trades,
         'limitations':['Short retrospective exploratory sample; no independent holdout or parameter optimization.',
         'OB/FVG generate alternate signals; no V3 setup score gate. V3 plan, M5 confirmation and paper fills reused.',
         'Same archived dataset as prior study, including latest revisions; no historical news or broker ticks.',
         'Illustrative costs 0/5/10 pips are not measured broker costs.',
         'TP2 full exit, SL first on ambiguous bars; next-full-M5-open fill and 40-pip entry cap.',
         'OB and FVG are separate portfolios and may overlap. Do not add their totals.',
         'No live engine, Telegram or Learning Journal changes.']}
    t.save(Path(__file__).parent/'results.json',out)
    print(json.dumps({k:out[k] for k in ('metrics','zone_counts','zone_status','diagnostics')},indent=2),flush=True)
if __name__=='__main__':main()
