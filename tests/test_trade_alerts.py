import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from test_engine import e, t, row, trade
from bosque_signal_audit import observe
import bosque_trade_alerts as a


class TradeAlertsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / a.FILENAME
        a.collect([], self.path, '2026-09-30T23:59Z', True)

    def new(self, direction='BUY'):
        x = trade()
        x.update(telegram_status='SENT')
        if direction == 'SELL':
            x.update(direction='SELL', sl=105, tp1=94, tp2=90, tp3=85)
        return x

    def advance(self, x, at='2026-10-01T00:00Z', o=100, h=107, l=99, c=100):
        df = row(at=at, o=o, h=h, l=l, c=c)
        t.advance(x, df); observe(x, df)

    def events(self):
        return t.load(self.path)['events']

    def send(self, at='2026-10-01T00:05:10Z', run='run:1', sender=None):
        sender = sender or Mock(return_value={'status': 'SENT', 'message_id': 123})
        a.reserve(self.path, run, at)
        a.dispatch(self.path, run, sender, lambda: at)
        return sender

    def test_buy_sell_all_targets_once_and_tp3_is_not_a_second_paper_win(self):
        for direction in ('BUY', 'SELL'):
            with self.subTest(direction=direction):
                x = self.new(direction); x['signal_id'] = direction
                prices = [(100,107,99,100),(100,111,99,100),(110,116,109,110)]
                if direction == 'SELL':
                    prices = [(100,101,93,100),(100,101,89,100),(90,91,84,90)]
                for i, (o,h,l,c) in enumerate(prices):
                    at = pd.Timestamp('2026-10-01T00:00Z') + pd.Timedelta(minutes=5*i)
                    self.advance(x, at, o,h,l,c)
                    now = at + pd.Timedelta(minutes=5, seconds=10)
                    before = copy.deepcopy(x)
                    a.collect([x], self.path, now, True)
                    sender = self.send(now, run=f'{direction}:{i}')
                    self.assertEqual(sender.call_count, 1)
                    text = sender.call_args.args[0]
                    self.assertIn(f'TP{i+1} DISENTUH', text)
                    self.assertIn('MYT', text); self.assertIn('Signal ID: '+direction, text)
                    if i == 1: self.assertIn('CLOSE POSITION', text)
                    if i == 2: self.assertIn('selepas exit TP2', text)
                    self.assertEqual(x, before)
                    a.collect([x], self.path, now, True)
                    self.assertEqual(self.send(now, run=f'retry:{direction}:{i}').call_count, 0)
                self.assertEqual(x['exit_reason'], 'TP2')
                self.assertEqual(t.stats([x])['trades'], 1)

    def test_same_candle_multiple_targets_each_notified_in_order(self):
        x = self.new(); self.advance(x, h=116)
        a.collect([x], self.path, '2026-10-01T00:05:10Z', True)
        sender = self.send()
        self.assertEqual(sender.call_count, 3)
        for i, call in enumerate(sender.call_args_list):
            self.assertIn(f'TP{i+1} DISENTUH', call.args[0])

    def test_sl_same_bar_tp_and_later_tp3_do_not_become_success_alerts(self):
        x = self.new(); self.advance(x, h=116, l=94)
        a.collect([x], self.path, '2026-10-01T00:05:10Z', True)
        self.assertEqual(list(self.events()), ['test:CLOSE'])
        text = self.send().call_args.args[0]
        self.assertIn('urutan tidak dapat disahkan', text)
        self.assertNotIn('TP3 DISENTUH', text)
        y = self.new(); y['signal_id'] = 'stopped'
        self.advance(y, h=101, l=94)
        self.advance(y, at='2026-10-01T00:05Z', h=116, l=99)
        self.assertEqual(y['signal_path']['status'], 'TP3_AFTER_SL')
        self.assertEqual([kind for kind, _ in a.candidates(y)], ['CLOSE'])

    def test_time_exit_close_and_fill_rejection(self):
        x = self.new(); self.advance(x, h=101)
        x['bars_held'] = 71
        self.advance(x, at='2026-10-01T00:05Z', h=101)
        self.assertEqual(x['exit_reason'], 'TIME_EXIT')
        a.collect([x], self.path, '2026-10-01T00:10:10Z', True)
        self.assertIn('HAD 72 CANDLE M5', self.send('2026-10-01T00:10:10Z').call_args.args[0])
        y = self.new(); y['signal_id'] = 'rejected'
        self.advance(y, o=102, h=116, l=101, c=102)
        self.assertEqual(y['result'], 'NOT_ENTERED')
        self.assertEqual([kind for kind, _ in a.candidates(y)], ['NO_ENTRY','OBS_TP1','OBS_TP2','OBS_TP3'])
        a.collect([y], self.path, '2026-10-01T00:05:10Z', True)
        self.assertIn('FILL_RR_BELOW_MIN', self.events()['rejected:NO_ENTRY']['text'])

    def test_rejected_observations_are_separate_and_idempotent(self):
        x=self.new(); self.advance(x,o=102,h=116,l=101,c=102)
        before=copy.deepcopy(x)
        a.collect([x],self.path,'2026-10-01T00:05:10Z',True)
        sender=self.send()
        self.assertEqual(sender.call_count,4)
        observed=[c.args[0] for c in sender.call_args_list if 'PARAS TP' in c.args[0]]
        self.assertEqual(len(observed),3)
        for text in observed:
            self.assertIn('PARAS TP',text)
            self.assertIn('bukan pengesahan keuntungan',text)
            self.assertNotIn('CLOSE POSITION',text)
        self.assertEqual(x,before)
        self.assertEqual(t.stats([x])['trades'],0)
        a.collect([x],self.path,'2026-10-01T00:05:20Z',True)
        self.assertEqual(self.send('2026-10-01T00:05:30Z',run='again').call_count,0)

    def test_rejected_sl_ambiguity_suppresses_tp_and_upgrade_history(self):
        x=self.new(); self.advance(x,o=102,h=116,l=94,c=102)
        self.assertEqual([k for k,_ in a.candidates(x)],['NO_ENTRY','OBS_SL'])
        self.assertIn('urutan tidak dapat disahkan',a.message(x,'OBS_SL','2026-10-01T00:05Z'))
        # Existing deployments have no observation activation field.
        state=t.load(self.path);state.pop('observation_activated_at');t.save(self.path,state)
        a.collect([x],self.path,'2026-10-01T00:05:10Z',True)
        self.assertNotIn('test:OBS_SL',self.events())

    def test_entry_window_inward_rounding_and_no_strategy_changes(self):
        from bosque_execution import entry_window, assess, ENTRY_ZONE_POLICY
        for direction,sl,tp in [('SELL',4142.67,4124.25),('BUY',4118.68,4139.63)]:
            tr={'direction':direction,'sl':sl,'tp2':tp,'parameters':{'entry_zone_policy':ENTRY_ZONE_POLICY}}
            before=copy.deepcopy(tr); w=entry_window(tr)
            self.assertEqual(w['status'],'AVAILABLE')
            self.assertEqual(assess(tr,w['low'])['status'],'PASS')
            self.assertEqual(assess(tr,w['high'])['status'],'PASS')
            self.assertNotEqual(assess(tr,w['low']-.01)['status'],'PASS')
            self.assertNotEqual(assess(tr,w['high']+.01)['status'],'PASS')
            self.assertEqual(tr,before)
        self.assertEqual(entry_window({'direction':'SELL','sl':4142.67,'tp2':4124.25})['low'],4136.53)
        self.assertEqual(entry_window({'direction':'BUY','sl':100,'tp2':101})['status'],'EMPTY')
        self.assertEqual(entry_window({})['status'],'UNAVAILABLE')

    def test_no_entry_text_shows_actual_simulation_numbers_and_window(self):
        x=self.new('SELL')
        x.update(planned_entry=4136.53,sl=4142.67,tp2=4124.25,next_open=4135.60453,
                 exit_reason='FILL_RR_BELOW_MIN')
        from bosque_execution import annotate
        annotate(x)
        text=a.message(x,'NO_ENTRY','2026-10-20T00:00Z')
        for part in ('4135.60','70.65','1:1.607','4136.53–4140.53','bukan pembatalan signal'):
            self.assertIn(part,text)

    def test_activation_excludes_history_reconstructions_and_unsent_signals(self):
        x = self.new(); self.advance(x)
        for update in ({'timestamp':'2026-09-29T00:00Z'}, {'telegram_status':'FAILED'},
                       {'performance_group':'HISTORICAL_RECONSTRUCTION'}, {'review_status':'REVIEW_REQUIRED'}):
            y = copy.deepcopy(x); y.update(update)
            a.collect([y], self.path, '2026-10-01T00:05:10Z', True)
        y = copy.deepcopy(x); y['signal_path']['source'] = 'HISTORICAL_REPLAY'
        a.collect([y], self.path, '2026-10-01T00:05:10Z', True)
        self.assertEqual(self.events(), {})

    def test_data_health_defers_targets_and_stale_events_never_send(self):
        x = self.new(); self.advance(x)
        a.collect([x], self.path, '2026-10-01T00:05:10Z', False)
        self.assertEqual(self.events(), {})
        a.collect([x], self.path, '2026-10-01T00:10:10Z', True)
        self.assertEqual(self.send('2026-10-01T00:20Z').call_count, 0)
        self.assertEqual(self.events()['test:TP1']['status'], 'EXPIRED')

    def test_gap_warns_without_claiming_close_and_no_gap_warning_after_tp2(self):
        x = self.new(); self.advance(x, h=101)
        self.advance(x, at='2026-10-01T00:10Z', h=101)
        a.collect([x], self.path, '2026-10-01T00:15Z', False)
        text = self.send('2026-10-01T00:15Z').call_args.args[0]
        self.assertIn('SEMAKAN MANUAL', text)
        self.assertNotIn('CLOSE POSITION', text)
        y = self.new(); self.advance(y, h=111)
        self.advance(y, at='2026-10-01T00:15Z', h=111)
        self.assertNotIn('DATA_GAP', [kind for kind, _ in a.candidates(y)])

    def test_timeout_and_interrupted_committed_reservation_never_resend(self):
        x = self.new(); self.advance(x)
        a.collect([x], self.path, '2026-10-01T00:05:10Z', True)
        a.reserve(self.path, '1:1', '2026-10-01T00:05:10Z')
        committed = t.load(self.path)
        sender = Mock(return_value={'status':'SENT','message_id':5})
        a.dispatch(self.path, '1:1', sender, lambda:'2026-10-01T00:05:15Z')
        # Simulate runner loss after Telegram accepted, before receipt git push.
        t.save(self.path, committed)
        again = self.send('2026-10-01T00:10:10Z', run='1:2')
        self.assertEqual(again.call_count, 0)
        self.assertEqual(self.events()['test:TP1']['status'], 'DELIVERY_UNKNOWN')
        t.save(self.path, committed)
        a.dispatch(self.path, '1:1', Mock(side_effect=TimeoutError('secret')), lambda:'2026-10-01T00:05:15Z')
        self.assertEqual(self.send('2026-10-01T00:10:10Z', run='2:1').call_count, 0)
        self.assertNotIn('secret', self.path.read_text())

    def test_explicit_rejection_retries_later_and_no_send_without_reservation(self):
        x = self.new(); self.advance(x)
        a.collect([x], self.path, '2026-10-01T00:05:10Z', True)
        sender = Mock(return_value={'status':'FAILED','reason':'TELEGRAM_REJECTED'})
        a.dispatch(self.path, '1:1', sender, lambda:'2026-10-01T00:05:10Z')
        sender.assert_not_called()
        self.send(sender=sender)
        self.assertEqual(self.send('2026-10-01T00:06Z', run='2:1').call_count, 0)
        self.assertEqual(self.send('2026-10-01T00:10:10Z', run='3:1').call_count, 1)
        self.assertEqual(self.events()['test:TP1']['attempts'], 2)

    def test_persistence_failure_and_corruption_stop_sends(self):
        x = self.new(); self.advance(x)
        a.collect([x], self.path, '2026-10-01T00:05:10Z', True)
        a.reserve(self.path, '1:1', '2026-10-01T00:05:10Z')
        sender = Mock()
        with patch.object(a, 'save', side_effect=OSError()):
            with self.assertRaises(OSError):
                a.dispatch(self.path, '1:1', sender, lambda:'2026-10-01T00:05:10Z')
        sender.assert_not_called()
        self.path.write_text('{broken')
        with self.assertRaises(ValueError): a.reserve(self.path, '2:1', '2026-10-01T00:10Z')

    def test_live_sync_collects_without_sending_and_preserves_plan(self):
        root = Path(self.temp.name); directory = root/'engine'; directory.mkdir()
        with patch.object(e, 'ENGINE_DIR', directory), patch.object(e, 'REPO_DIR', root), patch.object(e, 'send_telegram') as sender:
            a.collect([], directory/a.FILENAME, '2026-09-30T23:59Z', True)
            x = self.new(); observe(x, row().iloc[:0])
            t.save(directory/'trade_journal.json', {'trades':[x]})
            d = {'engine':{'timestamp':'2026-10-01T00:05:10Z'},'signal':{'active':False},
                 'plan':{},'session':'ASIAN','sop':{},'filters':{'session':True},'data_health':{'allowed':True}}
            t.sync(d, row(h=107,l=99), None)
            sender.assert_not_called()
            saved = t.load(directory/'trade_journal.json')['trades'][0]
            for k in ('planned_entry','sl','tp1','tp2','tp3'): self.assertEqual(saved[k], x[k])
            self.assertEqual(d['services']['trade_alerts']['counts'], {'PENDING':1})

    def test_workflow_pushes_reservation_before_dispatch_and_always_persists_receipts(self):
        flow = (Path(__file__).parents[1]/'.github/workflows/bosque-engine.yml').read_text()
        reservation = flow.index('bosque_trade_alerts.py reserve')
        push = flow.index('git push origin HEAD:main', reservation)
        dispatch = flow.index('bosque_trade_alerts.py dispatch')
        self.assertLess(reservation, push); self.assertLess(push, dispatch)
        self.assertIn('if: always()', flow[dispatch:])


if __name__ == '__main__':
    unittest.main()
