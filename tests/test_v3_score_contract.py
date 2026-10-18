"""Offline scorecard CONTRACT tests, no invented financial or provider data."""
from __future__ import annotations

import copy
import json
import math
import unittest

from v3_score_contract import (
    ScoreContractError, VERSION,
    PILLAR_POINTS, IAS_POINTS, NET_BUY_PERIOD_WEIGHT,
    _check_design, ea_candidate_conditions, research_scorecard,
)


class FullSixPillarContractTests(unittest.TestCase):
    def test_agreed_top_level_100_points_are_unchanged(self):
        self.assertEqual(dict(PILLAR_POINTS), {
            "ias": 25, "financial": 20, "valuation": 15,
            "technical": 15, "industry": 15, "momentum": 10,
        })
        self.assertEqual(sum(PILLAR_POINTS.values()), 100)
        _check_design()

    def test_ias_is_six_subcomponents_and_sums_to_100(self):
        self.assertEqual(dict(IAS_POINTS), {
            "long_term_holding": 20, "accumulated_net_buy": 25,
            "buy_persistence": 15, "investor_quality": 10,
            "obv_and_volume": 15, "price_not_reflected": 15,
        })
        self.assertEqual(sum(IAS_POINTS.values()), 100)

    def test_accumulated_net_buy_period_weight_is_nested_not_whole_ias(self):
        self.assertEqual(dict(NET_BUY_PERIOD_WEIGHT),
                         {20:15, 60:25, 120:30, 250:30})
        self.assertEqual(sum(NET_BUY_PERIOD_WEIGHT.values()), 100)
        blank = research_scorecard(asof="2026-10-16")
        weights = blank["accumulated_net_buy_periods"]
        self.assertEqual(sum(w["weight_percent_within_accumulated_net_buy"]
                             for w in weights.values()), 100)
        self.assertFalse(any(w["verified_trading_calendar"] for w in weights.values()))

    def test_no_fabricated_zero_if_all_inputs_are_absent(self):
        model = research_scorecard(asof="2026-10-16")
        self.assertEqual(model["schema"], VERSION)
        self.assertEqual(model["candidate_nonmissing_weight"], 0)
        self.assertEqual(set(model["unscored_pillars"]), set(PILLAR_POINTS))
        for p in model["pillars"].values():
            self.assertIsNone(p["candidate_points"])
            self.assertEqual(p["status"], "missing_unscored")
        for p in model["ias_subpillars"].values():
            self.assertIsNone(p["candidate_ias_points"])
        self.assertIsNone(model["candidate_full_total_research_only"])
        self.assertIsNone(model["confirmed_score"])
        self.assertFalse(model["v3_release_ready"])

    def test_original_four_observations_never_replace_all_six_pillars(self):
        # A partial "flow/chart" focus cannot promote a 100-point model.
        model = research_scorecard(
            asof="2026-10-16",
            pillars={"ias": 17.0, "technical": 10.0, "momentum": 6.0},
        )
        self.assertEqual(model["candidate_nonmissing_weight"], 50)
        self.assertIn("financial", model["unscored_pillars"])
        self.assertIn("valuation", model["unscored_pillars"])
        self.assertIn("industry", model["unscored_pillars"])
        self.assertIsNone(model["candidate_full_total_research_only"])
        self.assertIsNone(model["confirmed_score"])

    def test_ias_six_component_normalization_scales_to_25_only_if_complete(self):
        ia = dict(IAS_POINTS)
        model = research_scorecard(
            asof="2026-10-16", ias=ia,
            pillars={"financial": 18, "valuation": 12,
                     "technical": 10, "industry": 14, "momentum": 8},
        )
        self.assertEqual(model["pillars"]["ias"]["candidate_points"], 25.0)
        self.assertEqual(model["candidate_nonmissing_weight"], 100)
        self.assertEqual(model["candidate_full_total_research_only"], 87.0)
        self.assertTrue(model["ias_internal_six_components_complete"])
        self.assertIsNone(model["confirmed_score"])
        self.assertIsNone(model["confirmed_rank"])
        self.assertIsNone(model["ias_confirmed"])
        self.assertFalse(model["model_normalization_verified"])
        self.assertFalse(model["scoring_authorized_for_investment"])
        self.assertFalse(model["v3_release_ready"])

    def test_partial_ias_cannot_be_calculated_as_25_or_0(self):
        ia = dict(IAS_POINTS)
        ia.pop("buy_persistence")
        model = research_scorecard(asof="2026-10-16", ias=ia)
        self.assertIsNone(model["pillars"]["ias"]["candidate_points"])
        self.assertIsNone(model["ias_subpillars"]["buy_persistence"]["candidate_ias_points"])
        self.assertFalse(model["ias_internal_six_components_complete"])
        self.assertIsNone(model["candidate_full_total_research_only"])

    def test_top_level_ias_and_internal_ias_cannot_be_double_counted(self):
        with self.assertRaisesRegex(ScoreContractError, "ias_double_counting_forbidden"):
            research_scorecard(asof="2026-10-16",
                               pillars={"ias": 20, "financial": 10},
                               ias=dict(IAS_POINTS))

    def test_zero_is_valid_only_if_explicitly_observed(self):
        model = research_scorecard(asof="2026-10-16",
                                   pillars={"financial": 0})
        self.assertEqual(model["pillars"]["financial"]["candidate_points"], 0.0)
        self.assertEqual(model["candidate_nonmissing_weight"], 20)
        self.assertIsNone(model["pillars"]["valuation"]["candidate_points"])
        self.assertIsNone(model["confirmed_score"])

    def test_invalid_weights_unknown_categories_bad_numbers_blocked(self):
        candidates = (
            {"invented_sector": 14},
            {"ias": True},
            {"ias": "12"},
            {"ias": float("nan")},
            {"ias": float("inf")},
            {"ias": -1},
            {"financial": 21},
            {"momentum": 11},
        )
        for bad in candidates:
            with self.subTest(bad=bad), self.assertRaises(ScoreContractError):
                research_scorecard(asof="2026-10-16", pillars=bad)
        with self.assertRaises(ScoreContractError):
            research_scorecard(asof="2026-10-16", ias={"accumulated_net_buy": 26})

    def test_invalid_dates_or_mapping_types_fail(self):
        for bad in ("20261016", "2026-02-30", "2026-13-11", None, "2026-10-16Z"):
            with self.subTest(asof=bad), self.assertRaises(ScoreContractError):
                research_scorecard(asof=bad)
        for invalid in ([1, 2], "IAS 25"):
            with self.assertRaises(ScoreContractError):
                research_scorecard(asof="2026-10-16", pillars=invalid)

    def test_candidate_ias_full_then_any_missing_nulls_total(self):
        ia = dict(IAS_POINTS)
        ia["price_not_reflected"] = None
        model = research_scorecard(asof="2026-10-16",
                                   ias=ia, pillars={k:v for k,v in PILLAR_POINTS.items() if k!="ias"})
        self.assertIsNone(model["pillars"]["ias"]["candidate_points"])
        self.assertEqual(model["candidate_nonmissing_weight"], 75)
        self.assertIsNone(model["candidate_full_total_research_only"])
        self.assertIsNone(model["confirmed_score"])

    def test_user_example_missing_15_points_provisional_82_3(self):
        result=research_scorecard(asof="2026-10-16", pillars={
            "ias":20, "financial":16, "valuation":14,
            "technical":13, "momentum":7
        })
        self.assertEqual(result["candidate_observed_points"], 70)
        self.assertEqual(result["candidate_available_points"], 85)
        self.assertEqual(result["candidate_missing_points"], 15)
        self.assertEqual(result["candidate_coverage_pct"], 85)
        self.assertEqual(result["candidate_normalized_100"], 82.3)
        self.assertEqual(result["unscored_pillars"], ["industry"])
        self.assertEqual(result["candidate_score_type"], "provisional_partial_coverage")
        self.assertIsNone(result["confirmed_score"])
        self.assertIsNone(result["confirmed_rank"])
        self.assertFalse(result["v3_release_ready"])

    def test_partial_ias_references_only_observed_subcategory_weight(self):
        result=research_scorecard(asof="2026-10-16",
                                  ias={"accumulated_net_buy":20,
                                       "buy_persistence":10},
                                  pillars={"financial":18, "valuation":14})
        self.assertIsNone(result["pillars"]["ias"]["candidate_points"])
        self.assertEqual(result["ias_candidate_observed_100"], 30)
        self.assertEqual(result["ias_candidate_available_100"], 40)
        # IAS max25, 40% of its subcategories => 10 available and 7.5 earned.
        self.assertEqual(result["candidate_available_points"], 45)
        self.assertEqual(result["candidate_observed_points"], 39.5)
        self.assertEqual(result["candidate_normalized_100"], 87.7)
        self.assertIn("ias", result["unscored_pillars"])
        self.assertIsNone(result["confirmed_score"])

    def test_all_absent_does_not_yield_provisional_numeric_score(self):
        blank=research_scorecard(asof="2026-10-16")
        self.assertIsNone(blank["candidate_observed_points"])
        self.assertIsNone(blank["candidate_normalized_100"])
        self.assertEqual(blank["candidate_available_points"],0)
        self.assertEqual(blank["candidate_score_type"], "insufficient_data")

    def test_all_six_available_still_provisional_not_released(self):
        result=research_scorecard(asof="2026-10-16",pillars={
            "ias":20, "financial":20, "valuation":10, "technical":12,
            "industry":10, "momentum":8
        })
        self.assertEqual(result["candidate_available_points"],100)
        self.assertEqual(result["candidate_observed_points"],80)
        self.assertEqual(result["candidate_normalized_100"],80)
        self.assertEqual(result["candidate_score_type"],"provisional_full_coverage")
        self.assertIsNone(result["confirmed_score"])
        self.assertFalse(result["scoring_authorized_for_investment"])

    def test_ea_filters_remain_research_only_not_predictive_or_tradeable(self):
        ea = ea_candidate_conditions()
        self.assertEqual(ea["current_ias_threshold_candidate"],60)
        self.assertEqual(ea["four_week_price_change_limit_candidate_pct"],10)
        self.assertEqual(ea["eight_week_price_change_preference_pct"],15)
        self.assertEqual(ea["future_four_week_return_study_target_pct"],8)
        self.assertEqual(ea["future_eight_week_return_study_target_pct"],12)
        self.assertIn("exact_ias_normalization", ea["unresolved_definitions"])
        self.assertFalse(ea["signal_verified"])
        self.assertFalse(ea["deployable_rule"])


if __name__ == "__main__":
    unittest.main()
