import ast, importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import pandas as pd
import bosque_engine as e
import bosque_tracking as t
import bosque_backtest as b

ROOT=Path(__file__).resolve().parents[1]
def bars(n=500):
    import numpy as np
    c=4100+np.sin(np.arange(n)*.13)*7+np.arange(n)*.03
    return pd.DataFrame({'datetime':pd.date_range('2026-10-01',periods=n,freq='5min',tz='UTC'),'open':c-.2,'high':c+.5,'low':c-.5,'close':c})
def trade(cost=0):
    item = t.new_trade({'id':'test','direction':'BUY','opportunity':'BUY TEST','score':80},{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115},'2026-10-01T00:00:00Z',cost)
    # Existing regression fixtures exercise the historical immediate-open model.
    item['execution_model']='GUARDED_NEXT_M5_OPEN_TP2_V2'
    return item
def row(at='2026-10-01T00:00:00Z',o=100,h=101,l=99,c=100):
    return pd.DataFrame([{'datetime':t.stamp(at),'open':o,'high':h,'low':l,'close':c}])
class V3Tests(unittest.TestCase):
    def test_unchanged_v3_functions_identical(self):
        old=ast.parse((ROOT/'tests/fixtures/v3_original.py').read_text());new=ast.parse((ROOT/'engine/bosque_engine.py').read_text())
        funcs=lambda tree:{n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef)}
        a,z=funcs(old),funcs(new)
        for name in ['find_swing_highs','find_swing_lows','analyze_structure','pd_zone','liquidity_analysis','momentum','candle_confirmation','calculate_atr','volatility_analysis','m5_confirmation','create_trade_plan','make_signal_id','get_session']:
            self.assertEqual(a[name],z[name],name)
        assignments=lambda tree:{n.targets[0].id:ast.dump(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)}
        x,y=assignments(old),assignments(new)
        for name in ['MIN_SCORE','PIP_SIZE','MAX_RISK_PIPS','TP1_PIPS','MIN_TP2_PIPS','TP3_PIPS','MIN_RR']:self.assertEqual(x[name],y[name])
    def test_requested_risk_range_boundaries(self):
        # User-approved parameter change; retain the original historical fixture.
        self.assertEqual(e.MIN_RISK_PIPS,20)
        self.assertEqual(e.MAX_RISK_PIPS,80)
        frame=pd.DataFrame([{'close':100.0}])
        for risk,valid in [(19.9,False),(20.0,True),(24.0,True),(80.0,True),(80.1,False)]:
            with self.subTest(risk=risk),patch.object(e,'analyze_structure',return_value={'swing_low':100-risk*e.PIP_SIZE+.20,'swing_high':105}):
                plan=e.create_trade_plan('BUY',frame,80,{'atr':None})
                self.assertEqual(plan['valid'],valid)
    def test_adapter_matches_original_direct_decision(self):
        df=bars();at=df.iloc[-1].datetime+pd.Timedelta(minutes=5)
        h1=e.analyze_structure(e.aggregate(df,60));setup=e.detect_m15_setup(h1,e.aggregate(df,15),df);conf=e.m5_confirmation(df,setup['direction']);vol=e.volatility_analysis(df);sess=e.get_session(at.to_pydatetime().astimezone(e.MY_TZ));score=e.calculate_score(h1,setup,conf,sess,vol);plan=e.create_trade_plan(setup['direction'],df,score,vol)
        with patch('bosque_setups.select', side_effect=AssertionError('multi-setup selector must not run')):
            result=t.evaluate(df,at)
        self.assertEqual(result['plan'],plan);self.assertEqual(result['score'],score)
        self.assertEqual(result['opportunity'], setup['opportunity'])
        self.assertEqual(result['active'],all([score>=e.MIN_SCORE,setup['valid'],conf['confirmed'],plan.get('valid',False),plan.get('rr_tp2',0)>=e.MIN_RR]))
    def test_sl_first_cost_once_and_no_duplicate_bar(self):
        x=trade(2);t.advance(x,row(h=112,l=94));self.assertEqual(x['result'],'LOSS');self.assertEqual(x['result_pips'],-52);self.assertEqual(x['exit_reason'],'SL_AMBIGUOUS');t.advance(x,row());self.assertEqual(x['bars_held'],1)
    def test_next_open_fill(self):
        x=trade();x['execution_model']='NEXT_M5_OPEN_TP2_V1';t.advance(x,row(o=102,h=110,l=101,c=109));self.assertEqual(x['entry'],102);self.assertEqual(x['result_pips'],80)
    def test_time_exit_signed_loss(self):
        x=trade();df=pd.concat([row(at=pd.Timestamp('2026-10-01',tz='UTC')+pd.Timedelta(minutes=i*5),c=99) for i in range(72)],ignore_index=True);t.advance(x,df);self.assertEqual(x['result'],'LOSS');self.assertEqual(x['result_pips'],-10)
    def test_gap_unresolved(self):
        x=trade();t.advance(x,row(at='2026-10-01T00:10:00Z'));self.assertEqual(x['result'],'DATA_GAP');self.assertEqual(t.stats([x])['trades'],0)
    def test_future_bars_only_and_restart(self):
        x=trade();t.advance(x,row(at='2026-09-30T23:55:00Z'));self.assertEqual(x['result'],'PENDING');t.advance(x,row());y=json.loads(json.dumps(x));t.advance(y,row());self.assertEqual(y['bars_held'],1)
    def test_sell_and_gap_stop(self):
        x=trade();x.update(direction='SELL',sl=105,tp1=94,tp2=90,tp3=85);t.advance(x,row(h=101,l=89));self.assertEqual(x['result_pips'],100)
        x=trade();t.advance(x,row());t.advance(x,row(at='2026-10-01T00:05:00Z',o=93,h=94,l=92,c=93));self.assertEqual(x['result_pips'],-70)
    def test_live_journal_and_legacy_results(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'ENGINE_DIR',Path(tmp)/'engine'),patch.object(e,'REPO_DIR',Path(tmp)):
            e.ENGINE_DIR.mkdir();t.save(e.ENGINE_DIR/'backtest_results.json',{'version':'V5.3 CANDIDATE','status':'COMPLETED'})
            at='2026-10-02T12:00:01Z';d={'engine':{'timestamp':at},'signal':{'active':True,'id':'test','direction':'BUY','score':80},'plan':{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115},'session':'ASIAN','sop':{},'filters':{'session':False},'news':{'filter':'PASS'}}
            t.sync(d,bars(),True);t.sync(d,bars(),None)
            j=t.load(e.ENGINE_DIR/'trade_journal.json');self.assertEqual(len(j['trades']),1);self.assertEqual(d['backtest']['status'],'AWAITING_INTRABAR_REPLAY');self.assertTrue((Path(tmp)/'paper_journal.csv').exists())
    def test_replay_resolves_signal_on_later_bar(self):
        df=bars(362);df.loc[359:,'open']=100;df.loc[359:,'close']=100;df.loc[359:,'high']=111;df.loc[359:,'low']=99
        ev={'active':True,'score':80,'plan':{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115},'opportunity':'BUY TEST','direction':'BUY','session':'ASIAN'}
        with patch.object(t,'evaluate',return_value=ev): result=b.replay(df,0)
        self.assertEqual(result['stats']['trades'],1);self.assertEqual(result['trades'][0]['filled_at'],df.iloc[361].datetime.isoformat())
    def test_malformed_news_fails_closed(self):
        from unittest.mock import Mock
        response=Mock(); response.json.return_value={'message':'unavailable'}
        with patch.object(e.requests,'get',return_value=response):
            result=e.fetch_news_calendar()
        self.assertEqual(result['filter'],'BLOCK');self.assertEqual(result['status'],'ERROR')
    def test_main_persists_support_outputs(self):
        import contextlib,io
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'engine';directory.mkdir()
            with patch.object(e,'ENGINE_DIR',directory),patch.object(e,'REPO_DIR',root),patch.object(e,'DASHBOARD_FILE',root/'dashboard_data.json'),patch.object(e,'STATE_FILE',directory/'state.json'),patch.object(e,'fetch_m5',return_value=bars()),patch.object(e,'fetch_news_calendar',return_value={'filter':'BLOCK','status':'ERROR'}),patch.object(e,'send_telegram') as send,contextlib.redirect_stdout(io.StringIO()):
                e.main()
                result=t.load(root/'dashboard_data.json')
                self.assertFalse(result['signal']['active']);self.assertEqual(result['journal']['status'],'ACTIVE');self.assertTrue((directory/'forward_test.json').exists());send.assert_not_called()
    def test_news_country_is_not_inferred_from_gdp(self):
        self.assertFalse(e.is_us_event({'title':'UK GDP October 2026'}))
        self.assertFalse(e.is_us_event({'title':'GDP report'}))
        self.assertFalse(e.is_us_event({'title':'Canada CPI','currency':'CAD'}))
        self.assertTrue(e.is_us_event({'title':'US CPI October 2026'}))
        self.assertTrue(e.is_us_event({'title':'FOMC rate decision'}))
        self.assertTrue(e.is_us_event({'title':'Retail sales','currency':'USD'}))
    def test_news_history_coverage(self):
        cal={'coverage_start':'2026-10-01T00:00Z','coverage_end':'2026-10-02T00:00Z','events':[{'time':'2026-10-01T12:00Z','impact':'high','currency':'USD'}]}
        self.assertFalse(b.news_allowed(cal,t.stamp('2026-10-01T11:30Z')))
        with self.assertRaises(ValueError):b.news_allowed(cal,t.stamp('2026-10-03'))
