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
    return t.new_trade({'id':'test','direction':'BUY','opportunity':'BUY TEST','score':80},{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115},'2026-10-01T00:00:00Z',cost)
def row(at='2026-10-01T00:00:00Z',o=100,h=101,l=99,c=100):
    return pd.DataFrame([{'datetime':t.stamp(at),'open':o,'high':h,'low':l,'close':c}])
class V3Tests(unittest.TestCase):
    def test_original_entry_functions_identical(self):
        old=ast.parse((ROOT/'tests/fixtures/v3_original.py').read_text());new=ast.parse((ROOT/'engine/bosque_engine.py').read_text())
        funcs=lambda tree:{n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef)}
        a,z=funcs(old),funcs(new)
        for name in ['aggregate','find_swing_highs','find_swing_lows','analyze_structure','analyze_range','pd_zone','liquidity_analysis','momentum','candle_confirmation','calculate_atr','volatility_analysis','detect_m15_setup','m5_confirmation','calculate_score','create_trade_plan','make_signal_id','get_session']:
            self.assertEqual(a[name],z[name],name)
        assignments=lambda tree:{n.targets[0].id:ast.dump(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)}
        x,y=assignments(old),assignments(new)
        for name in ['MIN_SCORE','PIP_SIZE','MIN_RISK_PIPS','MAX_RISK_PIPS','TP1_PIPS','MIN_TP2_PIPS','TP3_PIPS','MIN_RR']:self.assertEqual(x[name],y[name])
    def test_adapter_matches_original_direct_decision(self):
        df=bars();at=df.iloc[-1].datetime+pd.Timedelta(minutes=5)
        h1=e.analyze_structure(e.aggregate(df,60));setup=e.detect_m15_setup(h1,e.aggregate(df,15),df);conf=e.m5_confirmation(df,setup['direction']);vol=e.volatility_analysis(df);sess=e.get_session(at.to_pydatetime().astimezone(e.MY_TZ));score=e.calculate_score(h1,setup,conf,sess,vol);plan=e.create_trade_plan(setup['direction'],df,score,vol)
        result=t.evaluate(df,at)
        self.assertEqual(result['plan'],plan);self.assertEqual(result['score'],score)
        self.assertEqual(result['active'],all([score>=e.MIN_SCORE,setup['valid'],conf['confirmed'],plan.get('valid',False),plan.get('rr_tp2',0)>=e.MIN_RR]))
    def test_sl_first_cost_once_and_no_duplicate_bar(self):
        x=trade(2);t.advance(x,row(h=112,l=94));self.assertEqual(x['result'],'LOSS');self.assertEqual(x['result_pips'],-52);self.assertEqual(x['exit_reason'],'SL_AMBIGUOUS');t.advance(x,row());self.assertEqual(x['bars_held'],1)
    def test_next_open_fill(self):
        x=trade();t.advance(x,row(o=102,h=110,l=101,c=109));self.assertEqual(x['entry'],102);self.assertEqual(x['result_pips'],80)
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
            j=t.load(e.ENGINE_DIR/'trade_journal.json');self.assertEqual(len(j['trades']),1);self.assertEqual(d['backtest']['status'],'AWAITING_V3_REPLAY');self.assertTrue((Path(tmp)/'paper_journal.csv').exists())
    def test_replay_resolves_signal_on_later_bar(self):
        df=bars(362);df.loc[359:,'open']=100;df.loc[359:,'close']=100;df.loc[359:,'high']=111;df.loc[359:,'low']=99
        ev={'active':True,'score':80,'plan':{'entry':100,'sl':95,'tp1':106,'tp2':110,'tp3':115},'opportunity':'BUY TEST','direction':'BUY','session':'ASIAN'}
        with patch.object(t,'evaluate',return_value=ev): result=b.replay(df,0)
        self.assertEqual(result['stats']['trades'],1);self.assertEqual(result['trades'][0]['filled_at'],df.iloc[360].datetime.isoformat())
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
    def test_news_history_coverage(self):
        cal={'coverage_start':'2026-10-01T00:00Z','coverage_end':'2026-10-02T00:00Z','events':[{'time':'2026-10-01T12:00Z','impact':'high','currency':'USD'}]}
        self.assertFalse(b.news_allowed(cal,t.stamp('2026-10-01T11:30Z')))
        with self.assertRaises(ValueError):b.news_allowed(cal,t.stamp('2026-10-03'))
if __name__=='__main__':unittest.main()
