import copy, json, unittest
import pandas as pd
from test_engine import e,t,row
from bosque_execution import ZONE_MODEL, MODEL
from bosque_signal_audit import observe
import bosque_trade_alerts as alerts

def pending(direction='BUY',at='2026-10-23T00:00Z'):
    s=1 if direction=='BUY' else -1
    return t.new_trade({'id':'waiting','direction':direction},
        {'entry':100,'sl':100-s*5,'tp1':100+s*6,'tp2':100+s*12,'tp3':100+s*18},at)

class RangeRepairTests(unittest.TestCase):
    def frame(self,closes):
        return pd.DataFrame({'close':closes,'high':[v+1 for v in closes],'low':[v-1 for v in closes]})
    def test_monotonic_narrow_trends_not_ranges(self):
        for closes in ([4100+i for i in range(20)],[4100-i for i in range(20)]):
            self.assertFalse(e.analyze_range(self.frame(closes))['is_range'])
    def test_oscillating_range_is_scale_independent(self):
        df=self.frame([100,102,101,99,100]*4)
        self.assertTrue(e.analyze_range(df)['is_range'])
        self.assertEqual(e.analyze_range(df)['is_range'],e.analyze_range(df*40)['is_range'])
    def test_flat_or_short_data_not_a_range(self):
        self.assertFalse(e.analyze_range(self.frame([100]*20))['is_range'])
        self.assertFalse(e.analyze_range(self.frame([100,101]))['is_range'])

class WaitingEntryTests(unittest.TestCase):
    def step(self,tr,n,o=102,h=103,l=101,c=102):
        df=row(at=pd.Timestamp('2026-10-23T00:00Z')+pd.Timedelta(minutes=n),o=o,h=h,l=l,c=c)
        t.advance(tr,df);return df
    def test_wait_then_fill_both_directions_and_frozen_plan(self):
        for direction in ('BUY','SELL'):
            tr=pending(direction);original={k:tr[k] for k in ('planned_entry','sl','tp2')}
            o=102 if direction=='BUY' else 98
            self.step(tr,0,o=o,h=o+1,l=o-1,c=o)
            self.assertEqual(tr['result'],'PENDING');self.assertEqual(tr['bars_held'],0)
            self.step(tr,5,o=100,h=101,l=99,c=100)
            self.assertEqual(tr['result'],'OPEN');self.assertEqual(tr['bars_held'],1)
            self.assertEqual(tr['entry'],100)
            self.assertEqual(original,{k:tr[k] for k in original})
    def test_expiry_is_no_entry_not_loss(self):
        tr=pending()
        for n in range(0,30,5):self.step(tr,n)
        self.assertEqual(tr['exit_reason'],'ENTRY_WINDOW_EXPIRED')
        self.assertEqual(tr['result'],'NOT_ENTERED');self.assertIsNone(tr['entry'])
        self.assertEqual(t.stats([tr])['trades'],0)
    def test_wick_only_not_fill_and_restart_idempotence(self):
        tr=pending();self.step(tr,0,l=99)
        self.assertTrue(tr['wick_zone_touch_observed']);self.assertIsNone(tr['entry'])
        tr=json.loads(json.dumps(tr));before=copy.deepcopy(tr);self.step(tr,0,l=99)
        self.assertEqual(before,tr)
        self.step(tr,5,o=100,h=101,l=99,c=100);self.assertEqual(tr['result'],'OPEN')
    def test_prefill_sl_or_tp_retires_plan(self):
        for kw,reason in [({'l':94},'SL_TOUCHED_BEFORE_FILL'),({'h':113},'TP2_TOUCHED_BEFORE_FILL')]:
            tr=pending();self.step(tr,0,**kw)
            self.assertEqual(tr['exit_reason'],reason);self.assertIsNone(tr['entry'])
    def test_deadline_boundary_does_not_fill(self):
        tr=pending();tr['last_bar']='2026-10-23T00:25:00+00:00'
        self.step(tr,30,o=100,h=101,l=99,c=100)
        self.assertEqual(tr['exit_reason'],'ENTRY_WINDOW_EXPIRED')
    def test_pending_gap_is_unknown_not_expired_or_loss(self):
        tr=pending();self.step(tr,0);self.step(tr,10)
        self.assertEqual(tr['result'],'DATA_GAP')
    def test_old_model_keeps_immediate_rejection(self):
        tr=pending();tr['execution_model']=MODEL;self.step(tr,0)
        self.assertEqual(tr['result'],'NOT_ENTERED')
        self.assertIn(tr['exit_reason'],['FILL_RR_BELOW_MIN','FILL_OUTSIDE_ENTRY_ZONE'])
    def test_prefill_target_not_sent_as_filled_trade_success(self):
        tr=pending();tr['telegram_status']='SENT'
        df=self.step(tr,0,h=107);observe(tr,df)
        df=self.step(tr,5,o=100,h=101,l=99,c=100);observe(tr,df)
        self.assertEqual(alerts.candidates(tr),[])
        df=self.step(tr,10,o=100,h=107,l=99,c=106);observe(tr,df)
        self.assertEqual([k for k,_ in alerts.candidates(tr)],['TP1'])
    def test_only_matching_configuration_in_current_statistics(self):
        old=pending();old['parameters']['entry_logic_version']='OLD'
        now=pending();out=t.version_performance([old,now])
        self.assertEqual(out['current_version']['open_trades'],1)
        self.assertEqual(len(out['version_groups']),2)

if __name__=='__main__':unittest.main()
