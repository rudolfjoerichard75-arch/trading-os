import unittest
from unittest.mock import patch
import pandas as pd
from test_engine import bars, e, t


class EntryRefinementTests(unittest.TestCase):
    def test_partial_higher_timeframes_excluded(self):
        df = bars(17)  # through 01:20; only one complete hour
        self.assertEqual(len(e.aggregate(df, 60)), 1)
        self.assertEqual(len(e.aggregate(df, 15)), 5)
        df = bars(18)
        self.assertEqual(len(e.aggregate(df, 15)), 6)

    def test_missing_and_duplicate_bars(self):
        df = bars(24).drop(index=4)
        self.assertEqual(list(e.aggregate(df, 60).datetime.dt.hour), [1])
        with self.assertRaises(ValueError):
            e.aggregate(pd.concat([bars(12), bars(1)]), 60)

    def test_partial_bar_cannot_change_closed_hour(self):
        df = bars(17)
        expected = e.aggregate(df, 60)
        df.loc[16, ['high', 'close']] = 9999
        pd.testing.assert_frame_equal(expected, e.aggregate(df, 60))

    def retest_frames(self, direction='BUY'):
        m15 = pd.DataFrame({'datetime': pd.date_range('2026-10-21', periods=6, freq='15min', tz='UTC'),
                            'open': [99]*6, 'high': [100]*5+[102],
                            'low': [98]*6, 'close': [99]*5+[101]})
        m5 = pd.DataFrame({'datetime': pd.to_datetime(['2026-10-21T01:30Z'], utc=True),
                           'open': [100.1], 'high': [101], 'low': [99.9], 'close': [100.8]})
        structure = {'swing_high': 100, 'swing_low': 95}
        if direction == 'SELL':
            for frame in (m15, m5):
                old = frame.copy()
                for col in ('open', 'close'): frame[col] = 200-old[col]
                frame['high'] = 200-old['low']; frame['low'] = 200-old['high']
            structure = {'swing_high': 105, 'swing_low': 100}
        return m15, m5, structure

    def test_buy_and_sell_retests(self):
        for side in ('BUY', 'SELL'):
            m15, m5, structure = self.retest_frames(side)
            with patch.object(e, 'analyze_structure', return_value=structure):
                self.assertEqual(e.breakout_retest(m15, m5)['direction'], side)

    def test_breakout_without_touch_is_not_retest(self):
        m15, m5, structure = self.retest_frames()
        m5['low'] = 100.05
        with patch.object(e, 'analyze_structure', return_value=structure):
            self.assertIsNone(e.breakout_retest(m15, m5))

    def test_break_candle_cannot_retest_itself(self):
        m15, m5, structure = self.retest_frames()
        m5['datetime'] -= pd.Timedelta(minutes=5)
        with patch.object(e, 'analyze_structure', return_value=structure):
            self.assertIsNone(e.breakout_retest(m15, m5))

    def test_failed_breakout_not_revived(self):
        m15, m5, structure = self.retest_frames()
        failed = m5.copy(); failed['close'] = 99
        m5['datetime'] += pd.Timedelta(minutes=5)
        with patch.object(e, 'analyze_structure', return_value=structure):
            self.assertIsNone(e.breakout_retest(m15, pd.concat([failed, m5])))

    def test_expired_breakout_not_reused(self):
        m15, m5, structure = self.retest_frames()
        extra = pd.concat([m15.tail(1)]*4, ignore_index=True)
        extra['datetime'] = pd.date_range('2026-10-21T01:30Z', periods=4, freq='15min')
        m15 = pd.concat([m15, extra], ignore_index=True)
        m5['datetime'] = pd.to_datetime(['2026-10-21T02:30Z'])
        with patch.object(e, 'analyze_structure', return_value=structure):
            self.assertIsNone(e.breakout_retest(m15, m5))

    def test_setup_requires_retest_when_only_bos_exists(self):
        m15, m5, _ = self.retest_frames()
        structure = {'bos': 'BULLISH BOS', 'direction': 'BULLISH'}
        with patch.object(e, 'analyze_structure', return_value=structure), \
             patch.object(e, 'pd_zone', return_value={'zone': 'PREMIUM'}), \
             patch.object(e, 'liquidity_analysis', return_value={'sell_side_sweep':False, 'buy_side_sweep':False}), \
             patch.object(e, 'analyze_range', return_value={'is_range':False}), \
             patch.object(e, 'breakout_retest', return_value=None):
            self.assertFalse(e.detect_m15_setup({'direction':'BULLISH'}, m15, m5)['valid'])

    def test_opposite_evidence_does_not_earn_points(self):
        h1 = {'direction': 'BEARISH', 'bos': 'BEARISH BOS'}
        setup = {'direction': 'BUY', 'valid': True, 'liquidity': {'buy_side_sweep': True},
                 'structure': {'bos': 'BEARISH BOS'}, 'pd': {'zone': 'PREMIUM'}}
        conf = {'confirmed': True, 'bos': 'BEARISH BOS', 'candle': True}
        self.assertEqual(e.calculate_score(h1, setup, conf, 'ASIAN', {'condition': 'NORMAL'}), 40)

    def test_aligned_evidence_can_reach_threshold(self):
        for side, trend, sweep, zone in [('BUY','BULLISH','sell_side_sweep','DISCOUNT'),
                                        ('SELL','BEARISH','buy_side_sweep','PREMIUM')]:
            h1 = {'direction': trend, 'bos': trend+' BOS'}
            setup = {'direction': side, 'valid': True, 'liquidity': {sweep: True},
                     'structure': {'bos': trend+' BOS'}, 'pd': {'zone': zone}}
            conf = {'confirmed': True, 'bos': trend+' BOS', 'candle': True}
            self.assertEqual(e.calculate_score(h1, setup, conf, 'LONDON', {'condition':'NORMAL'}), 100)

    def test_configuration_identifies_new_logic(self):
        self.assertEqual(t.configuration()['entry_logic_version'], e.ENTRY_LOGIC_VERSION)
        self.assertEqual(t.configuration()['min_risk_pips'], 20)
        self.assertEqual(t.configuration()['max_risk_pips'], 80)
