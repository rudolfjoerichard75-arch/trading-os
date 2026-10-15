import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from test_engine import e, t, b, bars, row, trade
from bosque_signals import select_alert, mark_delivered, POLICY
from bosque_signal_audit import observe, audit_unfilled, summary


class LifecycleTests(unittest.TestCase):
    def test_restarts_price_changes_and_gate_fluctuations_do_not_repeat(self):
        state = {}
        first = select_alert(state, True, 'BUY', True, '2026-10-01T00:00Z')
        self.assertTrue(first['allowed'])
        mark_delivered(state, '2026-10-01T00:05:30Z')
        state = json.loads(json.dumps(state))
        self.assertFalse(select_alert(state, True, 'BUY', False, '2026-10-01T00:05Z')['allowed'])
        again = select_alert(state, True, 'BUY', True, '2026-10-01T00:10Z')
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(again['status'], 'DUPLICATE_SUPPRESSED')

    def test_two_distinct_invalid_bars_rearm_and_direction_change_rearms(self):
        state = {}
        first = select_alert(state, True, 'BUY', True, '2026-10-01T00:00Z')
        mark_delivered(state, '2026-10-01T00:05Z')
        for _ in range(3): select_alert(state, False, None, False, '2026-10-01T00:05Z')
        self.assertEqual(state['signal_cycle']['inactive_bars'], 1)
        select_alert(state, False, None, False, '2026-10-01T00:10Z')
        new = select_alert(state, True, 'BUY', True, '2026-10-01T00:15Z')
        self.assertTrue(new['allowed']); self.assertNotEqual(first['id'], new['id'])
        mark_delivered(state, '2026-10-01T00:20Z')
        self.assertTrue(select_alert(state, True, 'SELL', True, '2026-10-01T00:20Z')['allowed'])

    def test_failed_attempt_retries_only_on_later_closed_bar(self):
        state = {}
        self.assertTrue(select_alert(state, True, 'BUY', True, '2026-10-01T00:00Z')['allowed'])
        state = json.loads(json.dumps(state))
        self.assertEqual(select_alert(state, True, 'BUY', True, '2026-10-01T00:00Z')['status'], 'ALREADY_ATTEMPTED_BAR')
        self.assertTrue(select_alert(state, True, 'BUY', True, '2026-10-01T00:05Z')['allowed'])

    def test_main_sends_once_and_keeps_original_plan_when_candidate_moves(self):
        setup = {'direction':'BUY','opportunity':'BUY PULLBACK','valid':True}
        plan = {'valid':True,'direction':'BUY','entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115,
                'risk_pips':50,'tp1_pips':60,'tp2_pips':100,'tp3_pips':150,'rr_tp2':2}
        now = pd.Timestamp('2026-10-03T00:00:30Z').to_pydatetime()
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root=Path(temp); directory=root/'engine';directory.mkdir()
            for key,value in {'ENGINE_DIR':directory,'REPO_DIR':root,'DASHBOARD_FILE':root/'dashboard_data.json','STATE_FILE':directory/'state.json'}.items():
                stack.enter_context(patch.object(e,key,value))
            clock=stack.enter_context(patch.object(e,'now_my',return_value=now))
            fetch=stack.enter_context(patch.object(e,'fetch_m5',return_value=bars()))
            stack.enter_context(patch.object(e,'detect_m15_setup',return_value=setup))
            stack.enter_context(patch.object(e,'m5_confirmation',return_value={'confirmed':True,'reason':'M5 BOS'}))
            stack.enter_context(patch.object(e,'calculate_score',return_value=80))
            planning=stack.enter_context(patch.object(e,'create_trade_plan',return_value=plan))
            stack.enter_context(patch.object(e,'fetch_news_calendar',return_value={'filter':'PASS'}))
            send=stack.enter_context(patch.object(e,'send_telegram',return_value=True))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            e.main(); first=t.load(root/'dashboard_data.json')
            changed={**plan,'entry':101,'sl':96,'tp2':111}
            planning.return_value=changed
            fetch.return_value=bars().assign(datetime=bars().datetime+pd.Timedelta(minutes=5))
            clock.return_value=now+pd.Timedelta(minutes=5)
            setup['opportunity']='BUY BREAKOUT RETEST'
            e.main(); second=t.load(root/'dashboard_data.json')
            self.assertEqual(send.call_count,1)
            self.assertNotEqual(first['signal']['candidate_id'],second['signal']['candidate_id'])
            self.assertEqual(first['signal']['id'],second['signal']['id'])
            self.assertEqual(second['signal']['delivery']['status'],'DUPLICATE_SUPPRESSED')
            journal=t.load(directory/'trade_journal.json')['trades']
            self.assertEqual(len(journal),1);self.assertEqual(journal[0]['planned_entry'],100)
            self.assertTrue((directory/'market_snapshot.csv').exists())

    def test_replay_uses_same_policy_with_moving_prices(self):
        df=bars(364)
        def candidate(frame, at, news):
            entry=100+len(frame)-360
            return {'active':True,'setup_valid':True,'direction':'BUY','opportunity':'BUY TEST','score':80,
                    'plan':{'entry':entry,'sl':entry-5,'tp1':entry+6,'tp2':entry+10,'tp3':entry+15}}
        with patch.object(t,'evaluate',side_effect=candidate): result=b.replay(df)
        self.assertEqual(result['eligible_signals'],1)
        self.assertEqual(result['repeated_candidates_suppressed'],4)
        self.assertEqual(result['trades'][0]['signal_policy'],POLICY)


