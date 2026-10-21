"""Loss-first investment research screening: synthetic OHLCV and issuer flags."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
import unittest

from v3_loss_first_screen import loss_first_screen, LossScreenError
from v3_price_feature_adapter import SESSION
from v3_offline_workbench import demo_bundle, run_workbench


def history(n=135, *, trend=0.12, confirmed_pivot=True):
    d = date(2026, 3, 2)
    rows = []
    while len(rows) < n:
        if d.weekday() < 5:
            i = len(rows)
            last = 100 + trend * i
            row = {
                "date": d.isoformat(), "session": SESSION,
                "open": last-0.2, "high": last+0.5,
                "low": last-0.5, "close": last,
                "volume": 1000+i,
            }
            rows.append(row)
        d += timedelta(days=1)
    if confirmed_pivot:
        rows[-15]["low"] = 80.0 if trend >= 0 else 10.0
    return rows


def screen(bars=None, *, features=None, finance=None, industry=None):
    p = bars if bars is not None else history()
    return loss_first_screen(
        asof=p[-1]["date"],
        price_bars=p,
        raw_features=features if features is not None else {
            "ias_flow20_pct_float": 1.5,
            "ias_flow60_pct_float": 2.7,
            "ias_obv_change_60_pct": 35.0,
        },
        financial_warning_candidates=finance,
        industry_warning_candidates=industry,
    )


class LossFirstGateTests(unittest.TestCase):
    def test_rising_structure_positive_flows_is_only_conditional_research(self):
        r = screen()
        self.assertEqual(r["status_research_only"], "conditional_research_review")
        self.assertTrue(r["structural_exit_reference_available"])
        self.assertEqual(r["latest_confirmed_swing_low"]["observed_confirmed_swing_low_price"], 80)
        self.assertGreater(r["distance_to_observed_swing_low_pct"], 0)
        self.assertEqual(r["daily_trend_observation"], "uptrend")
        self.assertFalse(r["confirmed_buy"])
        self.assertIsNone(r["estimated_expected_return"])
        self.assertIsNone(r["safe_position_size"])
        self.assertFalse(r["v3_release_ready"])
        self.assertFalse(r["weekly_structure_independently_confirmed"])

    def test_latest_confirmed_swing_low_broken_hard_excludes_even_if_scores_high(self):
        b = history()
        b[-1].update(open=70.0, high=71.0, low=69.0, close=70.0)
        result = screen(b, features={
            "ias_flow20_pct_float": 99, "ias_flow60_pct_float": 99,
            "ias_obv_change_60_pct": 99, "experimental_high_score": 100,
        })
        self.assertEqual(result["status_research_only"], "structural_exclusion")
        self.assertIn("latest_confirmed_swing_low_broken", result["exclusion_reasons"])
        self.assertTrue(result["risk_gate_overrides_provisional_high_scores"])
        self.assertFalse(result["structural_exit_reference_available"])
        self.assertFalse(result["confirmed_buy"])

    def test_broken_latest_pivot_does_not_fall_back_to_older_low(self):
        b = history(n=135, confirmed_pivot=False)
        b[-40]["low"] = 75
        b[-15]["low"] = 95
        b[-1].update(open=92.0, high=93.0, low=91.0, close=92.0)
        r = screen(b)
        self.assertEqual(r["latest_confirmed_swing_low"]["observed_confirmed_swing_low_price"], 95)
        self.assertEqual(r["status_research_only"], "structural_exclusion")
        self.assertIn("latest_confirmed_swing_low_broken", r["exclusion_reasons"])

    def test_negative_equity_alone_excludes_even_without_price_or_foreign_data(self):
        result = loss_first_screen(
            asof="2026-10-16",
            financial_warning_candidates=["negative_or_zero_equity"])
        self.assertEqual(result["status_research_only"], "structural_exclusion")
        self.assertIn("nonpositive_equity_exclusion", result["exclusion_reasons"])
        self.assertIsNone(result["latest_confirmed_swing_low"])
        self.assertFalse(result["confirmed_buy"])

    def test_negative_owner_profit_fcf_and_industry_warnings_are_not_recommendations(self):
        r = screen(finance=["negative_owners_profit", "negative_latest_free_cash_flow"],
                   industry=["aggregate_industry_operating_profit_declining"])
        self.assertEqual(r["status_research_only"], "downside_watch")
        self.assertIn("negative_owner_earnings", r["review_reasons"])
        self.assertIn("negative_last_fcf", r["review_reasons"])
        self.assertIn("industry_profit_declining", r["review_reasons"])

    def test_industry_only_warning_cannot_double_penalize_other_pillars(self):
        result=screen(industry=["aggregate_industry_operating_profit_declining",
                                "low_declared_cohort_above_ma_breadth",
                                "profit_recovery_concentrated_in_one_issuer"])
        self.assertEqual(result["status_research_only"],
                         "conditional_research_review")
        self.assertEqual(result["exclusion_reasons"],[])
        self.assertIn("industry_profit_declining",result["review_reasons"])
        self.assertIn("weak_industry_market_breadth",result["review_reasons"])
        self.assertIn("industry_profit_improvement_concentrated",
                      result["industry_advisories_not_separate_risk_gate"])
        self.assertTrue(result["industry_signals_only_contribute_within_industry15"])
        self.assertFalse(result["risk_gate_overrides_provisional_high_scores"])
        self.assertFalse(result["confirmed_buy"])

    def test_persistent_negative_20_60_flows_overrides_positive_price_signal(self):
        result = screen(features={
            "ias_flow20_pct_float": -0.2,
            "ias_flow60_pct_float": -0.7,
            "ias_obv_change_60_pct": 60.0,
        })
        self.assertEqual(result["status_research_only"], "downside_watch")
        self.assertIn("persistent_observed_net_selling_20_and_60",
                      result["review_reasons"])

    def test_price_trend_decline_warns_even_with_positive_flow(self):
        r = screen(history(trend=-0.35))
        self.assertEqual(r["daily_trend_observation"], "downtrend")
        self.assertEqual(r["status_research_only"], "downside_watch")

    def test_extreme_last_day_extension_chase_risk_not_trend_buy(self):
        b = history()
        b[-1].update(open=260, low=259, high=262, close=261, volume=100000)
        r = screen(b)
        self.assertEqual(r["status_research_only"], "chase_risk")
        self.assertIn("extended_price_chase_risk", r["review_reasons"])
        self.assertIn("exceptional_volume_requires_manual_review", r["review_reasons"])
        self.assertFalse(r["confirmed_buy"])

    def test_missing_stop_and_history_are_incomplete_not_assumed_five_percent(self):
        short = history(n=40)
        r = screen(short)
        self.assertEqual(r["status_research_only"], "incomplete_evidence")
        self.assertIsNone(r["distance_to_observed_swing_low_pct"])
        self.assertIsNone(r["latest_confirmed_swing_low"])
        b = history(135, confirmed_pivot=False)
        r = screen(b)
        self.assertEqual(r["status_research_only"], "incomplete_evidence")
        self.assertFalse(r["structural_exit_reference_available"])
        self.assertNotIn("confirmed_buy", r["exclusion_reasons"])

    def test_missing_flow_or_obv_does_not_invent_positive_evidence(self):
        r = screen(features={})
        self.assertEqual(r["status_research_only"], "watch_unconfirmed")
        self.assertIn("investor_20_60_evidence_missing", r["review_reasons"])
        self.assertIn("obv_volume_direction_missing", r["review_reasons"])

    def test_cross_session_future_unsorted_and_invalid_numeric_rejected(self):
        cases = {
            "future": lambda b: b[-1].update(date="2027-01-01"),
            "session": lambda b: b[-1].update(session="AFTER_HOURS"),
            "unsorted": lambda b: b[-1].update(date=b[-2]["date"]),
            "nonpositive": lambda b: b[-1].update(low=-5),
        }
        for name, change in cases.items():
            b = history()
            day = b[-1]["date"]
            change(b)
            with self.subTest(name=name), self.assertRaises(LossScreenError):
                loss_first_screen(asof=day, price_bars=b)
        with self.assertRaises(LossScreenError):
            screen(features={"ias_flow20_pct_float": float("nan")})
        with self.assertRaises(LossScreenError):
            screen(finance=["invented_certified_buy"])
        with self.assertRaises(LossScreenError):
            screen(industry=["a fake score"])

    def test_demo_workbench_includes_risk_gate_before_display_without_trading(self):
        r = run_workbench(demo_bundle())
        self.assertEqual(r["research_stock_count"], 64)
        self.assertEqual(r["fully_covered_six_pillar_research_only"], 64)
        for stock in r["stocks_in_code_order_not_ranked"]:
            risk = stock["downside_risk_screen"]
            self.assertFalse(risk["confirmed_buy"])
            self.assertIn(risk["status_research_only"], {
                "structural_exclusion", "downside_watch", "chase_risk",
                "incomplete_evidence", "conditional_research_review",
                "watch_unconfirmed"})
            self.assertFalse(stock["trade_approval"])
        self.assertIsNone(r["confirmed_stock_ranks"])
        self.assertIsNone(r["recommended_positions"])
        self.assertFalse(r["v3_release_ready"])

    def test_high_research_score_never_overrides_broken_structural_low(self):
        bundle = demo_bundle()
        target = bundle["stocks"][-1]
        price = target["price_bars"]
        price[-15]["low"] = price[-15]["close"] * 0.87
        violated = price[-15]["low"]
        price[-1].update(
            open=violated * 0.94, high=violated * 0.95,
            low=violated * 0.92, close=violated * 0.94)
        # The price is synthetic, and all financial / flow metrics remain.
        r = run_workbench(bundle)
        stock = next(x for x in r["stocks_in_code_order_not_ranked"]
                     if x["market"] == target["market"] and x["code"] == target["code"])
        self.assertIsNotNone(stock["candidate_full_total_research_only"])
        self.assertEqual(stock["downside_risk_screen"]["status_research_only"],
                         "structural_exclusion")
        self.assertTrue(stock["high_score_not_allowed_to_override_risk_screen"])
        self.assertFalse(stock["trade_approval"])
        self.assertIsNone(stock["confirmed_rank"])

    def test_output_does_not_leak_flow_units_or_raw_private_account_values(self):
        data = screen()
        text = json.dumps(data)
        self.assertNotIn("institution_total_net_shares", text)
        self.assertNotIn("API_KEY", text)


if __name__ == "__main__":
    unittest.main()
