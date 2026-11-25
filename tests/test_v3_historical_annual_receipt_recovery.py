from unittest import TestCase

from official_probe.clients import ProbeError
from v3_frozen_primary_statements import COMPARATIVE_COLUMNS, compare
from v3_historical_annual_receipt_recovery import choose_version

CORP = "00123456"
CODE = "123456"
LATER = "20260325000001"


def filing(receipt, title="사업보고서 (2024.12)", **overrides):
    return dict({"corp_code": CORP, "stock_code": CODE, "corp_cls": "Y",
                 "report_nm": title, "rcept_no": receipt, "rcept_dt": receipt[:8]}, **overrides)


def choose(*rows):
    return choose_version({"status": "000", "total_page": 1, "total_count": len(rows), "list": list(rows)},
                          corp=CORP, code=CODE, market="KOSPI", later=LATER)


class HistoricalAnnualRecoveryTests(TestCase):
    def test_latest_available_body_beats_attachment_and_future_body(self):
        result = choose(
            filing(LATER, "[기재정정]사업보고서 (2024.12)"),
            filing("20251113000007", "[첨부정정]사업보고서 (2024.12)"),
            filing("20250701000001", "[기재정정]사업보고서 (2024.12)"),
            filing("20250319000001"),
            filing("20250327000001", "사업보고서 (2023.12)"),
        )
        self.assertEqual(result["selected_receipt"], "20250701000001")
        self.assertEqual(len(result["versions"]), 4)

    def test_cutoff_date_body_requires_publication_time(self):
        result = choose(filing(LATER), filing("20250319000001"),
                        filing("20251113000007", "[기재정정]사업보고서 (2024.12)"))
        self.assertEqual(result["status"], "same_day_body_publication_time_unverified")
        self.assertIsNone(result["selected_receipt"])

    def test_period_is_bound_to_later_receipt(self):
        result = choose(filing(LATER, "[기재정정]사업보고서 (2025.03)"),
                        filing("20250701000001", "사업보고서 (2025.03)"),
                        filing("20250319000001"))
        self.assertEqual(result["report_period"], "2025-03")
        self.assertEqual(result["selected_receipt"], "20250701000001")

    def test_same_day_body_tie_and_missing_original_remain_unresolved(self):
        for revisions, status in (
            ([filing("20250319000001"), filing("20250612000100", "[기재정정]사업보고서 (2024.12)"),
              filing("20250612000300", "[기재정정]사업보고서 (2024.12)")], "multiple_bodies_on_last_prior_day"),
            ([filing("20250612000100", "[기재정정]사업보고서 (2024.12)")], "original_chain_unresolved"),
        ):
            with self.subTest(status=status):
                self.assertEqual(choose(filing(LATER), *revisions)["status"], status)

    def test_other_issuer_and_short_response_cannot_supply_evidence(self):
        original = filing("20250319000001")
        with self.assertRaises(ProbeError):
            choose(filing(LATER), dict(original, corp_code="00999999"))
        with self.assertRaises(ProbeError):
            choose_version({"status": "000", "total_page": 1, "total_count": 3,
                            "list": [filing(LATER), original]},
                           corp=CORP, code=CODE, market="KOSPI", later=LATER)
        self.assertEqual(choose(original)["status"], "missing_retained_receipt_in_official_list")


class FrozenPrimaryStatementComparisonTests(TestCase):
    def test_original_annual_columns_remain_distinct(self):
        receipt = "20250319000001"
        row = {
            "rcept_no": receipt, "corp_code": CORP, "reprt_code": "11011",
            "bsns_year": "2024", "fs_div": "CFS", "sj_div": "IS",
            "account_id": "ifrs-full_Revenue", "currency": "KRW",
            "thstrm_amount": "300", "frmtrm_amount": "200", "bfefrmtrm_amount": "100",
        }
        later = dict(row, rcept_no=LATER, frmtrm_amount="999")
        for year, amount in (("2023", 200), ("2022", 100)):
            with self.subTest(year=year):
                result = compare([later, row], CORP, receipt, "CFS", {"revenue": amount},
                                 amount_column=COMPARATIVE_COLUMNS[year])
                self.assertEqual(result["revenue"], "matched")
        self.assertEqual(compare([row], CORP, receipt, "CFS", {"revenue": 200})["revenue"],
                         "different_value")


class FrozenComparativeEvidenceContractTests(TestCase):
    def test_saved_comparative_counts_reconcile_without_certification(self):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "ops/v30b-fixed-pit-comparative-primary-statement-totals.json").read_text())
        self.assertEqual(data["anchor"], "2025-11-13")
        self.assertEqual(data["development_cutoff"], "2026-11-20")
        self.assertEqual(data["issuers"], 2267)
        self.assertEqual(sum(data["statuses"].values()), data["issuers"])
        compared = data["statuses"]["compared"]
        self.assertEqual(compared, 2122)
        self.assertEqual(set(data["comparative_primary_statement_fields"]), {"2022", "2023"})
        for fields in [data["primary_statement_fields"], *data["comparative_primary_statement_fields"].values()]:
            self.assertEqual(set(fields), {"assets", "liabilities", "equity", "operating_income", "revenue"})
            for outcomes in fields.values():
                self.assertEqual(sum(outcomes.values()), compared)
                self.assertTrue(all(type(n) is int and n >= 0 for n in outcomes.values()))
        self.assertGreater(data["comparative_primary_statement_fields"]["2022"]["operating_income"]["different_value"], 0)
        self.assertFalse(data["historical_point_in_time_certified"])
        self.assertEqual(data["new_provider_calls"], 0)
