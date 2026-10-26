import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from test_engine import e, t, bars, row
import bosque_learning as learning


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.at = '2026-10-21T00:05:30Z'
        self.frame = row(at='2026-10-21T00:00Z')
        self.plan = {'valid':True,'direction':'BUY','entry':100,'sl':95,
                     'tp1':106,'tp2':110,'tp3':115,'rr_tp2':2}
        self.payload = {'direction':'BUY','opportunity':'BUY PULLBACK','score':80,
                        'session':'ASIAN','h1':{'swing_low':95,'swing_high':105},
                        'm15':{},'m5':{},'confirmation':{'confirmed':True},
                        'pd':{'zone':'DISCOUNT'},'volatility':{'atr':1},
                        'news':{'filter':'PASS'},'data_health':{'allowed':True},
                        'plan':self.plan,'gates':{'setup':True,'risk':True,'news':True}}
        self.env = patch.dict(os.environ, {'ROUND_TRIP_COST_PIPS':''})
        self.env.start(); self.addCleanup(self.env.stop)

    def capture(self):
        return learning.capture(self.root,self.payload,self.frame,self.at)

    def dashboard(self, cap):
        return {'engine':{'timestamp':'2026-10-21T00:20Z'},'learning_capture':cap,
                'signal':{'delivery':{'status':'SENT'}}}

    def trade(self, cap):
        tr=t.new_trade({'id':'signal','learning_id':cap['id'],'direction':'BUY'},self.plan,self.at)
        tr.update(telegram_status='SENT',filled_at='2026-10-21T00:10Z',
                  entry=100,result='WIN',closed_at='2026-10-21T00:20Z',
                  result_r=2,result_pips=100,tp1_hit=True,exit_reason='TP2')
        return tr

    def test_snapshot_immutable_and_repeated_candle_idempotent(self):
        cap=self.capture();path=self.root/'learning/features/2026-10-21.json'
        before=path.read_bytes();self.assertEqual(self.capture()['id'],cap['id'])
        self.payload['score']=75
        self.assertEqual(self.capture()['status'],'REVISION_NOT_LINKED')
        self.assertEqual(path.read_bytes(),before)

    def test_future_input_rejected_and_no_outcome_fields_in_features(self):
        self.payload.update(result='WIN',result_r=3,closed_at='2099-01-01')
        cap=self.capture()
        rec=json.loads((self.root/'learning/features/2026-10-21.json').read_text())['records'][cap['id']]
        self.assertNotIn('result',rec['features'])
        future=self.frame.assign(datetime=self.frame.datetime+pd.Timedelta(hours=1))
        self.assertEqual(learning.safe_capture(self.root,self.payload,future,self.at)['status'],'ERROR')

    def test_rejected_candidate_is_not_a_loss(self):
        self.payload['gates']['news']=False
        cap=self.capture();summary=learning.sync(self.root,[],self.dashboard(cap))
        self.assertEqual(summary['rejected_today'],1)
        self.assertEqual(summary['closed_paper'],0)
        outcome=json.loads((self.root/'learning/outcomes/2026-10-21.json').read_text())['records'][cap['id']]
        self.assertIsNone(outcome['classification_label'])

    def test_forward_result_linked_without_mutating_features_or_trade(self):
        cap=self.capture();tr=self.trade(cap);original=copy.deepcopy(tr)
        path=self.root/'learning/features/2026-10-21.json';before=path.read_bytes()
        summary=learning.sync(self.root,[tr],self.dashboard(cap))
        self.assertEqual(summary['gross_research_rows'],1)
        self.assertEqual(summary['net_research_rows'],0)
        self.assertFalse(summary['model_trained']);self.assertEqual(tr,original)
        self.assertEqual(path.read_bytes(),before)
        learning.sync(self.root,[tr],self.dashboard(cap))
        self.assertEqual(len(json.loads((self.root/'learning/outcomes/2026-10-21.json').read_text())['records']),1)

    def test_unfilled_gap_reconstructed_ambiguous_and_late_capture_excluded(self):
        cap=self.capture()
        for updates in [{'result':'NOT_ENTERED','filled_at':None}, {'result':'DATA_GAP'},
                        {'performance_group':'HISTORICAL_RECONSTRUCTION'},
                        {'exit_reason':'SL_AMBIGUOUS'}, {'filled_at':'2026-10-21T00:05Z'},
                        {'parameters':{'entry_logic_version':'OLD'}}]:
            tr=self.trade(cap);tr.update(updates)
            with self.subTest(updates=updates):
                summary=learning.sync(self.root,[tr],self.dashboard(cap))
                self.assertEqual(summary['gross_research_rows'],0)

    def test_legacy_without_snapshot_is_excluded_not_backfilled(self):
        cap=self.capture();tr=self.trade(cap);tr.pop('learning_id')
        summary=learning.sync(self.root,[tr],self.dashboard(cap))
        self.assertEqual(summary['excluded_legacy_trades'],1)
        self.assertEqual(summary['linked_trades'],0)

    def test_new_config_has_distinct_id_and_missing_costs_stay_unknown(self):
        first=self.capture()
        with patch.object(e,'ENTRY_LOGIC_VERSION','NEW'):
            self.assertNotEqual(first['id'],self.capture()['id'])
        with patch.dict(os.environ,{'ROUND_TRIP_COST_PIPS':'2'}):
            cap=self.capture();tr=self.trade(cap);tr.update(cost_pips=2,cost_status='CONFIGURED')
            self.assertEqual(learning.sync(self.root,[tr],self.dashboard(cap))['net_research_rows'],1)

    def test_corrupt_file_preserved_and_learning_error_is_nonfatal(self):
        self.capture();path=self.root/'learning/features/2026-10-21.json';path.write_text('broken')
        self.assertEqual(learning.safe_capture(self.root,self.payload,self.frame,self.at)['status'],'ERROR')
        self.assertEqual(path.read_text(),'broken')
        self.assertEqual(learning.safe_sync(self.root,[],self.dashboard({}))['status'],'ERROR')

    def test_excursions_exclude_preentry_and_after_exit_bars(self):
        cap=self.capture();tr=self.trade(cap)
        frame=pd.concat([row(at='2026-10-21T00:05Z',h=150,l=50),
                         row(at='2026-10-21T00:10Z',h=106,l=98),
                         row(at='2026-10-21T00:15Z',h=110,l=99),
                         row(at='2026-10-21T00:20Z',h=200,l=1)],ignore_index=True)
        x=learning.excursions(tr,frame)
        self.assertEqual((x['mfe_pips'],x['mae_pips']),(100,20))
        self.assertIsNone(learning.excursions(tr,frame.iloc[2:]))

    def test_main_persists_features_before_send_and_links_ack(self):
        frame=bars();now=(frame.datetime.iloc[-1]+pd.Timedelta(minutes=2,seconds=30)).to_pydatetime()
        directory=self.root/'engine';directory.mkdir()
        def send(_):
            docs=list((directory/'learning/features').glob('*.json'))
            self.assertEqual(len(docs),1)
            records=json.loads(docs[0].read_text())['records']
            self.assertEqual(len(records),1)
            self.assertEqual(next(iter(records.values()))['provenance'],'LIVE_PRE_DELIVERY')
            return {'status':'SENT','message_id':999}
        with contextlib.ExitStack() as stack:
            for key,value in {'ENGINE_DIR':directory,'REPO_DIR':self.root,'DASHBOARD_FILE':self.root/'dashboard_data.json','STATE_FILE':directory/'state.json'}.items():
                stack.enter_context(patch.object(e,key,value))
            stack.enter_context(patch.object(e,'now_my',return_value=now))
            stack.enter_context(patch.object(e,'fetch_m5',return_value=frame))
            stack.enter_context(patch.object(e,'detect_m15_setup',return_value={'direction':'BUY','opportunity':'BUY PULLBACK','valid':True}))
            stack.enter_context(patch.object(e,'m5_confirmation',return_value={'confirmed':True,'reason':'TEST'}))
            stack.enter_context(patch.object(e,'calculate_score',return_value=80))
            stack.enter_context(patch.object(e,'create_trade_plan',return_value=self.plan))
            stack.enter_context(patch.object(e,'fetch_news_calendar',return_value={'filter':'PASS'}))
            stack.enter_context(patch.object(e,'send_telegram',side_effect=send))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            e.main()
        trade=t.load(directory/'trade_journal.json')['trades'][0]
        self.assertTrue(trade['learning_id'])
        self.assertEqual(t.load(self.root/'dashboard_data.json')['learning']['status'],'COLLECTING')

    def test_workflow_persists_learning_files(self):
        from test_engine import ROOT
        text=(ROOT/'.github/workflows/bosque-engine.yml').read_text()
        self.assertIn('git add engine/learning',text)
        self.assertIn('engine/learning_summary.json',text)
