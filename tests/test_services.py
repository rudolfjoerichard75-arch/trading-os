import unittest, tempfile, sys, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import pandas as pd
import bosque_engine as e
import bosque_services as s
import bosque_auto_backtest as a
from test_engine import bars

class ServiceTests(unittest.TestCase):
    def test_telegram_readiness_is_not_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(e,'DATA_DIR',Path(directory)),patch.object(e,'http_json',return_value={'ok':True}) as api,patch.dict(e.os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test'}):
                r=s.telegram_health(pd.Timestamp('2026-09-28T12:00Z'))
                self.assertEqual(r['status'],'BOT_AND_CHAT_ACCESS_VERIFIED')
                self.assertFalse(r['delivery_tested']); self.assertEqual(api.call_count,2)
                self.assertNotIn('sendMessage',str(api.call_args_list))
                s.telegram_health(pd.Timestamp('2026-09-28T12:05Z')); self.assertEqual(api.call_count,2)

    def test_news_archive_is_observed_not_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); cache={'fetched_at':'2026-09-28T12:00Z','coverage_start':'2026-09-27T00:00Z','coverage_end':'2026-10-04T00:00Z','events':[]}
            with patch.multiple(e,DATA_DIR=root,NEWS_CACHE_FILE=root/'cache.json'):
                e.save_json(e.NEWS_CACHE_FILE,cache)
                s.archive_calendar(pd.Timestamp('2026-09-28T12:00Z')); s.archive_calendar(pd.Timestamp('2026-09-28T12:10Z'))
                saved=e.load_json(root/'news_archive/2026-09-27.json')
                self.assertEqual(len(saved['snapshots']),1)
                self.assertEqual(saved['first_observed_at'],cache['fetched_at'])

    def test_history_rejects_provider_error(self):
        with patch.dict(e.os.environ,{'TWELVEDATA_API_KEY':'test'}),patch.object(e,'http_json',return_value={'code':429}):
            with self.assertRaises(RuntimeError): a.download_history()

    def test_history_uses_one_request_and_closed_bars(self):
        frame=bars(900);frame['datetime']=frame.datetime.astype(str)
        with patch.dict(e.os.environ,{'TWELVEDATA_API_KEY':'test'}),patch.object(e,'http_json',return_value={'values':frame.to_dict('records')}) as api,patch.object(e,'now_utc',return_value=pd.Timestamp('2026-10-10T00:00Z')):
            result=a.download_history(); self.assertEqual(len(result),900);self.assertEqual(api.call_count,1)

    def test_history_paginates_explicit_long_range(self):
        def chunk(start):
            frame=bars(500)
            frame['datetime']=pd.date_range(start,periods=500,freq='5min',tz='UTC').astype(str)
            return {'values':frame.to_dict('records')}
        responses=[chunk('2026-09-29T06:25Z'),chunk('2026-09-27T12:45Z'),chunk('2026-09-25T00:00Z')]
        with patch.dict(e.os.environ,{'TWELVEDATA_API_KEY':'test','HISTORY_START_DATE':'2026-09-25T00:00:00Z','HISTORY_END_DATE':'2026-10-01T00:00:00Z','HISTORY_MAX_CHUNKS':'3','HISTORY_CHUNK_SIZE':'500'}),patch.object(e,'http_json',side_effect=responses) as api,patch.object(e,'now_utc',return_value=pd.Timestamp('2026-10-02T00:00Z')):
            result,meta=a.download_history(return_meta=True)
        self.assertEqual(api.call_count,3)
        self.assertTrue(meta['coverage_complete'])
        self.assertEqual(meta['requests'],3)
        self.assertEqual(len(result),1499)  # requested_end is an exclusive boundary

    def test_export_zero_trades_does_not_add_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(e,'REPO_DIR',Path(directory)):
                result=s.export_journal({'trades':[]})
                self.assertEqual(result['rows'],0)
                self.assertEqual(len((Path(directory)/'paper_journal.csv').read_text().splitlines()),1)
