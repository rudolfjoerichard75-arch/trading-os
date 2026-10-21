import sys, unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import bosque_poi_zones as z

class ZoneTests(unittest.TestCase):
    def zone(self):
        return {'direction':'BUY','low':100.,'high':102.,'formed_at':'2026-10-20T04:00:00Z',
                'expires_at':'2026-10-20T08:00:00Z','status':'WAITING','touched_at':None}
    def bar(self,at,low=101,high=104,close=103):
        return pd.Series({'datetime':pd.Timestamp(at),'open':102,'low':low,'high':high,'close':close})
    def test_no_formation_bar_touch_and_later_confirmation(self):
        q=self.zone()
        self.assertFalse(z.advance_zone(q,self.bar('2026-10-20T03:55Z')))
        self.assertEqual(q['status'],'WAITING')
        self.assertFalse(z.advance_zone(q,self.bar('2026-10-20T04:00Z')))
        self.assertEqual(q['status'],'TOUCHED')
        self.assertTrue(z.advance_zone(q,self.bar('2026-10-20T04:05Z')))
    def test_invalidation_precedes_touch(self):
        q=self.zone()
        self.assertFalse(z.advance_zone(q,self.bar('2026-10-20T04:00Z',low=98,close=99)))
        self.assertEqual(q['status'],'INVALIDATED')
    def test_timeout_and_expiry(self):
        q=self.zone();z.advance_zone(q,self.bar('2026-10-20T04:00Z'))
        self.assertFalse(z.advance_zone(q,self.bar('2026-10-20T04:20Z')))
        self.assertEqual(q['status'],'CONFIRM_TIMEOUT')
        q=self.zone();z.advance_zone(q,self.bar('2026-10-20T08:00Z'))
        self.assertEqual(q['status'],'EXPIRED')
    def test_signal_consumed_once(self):
        q=self.zone();q['status']='SIGNALLED'
        self.assertFalse(z.advance_zone(q,self.bar('2026-10-20T04:05Z')))
    def frame(self):
        return pd.DataFrame({'datetime':pd.date_range('2026-10-20',periods=13,freq='15min',tz='UTC'),
            'open':[101.]*13,'close':[102.]*13,'low':[100.]*13,'high':[103.]*13})
    def test_fvg_known_only_on_third_close(self):
        df=self.frame();df.loc[11,['open','close','low','high']]=[102,106,101,107]
        df.loc[12,['open','close','low','high']]=[105,106,104,107]
        before=df.copy(deep=True)
        zones=z.form_zones(df);fvg=next(q for q in zones if q['type']=='FVG')
        self.assertEqual((fvg['low'],fvg['high']),(103,104))
        self.assertEqual(pd.Timestamp(fvg['formed_at']),df.iloc[-1].datetime+pd.Timedelta(minutes=15))
        self.assertEqual(z.form_zones(df.iloc[:-1]),[])
        pd.testing.assert_frame_equal(before,df)
    def test_ob_last_opposite_before_displacement_break(self):
        df=self.frame();df.loc[11,['open','close']]=[102,101]
        df.loc[12,['open','close','high']]=[101,106,107]
        ob=next(q for q in z.form_zones(df) if q['type']=='OB')
        self.assertEqual((ob['low'],ob['high']),(100,103))
        self.assertEqual(pd.Timestamp(ob['origin_at']),df.iloc[11].datetime)
    def test_gaps_cannot_form_zone(self):
        df=self.frame();df.loc[12,'datetime']+=pd.Timedelta(minutes=15)
        self.assertEqual(z.form_zones(df),[])
    def test_sell_mirrors_buy(self):
        q=self.zone();q['direction']='SELL'
        self.assertFalse(z.advance_zone(q,self.bar('2026-10-20T04:00Z',low=99,high=101,close=99)))
        self.assertTrue(z.advance_zone(q,self.bar('2026-10-20T04:05Z',low=98,high=100,close=99)))

    def test_replay_prefix_has_identical_zone_births_and_decisions(self):
        import importlib.util, json
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('zone_research',root/'research/poi_zones/run_study.py')
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        df=pd.read_csv(root/'research/poi/input.csv')
        # Include actual signal but exclude its later loss: future outcome must
        # not change the zone selection or signal decision.
        cutoff=pd.Timestamp('2026-10-21T15:55Z')
        df.datetime=pd.to_datetime(df.datetime,utc=True)
        prefix=df[df.datetime+pd.Timedelta(minutes=5)<=cutoff]
        trades,zones,_=runner.simulate(prefix)
        full=json.loads((root/'research/poi_zones/results.json').read_text())
        earlier=[tr for tr in full['trades'] if pd.Timestamp(tr['decision_at'])<=cutoff]
        fields=['signal_id','decision_at','direction','planned_entry','sl','tp2','poi']
        self.assertEqual([{k:t[k] for k in fields} for t in trades],
                         [{k:t[k] for k in fields} for t in earlier])
        self.assertTrue(all(t['result']=='PENDING' for t in trades))
        self.assertEqual([q['id'] for q in zones],
                         [q['id'] for q in full['zones'] if pd.Timestamp(q['formed_at'])<=cutoff])

if __name__=='__main__':unittest.main()
