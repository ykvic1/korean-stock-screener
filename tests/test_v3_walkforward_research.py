"""Adversarial and synthetic-only tests: no provider data, no predicted returns."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import math
import unittest

from v3_walkforward_research import (
    SESSION, HORIZONS, WalkForwardError, _outcome, evaluate
)


def weekdays_after(signal, count=252):
    current = date.fromisoformat(signal)
    dates = []
    while len(dates) < count:
        current += timedelta(days=1)
        if current.weekday() < 5:
            dates.append(current.isoformat())
    return dates


def example(code="000001", signal="2023-01-03", count=252):
    bars = [
        {"date": d, "open": 100.0, "high": 102.0, "low": 98.0,
         "close": 100.0, "session": SESSION}
        for d in weekdays_after(signal, count)
    ]
    return {
        "code": code, "signal_date": signal, "stop_price": 90.0,
        "target_price": 110.0, "bars": bars,
        "evidence": {
            "pit_universe_asserted": True,
            "regular_session_asserted": True,
            "corporate_actions_audited_asserted": True,
            "trading_calendar_complete_asserted": True,
            "signal_setup_frozen_asserted": True,
            "signal_snapshot_sha256": "a" * 64,
        }
    }


class WalkForwardResearchTests(unittest.TestCase):
    def test_horizons_are_session_proxies_not_calendar_promises(self):
        self.assertEqual(HORIZONS, {
            "1m": 21, "2m": 42, "3m": 63, "6m": 126, "12m": 252
        })

    def test_no_actual_data_yields_no_trading_output(self):
        r = evaluate([], split_date="2024-01-01")
        self.assertEqual(r["input_unique_signal_count"], 0)
        self.assertIsNone(r["future_return_forecasts"])
        self.assertIsNone(r["buy_signals"])
        self.assertFalse(r["authorized_for_v3_scoring"])
        self.assertFalse(r["provider_provenance_authenticated"])
        self.assertFalse(r["pit_universe_independently_verified"])
        self.assertFalse(r["out_of_sample_strategy_validated"])
        for row in r["periods"].values():
            self.assertEqual(row["train"]["n"], 0)
            self.assertIsNone(row["holdout"]["mean_historical_net_pct"])
            self.assertIsNone(row["train"]["forecast_success_probability"])

    def test_next_open_entry_and_both_touch_conservative_stop(self):
        a = example(count=21)
        a["bars"][0].update(high=112.0, low=89.0)
        hit = _outcome(a, 21, 30)
        self.assertEqual(hit["event"], "stop_first")
        self.assertTrue(hit["same_bar_stop_target_ambiguous"])
        self.assertAlmostEqual(hit["net_return_pct"], -10.3)
        self.assertEqual(hit["mae_pct"], -11.0)
        self.assertEqual(hit["mfe_pct"], 12.0)

    def test_opening_gap_below_stop_executes_at_worse_open(self):
        a = example(count=21)
        # Signal-time entry uses opening 100; next session opens 85.
        a["bars"][1].update(open=85.0, low=83.0, high=87.0, close=86.0)
        result = _outcome(a, 21, 30)
        self.assertEqual(result["event"], "stop_first")
        self.assertAlmostEqual(result["net_return_pct"], -15.3)

    def test_target_first_not_optimistically_filled_at_gap_high(self):
        a = example(count=21)
        a["bars"][1].update(open=115.0, low=114.0, high=118.0, close=116.0)
        result = _outcome(a, 21, 30)
        self.assertEqual(result["event"], "target_first")
        self.assertAlmostEqual(result["net_return_pct"], 9.7)

    def test_horizon_close_applies_explicit_round_trip_cost(self):
        a = example(count=21)
        result = _outcome(a, 21, 30)
        self.assertEqual(result["event"], "horizon_close")
        self.assertAlmostEqual(result["net_return_pct"], -0.3)

    def test_short_history_is_censored_not_selected_by_early_win(self):
        a = example(count=20)
        a["bars"][0].update(high=111.0)
        r = evaluate([a], split_date="2024-01-01", embargo_days=0)
        self.assertEqual(r["periods"]["1m"]["train"]["n"], 0)
        self.assertEqual(r["periods"]["1m"]["train"]["right_censored"], 1)
        self.assertIsNone(r["periods"]["1m"]["train"]["mean_historical_net_pct"])

    def test_future_label_over_split_is_purged(self):
        a = example(signal="2023-12-26", count=252)
        r = evaluate([a], split_date="2024-01-01", embargo_days=252)
        self.assertEqual(r["periods"]["1m"]["train"]["n"], 0)
        self.assertEqual(r["periods"]["1m"]["train"]["purged_or_embargoed"], 1)

    def test_holdout_requires_time_embargo(self):
        a = example(signal="2024-01-02")
        b = example(code="000002", signal="2024-10-01")
        r = evaluate([a, b], split_date="2024-01-01", embargo_days=252)
        self.assertEqual(r["periods"]["1m"]["holdout"]["n"], 1)
        self.assertEqual(r["periods"]["1m"]["holdout"]["purged_or_embargoed"], 1)
        self.assertIsNone(r["periods"]["1m"]["holdout"]["mean_historical_net_pct"])

    def test_quarter_and_symbol_floors_suppress_small_sample_results(self):
        many = []
        for i in range(44):
            many.append(example(code=f"{i:06d}", signal="2022-01-03"))
        r = evaluate(many, split_date="2024-01-01")
        train = r["periods"]["12m"]["train"]
        self.assertEqual(train["n"], 44)
        self.assertEqual(train["symbols"], 44)
        self.assertEqual(train["signal_quarters"], 1)
        self.assertFalse(train["meets_exploratory_sample_floor"])
        self.assertIsNone(train["historical_stop_first_pct"])

    def test_large_synthetic_cohort_still_not_forward_validated(self):
        # 48 hypothetical signals across 12 different calendar quarters.
        cases = []
        for q in range(12):
            year, month = 2020 + q // 4, (q % 4) * 3 + 1
            signal = date(year, month, 6)
            while signal.weekday() >= 5:
                signal += timedelta(days=1)
            for j in range(4):
                cases.append(example(code=f"{q*4+j+1:06d}",
                                     signal=signal.isoformat()))
        r = evaluate(cases, split_date="2024-01-01")
        train = r["periods"]["12m"]["train"]
        self.assertEqual(train["n"], 48)
        self.assertEqual(train["signal_quarters"], 12)
        self.assertGreaterEqual(train["symbols"], 10)
        self.assertTrue(train["meets_exploratory_sample_floor"])
        self.assertEqual(train["interpretation"], "exploratory_retrospective")
        self.assertAlmostEqual(train["mean_historical_net_pct"], -0.3)
        self.assertEqual(train["historical_stop_first_pct"], 0.0)
        self.assertEqual(train["historical_adverse_p75_pct"], 2.0)
        self.assertIsNone(train["forward_expected_return_pct"])
        self.assertFalse(train["confirmed_buy"])
        self.assertFalse(r["survivorship_bias_resolved"])

    def test_overlapping_stock_windows_suppressed_and_order_independent(self):
        a = example(code="000002", signal="2022-01-03")
        b = example(code="000002", signal="2022-02-10")
        forward = evaluate([a, b], split_date="2024-01-01")
        reversed_order = evaluate([b, a], split_date="2024-01-01")
        x = forward["periods"]["12m"]["train"]
        self.assertEqual(x["n"], 1)
        self.assertEqual(x["overlapping_same_symbol_suppressed"], 1)
        self.assertEqual(x, reversed_order["periods"]["12m"]["train"])
        self.assertEqual(forward["periods"]["1m"]["train"]["n"], 2)
        self.assertFalse(forward["authorized_for_v3_scoring"])

    def test_invalid_unverified_or_reused_history_fails_closed(self):
        changes = {
            "duplicate": lambda x: [x, copy.deepcopy(x)],
            "same_day": lambda x: set_date(x, 0, x["signal_date"]),
            "reversal": lambda x: set_date(x, 1, x["bars"][0]["date"]),
            "wrong_session": lambda x: x["bars"][0].update(session="AFTER_HOURS"),
            "bad_ohlc": lambda x: x["bars"][0].update(high=99.0),
            "nan": lambda x: x["bars"][0].update(low=float("nan")),
            "unverified": lambda x: x["evidence"].update(pit_universe_asserted=False),
            "missing_hash": lambda x: x["evidence"].update(signal_snapshot_sha256=""),
            "exit_bad": lambda x: x.update(target_price=89.0),
            "bad_entry": lambda x: x.update(stop_price=101.0),
        }
        for kind, change in changes.items():
            a = example()
            with self.subTest(kind=kind), self.assertRaises(WalkForwardError):
                c = change(a)
                evaluate(c if isinstance(c, list) else [a],
                         split_date="2024-01-01")

    def test_invalid_configuration_fails_closed(self):
        a = example()
        for opts in (
            {"embargo_days": True}, {"embargo_days": -1},
            {"round_trip_cost_bps": -1},
            {"round_trip_cost_bps": float("nan")},
            {"round_trip_cost_bps": 999},
        ):
            with self.subTest(opts=opts), self.assertRaises(WalkForwardError):
                evaluate([a], split_date="2024-01-01", **opts)


def set_date(case, index, value):
    case["bars"][index]["date"] = value


if __name__ == "__main__":
    unittest.main()
