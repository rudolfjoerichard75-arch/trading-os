import sys, tempfile, unittest, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import pandas as pd
import bosque_engine as e
import bosque_backtest as b


def bars(n=400):
    return pd.DataFrame({'datetime':pd.date_range('2026-09-28',periods=n,freq='5min',tz='UTC'),'open':100.,'high':101.,'low':99.,'close':100.})

def event():
    return {'decision_time':'2026-09-28T00:05:00+00:00','candle_time':'2026-09-28T00:00:00+00:00',
        'score':80,'session':'ASIAN','m15':{'opportunity':'TEST'},
        'plan':{'direction':'BUY','entry':100.,'sl':95.,'tp1':106.,'tp2':110.,'tp3':115.}}

class EngineTests(unittest.TestCase):
    def test_time_exit_loss(self):
        t=e.new_trade(event(),1)
        t['tp2']=112.
        frame=bars(73); frame.loc[72,'close']=99
        for row in frame.itertuples(index=False): e.process_bar(t,row)
        self.assertEqual(t['result'],'LOSS'); self.assertEqual(t['result_pips'],-11)
        self.assertEqual(t['bars_held'],72)

    def test_sl_tp_ambiguity_and_duplicate_bar(self):
        t=e.new_trade(event(),0); frame=bars(2); frame.loc[1,['high','low']]=[112.,94.]
        row=next(frame.iloc[1:].itertuples(index=False)); e.process_bar(t,row); e.process_bar(t,row)
        self.assertEqual(t['result'],'LOSS'); self.assertEqual(t['bars_held'],1)
        self.assertEqual(t['exit_reason'],'SL_AMBIGUOUS')

    def test_sell_profit(self):
        ev=event(); ev['plan'].update(direction='SELL',sl=105.,tp1=94.,tp2=90.,tp3=85.)
        t=e.new_trade(ev,1); frame=bars(2); frame.loc[1,'low']=89
        t['strategy_version']='V5.2 REPAIRED'  # existing V5.2 fills retain their contract
        e.process_bar(t,next(frame.iloc[1:].itertuples(index=False)))
        self.assertEqual(t['result'],'WIN'); self.assertEqual(t['result_pips'],99)

    def test_next_open_gap_cancels(self):
        t=e.new_trade(event(),0); frame=bars(2); frame.loc[1,'open']=104
        e.process_bar(t,next(frame.iloc[1:].itertuples(index=False)))
        self.assertEqual(t['result'],'CANCELLED')

    def test_aggregate_no_partial(self):
        self.assertEqual(len(e.aggregate(bars(5),60)),0)
        self.assertEqual(len(e.aggregate(bars(12),60)),1)
        self.assertEqual(len(e.aggregate(bars(12).drop(index=5),60)),0)

    def test_closed_candle_cutoff(self):
        self.assertEqual(len(e.closed_bars(bars(2),pd.Timestamp('2026-09-28T00:07:00Z'))),1)

    def test_gap_does_not_invent_outcome(self):
        t=e.new_trade(event(),0)
        e.advance([t],bars(5).iloc[[1,3]])
        self.assertEqual(t['result'],'DATA_GAP')
        self.assertFalse(e.entry_allowed([t],pd.Timestamp('2026-09-29T00:00:00Z')))

    def test_resume_journal(self):
        t=e.new_trade(event(),0); frame=bars(10)
        e.advance([t],frame.iloc[:5]); e.advance([t],frame.iloc[:5])
        self.assertEqual(t['bars_held'],4)
        restored=json.loads(json.dumps(t)); e.advance([restored],frame)
        self.assertEqual(restored['bars_held'],9)

    def test_directional_score(self):
        s={'direction':'BUY','valid':True,'liquidity':{},'structure':{'bos':'BEARISH BOS'},'pd':{'zone':'PREMIUM'}}
        c={'confirmed':False,'bos':'BEARISH BOS','candle':False}
        self.assertEqual(e.score({'direction':'BEARISH','bos':'BEARISH BOS'},s,c,'ASIAN',{'condition':'HIGH'}),10)

    def test_bad_calendar(self):
        for raw in ({'error':'bad'},[],[{'country':'USD','impact':'High','date':'bad'}]):
            with self.assertRaises((ValueError,TypeError)): e.normalize_events(raw)

    def test_news_window_and_coverage(self):
        cal={'coverage_start':'2026-09-28T00:00:00Z','coverage_end':'2026-10-01T00:00:00Z',
             'events':e.normalize_events([{'country':'USD','impact':'High','date':'2026-09-28T08:00:00-04:00','title':'CPI'}])}
        for time in ['11:30','12:20']:
            self.assertFalse(e.news_at(cal,pd.Timestamp('2026-09-28T'+time+':00Z'))['ok'])
        self.assertTrue(e.news_at(cal,pd.Timestamp('2026-09-28T12:21:00Z'))['ok'])
        self.assertFalse(e.news_at(cal,pd.Timestamp('2026-10-02T12:21:00Z'))['ok'])

    def test_corrupt_journal_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'journal.json'; p.write_text('{bad')
            with self.assertRaises(json.JSONDecodeError): e.load_json(p)
            self.assertEqual(p.read_text(),'{bad')

    def test_bad_ohlc(self):
        frame=bars(2); frame.loc[0,'high']=98
        with self.assertRaises(ValueError): e.validate_bars(frame)

    def test_stats_and_confidence(self):
        t=e.new_trade(event(),0); t['entry']=100.; t['risk_pips']=50
        e.finish(t,95,pd.Timestamp('2026-09-28T01:00Z'),'SL')
        s=e.stats([t]); self.assertEqual(s['max_drawdown'],1)
        self.assertEqual(s['win_rate'],0); self.assertIsNotNone(s['win_rate_95_interval'])

    def test_replay_uses_shared_strategy(self):
        with patch.object(e,'evaluate',return_value={'active':False}) as f:
            out=b.replay(bars(8),1)
        self.assertEqual(f.call_count,8); self.assertEqual(out['stats']['trades'],0)

    def test_causal_signal(self):
        # Same prefix gives the same signal regardless of future prices.
        frame=bars(); before=e.evaluate(frame.iloc[:380])
        frame.loc[380:,'close']=1000
        self.assertEqual(before,e.evaluate(frame.iloc[:380]))

