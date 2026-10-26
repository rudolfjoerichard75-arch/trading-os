import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from bosque_mt5 import load_m1

class MT5Tests(unittest.TestCase):
    def load(self, minutes, timezone='UTC'):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bars.csv'
            p.write_text('<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\n'+''.join(
                f'2026.10.20\t01:{m:02}:00\t100\t102\t99\t101\n' for m in minutes))
            return load_m1(p,timezone)
    def test_missing_minute_not_filled(self):
        df,audit=self.load([0,1,3,4,5,6,7,8,9])
        self.assertEqual(len(df),1)
        self.assertEqual(df.iloc[0].datetime.minute,5)
        self.assertEqual(audit['partial_m5_discarded'],1)
    def test_explicit_timezone_conversion(self):
        df,_=self.load(range(5),'Etc/GMT-3')
        self.assertEqual(df.iloc[0].datetime.isoformat(),'2026-10-19T22:00:00+00:00')
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError): self.load([0,1,1,3,4])
    def test_timezone_required(self):
        with self.assertRaises(ValueError): self.load(range(5),None)
    def test_unsorted_rejected(self):
        with self.assertRaises(ValueError): self.load([1,0,2,3,4])

class GateAuditTests(unittest.TestCase):
    def test_overlapping_rejections_and_reporting_window(self):
        from unittest.mock import patch
        import pandas as pd
        import bosque_backtest as b
        import bosque_tracking as t
        n=362
        df=pd.DataFrame({'datetime':pd.date_range('2026-10-01',periods=n,freq='5min',tz='UTC'),
                         'open':[100]*n,'high':[101]*n,'low':[99]*n,'close':[100]*n})
        ev={'active':False,'setup_valid':False,'direction':'WAIT','blocked_by':['setup','score']}
        with patch.object(t,'evaluate',return_value=ev):
            result=b.replay(df,start=df.iloc[361].datetime)
        self.assertEqual(result['gate_audit']['evaluated_scans'],2)
        self.assertEqual(result['gate_audit']['blocked_counts'],{'setup':2,'score':2})
        self.assertEqual(result['eligible_signals'],0)

if __name__=='__main__': unittest.main()
