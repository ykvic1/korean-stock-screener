"""Actual saved official DART profile x annual source join, no provider calls."""
import copy
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest

from v3_dart_industry_earnings_sample import (
    build,source_join_ready,main,IndustryEarningsError,MIN_GROUP_ISSUERS_FOR_AGGREGATE,
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

    def test_every_source_ingest_refreshes_official_earnings_pair_evidence(self):
        workflow=(DOCS.parent/".github"/"workflows"/
                  "v3-dart-industry-codes.yml").read_text(encoding="utf8")
        self.assertIn("cron: '6,21,36,51 * * * *'",workflow)
        self.assertIn("python v3_dart_industry_earnings_sample.py --mode write-if-ready",
                      workflow)
        self.assertIn("python v3_dart_industry_earnings_sample.py --mode verify-if-ready",
                      workflow)
        self.assertGreaterEqual(workflow.count(
            "git add docs/v3-dart-industry-classification.json docs/v3-dart-industry-earnings-sample.json"),2)
        self.assertLess(workflow.index(
            "python v3_dart_industry_codes.py --mode rebind"),
            workflow.rindex(
                "python v3_dart_industry_earnings_sample.py --mode write-if-ready"))
        self.assertIn("cancel-in-progress: false",workflow)

    def test_new_market_date_defers_old_annual_join_without_data_loss(self):
        market=copy.deepcopy(self.market)
        catalog=copy.deepcopy(self.catalog)
        market["date"]="2026-10-21"
        catalog["date"]="2026-10-21"
        for stock in market["stocks"]:
            stock["price_date"]="2026-10-21"
        self.assertTrue(source_join_ready(
            self.market,self.catalog,self.codes,self.bulk))
        self.assertFalse(source_join_ready(
            market,catalog,self.codes,self.bulk))
        # The old "build" operation is still STRICT, even when this
        # explicitly bounded, standalone publication option can defer.
        with self.assertRaises(IndustryEarningsError):
            build(market,catalog,self.codes,self.bulk)
        original=DOCS/"v3-dart-industry-earnings-sample.json"
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            contents={
                "data.json":market,
                "v22-market-catalog.json":catalog,
                "v3-dart-industry-classification.json":self.codes,
                "v3-dart-bulk-history.json":self.bulk,
            }
            for name,obj in contents.items():
                (folder/name).write_text(json.dumps(obj),encoding="utf8")
            prior=original.read_bytes()
            dest=folder/original.name
            dest.write_bytes(prior)
            with redirect_stdout(io.StringIO()) as captured:
                main(["--docs",str(folder),"--mode","write-if-ready"])
            notice=json.loads(captured.getvalue().strip())
            self.assertEqual(notice["status"],"deferred_industry_earnings_join")
            self.assertEqual(notice["market_asof"],"2026-10-21")
            self.assertEqual(notice["annual_source_asof"],"2026-10-20")
            self.assertFalse(notice["industry_score_certified"])
            self.assertEqual(dest.read_bytes(),prior)

    def test_bad_future_or_corrupt_annual_source_not_silently_deferred(self):
        market=copy.deepcopy(self.market)
        catalog=copy.deepcopy(self.catalog)
        market["date"]=catalog["date"]="2026-10-21"
        for stock in market["stocks"]:
            stock["price_date"]="2026-10-21"
        bad=copy.deepcopy(self.bulk)
        bad["asof"]="2026-10-22"
        with self.assertRaises(IndustryEarningsError):
            source_join_ready(market,catalog,self.codes,bad)
        bad=copy.deepcopy(self.bulk)
        bad["raw_source_rows_published"]=True
        with self.assertRaises(IndustryEarningsError):
            source_join_ready(market,catalog,self.codes,bad)
        bad=copy.deepcopy(self.bulk)
        bad["asof"]="2026-10-01"
        with self.assertRaises(IndustryEarningsError):
            source_join_ready(market,catalog,self.codes,bad)

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
