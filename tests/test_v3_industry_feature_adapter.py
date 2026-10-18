"""Synthetic off-network sector membership, profit-cycle and breadth tests."""
from __future__ import annotations
from copy import deepcopy
import json
import unittest

from v3_industry_feature_adapter import (
    IndustryFeatureError, evaluate_industry_cohort
)
from v3_offline_workbench import demo_bundle, run_workbench, WorkbenchError


class IndustryCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = demo_bundle()
        cls.sector = cls.sample["industry_cohorts"][0]
        cls.day = cls.sample["asof"]

    def evaluate(self, sector=None):
        return evaluate_industry_cohort(
            self.sector if sector is None else sector, asof=self.day,
            decision_at_utc=self.day + "T07:10:00Z")

    def test_sector_profit_from_member_sum_and_breadth_from_all_members(self):
        report = self.evaluate()
        self.assertEqual(report["declared_member_count"], 32)
        self.assertEqual(report["profit_covered_issuers"], 32)
        self.assertEqual(report["breadth_covered_issuers"], 32)
        profit_last = sum(m["annual_operating_profit"][1]["operating_profit_krw"]
                          for m in self.sector["members"])
        profit_prev = sum(m["annual_operating_profit"][0]["operating_profit_krw"]
                          for m in self.sector["members"])
        self.assertAlmostEqual(report["features"]["industry_profit_growth_12m_pct"],
                               100*(profit_last/profit_prev-1), places=5)
        self.assertEqual(report["features"]["industry_breadth_above_ma_ratio"], 1)
        self.assertFalse(report["sector_membership_original_pit_verified"])
        self.assertFalse(report["price_corporate_action_and_session_verified"])
        self.assertFalse(report["confirmed_buy"])

    def test_64_stock_demo_now_computes_industry_from_raw_cohort(self):
        result = run_workbench(self.sample)
        self.assertEqual(result["fully_covered_six_pillar_research_only"], 64)
        self.assertTrue(all(
            s["pillar_research_candidates"]["industry"] is not None
            for s in result["stocks_in_code_order_not_ranked"]))
        self.assertIsNone(result["confirmed_stock_ranks"])
        self.assertFalse(result["v3_release_ready"])

    def test_one_missing_constituent_price_does_not_reweight_survivors(self):
        c = deepcopy(self.sector)
        c["members"][0]["daily_closes"] = None
        result = self.evaluate(c)
        self.assertIsNone(result["features"]["industry_breadth_above_ma_ratio"])
        self.assertIsNotNone(result["features"]["industry_profit_growth_12m_pct"])
        self.assertIsNone(result["breadth_covered_issuers"])

    def test_missing_filing_or_delayed_observation_cannot_create_profit_growth(self):
        c = deepcopy(self.sector)
        c["members"][0]["annual_operating_profit"][1]["operating_profit_krw"] = None
        self.assertIsNone(self.evaluate(c)["features"]["industry_profit_growth_12m_pct"])
        c = deepcopy(self.sector)
        c["members"][0]["annual_operating_profit"][1]["observed_at_utc"] = (
            "2026-10-19T01:00:00Z")
        self.assertIsNone(self.evaluate(c)["features"]["industry_profit_growth_12m_pct"])
        c = deepcopy(self.sector)
        c["members"][0]["membership_observed_at_utc"] = "2026-10-19T01:00:00Z"
        r = self.evaluate(c)
        self.assertEqual(r["late_membership_sources"], 1)
        self.assertIsNone(r["features"]["industry_breadth_above_ma_ratio"])
        self.assertIsNone(r["features"]["industry_profit_growth_12m_pct"])

    def test_negative_base_cannot_display_explosive_recovery_percentage(self):
        c = deepcopy(self.sector)
        for item in c["members"]:
            item["annual_operating_profit"][0]["operating_profit_krw"] = -500
        result = self.evaluate(c)
        self.assertIsNone(result["features"]["industry_profit_growth_12m_pct"])
        self.assertIn("zero_or_loss_base_prevents_growth_ratio",
                      result["risk_flags_research"])

    def test_declining_industry_profits_are_warning_not_blocked_positive(self):
        c = deepcopy(self.sector)
        for item in c["members"]:
            item["annual_operating_profit"][1]["operating_profit_krw"] = 10
        result = self.evaluate(c)
        self.assertLess(result["features"]["industry_profit_growth_12m_pct"], 0)
        self.assertIn("aggregate_industry_operating_profit_declining",
                      result["risk_flags_research"])

    def test_duplicate_reused_issuer_mixed_market_and_future_membership_rejected(self):
        mutations = {
            "duplicate": lambda c: c["members"].append(deepcopy(c["members"][0])),
            "wrong_market": lambda c: c["members"][0].update(market="KOSDAQ"),
            "future_member": lambda c: c["members"][0].update(valid_from="2027-01-01"),
            "expired_member": lambda c: c["members"][0].update(valid_to=self.day),
            "duplicate_profit_year": lambda c: c["members"][0][
                "annual_operating_profit"][1].update(fiscal_year=2024),
            "post_asof_price": lambda c: c["members"][0]["daily_closes"][-1].update(
                date="2027-01-01"),
            "invalid_price": lambda c: c["members"][0]["daily_closes"][0].update(
                close=0),
            "bad_scope": lambda c: c.update(market="NYSE"),
            "bad_year": lambda c: c.update(latest_comparable_fiscal_year=2027),
        }
        for k, f in mutations.items():
            with self.subTest(k=k):
                c = deepcopy(self.sector)
                f(c)
                with self.assertRaises(IndustryFeatureError):
                    self.evaluate(c)

    def test_workbench_rejects_survivor_only_cohort_or_wrong_issuer(self):
        bundle = deepcopy(self.sample)
        bundle["industry_cohorts"][0]["members"].pop()
        with self.assertRaisesRegex(WorkbenchError, "roster_not_same_research_cohort"):
            run_workbench(bundle)
        bundle = deepcopy(self.sample)
        bundle["industry_cohorts"][0]["members"][0]["issuer_id"] = "99999999"
        with self.assertRaisesRegex(WorkbenchError, "roster_not_same_research_cohort"):
            run_workbench(bundle)

    def test_workbench_rejects_manual_industry_override(self):
        b = deepcopy(self.sample)
        b["stocks"][0]["manual_raw_features"]["industry_profit_growth_12m_pct"] = 999
        with self.assertRaisesRegex(WorkbenchError, "manual_industry_metric_conflicts"):
            run_workbench(b)

    def test_readiness_cannot_be_set_from_caller_flags(self):
        c = deepcopy(self.sector)
        c.update({"official_verified": True, "confirmed_buy": True})
        r = self.evaluate(c)
        self.assertFalse(r["v3_scoring_authorized"])
        self.assertFalse(r["confirmed_buy"])
        self.assertNotIn("operating_profit_krw", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