class TargetPathTests(unittest.TestCase):
    def test_unsent_alert_is_not_added_and_old_backtest_is_marked_outdated(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(e,'ENGINE_DIR',Path(temp)/'engine'), patch.object(e,'REPO_DIR',Path(temp)):
            e.ENGINE_DIR.mkdir()
            t.save(e.ENGINE_DIR/'backtest_results.json',{'version':t.VERSION,'status':'COMPLETED','results':{'old':{'stats':{'win_rate':100}}}})
            d={'engine':{'timestamp':'2026-10-03T00:00:30Z'},'signal':{'active':True,'id':'not-delivered','direction':'BUY','score':80},'plan':{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115},'session':'ASIAN','sop':{},'filters':{'session':False},'news':{'filter':'PASS'}}
            t.sync(d,bars(),False)
            self.assertEqual(d['journal']['recorded_signals'],0)
            self.assertEqual(d['backtest']['status'],'OUTDATED_SETTINGS')
            self.assertEqual(d['backtest']['results'],{})
            self.assertEqual(t.load(e.ENGINE_DIR/'backtest_results.json')['status'],'COMPLETED')

    def test_tp3_after_paper_tp2_is_recorded_without_changing_profit(self):
        x=trade(); first=row(h=111,l=99,c=110)
        t.advance(x,first);before=copy.deepcopy(x)
        observe(x,first)
        observe(x,row(at='2026-10-01T00:05Z',o=110,h=116,l=109,c=115))
        self.assertEqual(x['signal_path']['status'],'TP3_OBSERVED')
        for key in before:self.assertEqual(x[key],before[key],key)
        self.assertEqual(t.stats([x])['trades'],1)

    def test_not_entered_then_tp3_after_sl_is_not_a_win(self):
        x=trade(); first=row(o=94,h=96,l=93,c=95)
        t.advance(x,first);observe(x,first)
        self.assertEqual(x['result'],'NOT_ENTERED');self.assertEqual(x['exit_reason'],'OPEN_BEYOND_SL')
        observe(x,row(at='2026-10-01T00:05Z',o=100,h=116,l=99,c=115))
        self.assertEqual(x['signal_path']['status'],'TP3_AFTER_SL')
        self.assertEqual(t.stats([x])['trades'],0)
        self.assertEqual(summary([x])['tp3_after_sl'],1)

    def test_sl_tp_same_candle_is_ambiguous_and_sell_is_symmetric(self):
        x=trade();observe(x,row(h=116,l=94))
        self.assertEqual(x['signal_path']['status'],'AMBIGUOUS_SL_TP3')
        y=trade();y.update(direction='SELL',sl=105,tp1=94,tp2=90,tp3=85)
        observe(y,row(o=100,h=101,l=84,c=85))
        self.assertEqual(y['signal_path']['status'],'TP3_OBSERVED')

    def test_partial_signal_bar_and_duplicates_are_excluded(self):
        x=trade();x['timestamp']='2026-10-01T00:00:30Z'
        observe(x,row(h=116,l=94));self.assertEqual(x['signal_path']['bars_observed'],0)
        future=row(at='2026-10-01T00:05Z');observe(x,future);observe(x,future)
        self.assertEqual(x['signal_path']['bars_observed'],1)
        self.assertTrue(x['signal_path']['partial_signal_bar_excluded'])

    def test_gap_does_not_claim_target_order(self):
        x=trade();observe(x,row(at='2026-10-01T00:10Z',h=116,l=99))
        self.assertEqual(x['signal_path']['status'],'DATA_GAP')
        self.assertEqual(x['signal_path']['targets'],{})

    def test_old_cancelled_record_keeps_result_and_gets_evidence(self):
        x=trade();x.update(result='CANCELLED',exit_reason='NEXT_OPEN_OUTSIDE_PLAN')
        original=copy.deepcopy(x);audit_unfilled(x,row(o=111,h=112,l=110,c=111))
        self.assertEqual(x['execution_audit']['reason'],'OPEN_BEYOND_TP2')
        for key in original:self.assertEqual(x[key],original[key])


if __name__=='__main__':unittest.main()
