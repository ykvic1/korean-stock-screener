"""Offline tests for 20/60-session stop *reference* (no score impact)."""

import unittest

import numpy as np
import pandas as pd

from v3_structural_stop_reference import observe


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

    def test_pivot_requires_three_subsequent_closed_candles(self):
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

    def test_pattern_invalidation_requires_explicit_heuristic_flag(self):
        df = history()
        self.assertIsNone(observe(df, asof='2026-11-10')['pattern_invalidation'])
        ref = observe(df, asof='2026-11-10', patterns={'is_triangle': True})
        self.assertIsNotNone(ref['pattern_invalidation'])
        self.assertIn('근사', ref['pattern_invalidation']['basis'])


if __name__ == '__main__':
    unittest.main()
