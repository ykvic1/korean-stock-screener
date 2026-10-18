"""Synthetic v3 same-market rank overlap diagnostics and workbench integration."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from v3_factor_overlap_audit import (
    FeatureOverlapError, SUSPECTED_PAIRS, _spearman,
    audit_factor_overlap
)
from v3_offline_workbench import demo_bundle, run_workbench
from v3_research_factor_engine import FIELD_SPECS


def rows(n=40, *, market="KOSPI"):
    sample = []
    for i in range(n):
        sample.append({
            "asof": "2026-10-16",
            "code": f"{i+1+(100000 if market=='KOSDAQ' else 0):06d}",
            "issuer_id": f"{i+1+(10000000 if market=='KOSDAQ' else 0):08d}",
            "market": market,
            "features": {
                "ias_flow20_pct_float": i * 2,
                "ias_flow60_pct_float": i * 3,
                "ias_flow120_pct_float": i * 4,
                "ias_flow250_pct_float": i * 5,
                "momentum_stock_relative_strength_20_pct": 50-i,
                "momentum_stock_relative_strength_60_pct": 2*i,
                "financial_fcf_assets_3y_pct": 1+i,
                "valuation_fcf_yield_pct": i*2,
            },
        })
    return sample


class FactorOverlapTests(unittest.TestCase):
    def test_midrank_ties_and_constant_are_not_false_zero(self):
        self.assertAlmostEqual(_spearman([(1, 2), (2, 3), (3, 4)]), 1.0)
        self.assertAlmostEqual(_spearman([(1, 4), (2, 3), (3, 2)]), -1.0)
        self.assertIsNone(_spearman([(1, 2), (1, 3), (1, 4)]))
        self.assertIsNone(_spearman([(1, 2)]))
        self.assertAlmostEqual(_spearman([(1, 3), (1, 3), (2, 4)]), 1.0)

    def test_overlapping_flow_periods_and_fcf_share_must_warn(self):
        result = audit_factor_overlap(rows(), asof="2026-10-16")
        comparisons = result["markets"]["KOSPI"]["hypothesis_pairs"]
        self.assertEqual(len(comparisons), len(SUSPECTED_PAIRS))
        for field_a, field_b in (
            ("ias_flow20_pct_float", "ias_flow60_pct_float"),
            ("ias_flow60_pct_float", "ias_flow120_pct_float"),
            ("ias_flow120_pct_float", "ias_flow250_pct_float"),
            ("financial_fcf_assets_3y_pct", "valuation_fcf_yield_pct"),
        ):
            comparison = next(
                p for p in comparisons if
                p["left"] == field_a and p["right"] == field_b)
            self.assertEqual(comparison["complete_pair_n"], 40)
            self.assertAlmostEqual(comparison["spearman_rho"], 1.0)
            self.assertTrue(comparison["research_redundancy_warning"])
        self.assertGreaterEqual(result["research_redundancy_warning_count"], 4)
        self.assertFalse(result["scores_reweighted_by_audit"])
        self.assertFalse(result["v3_reweighting_authorized"])
        self.assertIsNone(result["confirmed_scores"])
        self.assertIsNone(result["confirmed_ranks"])
        self.assertFalse(result["confirmed_buy"])

    def test_missing_pair_count_yields_null_not_zero_or_anti_correlation(self):
        inp = rows()
        for x in inp[:15]:
            x["features"]["ias_flow20_pct_float"] = None
        report = audit_factor_overlap(inp, asof="2026-10-16")
        row = report["markets"]["KOSPI"]["hypothesis_pairs"][0]
        self.assertEqual(row["complete_pair_n"], 25)
        self.assertIsNone(row["spearman_rho"])
        self.assertTrue(row["insufficient_or_constant"])
        self.assertFalse(row["research_redundancy_warning"])

    def test_constant_indicator_is_undefined_even_with_large_cohort(self):
        inp = rows()
        for row in inp:
            row["features"]["ias_flow20_pct_float"] = 123
        summary = audit_factor_overlap(inp, asof="2026-10-16")
        first = summary["markets"]["KOSPI"]["hypothesis_pairs"][0]
        self.assertEqual(first["complete_pair_n"], 40)
        self.assertIsNone(first["spearman_rho"])
        self.assertTrue(first["insufficient_or_constant"])

    def test_market_separation_blocks_cross_market_spurious_similarity(self):
        kosdaq = rows(market="KOSDAQ")
        for v in kosdaq:
            v["features"]["ias_flow20_pct_float"] = 1
        summary = audit_factor_overlap(rows() + kosdaq, asof="2026-10-16")
        k1 = summary["markets"]["KOSPI"]["hypothesis_pairs"][0]
        k2 = summary["markets"]["KOSDAQ"]["hypothesis_pairs"][0]
        self.assertEqual(k1["spearman_rho"], 1)
        self.assertIsNone(k2["spearman_rho"])
        self.assertEqual(summary["markets"]["KOSPI"]["market_cohort_size"], 40)
        self.assertEqual(summary["markets"]["KOSDAQ"]["market_cohort_size"], 40)

    def test_bad_correlation_settings_and_future_mixed_asof_block(self):
        for kwargs in ({"min_pairs": 0}, {"absolute_rho_warning": 0.1},
                       {"absolute_rho_warning": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(FeatureOverlapError):
                audit_factor_overlap(rows(), asof="2026-10-16", **kwargs)
        x = rows()
        x[0]["asof"] = "2026-10-17"
        with self.assertRaisesRegex(FeatureOverlapError, "mixed_overlap_snapshot"):
            audit_factor_overlap(x, asof="2026-10-16")
        x = rows()
        x.append(deepcopy(x[0]))
        with self.assertRaisesRegex(FeatureOverlapError, "duplicate_overlap_symbol"):
            audit_factor_overlap(x, asof="2026-10-16")
        x = rows()
        x[0]["features"]["ias_flow20_pct_float"] = float("nan")
        with self.assertRaisesRegex(FeatureOverlapError, "invalid_overlap_numeric"):
            audit_factor_overlap(x, asof="2026-10-16")

    def test_demo_connected_to_audit_without_change_to_scores(self):
        payload = demo_bundle()
        original = run_workbench(payload)
        result = original["factor_overlap_diagnostics_research_only"]
        self.assertEqual(set(result["markets"]), {"KOSPI", "KOSDAQ"})
        self.assertEqual(result["markets"]["KOSPI"]["market_cohort_size"], 32)
        self.assertEqual(original["fully_covered_six_pillar_research_only"], 64)
        self.assertFalse(result["scores_reweighted_by_audit"])
        self.assertFalse(original["confirmed_buy"])
        self.assertFalse(original["v3_release_ready"])
        output = json.dumps(result)
        self.assertNotIn("institution_total_net_shares", output)
        self.assertNotIn("operating_profit_krw", output)

    def test_zero_metric_allowed_and_audit_never_returns_stock_values(self):
        inp = rows()
        inp[0]["features"]["ias_flow20_pct_float"] = 0
        out = audit_factor_overlap(inp, asof="2026-10-16")
        self.assertEqual(out["markets"]["KOSPI"]["hypothesis_pairs"][0]
                         ["complete_pair_n"], 40)
        self.assertNotIn("issuer_id", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
