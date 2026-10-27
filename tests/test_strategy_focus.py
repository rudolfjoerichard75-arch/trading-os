import contextlib, io, tempfile, unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from test_engine import bars, e, t

class FocusTests(unittest.TestCase):
    def test_all_sessions_and_directions_enabled(self):
        for hour in range(24):
            session=e.get_session(datetime(2026,10,27,hour,0,tzinfo=e.MY_TZ))
            for side in ('BUY','SELL'):
                for name in ('BREAKOUT RETEST','FVG','IFVG','PULLBACK'):
                    self.assertTrue(e.strategy_focus(side,side+' '+name,session)['allowed'])

    def test_live_entry_allowlist_without_bypassing_news(self):
        frame=bars();now=(frame.datetime.iloc[-1]+pd.Timedelta(minutes=2)).to_pydatetime()
        for direction,opportunity,session,news,expected in [
            ('SELL','SELL BREAKOUT RETEST','OFF SESSION','PASS',True),
            ('SELL','SELL BREAKOUT RETEST','LONDON','PASS',True),
            ('BUY','BUY BREAKOUT RETEST','OFF SESSION','PASS',True),
            ('SELL','SELL PULLBACK','OFF SESSION','PASS',True),
            ('BUY','BUY FVG','ASIAN','PASS',True),
            ('SELL','SELL IFVG','NEW YORK','PASS',True),
            ('SELL','SELL BREAKOUT RETEST','OFF SESSION','BLOCK',False)]:
            with self.subTest(opportunity=opportunity,session=session,news=news),tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
                root=Path(tmp);directory=root/'engine';directory.mkdir()
                plan={'valid':True,'direction':direction,'entry':100,'sl':105,'tp1':94,'tp2':90,'tp3':85,'risk_pips':50,'rr_tp2':2}
                if direction=='BUY': plan.update(sl=95,tp1=106,tp2=110,tp3=115)
                patches={'ENGINE_DIR':directory,'REPO_DIR':root,'DASHBOARD_FILE':root/'dashboard_data.json','STATE_FILE':directory/'state.json'}
                for key,value in patches.items(): stack.enter_context(patch.object(e,key,value))
                for key,value in {'now_my':now,'fetch_m5':frame,'get_session':session,'detect_m15_setup':{'direction':direction,'opportunity':opportunity,'valid':True},'m5_confirmation':{'confirmed':True,'reason':'TEST'},'calculate_score':80,'create_trade_plan':plan,'fetch_news_calendar':{'filter':news}}.items():
                    stack.enter_context(patch.object(e,key,return_value=value))
                candidate={'direction':direction,'opportunity':opportunity,'valid':True}
                if opportunity.endswith((' FVG',' IFVG')):
                    candidate['zone']={'kind':opportunity.split()[-1],'low':98,'high':102,'id':'test-zone'}
                stack.enter_context(patch('bosque_setups.candidates',return_value=[candidate]))
                send=stack.enter_context(patch.object(e,'send_telegram',return_value={'status':'SENT','message_id':1}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                e.main()
                d=t.load(root/'dashboard_data.json')
                self.assertEqual(d['signal']['active'],expected)
                self.assertEqual(send.call_count,int(expected))
                self.assertEqual(len(t.load(directory/'trade_journal.json')['trades']),int(expected))
                if expected:
                    self.assertIn('MULTI_SETUP',d['signal']['parameters']['entry_logic_version'])
                    if 'zone' in candidate:
                        trade=t.load(directory/'trade_journal.json')['trades'][0]
                        self.assertEqual(trade['setup_evidence'],candidate['zone'])
                        self.assertIn('Zon '+candidate['zone']['kind'],send.call_args.args[0])
                elif news=='PASS':
                    self.assertIn('strategy_focus',d['gate_reasons'])

if __name__=='__main__': unittest.main()
