"""No-network tests: prior-year comparatives are candidates, not FCF facts."""
from collections import Counter
import copy
from datetime import date, timedelta
import json
from pathlib import Path
import unittest

import v3_dart_fcf_comparative_probe as probe


DOCS = Path(__file__).resolve().parents[1] / "docs"
INPUTS = (
    "data.json", "v22-market-catalog.json", "v3-dart-bulk-history.json",
    "v3-dart-book-reference.json", "v3-dart-cashflow-reference.json",
    "v3-operational-research-scores.json", "v3-operational-research-comparison.json",
    "v3-operational-evidence-priority.json",
)


def row(*, kind="ppe", current="20", last="10", before="0", **changes):
    ids = {
        "ppe": "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
        "intangible": "ifrs-full_PurchaseOfIntangibleAssets",
        "cfo": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
    }
    value = {
        "corp_code": "00123456", "stock_code": "000100",
        "bsns_year": "2025", "reprt_code": "11011", "fs_div": "CFS",
        "sj_div": "CF", "account_detail": "-", "currency": "KRW",
        "rcept_no": "20260312001236", "account_id": ids[kind],
        "thstrm_amount": current, "frmtrm_amount": last,
        "bfefrmtrm_amount": before,
    }
    value.update(changes)
    return value


def inspect(rows, *, missing=None, current=None):
    return probe.inspect(
        {"status": "000", "list": rows},
        corp="00123456", code="000100", scope="CFS",
        receipt="20260312001236",
        current=current or {"ppe_capex_krw": 20, "intangible_capex_krw": 0,
                            "operating_cashflow_krw": -15},
        missing=missing or {"2024": ("ppe_capex_krw",),
                            "2023": ("ppe_capex_krw",)},
    )


def filing(receipt, title):
    return {"corp_code": "00123456", "rcept_no": receipt,
            "rcept_dt": receipt[:8], "report_nm": title}


