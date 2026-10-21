"""Actual saved official DART profile x annual source join, no provider calls."""
import copy
import json
from pathlib import Path
import unittest

from v3_dart_industry_earnings_sample import (
    build,IndustryEarningsError,MIN_GROUP_ISSUERS_FOR_AGGREGATE,
)
from v3_dart_bulk_history import _stable_digest

DOCS=Path(__file__).resolve().parents[1]/"docs"


def sources():
    def load(name):
        return json.loads((DOCS/name).read_text(encoding="utf8"))
    return [load(n) for n in ("data.json","v22-market-catalog.json",
        "v3-dart-industry-classification.json","v3-dart-bulk-history.json")]


class ObservedIndustryEarningsSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market,cls.catalog,cls.codes,cls.bulk=sources()

    def test_real_profiles_earnings_matched_no_investment_score(self):
        out=build(self.market,self.catalog,self.codes,self.bulk)
        self.assertEqual(out["current_listed_total"],self.catalog["listed_total"])
        self.assertEqual(out["dart_current_industry_codes_observed"],
                         self.codes["observed_industry_codes"])
        self.assertGreater(out["mapped_industry_issuer_earnings_two_year_matched"],0)
        self.assertLessEqual(
            out["mapped_industry_issuer_earnings_two_year_matched"],
            out["dart_current_industry_codes_observed"])
        self.assertEqual(out["sector_15_points_earned_issuer_count"],0)
        self.assertFalse(out["full_market_industry_membership_pit_verified"])
        self.assertEqual(sum(out["industry_subweights_locked"].values()),15)
        self.assertEqual(out["fiscal_year_pair"],[2024,2025])
        self.assertEqual(out["source_sha256"]["industry_current_company_codes"],
                         _stable_digest(self.codes))
        self.assertEqual(out["source_sha256"]["dart_verified_major_account"],
                         _stable_digest(self.bulk))
        self.assertNotIn("corp_code",json.dumps(out))
        for item in out["groups"]:
            self.assertIsNone(item["group_15point_investment_score"])
            self.assertFalse(item["group_is_approved_historical_industry_cohort"])
            self.assertGreaterEqual(item["current_classified_issuers"],
                                    item["issuer_year_pair_observed"])
            self.assertEqual(item["current_classified_issuers"]-
                             item["issuer_year_pair_observed"],
                             item["unobserved_income_among_known_codes"])
            if item["issuer_year_pair_observed"]<MIN_GROUP_ISSUERS_FOR_AGGREGATE:
                self.assertIsNone(item[
                    "observed_earnings_improving_issuer_ratio_sample_only"])
                self.assertIsNone(item[
                    "improvement_breadth_lower_bound_known_classified_only"])
                self.assertIsNone(item[
                    "improvement_breadth_upper_bound_known_classified_only"])
                self.assertIsNone(item[
                    "sample_aggregate_operating_profit_growth_pct"])
            else:
                ratio=item["observed_earnings_improving_issuer_ratio_sample_only"]
                self.assertGreaterEqual(ratio,0)
                self.assertLessEqual(ratio,1)
                lo=item["improvement_breadth_lower_bound_known_classified_only"]
                hi=item["improvement_breadth_upper_bound_known_classified_only"]
                self.assertLessEqual(lo,ratio+0.000002)
                self.assertGreaterEqual(hi+0.000002,ratio)
                self.assertEqual(item["classified_sample_earnings_coverage_ratio"],
                                 round(item["issuer_year_pair_observed"]/
                                     item["current_classified_issuers"],6))
                if not item["observed_pair_scope_uniform"]:
                    self.assertIsNone(item[
                        "sample_aggregate_operating_profit_growth_pct"])

    def test_deceptive_industry_industry15_flag_rejected(self):
        fake=copy.deepcopy(self.codes)
        fake["industry_5433_points_authorized"]=True
        with self.assertRaises(IndustryEarningsError):
            build(self.market,self.catalog,fake,self.bulk)

    def test_tampered_industry_code_digest_rejected(self):
        wrong=copy.deepcopy(self.codes)
        key=sorted(wrong["records"])[0]
        wrong["records"][key]["industry_code"]="99999"
        with self.assertRaises(IndustryEarningsError):
            build(self.market,self.catalog,wrong,self.bulk)

    def test_mixed_financial_market_asof_or_data_leakage_rejected(self):
        wrong=copy.deepcopy(self.bulk)
        wrong["raw_source_rows_published"]=True
        with self.assertRaises(IndustryEarningsError):
            build(self.market,self.catalog,self.codes,wrong)
        wrong=copy.deepcopy(self.bulk)
        wrong["asof"]="2026-10-19"
        with self.assertRaises(IndustryEarningsError):
            build(self.market,self.catalog,self.codes,wrong)

    def test_absent_annual_earnings_not_imputed_to_nonzero(self):
        source=copy.deepcopy(self.bulk)
        some=next((k for k in self.codes["records"] if k in source["records"]),
                  None)
        self.assertIsNotNone(some)
        old=build(self.market,self.catalog,self.codes,self.bulk)
        source["records"][some]["years"].pop("2025",None)
        after=build(self.market,self.catalog,self.codes,source)
        self.assertLessEqual(
            after["mapped_industry_issuer_earnings_two_year_matched"],
            old["mapped_industry_issuer_earnings_two_year_matched"])
        self.assertEqual(after["sector_15_points_earned_issuer_count"],0)

if __name__=="__main__":
    unittest.main()
