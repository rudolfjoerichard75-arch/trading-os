import contextlib, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from test_engine import bars, e, t, b
import pandas as pd
import bosque_learning as learning
from bosque_intrabar import decision_frames

class IntrabarTests(unittest.TestCase):
    def setUp(self):
        self.df=bars()
        self.at=self.df.iloc[-1].datetime+pd.Timedelta(minutes=2)
    def test_current_candle_for_decision_only(self):
        live,closed,health=decision_frames(self.df,self.at)
        self.assertTrue(health['allowed'])
        self.assertEqual(len(live),500); self.assertEqual(len(closed),499)
        self.assertLessEqual((closed.datetime+pd.Timedelta(minutes=5)).max(),self.at)
        self.assertEqual(health['entry_mode'],'INTRABAR')
    def test_future_missing_and_stale_inputs_blocked(self):
        for frame,at in [(self.df,self.at-pd.Timedelta(minutes=3)),
                         (self.df.iloc[:-1],self.at),
                         (self.df,self.at+pd.Timedelta(minutes=30)),
                         (self.df.drop(index=498),self.at)]:
            self.assertFalse(decision_frames(frame,at)[2]['allowed'])
    def test_provider_evidence_does_not_relax_block_or_claim_quote_freshness(self):
        self.df.attrs['provider_meta']={'symbol':'XAU/USD','interval':'5min','apikey':'secret'}
        _,_,health=decision_frames(self.df.iloc[:-1],self.at)
        evidence=health['provider_evidence']
        self.assertFalse(health['allowed'])
        self.assertEqual(evidence['expected_current_open'],self.df.iloc[-1].datetime.isoformat())
        self.assertEqual(evidence['latest_returned_open'],self.df.iloc[-2].datetime.isoformat())
        self.assertEqual(evidence['missing_current_intervals'],1)
        self.assertEqual(evidence['current_bar_count'],0)
        self.assertFalse(evidence['quote_timestamp_verified'])
        self.assertNotIn('secret',json.dumps(evidence))
        _,_,current=decision_frames(self.df,self.at)
        self.assertTrue(current['allowed'])
        self.assertEqual(current['provider_evidence']['current_bar_count'],1)
        self.assertFalse(current['provider_evidence']['quote_timestamp_verified'])
    def test_completed_higher_timeframes_ignore_forming_bar(self):
        _,closed,_=decision_frames(self.df,self.at)
        for minutes in (15,60):
            agg=e.aggregate(closed,minutes)
            self.assertTrue((agg.datetime+pd.Timedelta(minutes=minutes)<=self.at).all())
    def test_learning_preserves_two_observed_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload={'entry_timing':{'mode':'INTRABAR'},'direction':None,'plan':{}}
            a=learning.capture(tmp,payload,self.df,self.at)
            changed=self.df.copy();changed.loc[499,'close']-=.1
            z=learning.capture(tmp,payload,changed,self.at+pd.Timedelta(seconds=30))
            self.assertNotEqual(a['id'],z['id'])
            self.assertEqual(learning.safe_capture(tmp,payload,self.df,self.at-pd.Timedelta(minutes=3))['status'],'ERROR')
    def test_main_uses_forming_confirmation_closed_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'engine';directory.mkdir()
            with patch.object(e,'ENGINE_DIR',directory),patch.object(e,'REPO_DIR',root),patch.object(e,'DASHBOARD_FILE',root/'dashboard_data.json'),patch.object(e,'STATE_FILE',directory/'state.json'),patch.object(e,'fetch_m5',return_value=self.df),patch.object(e,'now_my',return_value=self.at.to_pydatetime()),patch.object(e,'fetch_news_calendar',return_value={'filter':'BLOCK','status':'ERROR'}),patch.object(e,'m5_confirmation',wraps=e.m5_confirmation) as confirm,patch.object(t,'sync',wraps=t.sync) as sync,patch.object(e,'send_telegram') as send,contextlib.redirect_stdout(io.StringIO()):
                e.main()
                self.assertEqual(len(confirm.call_args.args[0]),500)
                self.assertEqual(len(sync.call_args.args[1]),499)
                d=json.loads((root/'dashboard_data.json').read_text())
                self.assertEqual(d['engine']['entry_mode'],'INTRABAR')
                self.assertEqual(d['learning_capture']['status'],'CAPTURED')
                self.assertFalse(d['signal']['active']);send.assert_not_called()
    def test_automatic_backtest_cannot_claim_intrabar_results(self):
        import bosque_auto_backtest as auto
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'ENGINE_DIR',Path(tmp)),patch.object(auto,'download_history') as download:
            auto.main();download.assert_not_called()
            result=t.load(Path(tmp)/'backtest_results.json')
            self.assertEqual(result['status'],'AWAITING_INTRABAR_REPLAY')
            self.assertEqual(result['results'],{})

if __name__=='__main__': unittest.main()

