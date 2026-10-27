import json, unittest
from unittest.mock import patch
import pandas as pd
from test_engine import e
from bosque_setups import gap_candidates, select
from bosque_signals import select_candidate_alerts, mark_delivered

class GapTests(unittest.TestCase):
    def frames(self, bearish=False):
        m15=pd.DataFrame({'datetime':pd.date_range('2026-10-27',periods=3,freq='15min',tz='UTC'),
                          'open':[98,99,103],'high':[100,104,105],'low':[97,98,102],'close':[99,103,104]})
        m5=pd.DataFrame({'datetime':pd.date_range('2026-10-27T00:40Z',periods=3,freq='5min'),
                         'open':[103,103,102.5],'high':[104,104,104],'low':[102.5,102.5,101],'close':[103.5,103.5,103]})
        if bearish:
            for df in (m15,m5):
                old=df.copy()
                df['open']=200-old.open;df['close']=200-old.close
                df['high']=200-old.low;df['low']=200-old.high
        return m15,m5
    def test_fvg_buy_sell_without_breakout(self):
        for bearish,side in [(False,'BUY'),(True,'SELL')]:
            a,b=self.frames(bearish);items=gap_candidates(a,b)
            self.assertEqual(len(items),1);self.assertEqual(items[0]['opportunity'],side+' FVG')
    def test_ifvg_requires_completed_break_and_later_retest(self):
        for bearish,side in [(False,'SELL'),(True,'BUY')]:
            a,b=self.frames()
            b.loc[1,['open','high','low','close']]=[103,103,98,99]
            b.loc[2,['open','high','low','close']]=[99.5,101,98,99]
            if bearish:
                for df in (a,b):
                    old=df.copy();df['open']=200-old.open;df['close']=200-old.close
                    df['high']=200-old.low;df['low']=200-old.high
            items=gap_candidates(a,b)
            self.assertEqual(items[0]['opportunity'],side+' IFVG')
            self.assertEqual(items[0]['zone']['inverted_at'],'2026-10-27T00:50:00+00:00')
            self.assertEqual(gap_candidates(a,b.iloc[:-1]),[])
    def test_touch_overlap_mitigation_expiry_and_gaps(self):
        a,b=self.frames();a.loc[2,'low']=100
        self.assertEqual(gap_candidates(a,b),[])
        a,b=self.frames();b.loc[1,'low']=101
        self.assertEqual(gap_candidates(a,b),[])
        a,b=self.frames();b['datetime']+=pd.Timedelta(hours=5)
        self.assertEqual(gap_candidates(a,b),[])
        a,b=self.frames();b=b.drop(index=1)
        self.assertEqual(gap_candidates(a,b),[])
        a,b=self.frames();a.loc[1,'datetime']-=pd.Timedelta(minutes=15)
        self.assertEqual(gap_candidates(a,b),[])
    def test_unformed_gap_and_wick_only_inversion(self):
        a,b=self.frames();b.datetime-=pd.Timedelta(minutes=15)
        self.assertEqual(gap_candidates(a,b),[])
        a,b=self.frames();b.loc[1,['open','high','low','close']]=[103,104,99,103]
        b.loc[2,['open','high','low','close']]=[99.5,101,98,99]
        self.assertEqual(gap_candidates(a,b),[])
    def test_valid_retest_priority_and_invalid_retest_fallback(self):
        a,b=self.frames()
        candidates=[{'direction':'SELL','opportunity':'SELL BREAKOUT RETEST','valid':True},
                    {'direction':'BUY','opportunity':'BUY FVG','valid':True,'zone':{'kind':'FVG','low':100,'high':102}}]
        good={'valid':True,'rr_tp2':2,'sl':95}
        with patch('bosque_setups.candidates',return_value=candidates),patch.object(e,'m5_confirmation',return_value={'confirmed':True}),patch.object(e,'calculate_score',side_effect=[70,90]),patch.object(e,'create_trade_plan',return_value=good):
            self.assertEqual(select({},a,b,'ASIAN',{})['setup']['opportunity'],'SELL BREAKOUT RETEST')
        with patch('bosque_setups.candidates',return_value=candidates),patch.object(e,'m5_confirmation',return_value={'confirmed':True}),patch.object(e,'calculate_score',side_effect=[70,90]),patch.object(e,'create_trade_plan',side_effect=[{'valid':False},good]):
            self.assertEqual(select({},a,b,'ASIAN',{})['setup']['opportunity'],'BUY FVG')
    def test_stop_inside_gap_blocks_candidate(self):
        a,b=self.frames();candidate=gap_candidates(a,b)
        with patch('bosque_setups.candidates',return_value=candidate),patch.object(e,'m5_confirmation',return_value={'confirmed':True}),patch.object(e,'calculate_score',return_value=90),patch.object(e,'create_trade_plan',return_value={'valid':True,'rr_tp2':2,'sl':101}):
            self.assertFalse(select({},a,b,'ASIAN',{})['eligible'])
    def test_independent_family_cycles_persist_without_repeat_spam(self):
        state={};items=[{'setup':{'direction':'SELL','opportunity':'SELL '+name,'valid':True},'score':80} for name in ('BREAKOUT RETEST','FVG')]
        first=select_candidate_alerts(state,items,'SELL BREAKOUT RETEST',True,'2026-10-27T00:50Z')
        self.assertTrue(first['allowed']);mark_delivered(state,'2026-10-27T00:51Z')
        state=json.loads(json.dumps(state))
        second=select_candidate_alerts(state,items,'SELL FVG',True,'2026-10-27T00:55Z')
        self.assertTrue(second['allowed']);self.assertNotEqual(first['id'],second['id'])
        mark_delivered(state,'2026-10-27T00:56Z');state=json.loads(json.dumps(state))
        self.assertFalse(select_candidate_alerts(state,items,'SELL BREAKOUT RETEST',True,'2026-10-27T01:00Z')['allowed'])
        for at in ('2026-10-27T01:05Z','2026-10-27T01:10Z'):
            select_candidate_alerts(state,[],'NO VALID SETUP',False,at)
        self.assertTrue(select_candidate_alerts(state,items,'SELL BREAKOUT RETEST',True,'2026-10-27T01:15Z')['allowed'])

if __name__=='__main__': unittest.main()
