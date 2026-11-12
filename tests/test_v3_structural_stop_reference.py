"""Offline tests for 20/60-session stop *reference* (no score impact)."""

import unittest

import numpy as np
import pandas as pd

from v3_structural_stop_reference import observe, valid_reference


def history(length=100):
    df = pd.DataFrame({
        'Low': np.full(length, 96.),
        'High': np.full(length, 110.),
        'Close': np.full(length, 105.),
        'Volume': np.full(length, 100000.),
    })
    return df


class StructuralStopReferenceTests(unittest.TestCase):
    def test_20_60_confirmed_swing_and_unscored_confluence(self):
        df = history()
        df.loc[84, 'Low'] = 91
        df.loc[84, 'Close'] = 96
        result = observe(df, asof='2026-11-10')
        self.assertEqual(result['low20'], 91)
        self.assertEqual(result['low60'], 91)
        self.assertEqual(result['confirmed_swing_low'], 91)
        self.assertEqual(result['reference_line'], 90.55)
        self.assertEqual(result['confluence_count'], 1,
                         'one price trough counted as 20d, 60d and swing is NOT independent')
        self.assertEqual(result['confidence'], 'single_support')
        self.assertEqual(result['score_impact'], 0)
        self.assertTrue(result['unscored_reference_only'])
        self.assertFalse(result['krx_regular_session_verified'])

    def test_actual_sma20_sma60_replace_low_only_stop_candidate_gap(self):
        # First 40 closes 100, next 19 110, final 120. The 20/60
        # simple moving averages are 110.5 and 103.5, not the low of 90.
        df = pd.DataFrame({
            'Low': np.full(80, 90.),
            'High': np.full(80, 130.),
            'Close': np.r_[np.full(60, 100.), np.full(19, 110.), 120.],
            'Volume': np.zeros(80),
        })
        row = observe(df, asof='2026-11-12')
        self.assertEqual(row['low20'], 90.)
        self.assertEqual(row['ma20'], 110.5)
        self.assertEqual(row['ma60'], 103.5)
        self.assertEqual(row['reference_line'], 109.95)
        self.assertEqual(row['basis'], ['20일 이동평균선'])
        self.assertEqual(row['confluence_count'], 1)
        self.assertEqual(row['score_impact'], 0)
        self.assertTrue(valid_reference(row, '2026-11-12', 120.))
        # Never manufacture a certified KRX session or stop probability.
        self.assertFalse(row['krx_regular_session_verified'])
        self.assertFalse(row['corporate_actions_verified'])

    def test_two_price_based_ma_levels_do_not_double_count_confirmation(self):
        df = pd.DataFrame({
            'Low': np.full(80, 90.),
            'High': np.full(80, 130.),
            'Close': np.r_[np.full(79, 105.), 120.],
            'Volume': np.zeros(80),
        })
        row = observe(df, asof='2026-11-12')
        self.assertEqual(row['ma20'], 105.75)
        self.assertEqual(row['ma60'], 105.25)
        self.assertEqual(row['confluence_count'], 1)
        self.assertEqual(row['confidence'], 'single_support')
        invalid = dict(row, ma20=float('nan'))
        self.assertFalse(valid_reference(invalid, '2026-11-12', 120.))


        df = history()
        df.loc[98, 'Low'] = 88
        df.loc[98, 'Close'] = 96
        ref = observe(df, asof='2026-11-10')
        self.assertIsNone(ref['confirmed_swing_low'])
        self.assertEqual(ref['low20'], 88)

    def test_20_bars_does_not_invent_60_day_support(self):
        ref = observe(history(20), asof='2026-11-10')
        self.assertEqual(ref['low20'], 96)
        self.assertIsNone(ref['low60'])
        self.assertIsNone(ref['confirmed_swing_low'])

    def test_volume_profile_is_daily_proxy_not_actual_execution(self):
        df = history(120)
        for i in range(80, 94):
            df.loc[i, ['High', 'Low', 'Close', 'Volume']] = [100, 93, 97, 300000]
        ref = observe(df, asof='2026-11-10')
        zone = ref['lower_volume_zone']
        self.assertIsNotNone(zone)
        self.assertEqual(zone['method'], 'daily_typical_price_volume_proxy')
        self.assertFalse(zone['exact_volume_at_price'])
        self.assertGreaterEqual(zone['observed_daily_candles'], 3)
        self.assertLess(zone['upper'], 105)
        self.assertGreaterEqual(ref['confluence_count'], 2)

    def test_zero_volume_and_missing_volume_not_invented(self):
        df = history()
        df['Volume'] = 0
        self.assertIsNone(observe(df, asof='2026-11-10')['lower_volume_zone'])
        self.assertIsNone(observe(df.drop(columns=['Volume']),
                                  asof='2026-11-10')['lower_volume_zone'])

    def test_invalid_price_returns_no_reference(self):
        df = history()
        df.loc[92, 'Low'] = float('nan')
        ref = observe(df, asof='2026-11-10')
        self.assertIsNone(ref['reference_line'])
        self.assertIn('무효', ref['warning'])

    def test_ancient_bad_candle_does_not_erase_recent_120_session_support(self):
        df = history(200)
        df.loc[5, 'Low'] = 120  # Invalid, but outside every stop horizon.
        result = observe(df, asof='2026-11-10')
        expected = observe(df.iloc[-120:], asof='2026-11-10')
        self.assertEqual(result, expected)
        self.assertEqual(result['validation_window_bars'], 120)
        self.assertIsNotNone(result['reference_line'])

    def test_bad_candle_inside_recent_120_still_fails_closed(self):
        df = history(200)
        df.loc[100, 'Low'] = 120
        result = observe(df, asof='2026-11-10')
        self.assertEqual(result['validation_window_bars'], 120)
        self.assertIsNone(result['reference_line'])
        self.assertIn('무효', result['warning'])

    def test_pattern_invalidation_requires_explicit_heuristic_flag(self):
        df = history()
        self.assertIsNone(observe(df, asof='2026-11-10')['pattern_invalidation'])
        ref = observe(df, asof='2026-11-10', patterns={'is_triangle': True})
        self.assertIsNotNone(ref['pattern_invalidation'])
        self.assertIn('근사', ref['pattern_invalidation']['basis'])


if __name__ == '__main__':
    unittest.main()
