import sys, unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'engine'))
import bosque_poi as p

class POITest(unittest.TestCase):
    def fixture(self, invalid=False, expired=False):
        h = pd.DataFrame({'datetime': pd.date_range('2026-10-20', periods=5, freq='h', tz='UTC'),
                          'open':[110]*5,'high':[112]*5,'low':[105,104,100,104,105],'close':[110]*5})
        start = '2026-10-21T06:00Z' if expired else '2026-10-20T05:00Z'
        m = pd.DataFrame({'datetime':pd.date_range(start, periods=2, freq='5min'),
                         'open':[101,101],'high':[103,103], 'low':[99 if invalid else 101,99],
                         'close':[99 if invalid else 102,102]})
        return h, m

    def check_liquidity(self, invalid=False, expired=False):
        h,m=self.fixture(invalid,expired)
        with patch.object(p.e,'aggregate',side_effect=lambda df,n: h if n==60 else df), patch.object(p.e,'breakout_retest',return_value=None):
            return p.annotate(m,m.iloc[-1].datetime+pd.Timedelta(minutes=5),'BUY')

    def test_sweep_reclaim_of_previously_confirmed_pivot(self):
        self.assertTrue(self.check_liquidity()['h1_liquidity'])

    def test_prior_close_through_invalidates(self):
        self.assertFalse(self.check_liquidity(invalid=True)['either'])

    def test_expiry(self):
        self.assertFalse(self.check_liquidity(expired=True)['either'])

    def test_unclosed_input_rejected(self):
        _,m=self.fixture()
        with self.assertRaises(ValueError): p.annotate(m,m.iloc[-1].datetime,'BUY')

    def test_wrong_direction_retest_and_no_mutation(self):
        _,m=self.fixture(); before=m.copy(deep=True)
        with patch.object(p.e,'breakout_retest',return_value={'direction':'SELL','level':100}):
            self.assertFalse(p.annotate(m,m.iloc[-1].datetime+pd.Timedelta(minutes=5),'BUY')['breakout_retest'])
        pd.testing.assert_frame_equal(before,m)

if __name__=='__main__': unittest.main()