class DiagnosticsTests(unittest.TestCase):
    def test_risk_reason_preserves_entry_decision(self):
        import copy
        d={'opportunity':{'direction':'BUY','score':80},'plan':{'valid':False,'reason':'Risk 143.0 pips outside 20-80'},'filters':{'setup':True,'score':True,'m5_confirmation':True,'risk':False,'rr':False,'news':True},'signal':{'active':False}}
        old=copy.deepcopy(d);t.add_diagnostics(d)
        for key in old:self.assertEqual(old[key],d[key],key)
        self.assertEqual(d['gate_reasons'],{'risk':'SL 143.0 pips melebihi had 80 pips','rr':'Belum dikira: risk tidak lulus'})
        self.assertEqual(d['diagnostics']['risk_distance_pips'],143.)
        d['plan']['reason']='Risk 19.0 pips outside 20-80';t.add_diagnostics(d)
        self.assertIn('bawah minimum 20',d['gate_reasons']['risk'])
    def test_each_gate_has_reason_only_when_waiting(self):
        d={'opportunity':{'score':50},'plan':{},'filters':{},'news':{'filter':'BLOCK','status':'ERROR'}}
        t.add_diagnostics(d)
        self.assertEqual(set(d['gate_reasons']),{'setup','score','m5_confirmation','risk','rr','news'})
        self.assertIn('50/100 < minimum 70',d['gate_reasons']['score'])
        self.assertEqual(d['gate_reasons']['risk'],'Belum dikira: tiada arah setup')
        self.assertIn('gagal disahkan',d['gate_reasons']['news'])
        d['filters']={key:True for key in d['gate_reasons']};t.add_diagnostics(d)
        self.assertEqual(d['gate_reasons'],{});self.assertEqual(d['diagnostics']['blocking_reasons'],[])
    def test_missing_swing_and_low_rr(self):
        d={'opportunity':{'direction':'SELL','score':80},'plan':{},'filters':{}}
        t.add_diagnostics(d);self.assertEqual(d['gate_reasons']['risk'],'Swing M5 untuk menentukan SL belum tersedia')
        d['filters']['risk']=True;d['plan']['rr_tp2']=1.5;t.add_diagnostics(d)
        self.assertEqual(d['gate_reasons']['rr'],'RR TP2 1:1.5 di bawah 1:2')

