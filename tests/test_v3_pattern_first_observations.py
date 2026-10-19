"""Synthetic future-free regression for a non-scoring pattern/RSI observation."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

import v3_pattern_first_observations as mod


def make_df(close, volume=None, high=None, low=None):
    close = np.asarray(close, dtype=float)
    n = len(close)
    volume = np.ones(n) * 100 if volume is None else np.asarray(volume, dtype=float)
    hi = close * 1.01 if high is None else np.asarray(high, dtype=float)
    lo = close * .99 if low is None else np.asarray(low, dtype=float)
    return pd.DataFrame({"Open": close, "High": hi, "Low": lo, "Close": close,
                         "Volume": volume},
                        index=pd.bdate_range("2025-01-01", periods=n))


class PatternFirstObservations(unittest.TestCase):
    def test_bull_flag_requires_pole_shallow_consolidation_and_lower_flag_volume(self):
        c = np.ones(130) * 100
        c[-36:-12] = np.linspace(100, 132, 24)
        c[-12:-1] = np.linspace(130, 126, 11)
        c[-1] = 128
        v = np.ones(130) * 100
        v[-30:-13] = 330
        v[-12:] = 90
        d = make_df(c, v)
        x = mod.observe(d)
        flag = next((p for p in x["patterns"] if p["type"] == "bull_flag"), None)
        self.assertIsNotNone(flag)
        self.assertEqual(flag["stage"], "forming")
        self.assertFalse(x["buy_signal"])
        self.assertFalse(x["scoring_allowed"])
        self.assertFalse(x["official_regular_session_verified"])
        break_df = d.copy()
        break_df.iloc[-1, break_df.columns.get_loc("Close")] = 135
        break_df.iloc[-1, break_df.columns.get_loc("Open")] = 135
        break_df.iloc[-1, break_df.columns.get_loc("High")] = 137
        break_df.iloc[-1, break_df.columns.get_loc("Volume")] = 210
        breaker = mod.observe(break_df)
        self.assertEqual(next(p for p in breaker["patterns"]
                              if p["type"] == "bull_flag")["stage"],
                         "breakout_observed")
        # Not a flag when the original pole never rose >=16%.
        flat = make_df(np.ones(130) * 100)
        self.assertFalse(any(p["type"] == "bull_flag" for p in mod.observe(flat)["patterns"]))
        # No prior pole/flag contrast in turnover must reject the flag.
        equal_volume = make_df(c)
        self.assertFalse(any(p["type"] == "bull_flag"
                             for p in mod.observe(equal_volume)["patterns"]))

    def test_two_confirmed_bottoms_with_neckline_and_no_future_peeking(self):
        n = 130
        c = np.ones(n) * 105
        hi = np.ones(n) * 108
        lo = np.ones(n) * 101
        a, b = n - 38, n - 18
        c[a], c[b] = 94, 95
        hi[a], hi[b] = 97, 98
        lo[a], lo[b] = 90, 91
        hi[n - 28] = 110
        d = make_df(c, high=hi, low=lo)
        result = mod.observe(d)
        double = next((p for p in result["patterns"] if p["type"] == "double_bottom"), None)
        self.assertIsNotNone(double)
        self.assertEqual(double["stage"], "forming")
        self.assertEqual(double["reference_neckline"], 110)
        # A second minimum cannot be confirmed without subsequent candles.
        first_half = mod.observe(d.iloc[:b+1])
        self.assertFalse(any(p["type"] == "double_bottom"
                             for p in first_half["patterns"]))

    def test_rsi_below_30_to_above_30_is_recovery_not_obv_or_buy(self):
        c = np.r_[np.ones(100) * 100,
                  100 - np.arange(1, 16) * 1.3,
                  100 - 15 * 1.3 + np.arange(1, 6) * 1.6]
        d = make_df(c)
        out = mod.observe(d)
        self.assertGreater(out["rsi14"], 30)
        self.assertLess(out["rsi14"], 60)
        self.assertTrue(out["rsi30_cross_recent"])
        self.assertFalse(out["buy_signal"])
        self.assertFalse(out["scoring_allowed"])
        flat = mod.observe(make_df(np.ones(120) * 100))
        self.assertFalse(flat["rsi30_cross_recent"])

    def test_recent_divergence_only_after_confirmed_pivot(self):
        d = make_df(np.linspace(100, 97, 120))
        yes = np.zeros(120, dtype=bool)
        yes[-5] = True
        with patch.object(mod, "divergence", return_value=(yes, np.zeros(120, dtype=bool))):
            self.assertTrue(mod.observe(d)["rsi_bullish_divergence_recent"])
        stale = np.zeros(120, dtype=bool)
        stale[-25] = True
        with patch.object(mod, "divergence", return_value=(stale, np.zeros(120, dtype=bool))):
            self.assertFalse(mod.observe(d)["rsi_bullish_divergence_recent"])

    def test_completed_week_only_never_uses_current_partial_bar(self):
        c = np.ones(130) * 100
        c[-36:-12] = np.linspace(100, 132, 24)
        c[-12:-1] = np.linspace(130, 126, 11)
        c[-1] = 128
        v = np.ones(130) * 100
        v[-30:-13] = 330
        v[-12:] = 90
        weekly_source = make_df(c, v)
        weekly_source.index = pd.date_range("2023-01-06", periods=130, freq="W-FRI")
        base = mod.observe_completed_weekly(weekly_source)
        self.assertEqual(base["status"], "heuristic_unverified")
        self.assertEqual(base["timeframe"], "weekly")
        self.assertTrue(base["completed_week_only"])
        self.assertEqual(base["asof"], weekly_source.index[-1].date().isoformat())
        self.assertTrue(any(p["type"] == "bull_flag" for p in base["patterns"]))
        self.assertFalse(base["scoring_allowed"])
        self.assertFalse(base["buy_signal"])

        # Very bullish high-volume next Tuesday cannot trigger a new
        # incomplete Friday bar; last observed Friday signals must be equal.
        tuesday = weekly_source.index[-1] + pd.Timedelta(days=4)
        appended = weekly_source.copy()
        appended.loc[tuesday] = [150, 182, 148, 180, 100000]
        held = mod.observe_completed_weekly(appended)
        self.assertEqual(held, base,
                         "unfinished weekly OHLCV must not contaminate signals")
        new_friday = weekly_source.index[-1] + pd.Timedelta(days=7)
        appended.loc[new_friday] = [132, 140, 127, 135, 220]
        completed = mod.observe_completed_weekly(appended)
        self.assertEqual(completed["asof"], new_friday.date().isoformat())
        self.assertEqual(completed["timeframe"], "weekly")

    def test_completed_week_fail_closed_invalid_dates_and_daily_bar(self):
        c = np.ones(110)*100
        d = make_df(c)
        d.index = pd.date_range("2023-01-06", periods=len(d), freq="W-FRI")
        duplicate = d.copy()
        ix = duplicate.index.to_list()
        ix[-1] = ix[-2]
        duplicate.index = pd.DatetimeIndex(ix)
        self.assertEqual(mod.observe_completed_weekly(duplicate)["status"],
                         "insufficient")
        malformed = d.copy()
        malformed.iloc[-1, malformed.columns.get_loc("High")] = 1
        self.assertEqual(mod.observe_completed_weekly(malformed)["status"],
                         "insufficient")
        short = d.iloc[-80:]
        self.assertEqual(mod.observe_completed_weekly(short)["status"],
                         "insufficient")

    def test_missing_invalid_ohlcv_remains_unconfirmed(self):
        self.assertEqual(mod.observe(make_df(np.ones(70) * 100))["status"], "insufficient")
        invalid = make_df(np.ones(120) * 100)
        invalid.iloc[-1, invalid.columns.get_loc("Volume")] = -3
        self.assertEqual(mod.observe(invalid)["status"], "insufficient")
        self.assertFalse(mod.observe(invalid)["patterns"])


if __name__ == "__main__":
    unittest.main()