def annual_listing(rows):
    return {"status": "000", "total_count": str(len(rows)),
            "total_page": str((len(rows) + 99) // 100), "page_count": "100",
            "page_no": "1", "list": rows}


def annual_audit(payload):
    return probe.audit_annual_receipts(payload, corp="00123456",
        original_receipts={"2023": "20240315000001", "2024": "20250315000002"},
        asof="2026-11-06")


class ComparativeProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = [json.loads((DOCS / n).read_text(encoding="utf8"))
                      for n in INPUTS]

    def test_reconciled_live_ledger_exposes_bounded_research_cohorts(self):
        args = self.inputs
        if not probe.source_day_aligned(*args):
            self.skipTest("normal market/DART source-day rollover is still pending")
        try:
            day, members, sample, total = probe.select_targets(
                *args, limit=20, cohort_index=0)
        except probe.ComparativeProbeError as error:
            if str(error) in ("unreconciled_comparative_priority",
                              "untrusted_comparative_cashflow_source"):
                self.skipTest("saved-source writer is currently reconciling")
            raise
        self.assertEqual(day, args[1]["date"])
        self.assertGreaterEqual(total, 0)
        self.assertLessEqual(total, len(args[-1]["single_component_queue"]))
        self.assertEqual(len(sample), min(total, 20))
        self.assertEqual(len({s[0] for s in sample}), len(sample))
        self.assertTrue(all(s[0] in members and s[4] for s in sample))
        first = {s[0] for s in sample}
        second = {s[0] for s in probe.select_targets(
            *args, limit=20, cohort_index=1)[2]}
        if total >= 40:
            self.assertFalse(first & second)
        self.assertEqual(probe.run(["--docs", str(DOCS),
                                    "--mode", "dry-run"])["provider_calls"], 0)

    def test_stale_or_tampered_priority_makes_zero_requests(self):
        args = copy.deepcopy(self.inputs)
        args[-1]["operational_source_digest"] = "untrusted"
        with self.assertRaisesRegex(probe.ComparativeProbeError,
                                    "unreconciled_comparative_priority"):
            probe.select_targets(*args)
        with self.assertRaisesRegex(probe.ComparativeProbeError,
                                    "invalid_comparative_budget"):
            probe.select_targets(*self.inputs, limit=21)

    def test_known_source_day_lag_waits_without_provider_calls(self):
        args = copy.deepcopy(self.inputs)
        # Check only the pure date gate, independent of a real daily
        # collector rollover happening while this test runs.
        day = args[1]["date"]
        args[0]["date"] = day
        for idx in (2, 3, 4, 5, 6, 7):
            args[idx]["asof"] = day
        self.assertTrue(probe.source_day_aligned(*args))
        args[4]["asof"] = (date.fromisoformat(day)-timedelta(days=1)).isoformat()
        self.assertFalse(probe.source_day_aligned(*args))
        args[4]["asof"] = "2035-01-01"
        with self.assertRaisesRegex(probe.ComparativeProbeError,
                                    "expired_comparative_source_day"):
            probe.source_day_aligned(*args)

    def test_same_receipt_same_2025_fact_is_required(self):
        counts = inspect([row()])
        self.assertEqual(counts["candidate_2024_ppe_capex_krw"], 1)
        self.assertEqual(counts["candidate_2023_ppe_capex_krw"], 1)
        self.assertEqual(counts["checked_2024_ppe_capex_krw"], 1)
        self.assertEqual(counts["checked_2023_ppe_capex_krw"], 1)
        self.assertEqual(counts["issuers_with_any_comparative_candidate"], 1)
        self.assertEqual(counts["issuers_with_all_missing_comparatives"], 1)
        self.assertEqual(inspect([row(current="21")])["candidate_2024_ppe_capex_krw"], 0)
        self.assertEqual(inspect([row(last="-10")])["candidate_2024_ppe_capex_krw"], 0)

    def test_issuer_completeness_is_not_inferred_from_partial_candidates(self):
        partial = inspect([row(last="-10")])
        self.assertEqual(partial["issuers_with_any_comparative_candidate"], 1)
        self.assertEqual(partial["issuers_with_all_missing_comparatives"], 0)
        wrong_anchor = inspect([row(current="21")])
        self.assertEqual(wrong_anchor["issuers_with_any_comparative_candidate"], 0)
        self.assertEqual(wrong_anchor["issuers_with_all_missing_comparatives"], 0)
        self.assertEqual(inspect([])["issuers_with_all_missing_comparatives"], 0)

    def test_official_list_matches_each_original_annual_receipt_without_promoting_facts(self):
        counts = annual_audit(annual_listing([
            filing("20240315000001", "사업보고서 (2023.12)"),
            filing("20250315000002", "사업보고서 (2024.12)"),
            filing("20250601000003", "[기재정정]사업보고서 (2024.12)"),
        ]))
        self.assertEqual(counts["matched_historical_annual_receipt_2023"], 1)
        self.assertEqual(counts["matched_historical_annual_receipt_2024"], 1)
        self.assertEqual(counts["issuers_with_all_historical_annual_receipts_listed"], 1)
        self.assertEqual(counts["later_annual_filing_exists_2024"], 1)
        self.assertEqual(counts["later_annual_filing_exists_2023"], 0)
        self.assertEqual(counts["source_receipt_earliest_listed_2024"], 1)

    def test_missing_year_annual_receipt_cannot_be_filled_with_other_filing(self):
        counts = annual_audit(annual_listing([
            filing("20240315000001", "사업보고서 (2023.12)"),
            filing("20250315000199", "사업보고서 (2024.12)"),
        ]))
        self.assertEqual(counts["matched_historical_annual_receipt_2023"], 1)
        self.assertEqual(counts["matched_historical_annual_receipt_2024"], 0)
        self.assertEqual(counts["other_annual_filing_only_2024"], 1)
        self.assertEqual(counts["issuers_with_all_historical_annual_receipts_listed"], 0)
        self.assertEqual(annual_audit({"status":"013"})[
            "issuers_with_all_historical_annual_receipts_listed"], 0)

    def test_pagination_identity_and_future_receipts_fail_closed(self):
        listing = annual_listing([filing("20240315000001", "사업보고서 (2023.12)")])
        listing["total_count"], listing["total_page"] = "101", "2"
        self.assertEqual(annual_audit(listing)[
            "annual_receipt_list_censored_multi_page"], 1)
        for changes, reason in [
            ({"corp_code": "99999999"}, "issuer_mismatch"),
            ({"rcept_dt": "20240316"}, "invalid_annual_receipt_metadata"),
            ({"rcept_no": "20270315000001", "rcept_dt": "20270315"},
                "future_annual_receipt_metadata"),
        ]:
            with self.subTest(changes=changes):
                row_value = filing("20240315000001", "사업보고서 (2023.12)")
                row_value.update(changes)
                with self.assertRaisesRegex(probe.ComparativeProbeError, reason):
                    annual_audit(annual_listing([row_value]))
        with self.assertRaisesRegex(probe.ComparativeProbeError,
                                    "official_annual_receipt_list_unavailable"):
            annual_audit({"status": "020"})

    def test_current_zero_or_negative_cfo_remains_valid_observation(self):
        missing = {"2023": ("intangible_capex_krw", "operating_cashflow_krw")}
        counts = inspect([
            row(kind="intangible", current="0", before="0"),
            row(kind="cfo", current="-15", before="-35"),
        ], missing=missing)
        self.assertEqual(counts["candidate_2023_intangible_capex_krw"], 1)
        self.assertEqual(counts["candidate_2023_operating_cashflow_krw"], 1)

    def test_conflicting_comparatives_are_not_candidates(self):
        counts = inspect([row(last="10"), row(last="11")])
        self.assertEqual(counts["candidate_2024_ppe_capex_krw"], 0)
        self.assertEqual(counts["ambiguous_2024_ppe_capex_krw"], 1)
        self.assertEqual(counts["candidate_2023_ppe_capex_krw"], 1)

    def test_wrong_identity_currency_receipt_and_provider_status_fail_closed(self):
        for changes, reason in [
            ({"corp_code": "99999999"}, "company_or_period"),
            ({"fs_div": "OFS"}, "company_or_period"),
            ({"bsns_year": "2024"}, "company_or_period"),
            ({"currency": "USD"}, "currency_or_receipt"),
            ({"rcept_no": "20260311001236"}, "currency_or_receipt"),
        ]:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(probe.ComparativeProbeError, reason):
                    inspect([row(**changes)])
        with self.assertRaisesRegex(probe.ComparativeProbeError,
                                    "comparative_source_unavailable"):
            probe.inspect({"status": "020"}, corp="00123456", code="000100",
                          scope="CFS", receipt="20260312001236",
                          current={}, missing={})

    def test_irrelevant_accounts_cannot_supply_missing_facts(self):
        counts = inspect([row(sj_div="BS"), row(account_detail="기업별"),
                          row(account_id="-표준계정코드 미사용-",
                              account_nm="유형자산 취득")])
        self.assertEqual(counts["candidate_2024_ppe_capex_krw"], 0)
        accepted = inspect([row(account_id="-표준계정코드 미사용-",
                                account_nm="유형자산의 취득")])
        self.assertEqual(accepted["candidate_2024_ppe_capex_krw"], 1)

    def test_automated_research_never_publishes_amounts_or_scores(self):
        path = DOCS.parent / ".github/workflows/v3-dart-fcf-comparative-probe.yml"
        workflow = path.read_text(encoding="utf8")
        self.assertIn("contents: read", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("python v3_dart_fcf_comparative_probe.py --mode", workflow)
        self.assertIn("--limit 20", workflow)
        self.assertIn("--audit-annual-receipts", workflow)
        self.assertIn("| tee /tmp/v3-cf-comparative-counts.json", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("git add", workflow)
        self.assertNotIn("docs/v3-dart-cashflow-reference.json", workflow)
        self.assertNotIn("docs/v3-operational-research-scores.json", workflow)


if __name__ == "__main__":
    unittest.main()
