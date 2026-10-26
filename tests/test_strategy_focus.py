import contextlib, io, tempfile, unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from test_engine import bars, e, t

class FocusTests(unittest.TestCase):
    def test_allowlist_and_session_boundaries(self):
        for hour,minute,expected in [(0,59,False),(1,0,True),(6,59,True),(7,0,False),(22,59,False),(23,0,True),(23,59,True)]:
            session=e.get_session(datetime(2026,10,27,hour,minute,tzinfo=e.MY_TZ))
            self.assertEqual(e.strategy_focus('SELL','SELL BREAKOUT RETEST',session)['allowed'],expected)
        self.assertFalse(e.strategy_focus('BUY','BUY BREAKOUT RETEST','OFF SESSION')['allowed'])
        self.assertFalse(e.strategy_focus('SELL','SELL PULLBACK','OFF SESSION')['allowed'])

    def test_live_entry_allowlist_without_bypassing_news(self):
        frame=bars();now=(frame.datetime.iloc[-1]+pd.Timedelta(minutes=2)).to_pydatetime()
        for direction,opportunity,session,news,expected in [
            ('SELL','SELL BREAKOUT RETEST','OFF SESSION','PASS',True),
            ('SELL','SELL BREAKOUT RETEST','LONDON','PASS',False),
            ('BUY','BUY BREAKOUT RETEST','OFF SESSION','PASS',False),
            ('SELL','SELL PULLBACK','OFF SESSION','PASS',False),
            ('SELL','SELL BREAKOUT RETEST','OFF SESSION','BLOCK',False)]:
            with self.subTest(opportunity=opportunity,session=session,news=news),tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
                root=Path(tmp);directory=root/'engine';directory.mkdir()
                plan={'valid':True,'direction':direction,'entry':100,'sl':105,'tp1':94,'tp2':90,'tp3':85,'risk_pips':50,'rr_tp2':2}
                patches={'ENGINE_DIR':directory,'REPO_DIR':root,'DASHBOARD_FILE':root/'dashboard_data.json','STATE_FILE':directory/'state.json'}
                for key,value in patches.items(): stack.enter_context(patch.object(e,key,value))
                for key,value in {'now_my':now,'fetch_m5':frame,'get_session':session,'detect_m15_setup':{'direction':direction,'opportunity':opportunity,'valid':True},'m5_confirmation':{'confirmed':True,'reason':'TEST'},'calculate_score':80,'create_trade_plan':plan,'fetch_news_calendar':{'filter':news}}.items():
                    stack.enter_context(patch.object(e,key,return_value=value))
                send=stack.enter_context(patch.object(e,'send_telegram',return_value={'status':'SENT','message_id':1}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                e.main()
                d=t.load(root/'dashboard_data.json')
                self.assertEqual(d['signal']['active'],expected)
                self.assertEqual(send.call_count,int(expected))
                self.assertEqual(len(t.load(directory/'trade_journal.json')['trades']),int(expected))
                if expected:
                    self.assertIn('SELL_RETEST_OFF_SESSION',d['signal']['parameters']['entry_logic_version'])
                elif news=='PASS':
                    self.assertIn('strategy_focus',d['gate_reasons'])

if __name__=='__main__': unittest.main()