class NewsGateTests(unittest.TestCase):
    now=pd.Timestamp('2026-10-15T12:30:00Z').to_pydatetime()
    def response(self,minutes=15,currency='USD',impact='high',invalid_time=False):
        from unittest.mock import Mock
        event={'title':'US test event' if currency=='USD' else 'UK GDP','currency':currency,'impact':impact,'time_utc':'bad-time' if invalid_time else (self.now+pd.Timedelta(minutes=minutes)).isoformat()}
        response=Mock();response.json.return_value={'count':1,'events':[event]}
        return response
    def test_news_block_boundaries_and_metadata(self):
        for minutes,wanted in [(-31,'PASS'),(-30,'BLOCK'),(-15,'BLOCK'),(0,'BLOCK'),(15,'BLOCK'),(30,'BLOCK'),(31,'PASS')]:
            with self.subTest(minutes=minutes),patch.object(e,'now_my',return_value=self.now),patch.object(e.requests,'get',return_value=self.response(minutes)):
                result=e.fetch_news_calendar()
                self.assertEqual(result['filter'],wanted)
                self.assertEqual(result['evidence']['events_received'],1)
                self.assertEqual(result['evidence']['usd_high_events'],1)
                self.assertEqual(result['evidence']['checked_at'],self.now.isoformat())
                self.assertEqual(result['block_before_minutes'],30)
                self.assertEqual(result['block_after_minutes'],30)
    def test_non_us_and_low_impact_do_not_block(self):
        for currency,impact in [('GBP','high'),('USD','low')]:
            with self.subTest(currency=currency,impact=impact),patch.object(e,'now_my',return_value=self.now),patch.object(e.requests,'get',return_value=self.response(currency=currency,impact=impact)):
                result=e.fetch_news_calendar();self.assertEqual(result['filter'],'PASS');self.assertEqual(result['evidence']['usd_high_events'],0)
    def test_source_timeout_blocks(self):
        with patch.object(e.requests,'get',side_effect=e.requests.Timeout('test timeout')):
            result=e.fetch_news_calendar()
        self.assertEqual(result['filter'],'BLOCK');self.assertEqual(result['status'],'ERROR');self.assertIn('gagal disahkan',result['reason'])
    def test_unreadable_event_time_is_exposed(self):
        with patch.object(e,'now_my',return_value=self.now),patch.object(e.requests,'get',return_value=self.response(invalid_time=True)):
            result=e.fetch_news_calendar()
        self.assertEqual(result['evidence']['skipped_usd_times'],1)
        self.assertEqual(result['filter'],'BLOCK')
    def test_news_gate_controls_telegram_with_other_gates_passed(self):
        import contextlib,io
        plan={'valid':True,'direction':'BUY','entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115,'risk_pips':50,'tp1_pips':60,'tp2_pips':100,'tp3_pips':150,'rr_tp2':2}
        setup={'direction':'BUY','opportunity':'BUY BREAKOUT RETEST','valid':True}
        for response,expected in [(self.response(15),False),(self.response(-15),False),(self.response(45),True),(e.requests.Timeout('offline'),False)]:
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
                root=Path(tmp);directory=root/'engine';directory.mkdir()
                patches={'ENGINE_DIR':directory,'REPO_DIR':root,'DASHBOARD_FILE':root/'dashboard_data.json','STATE_FILE':directory/'state.json'}
                for key,value in patches.items():stack.enter_context(patch.object(e,key,value))
                stack.enter_context(patch.object(e,'now_my',return_value=self.now))
                fresh=bars();fresh['datetime']=pd.date_range(end=pd.Timestamp(self.now).floor('5min'),periods=len(fresh),freq='5min')
                stack.enter_context(patch.object(e,'FOCUS_ENABLED',False))
                stack.enter_context(patch.object(e,'fetch_m5',return_value=fresh))
                stack.enter_context(patch.object(e,'detect_m15_setup',return_value=setup))
                stack.enter_context(patch.object(e,'m5_confirmation',return_value={'confirmed':True,'reason':'M5 bullish BOS'}))
                stack.enter_context(patch.object(e,'calculate_score',return_value=80))
                stack.enter_context(patch.object(e,'create_trade_plan',return_value=plan))
                stack.enter_context(patch.object(e.requests,'get',**({'side_effect':response} if isinstance(response,Exception) else {'return_value':response})))
                send=stack.enter_context(patch.object(e,'send_telegram',return_value=False))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                e.main();result=t.load(root/'dashboard_data.json')
                for gate in ['setup','score','m5_confirmation','risk','rr']:self.assertTrue(result['filters'][gate],gate)
                self.assertEqual(result['signal']['active'],expected)
                self.assertEqual(send.call_count,int(expected))
                if not expected:self.assertEqual(set(result['gate_reasons']),{'news'})

if __name__=='__main__':unittest.main()