class IntegrationTests(unittest.TestCase):
    def test_live_round_trip_persists_journal_and_suppresses_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); frame=bars(400)
            at=frame.iloc[-1].datetime+pd.Timedelta(minutes=5,seconds=10)
            ev=event(); ev.update(active=True,decision_time=(frame.iloc[-1].datetime+pd.Timedelta(minutes=5)).isoformat(),candle_time=frame.iloc[-1].datetime.isoformat())
            ev['plan'].update(valid=True,rr_tp2=2.4,tp2=112.)
            values=frame.copy(); values['datetime']=values.datetime.astype(str)
            patches={'DATA_DIR':root,'REPO_DIR':root,'DASHBOARD_FILE':root/'dashboard.json','JOURNAL_FILE':root/'journal.json','NEWS_CACHE_FILE':root/'news.json','BACKTEST_FILE':root/'backtest.json'}
            with patch.multiple(e,**patches), patch.object(e,'now_utc',return_value=at.to_pydatetime()), patch.object(e,'http_json',return_value={'values':values.to_dict('records')}), patch.object(e,'evaluate',return_value=ev), patch.object(e,'fetch_news',return_value={'ok':True,'status':'CLEAR','reason':'test'}), patch.object(e,'telegram',return_value='SENT') as send, patch.dict(e.os.environ,{'TWELVEDATA_API_KEY':'test','ROUND_TRIP_COST_PIPS':'1'}):
                e.run_live(); e.run_live()
                journal=e.load_json(e.JOURNAL_FILE)
                self.assertEqual(len(journal['trades']),1)
                self.assertEqual(send.call_count,1)
                self.assertEqual(journal['trades'][0]['telegram_status'],'SENT')
                self.assertFalse(e.load_json(e.DASHBOARD_FILE)['signal']['active'])

    def test_failure_disables_signal_preserves_journal(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'dashboard.json'
            e.save_json(path,{'signal':{'active':True},'_journal_store':{'trades':[{'signal_id':'legacy'}]}})
            with patch.object(e,'DASHBOARD_FILE',path),patch.object(e,'run_live',side_effect=RuntimeError('STALE')):
                with self.assertRaises(SystemExit): e.main()
            result=e.load_json(path)
            self.assertEqual(result['engine']['status'],'ERROR')
            self.assertFalse(result['signal']['active'])
            self.assertEqual(len(result['_journal_store']['trades']),1)

    def test_backtest_cli_output_contract(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); csv=root/'history.csv'; bars().to_csv(csv,index=False)
            args=SimpleNamespace(csv=[str(csv)],timezone=None,spread_pips=1.,slippage_pips=.5,commission_pips=0.,news=None,split=None)
            with patch.multiple(e,DATA_DIR=root,BACKTEST_FILE=root/'backtest.json',DASHBOARD_FILE=root/'dashboard.json'):
                out=b.run(args)
                self.assertEqual(out['status'],'COMPLETED')
                self.assertEqual(out['news_mode'],'WITHOUT_NEWS_NOT_LIVE_EQUIVALENT')
                self.assertTrue((root/'backtest_full_sample_trades.json').exists())

    def test_backtest_reports_quality_and_walk_forward_diagnostics(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); csv=root/'history.csv'; bars(800).to_csv(csv,index=False)
            args=SimpleNamespace(csv=[str(csv)],timezone=None,spread_pips=1.,slippage_pips=.5,commission_pips=0.,news=None,split=None,walk_forward_folds=2)
            with patch.multiple(e,DATA_DIR=root,BACKTEST_FILE=root/'backtest.json',DASHBOARD_FILE=root/'dashboard.json'):
                out=b.run(args)
            self.assertEqual(out['data_quality']['rows'],800)
            self.assertEqual(out['data_quality']['gaps'],0)
            self.assertEqual(set(out['walk_forward']),{'fold_1','fold_2'})
            self.assertIn('by_session',out['results']['full_sample']['diagnostics'])

if __name__=='__main__': unittest.main()
