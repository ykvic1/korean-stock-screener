"""Synthetic OHLCV to technical/IAS-obv/benchmark features without future bars."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import json
import unittest

from v3_price_feature_adapter import (
    PriceFeatureError, extract_price_features, SESSION
)
from v3_research_factor_engine import FIELD_SPECS


def samples(n=720, change=0.12):
    d = date(2022, 1, 3)
    rows, market, industry = [], [], []
    while len(rows) < n:
        if d.weekday() < 5:
            i = len(rows)
            c = 100 + change*i
            o = c - 0.1
            rows.append({
                "date": d.isoformat(), "session": SESSION,
                "open": o, "high": c + 1, "low": o - 1,
                "close": c, "volume": 1000 + i,
            })
            market.append({"date": d.isoformat(), "close": 1000 + 0.03*i})
            industry.append({"date": d.isoformat(), "close": 800 + 0.1*i})
        d += timedelta(days=1)
    return rows, market, industry


def calculate(bars, market=None, industry=None):
    return extract_price_features(
        bars, asof=bars[-1]["date"],
        market_index=market, industry_index=industry)


class TechnicalOHLCVTests(unittest.TestCase):
    def test_complete_technical_and_relative_strength_features(self):
        bars, market, industry = samples()
        r = calculate(bars, market, industry)
        f = r["features"]
        self.assertEqual(r["observed_rows"], 720)
        self.assertTrue(r["partial_week_included"])
        self.assertEqual(f["ias_obv_change_60_pct"], 100.0)
        self.assertGreater(f["technical_volume_breakout_ratio"], 1)
        self.assertGreater(f["momentum_stock_relative_strength_20_pct"], 0)
        self.assertGreater(f["momentum_stock_relative_strength_60_pct"], 0)
        self.assertGreater(f["industry_relative_strength_60_pct"], 0)
        self.assertEqual(f["momentum_positive_days_20_ratio"], 1)
        self.assertIn("technical_weekly_ma120_reclaim_strength", r["available_features"])
        self.assertIn("technical_weekly_rsi_divergence_strength", r["available_features"])
        self.assertIn("technical_pattern_confirmation_strength", r["available_features"])
        self.assertTrue(all(k in FIELD_SPECS for k in f))
        # NumPy scalars are not accepted by the strict v3 normalization
        # boundary; cross-module API uses plain JSON-compatible floats.
        self.assertTrue(all(type(v) in (int, float) for v in f.values() if v is not None))
        self.assertFalse(r["source_regular_only_volume_verified"])
        self.assertFalse(r["corporate_action_history_verified"])
        self.assertFalse(r["safe_to_feed_investable_v3_score"])
        self.assertFalse(r["confirmed_buy"])
        self.assertIsNone(r["pattern_success_probability"])

    def test_partial_history_explicitly_leaves_weekly_unknown(self):
        bars, market, industry = samples(100)
        r = calculate(bars, market, industry)
        self.assertIsNone(r["features"]["technical_weekly_ma120_reclaim_strength"])
        self.assertIsNone(r["features"]["technical_weekly_rsi_divergence_strength"])
        self.assertIsNotNone(r["features"]["technical_pattern_confirmation_strength"])
        self.assertIsNotNone(r["features"]["ias_obv_change_60_pct"])
        self.assertNotIn("technical_weekly_rsi_divergence_strength", r["available_features"])

    def test_no_future_friday_or_later_price_can_change_prior_signal(self):
        full, market, industry = samples(720)
        pre = calculate(full[:691], market[:691], industry[:691])
        after = calculate(full, market, industry)
        original_prefix = copy.deepcopy(full[:691])
        market_prefix = copy.deepcopy(market[:691])
        industry_prefix = copy.deepcopy(industry[:691])
        for b in full[691:]:
            b["close"] *= 3
            b["open"] = b["close"]
            b["high"] = b["close"]+1
            b["low"] = b["close"]-1
        for m in market[691:]:
            m["close"] *= 2
        self.assertEqual(pre, calculate(original_prefix, market_prefix, industry_prefix))
        self.assertEqual(pre["asof"], original_prefix[-1]["date"])
        self.assertNotEqual(pre["asof"], after["asof"])

    def test_benchmark_calendar_missing_observations_yields_null_not_interpolation(self):
        bars, market, industry = samples(710)
        market.pop(-40)
        result = calculate(bars, market, industry)
        self.assertIsNone(result["features"]["momentum_stock_relative_strength_60_pct"])
        self.assertIsNotNone(result["features"]["momentum_stock_relative_strength_20_pct"])
        self.assertIsNone(result["features"]["industry_relative_strength_60_pct"])
        no_benchmark = calculate(bars)
        self.assertIsNone(no_benchmark["features"]["momentum_stock_relative_strength_20_pct"])
        self.assertIsNone(no_benchmark["features"]["industry_relative_strength_60_pct"])
        self.assertIsNotNone(no_benchmark["features"]["momentum_positive_days_20_ratio"])

    def test_obv_is_volume_normalized_not_unstable_percent_change_of_obv_baseline(self):
        bars, _, _ = samples(150, change=-0.12)
        r = calculate(bars)
        self.assertEqual(r["features"]["ias_obv_change_60_pct"], -100)
        for bar in bars[-60:]:
            bar["volume"] = 0
        r = calculate(bars)
        self.assertIsNone(r["features"]["ias_obv_change_60_pct"])
        self.assertIsNone(r["features"]["technical_volume_breakout_ratio"])

    def test_overheated_chase_flags_not_auto_buy(self):
        bars, m, i = samples()
        last = bars[-1]
        last.update(
            open=last["close"]*2, low=last["close"]*2-1,
            high=last["close"]*2+2, close=last["close"]*2+1,
            volume=20000,
        )
        r = calculate(bars, m, i)
        self.assertIn("four_week_price_extension_review", r["price_extension_caution"])
        self.assertIn("twelve_week_price_extension_review", r["price_extension_caution"])
        self.assertIn("exceptional_single_day_volume_review", r["price_extension_caution"])
        self.assertFalse(r["confirmed_buy"])

    def test_malformed_or_stale_input_blocks(self):
        examples = {
            "invalid_high": lambda b: b[3].update(high=0),
            "negative_volume": lambda b: b[3].update(volume=-5),
            "bool_volume": lambda b: b[3].update(volume=True),
            "duplicate_day": lambda b: b[3].update(date=b[2]["date"]),
            "future_date": lambda b: b[4].update(date="2027-01-01"),
            "session": lambda b: b[3].update(session="AFTER_HOURS"),
            "bad_ohlc_range": lambda b: b[3].update(low=200),
        }
        for label, mutate in examples.items():
            with self.subTest(label=label):
                b, _, _ = samples(100)
                mutate(b)
                with self.assertRaises(PriceFeatureError):
                    calculate(b)
        b, _, _ = samples()
        with self.assertRaisesRegex(PriceFeatureError, "stale"):
            extract_price_features(b, asof="2027-12-31")

    def test_benchmark_future_and_duplicate_dates_rejected(self):
        b, m, i = samples()
        bad = copy.deepcopy(m)
        bad[-1]["date"] = "2027-01-01"
        with self.assertRaises(PriceFeatureError):
            calculate(b, bad, i)
        bad = copy.deepcopy(m)
        bad[5]["date"] = bad[4]["date"]
        with self.assertRaises(PriceFeatureError):
            calculate(b, bad, i)

    def test_no_underlying_ohlcv_published_in_feature_report(self):
        b, m, i = samples()
        b[-1]["volume"] = 99999991
        r = calculate(b, m, i)
        raw = json.dumps(r)
        self.assertNotIn("99999991", raw)
        self.assertNotIn("API_KEY", raw)
        self.assertFalse(r["historical_universe_authenticated"])


if __name__ == "__main__":
    unittest.main()
