"""Pure math portfolio risk tests; all levels are synthetic user plans."""
import copy
import math
import unittest

from v3_portfolio_risk import (
    MAX_POSITION_COUNT, PortfolioRiskError, assess_position, evaluate_portfolio
)


def plan(code="000001", allocation=20, stop=92, gap=5, target=120):
    return {
        "code": code, "entry_price": 100, "stop_price": stop,
        "planned_position_pct": allocation,
        "stress_gap_below_stop_pct": gap,
        "reference_target_price": target,
    }


class PortfolioRiskTests(unittest.TestCase):
    def test_single_position_stop_and_gap_nav_loss_exact(self):
        row = assess_position(plan())
        self.assertAlmostEqual(row["stop_distance_pct"], 8.0)
        self.assertEqual(row["stop_distance_band"], "short_distance")
        self.assertAlmostEqual(row["hypothetical_stress_fill_price"], 87.4)
        self.assertAlmostEqual(row["hypothetical_stress_loss_per_position_pct"], 12.6)
        self.assertAlmostEqual(row["planned_stop_loss_of_nav_pct"], 1.6)
        self.assertAlmostEqual(row["hypothetical_gap_loss_of_nav_pct"], 2.52)
        self.assertAlmostEqual(row["reference_target_distance_pct"], 20.0)
        self.assertAlmostEqual(
            row["reference_price_reward_risk_arithmetic_only"], 2.5)
        self.assertFalse(row["confirmed_buy"])
        self.assertIsNone(row["expected_upside_pct"])
        self.assertIsNone(row["actual_loss_probability"])

    def test_no_forecast_or_safe_allocation_from_reference_target(self):
        r = evaluate_portfolio([plan()], total_nav_risk_budget_pct=1)
        self.assertEqual(r["stress_budget_status"], "exceeds_assumed_budget")
        self.assertFalse(r["stress_budget_met"])
        self.assertEqual(r["max_preferred_holdings"], 3)
        self.assertAlmostEqual(r["planned_invested_pct"], 20)
        self.assertAlmostEqual(r["planned_cash_pct"], 80)
        self.assertAlmostEqual(r["total_stop_loss_of_nav_pct"], 1.6)
        self.assertAlmostEqual(r["total_hypothetical_gap_loss_of_nav_pct"], 2.52)
        self.assertEqual(
            r["positions"][0]["theoretical_max_pct_with_other_plans_unchanged"],
            7.93)
        self.assertIsNone(r["positions"][0]["actual_allocation_recommendation_pct"])
        self.assertIsNone(r["portfolio_expected_return_pct"])
        self.assertIsNone(r["probability_of_loss"])
        self.assertIsNone(r["safe_position_size_pct"])
        self.assertFalse(r["trade_approval"])
        self.assertFalse(r["v3_scoring_authorized"])

    def test_three_holdings_and_concurrent_loss_assumption(self):
        p = [plan("000001", 5), plan("000002", 5), plan("000003", 5)]
        r = evaluate_portfolio(p, total_nav_risk_budget_pct=2)
        self.assertTrue(r["concentration_limit_met"])
        self.assertTrue(r["assumes_simultaneous_stress"])
        self.assertTrue(r["stress_budget_met"])
        self.assertAlmostEqual(r["total_hypothetical_gap_loss_of_nav_pct"], 1.89)
        self.assertFalse(r["trade_approval"])
        self.assertTrue(r["more_severe_losses_are_possible"])

    def test_four_holdings_cannot_pass_concentration_preference(self):
        p = [plan(f"{i:06d}", 5) for i in range(1, 5)]
        r = evaluate_portfolio(p, total_nav_risk_budget_pct=5)
        self.assertFalse(r["concentration_limit_met"])
        self.assertEqual(r["number_of_positions"], 4)
        self.assertFalse(r["trade_approval"])

    def test_no_target_means_no_reward_risk_number(self):
        p = plan()
        p["reference_target_price"] = None
        x = assess_position(p)
        self.assertIsNone(x["reference_target_distance_pct"])
        self.assertIsNone(x["reference_price_reward_risk_arithmetic_only"])

    def test_stop_distance_only_not_whole_company_risk(self):
        self.assertEqual(assess_position(plan(stop=99))["stop_distance_band"],
                         "too_close")
        self.assertEqual(assess_position(plan(stop=90))["stop_distance_band"],
                         "medium_distance")
        self.assertEqual(assess_position(plan(stop=80))["stop_distance_band"],
                         "wide")
        self.assertFalse(assess_position(plan(stop=99))["confirmed_buy"])

    def test_rounded_display_never_approves_overbudget_loss(self):
        p = plan(allocation=1, stop=99, gap=0)
        # The actual computed NAV loss is 0.01%, not a rounded safe zero.
        result = evaluate_portfolio([p], total_nav_risk_budget_pct=0.005)
        self.assertFalse(result["stress_budget_met"])

    def test_claimed_verified_inputs_never_authorize_trading(self):
        x = plan()
        x.update({"official_price_verified": True,
                  "all_six_pillars_verified": True,
                  "confirmed_buy": True})
        result = evaluate_portfolio([x], total_nav_risk_budget_pct=100)
        self.assertFalse(result["trade_approval"])
        self.assertTrue(result["all_prices_and_stops_are_unverified_user_inputs"])
        self.assertFalse(result["v3_scoring_authorized"])

    def test_bad_prices_gap_allocations_targets_and_symbols_blocked(self):
        changes = [
            {"entry_price": 0}, {"entry_price": float("nan")},
            {"stop_price": 100}, {"stop_price": -2},
            {"planned_position_pct": 0},
            {"planned_position_pct": True},
            {"stress_gap_below_stop_pct": float("inf")},
            {"stress_gap_below_stop_pct": 101},
            {"reference_target_price": 100},
            {"reference_target_price": -1},
            {"code": "abc"}, {"code": 1234},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(PortfolioRiskError):
                assess_position({**plan(), **change})

    def test_duplicate_or_overallocated_portfolio_rejected(self):
        with self.assertRaises(PortfolioRiskError):
            evaluate_portfolio([plan(), plan()], total_nav_risk_budget_pct=10)
        with self.assertRaises(PortfolioRiskError):
            evaluate_portfolio([plan("000001", 80), plan("000002", 80)],
                               total_nav_risk_budget_pct=10)
        with self.assertRaises(PortfolioRiskError):
            evaluate_portfolio([], total_nav_risk_budget_pct=1)

    def test_budget_requires_explicit_valid_number(self):
        for bad in (None, 0, -1, 200, float("nan"), False):
            with self.subTest(budget=bad), self.assertRaises(PortfolioRiskError):
                evaluate_portfolio([plan()], total_nav_risk_budget_pct=bad)


if __name__ == "__main__":
    unittest.main()
