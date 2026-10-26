import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pandas as pd
from test_engine import e, t, b, bars, row, trade
from bosque_execution import MODEL, annotate
from bosque_health import market_health
from bosque_news import fetch_calendar, historical_calendar
from bosque_delivery import deliver
from bosque_signals import select_alert, mark_delivered


class FillPolicyTests(unittest.TestCase):
    def test_buy_and_sell_fill_boundaries_and_rr(self):
        for direction in ('BUY','SELL'):
            for distance,expected in [(1.99,'FILL_RISK_BELOW_MIN'),(2,None),(8,None),(8.01,'FILL_RISK_ABOVE_MAX')]:
                x=trade();sgn=1 if direction=='BUY' else -1
                x.update(direction=direction,sl=100-sgn*distance,tp1=100+sgn*20,tp2=100+sgn*30,tp3=100+sgn*40)
                with self.subTest(direction=direction,risk=distance):
                    t.advance(x,row())
                    self.assertEqual(x['result'],'NOT_ENTERED' if expected else 'OPEN')
                    if expected: self.assertEqual(x['exit_reason'],expected)
        x=trade();t.advance(x,row(o=102,h=103,l=101,c=102))
        self.assertEqual(x['exit_reason'],'FILL_RR_BELOW_MIN')
        self.assertIsNone(x['entry']);self.assertIsNone(x['result_r'])

    def test_real_observed_fill_drift_is_rejected_without_moving_plan(self):
        examples=[('BUY',4180.42,4181.36603,4172.78,4195.71,'FILL_RISK_ABOVE_MAX'),
                  ('SELL',4188.36,4184.79742,4192,4176.36,'FILL_RR_BELOW_MIN'),
                  ('BUY',4188.04,4182.38641,4181.7,4200.73,'FILL_RISK_BELOW_MIN')]
        for direction,planned,opened,sl,tp2,reason in examples:
            x=trade();x.update(direction=direction,planned_entry=planned,sl=sl,tp2=tp2)
            t.advance(x,row(o=opened,h=opened+.1,l=opened-.1,c=opened))
            self.assertEqual(x['exit_reason'],reason)
            self.assertEqual(x['planned_entry'],planned);self.assertEqual(x['sl'],sl);self.assertEqual(x['tp2'],tp2)
            self.assertEqual(t.stats([x])['trades'],0)

    def test_auditing_old_outcome_never_changes_money_or_result(self):
        x=trade();x.update(execution_model='NEXT_M5_OPEN_TP2_V1',result='LOSS',entry=102,result_pips=-70,result_r=-1,closed_at='2026-10-01T01:00Z')
        before=copy.deepcopy(x);annotate(x)
        for key in before:self.assertEqual(x[key],before[key])
        self.assertFalse(x['fill_audit']['enforced'])
        self.assertIn('FILL_RR_BELOW_MIN',x['fill_audit']['reasons'])
        self.assertEqual(t.stats([x])['losses'],1)

    def test_open_legacy_trade_is_not_revalidated_mid_trade(self):
        x=trade();x.update(execution_model='NEXT_M5_OPEN_TP2_V1',result='OPEN',entry=102,last_bar='2026-09-30T23:55:00Z')
        t.advance(x,row(h=111,l=99))
        self.assertEqual(x['result'],'WIN')


