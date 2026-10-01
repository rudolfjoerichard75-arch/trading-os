import unittest
from unittest.mock import patch
import pandas as pd
import bosque_engine as e
import bosque_quality as q
from test_engine import bars, event


class QualityTests(unittest.TestCase):
    def test_first_touch_not_reused(self):
        df=pd.DataFrame([
            [100,101,99,100], [102,104,101,103], [104,106,103,105],
            [105,106,104,105], [104,105,102,104],
        ],columns=['open','high','low','close'])
        df['datetime']=pd.date_range('2026-09-28',periods=5,freq='15min',tz='UTC')
        self.assertTrue(q.fresh_retest(df,'BUY')['valid'])
        df.loc[3,'low']=102
        self.assertFalse(q.fresh_retest(df,'BUY')['valid'])

    def test_monotonic_market_is_not_range(self):
        df=bars(20);df['close']=range(100,120);df['high']=df.close+1;df['low']=df.close-1
        self.assertFalse(q.range_quality(df)['is_range'])

    def test_h4_history_and_future_independence(self):
        df=bars(2000)
        self.assertFalse(e.evaluate(df.iloc[:500])['active'])
        result=e.evaluate(df.iloc[:1900])
        df.loc[1900:,'close']=200
        self.assertEqual(result,e.evaluate(df.iloc[:1900]))

    def test_cost_adjusted_rr(self):
        plan={'valid':True,'entry':100,'sl':95,'tp2':110}
        self.assertLess(q.net_rr(plan,1),2)
        self.assertIsNone(q.net_rr(plan,None))

    def test_weekly_loss_lock(self):
        trades=[{'result':'LOSS','result_r':-2,'closed_at':f'2026-09-{day}T01:00Z','timestamp':f'2026-09-{day}T00:00Z'} for day in ('28','29')]
        self.assertFalse(e.entry_allowed(trades,pd.Timestamp('2026-09-30T00:00Z')))
        self.assertTrue(e.entry_allowed(trades,pd.Timestamp('2026-10-05T00:00Z')))

    def test_v52_open_trade_keeps_its_execution(self):
        t=e.new_trade(event(),0);t['strategy_version']='V5.2 REPAIRED'
        e.advance([t],bars(3))
        self.assertEqual(t['result'],'OPEN')

    def test_all_gates_required(self):
        df=bars(2000)
        st={'direction':'BULLISH','bos':'BULLISH BOS','swing_high':99,'swing_low':95}
        setup={'pd':{'zone':'DISCOUNT'},'liquidity':{'sell_side_sweep':True,'buy_side_sweep':False},'structure':st}
        with patch.object(e,'structure',return_value=st),patch.object(e,'m15_setup',return_value=setup),patch.object(q,'fresh_retest',return_value={'valid':True}),patch.object(q,'range_quality',return_value={'is_range':False}),patch.object(e,'m5_confirm',return_value={'bos':'BULLISH BOS','candle':True}),patch.object(e,'session',return_value='LONDON'),patch.object(e,'plan',return_value={'valid':True,'rr_tp2':2.5}):
            self.assertTrue(e.evaluate(df)['active'])
            with patch.object(e,'session',return_value='ASIAN'):
                self.assertFalse(e.evaluate(df)['active'])
