"""v2.2 entire stock universe accounting and fail-closed regressions."""
import copy
import json
import tempfile
from pathlib import Path
import unittest

from v22_full_universe_audit import audit_published_universe, UniverseAccountingError, main


def sample(*,missing=0):
    stocks=[
        {"code":"005930","name":"Synthetic KOSPI","market":"KOSPI",
         "price":65000,"price_date":"2026-10-16","flows_confirmed":False,
         "pbr":1.25},
        {"code":"247540","name":"Synthetic KOSDAQ","market":"KOSDAQ",
         "price":12000,"price_date":"2026-10-16","flows_confirmed":True,
         "pbr":None},
    ]
    return {
        "version":"2.0","status":"success","date":"2026-10-16",
        "candidate_count":2,"history_usable_count":2,"stocks":stocks,
        "eligible_count":5+missing,"examined_count":5,
        "history_deferred_count":missing,"history_scan_complete":missing==0,
        "error_count":2,
        "collection_errors":[["000003","가격이력","TimeoutError"],
                             ["005930","기관·외국인 수급","Unverified"]],
        "history_exclusions":[["000004","insufficient_valid_price_history"],
                              ["000005","insufficient_valid_price_history"]],
        "flow_confirmed_count":1,"fundamental_count":1,
    }


class MarketAllStockAccountAudit(unittest.TestCase):
    def test_all_attempted_separate_price_failure_vs_flow_failure(self):
        r=audit_published_universe(sample())
        self.assertEqual(r["market_listing_size"],5)
        self.assertEqual(r["attempted_stock_count"],5)
        self.assertEqual(r["priced_current_session_count"],2)
        self.assertEqual(r["not_priced_total"],3)
        self.assertEqual(r["price_failure_count"],1)
        self.assertEqual(r["insufficient_price_history_count"],2)
        self.assertEqual(r["flow_unavailable_in_errors_count"],1)
        self.assertEqual(r["flow_confirmed_count"],1)
        self.assertEqual(r["priced_by_market"],{"KOSPI":1,"KOSDAQ":1})
        self.assertTrue(r["all_attempted_stock_codes_reconciled_once"])
        for flag in ("independent_historical_prices_verified",
                     "full_5_to_10_year_history_verified",
                     "all_stock_corporate_actions_verified",
                     "official_regular_only_volume_verified",
                     "historical_issuer_delisting_pit_verified",
                     "foreign_institution_250_day_history_verified",
                     "v3_full_score_release_ready",
                     "private_or_vendor_raw_copied"):
            self.assertFalse(r[flag])
        self.assertNotIn("005930",json.dumps(r))
        self.assertNotIn("247540",json.dumps(r))

    def test_missing_entire_market_job_is_unattempted_not_price_fail(self):
        r=audit_published_universe(sample(missing=100))
        self.assertEqual(r["market_listing_size"],105)
        self.assertEqual(r["unattempted_batch_count"],100)
        self.assertEqual(r["not_priced_total"],103)
        self.assertEqual(r["price_coverage_pct"],1.9)
        self.assertFalse(r["complete_batch_census"])

    def test_duplicate_valid_code_rejected(self):
        d=sample()
        d["stocks"].append(copy.deepcopy(d["stocks"][0]))
        d["candidate_count"]+=1
        d["history_usable_count"]+=1
        with self.assertRaisesRegex(UniverseAccountingError,"duplicate_successful_stock"):
            audit_published_universe(d)

    def test_same_code_in_price_and_insufficient_rejected(self):
        d=sample()
        d["history_exclusions"][0][0]="000003"
        with self.assertRaisesRegex(UniverseAccountingError,"duplicate_excluded_stock"):
            audit_published_universe(d)

    def test_inconsistent_attempt_and_eligible_rejected(self):
        d=sample()
        d["eligible_count"]=6
        with self.assertRaisesRegex(UniverseAccountingError,"universe_count_mismatch"):
            audit_published_universe(d)

    def test_orphan_price_or_flow_rejected(self):
        d=sample()
        d["collection_errors"][1][0]="000099"
        with self.assertRaisesRegex(UniverseAccountingError,"orphan_flow_failure"):
            audit_published_universe(d)

    def test_missing_price_failure_no_silent_normalization(self):
        d=sample()
        d["collection_errors"]=d["collection_errors"][1:]
        d["error_count"]-=1
        with self.assertRaisesRegex(UniverseAccountingError,"attempted_stock_accounting_mismatch"):
            audit_published_universe(d)

    def test_stale_date_zero_and_bad_prices_rejected(self):
        d=sample()
        d["stocks"][0]["price_date"]="2026-10-15"
        with self.assertRaisesRegex(UniverseAccountingError,"mismatched_price_asof"):
            audit_published_universe(d)
        d=sample()
        d["stocks"][0]["price"]=-1
        with self.assertRaisesRegex(UniverseAccountingError,"invalid_stock_price"):
            audit_published_universe(d)

    def test_flow_and_fundamental_summary_must_match(self):
        d=sample()
        d["flow_confirmed_count"]=2
        with self.assertRaisesRegex(UniverseAccountingError,"flow_count_mismatch"):
            audit_published_universe(d)
        d=sample()
        d["fundamental_count"]=2
        with self.assertRaisesRegex(UniverseAccountingError,"fundamental_count_mismatch"):
            audit_published_universe(d)

    def test_failure_is_aggregated_without_vendor_exception(self):
        d=sample()
        d["collection_errors"][0][2]="https://evil/SECRET_KRX_TOKEN"
        r=audit_published_universe(d)
        self.assertEqual(r["price_issue_types"],{"Other_or_stale":1})
        self.assertNotIn("SECRET_KRX_TOKEN",json.dumps(r))

    def test_cli_generates_only_aggregate_report_and_handles_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/"data.json"
            dst=Path(tmp)/"coverage.json"
            src.write_text(json.dumps(sample()),encoding="utf-8")
            self.assertEqual(main(["--daily",str(src),"--output",str(dst)]),0)
            out=dst.read_text()
            self.assertIn('"market_listing_size": 5',out)
            self.assertNotIn("Synthetic KOSPI",out)
            self.assertNotIn("005930",out)
            src.write_text("{}")
            self.assertEqual(main(["--daily",str(src),"--output",str(dst)]),1)


if __name__=="__main__":
    unittest.main()