class DataHealthTests(unittest.TestCase):
    def test_fresh_stale_future_and_gap(self):
        df=bars();at=df.iloc[-1].datetime+pd.Timedelta(minutes=5,seconds=30)
        self.assertTrue(market_health(df,at)['allowed'])
        self.assertFalse(market_health(df,at+pd.Timedelta(minutes=11))['allowed'])
        self.assertFalse(market_health(df,at-pd.Timedelta(minutes=6))['allowed'])
        self.assertFalse(market_health(df.drop(index=df.index[-2]),at)['allowed'])

    def test_all_incomplete_and_future_candles_are_removed_and_flagged(self):
        df=bars(10);at=df.iloc[5].datetime+pd.Timedelta(minutes=1)
        with patch.object(e,'now_my',return_value=at.to_pydatetime()):closed=e.remove_incomplete_candle(df)
        self.assertEqual(len(closed),5)
        self.assertEqual(closed.attrs['future_candles'],4)
        self.assertFalse(market_health(closed,at)['allowed'])

    def test_invalid_timestamps_and_duplicate_prices_fail_validation(self):
        df=bars(5);df.loc[0,'datetime']=pd.NaT
        with self.assertRaises(ValueError):t.bars(df)
        df=bars(5);df.loc[1,'datetime']=df.loc[0,'datetime']
        with self.assertRaises(ValueError):t.bars(df)

    def test_failure_does_not_refresh_last_success_or_leave_active_signal(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root=Path(temp)
            stack.enter_context(patch.object(e,'ENGINE_DIR',root/'engine'))
            stack.enter_context(patch.object(e,'DASHBOARD_FILE',root/'dashboard_data.json'))
            t.save(e.DASHBOARD_FILE,{'engine':{'timestamp':'2026-10-01T00:00Z'},'signal':{'active':True}})
            stack.enter_context(patch.object(e,'main',side_effect=RuntimeError('secret must not be printed')))
            output=io.StringIO();stack.enter_context(contextlib.redirect_stdout(output))
            with self.assertRaises(SystemExit):e.run_live()
            d=t.load(e.DASHBOARD_FILE)
            self.assertFalse(d['signal']['active']);self.assertFalse(d['data_health']['allowed'])
            self.assertEqual(d['engine']['timestamp'],'2026-10-01T00:00Z')
            self.assertNotIn('secret',output.getvalue()+json.dumps(d))


class NewsIntegrityTests(unittest.TestCase):
    now=pd.Timestamp('2026-10-16T12:00Z')
    def fetch(self,items,count=None,**kwargs):
        response=Mock();response.json.return_value={'events':items,'count':len(items) if count is None else count}
        return fetch_calendar(self.now,Mock(return_value=response),'https://provider.invalid',**kwargs)

    def test_unknown_geography_invalid_usd_time_and_truncation_block(self):
        for item in [{'title':'GDP report','impact':'high','time_utc':self.now.isoformat()},
                     {'currency':'USD','impact':'high','time_utc':'NaT'},
                     {'currency':'USD','impact':'high','time':'2026-10-16 12:00:00'}]:
            self.assertEqual(self.fetch([item])['filter'],'BLOCK')
        self.assertEqual(self.fetch([],count=5)['status'],'ERROR')

    def test_explicit_empty_provider_response_is_not_claimed_complete(self):
        result=self.fetch([])
        self.assertEqual(result['filter'],'PASS')
        self.assertEqual(result['evidence']['coverage_status'],'PROVIDER_RESPONSE_ONLY')

    def test_asymmetric_window_blocks_any_relevant_event_not_only_nearest(self):
        events=[{'currency':'USD','impact':'high','time_utc':(self.now+pd.Timedelta(minutes=m)).isoformat()} for m in (-11,20)]
        result=self.fetch(events,before=30,after=10)
        self.assertEqual(result['filter'],'BLOCK');self.assertEqual(result['minutes_to_news'],20)

    def test_historical_schedule_preserves_provenance_and_boundary_events(self):
        response=Mock();response.json.return_value={'count':1,'events':[{'title':'US CPI','impact':'high','time_utc':'2026-10-16T12:20Z'}]}
        cal=historical_calendar(pd.Timestamp('2026-10-16T00:00Z'),self.now,Mock(return_value=response),'https://provider.invalid')
        self.assertFalse(b.news_allowed(cal,self.now))
        self.assertEqual(cal['coverage_status'],'PROVIDER_SCHEDULE_NOT_POINT_IN_TIME')
        response.json.return_value={'count':1,'events':[{'title':'Unclassified report','impact':'high','time_utc':'2026-10-16T12:00Z'}]}
        with self.assertRaises(ValueError):historical_calendar(self.now,self.now+pd.Timedelta(days=1),Mock(return_value=response),'https://provider.invalid')


class DeliveryTests(unittest.TestCase):
    def test_http_success_without_message_ack_is_not_sent(self):
        response=Mock(ok=True,status_code=200)
        for body in ({'ok':True,'result':{}},{'unexpected':True}):
            response.json.return_value=body
            with patch('bosque_delivery.requests.post',return_value=response):
                self.assertEqual(deliver('test','fake','fake')['status'],'DELIVERY_UNKNOWN')
        response.json.return_value={'ok':True,'result':{'message_id':123,'date':1700000000}}
        with patch('bosque_delivery.requests.post',return_value=response):
            self.assertEqual(deliver('test','fake','fake')['status'],'SENT')

    def test_timeout_is_unknown_but_explicit_rejection_is_retryable(self):
        with patch('bosque_delivery.requests.post',side_effect=e.requests.Timeout()):
            self.assertEqual(deliver('test','fake','fake')['status'],'DELIVERY_UNKNOWN')
        response=Mock(ok=False,status_code=400);response.json.return_value={'ok':False}
        with patch('bosque_delivery.requests.post',return_value=response):
            self.assertEqual(deliver('test','fake','fake')['status'],'FAILED')

    def test_unknown_delivery_and_older_candles_cannot_repeat(self):
        state={};first=select_alert(state,True,'BUY',True,'2026-10-01T00:00Z')
        state['signal_cycle']['delivery_pending']=True
        state=json.loads(json.dumps(state))
        self.assertEqual(select_alert(state,True,'BUY',True,'2026-10-01T00:05Z')['status'],'DELIVERY_UNKNOWN')
        self.assertEqual(select_alert(state,True,'SELL',True,'2026-09-30T23:55Z')['status'],'STALE_CANDLE')
        self.assertEqual(state['signal_cycle']['id'],first['id'])

    def test_same_closed_bar_revision_cannot_send_opposite_alert(self):
        state={};select_alert(state,True,'BUY',True,'2026-10-01T00:00Z')
        mark_delivered(state,'2026-10-01T00:05:30Z')
        self.assertEqual(select_alert(state,True,'SELL',True,'2026-10-01T00:00Z')['status'],'SAME_BAR_REEVALUATION')
        self.assertTrue(select_alert(state,True,'SELL',True,'2026-10-01T00:05Z')['allowed'])

    def test_corrupt_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(e,'STATE_FILE',Path(temp)/'state.json'):
            e.STATE_FILE.write_text('{broken')
            with self.assertRaises(ValueError):e.load_state()
            self.assertEqual(e.STATE_FILE.read_text(),'{broken')

    def test_acknowledged_outbox_recovers_once_after_journal_failure(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(e,'ENGINE_DIR',Path(temp)/'engine'),patch.object(e,'REPO_DIR',Path(temp)):
            e.ENGINE_DIR.mkdir()
            sig={'id':'ack','direction':'BUY','score':80}
            plan={'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115}
            t.save(e.ENGINE_DIR/'state.json',{'delivered_alerts':[{'signal':sig,'plan':plan,'session':'ASIAN','timestamp':'2026-10-01T00:00Z'}]})
            d={'engine':{'timestamp':'2026-10-01T00:05Z'},'signal':{'active':False},'plan':{},'session':'ASIAN','sop':{},'filters':{'session':True}}
            t.sync(d,row(h=111,l=99),None);t.sync(d,row(h=111,l=99),None)
            self.assertEqual(len(t.load(e.ENGINE_DIR/'trade_journal.json')['trades']),1)
            self.assertEqual(t.load(e.ENGINE_DIR/'state.json')['delivered_alerts'],[])


class ReplayIntegrityTests(unittest.TestCase):
    def test_shared_prefix_cache_preserves_oos_results_and_avoids_duplicate_decisions(self):
        df=bars(370);split=df.iloc[365].datetime
        def evaluate(frame,at,news):
            direction='BUY' if at<split else 'SELL'
            return {'active':True,'setup_valid':True,'direction':direction,'score':80,
                'plan':{'entry':100,'sl':95 if direction=='BUY' else 105,
                        'tp1':106 if direction=='BUY' else 94,
                        'tp2':110 if direction=='BUY' else 90,
                        'tp3':115 if direction=='BUY' else 85}}
        with patch.object(t,'evaluate',side_effect=evaluate) as ev:
            expected=b.replay(df,start=split)
            unshared_calls=ev.call_count
            ev.reset_mock();cache={}
            b.replay(df[df.datetime+pd.Timedelta(minutes=5)<split],evaluation_cache=cache)
            actual=b.replay(df,start=split,evaluation_cache=cache)
            self.assertEqual(expected,actual)
            self.assertEqual(ev.call_count,unshared_calls)

    def test_provider_failure_cannot_publish_credentials_in_research_json(self):
        import bosque_auto_backtest as auto
        with tempfile.TemporaryDirectory() as temp,patch.object(e,'ENGINE_DIR',Path(temp)),patch.object(auto,'download_history',side_effect=e.requests.Timeout('https://provider.invalid?apikey=fake-secret')),contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(e,'ENTRY_MODE','CLOSED_CANDLE'),self.assertRaises(SystemExit):auto.main()
            result=(Path(temp)/'backtest_results.json').read_text()
            self.assertNotIn('fake-secret',result+output.getvalue())
            self.assertEqual(json.loads(result)['status'],'FAILED')

    def test_oos_split_does_not_rearm_an_existing_setup(self):
        df=bars(365)
        ev={'active':True,'setup_valid':True,'direction':'BUY','opportunity':'BUY TEST','score':80,
            'plan':{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115}}
        with patch.object(t,'evaluate',return_value=ev):
            result=b.replay(df,start=df.iloc[362].datetime)
        self.assertEqual(result['eligible_signals'],0)
        self.assertGreater(result['repeated_candidates_suppressed'],0)

    def test_prefix_only_data_and_explicit_delay(self):
        df=bars(364)
        def check(frame,at,news):
            self.assertTrue((frame.datetime+pd.Timedelta(minutes=5)<=at).all())
            return {'active':False,'setup_valid':False}
        with patch.object(t,'evaluate',side_effect=check):b.replay(df)
        with self.assertRaises(ValueError):b.replay(df,alert_delay_seconds=300)


if __name__=='__main__':unittest.main()
