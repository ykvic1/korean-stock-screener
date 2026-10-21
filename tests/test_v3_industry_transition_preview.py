"""Only *synthetic* source-derived industry scoring hypotheses; never live."""
from copy import deepcopy
import json
import unittest

from v3_industry_feature_adapter import evaluate_industry_cohort
from v3_industry_transition_preview import (
    preview, IndustryTransitionError, WEIGHTS
)
from v3_offline_workbench import demo_bundle


class IndustryTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle=demo_bundle()
        cls.day=bundle["asof"]
        cls.cohort=bundle["industry_cohorts"][0]

    def snapshots(self, size=6):
        results=[]
        for i in range(size):
            s=deepcopy(self.cohort)
            s["industry_code"]=f"TEST{i:02d}"
            r=evaluate_industry_cohort(
                s,asof=self.day,decision_at_utc=self.day+"T07:10:00Z")
            results.append(r)
        return results

    def test_five_or_more_industries_yield_experimental_only_15pt(self):
        inputs=self.snapshots()
        results=preview(inputs,asof=self.day)
        self.assertEqual(sum(WEIGHTS.values()),15)
        self.assertEqual(WEIGHTS,{"early_rotation":5,"profit_transition":4,
            "improvement_diffusion":3,"overheat_crowding_quality":3})
        self.assertEqual(results["input_sector_snapshots"],6)
        self.assertFalse(results["approved_for_live_stock_scoring"])
        self.assertEqual(results["observed_issuer_investment_scores"],0)
        self.assertEqual(results["full_six_pillar_issuer_scores_certified"],0)
        self.assertNotIn("operating_profit_krw",json.dumps(results))
        self.assertNotIn("daily_closes",json.dumps(results))
        for item in results["research_only_sectors"]:
            self.assertTrue(item["industry_5433_weights_user_approved"])
            self.assertEqual(item["provisional_available_weight"],15)
            self.assertEqual(item["provisional_missing_weight"],0)
            self.assertIsNone(item["investment_grade_industry15_score"])
            self.assertGreaterEqual(item["provisional_observed_score"],0)
            self.assertLessEqual(item["provisional_observed_score"],15)
            self.assertFalse(item["stock_buy_signal"])

    def test_single_sector_cannot_self_normalize_or_claim_pit(self):
        result=preview(self.snapshots(1),asof=self.day)
        self.assertEqual(result["input_sector_snapshots"],1)
        self.assertEqual(
            result["research_only_sectors"][0]["provisional_available_weight"],0)
        self.assertIsNone(
            result["research_only_sectors"][0]["provisional_observed_score"])
        self.assertFalse(result["source_historical_pit_verified"])

    def test_declining_profits_score_observed_zero_not_missing(self):
        sectors=self.snapshots()
        changed=deepcopy(sectors[0])
        changed["features"]["industry_profit_growth_12m_pct"]=-35
        sectors[0]=changed
        row=preview(sectors,asof=self.day)["research_only_sectors"][0]
        self.assertEqual(row["provisional_15point_parts"]["profit_transition"],0)
        self.assertEqual(row["provisional_available_weight"],15)

    def test_missing_member_profits_cannot_receive_subset_points(self):
        cohort=deepcopy(self.cohort)
        cohort["industry_code"]="MISS00"
        cohort["members"][0]["annual_operating_profit"][1]["operating_profit_krw"]=None
        member_missing=evaluate_industry_cohort(
            cohort,asof=self.day,decision_at_utc=self.day+"T07:10:00Z")
        self.assertIsNone(
            member_missing["features"]["industry_profit_improving_issuer_ratio"])
        self.assertIsNone(
            member_missing["features"]["industry_recovery_largest_issuer_delta_share"])
        # In a plausible 6-sector group the price part remains visible;
        # unavailable profit evidence doesn't get zero-filled.
        sectors=self.snapshots(5)+[member_missing]
        result=preview(sectors,asof=self.day)
        row=[r for r in result["research_only_sectors"]
             if r["industry_code"]=="MISS00"][0]
        self.assertIsNone(row["provisional_15point_parts"]["profit_transition"])
        self.assertIsNone(row["provisional_15point_parts"]["improvement_diffusion"])
        self.assertIsNone(row["provisional_15point_parts"]["overheat_crowding_quality"])
        self.assertEqual(row["provisional_available_weight"],5)
        self.assertGreaterEqual(row["provisional_15point_parts"]["early_rotation"],0)

    def test_sector_concentration_penalizes_one_company_recovery(self):
        source=deepcopy(self.cohort)
        source["industry_code"]="SOLO"
        for i,m in enumerate(source["members"]):
            m["annual_operating_profit"][0]["operating_profit_krw"]=100
            m["annual_operating_profit"][1]["operating_profit_krw"]=500 if i==0 else 100
        r=evaluate_industry_cohort(
            source,asof=self.day,decision_at_utc=self.day+"T07:10:00Z")
        self.assertEqual(
            r["features"]["industry_recovery_largest_issuer_delta_share"],1)
        self.assertIn("profit_recovery_concentrated_in_one_issuer",
                      r["risk_flags_research"])
        rs=preview(self.snapshots(5)+[r],asof=self.day)
        target=[x for x in rs["research_only_sectors"]
                if x["industry_code"]=="SOLO"][0]
        self.assertEqual(
            target["provisional_15point_parts"]["overheat_crowding_quality"],1)

    def test_fake_authentication_cannot_unlock_live_issuer_scores(self):
        sectors=self.snapshots()
        wrong=deepcopy(sectors)
        wrong[0]["v3_scoring_authorized"]=True
        with self.assertRaisesRegex(IndustryTransitionError,"untrusted"):
            preview(wrong,asof=self.day)
        wrong=deepcopy(sectors)
        wrong[1]["features"]["industry_profit_improving_issuer_ratio"]=1.5
        with self.assertRaisesRegex(IndustryTransitionError,"out_of_range"):
            preview(wrong,asof=self.day)
        wrong=deepcopy(sectors)
        wrong.append(deepcopy(wrong[0]))
        with self.assertRaisesRegex(IndustryTransitionError,"duplicate"):
            preview(wrong,asof=self.day)
        wrong=deepcopy(sectors)
        wrong[0]["declared_member_count"]=20
        with self.assertRaisesRegex(IndustryTransitionError,"incomplete_sector"):
            preview(wrong,asof=self.day)

    def test_low_raw_data_coverage_cannot_fake_forecast_signal(self):
        missing=deepcopy(self.snapshots())
        missing[0]["features"]["industry_breadth_above_ma_ratio"]=None
        missing[0]["features"]["industry_early_rotation_signal"]=None
        missing[0]["features"]["industry_overheat_concentration_quality"]=None
        missing[0]["breadth_covered_issuers"]=None
        result=preview(missing,asof=self.day)
        row=result["research_only_sectors"][0]
        self.assertIsNone(row["provisional_15point_parts"]["early_rotation"])
        self.assertIsNone(row["provisional_15point_parts"]["overheat_crowding_quality"])
        self.assertEqual(row["provisional_available_weight"],7)
        self.assertFalse(result["approved_for_live_stock_scoring"])


if __name__=="__main__":
    unittest.main()
