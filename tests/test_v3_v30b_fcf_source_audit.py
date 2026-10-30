"""Offline three-source FCF integrity and no-premature-scoring regression."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from v3_v30b_fcf_source_audit import (
    audit_fcf_sources, FCFReconciliationError, main,
)

ROOT = Path(__file__).resolve().parents[1]


def sources():
    paths = (
        "v3-current-observed-all-listed-summary.json",
        "v3-dart-cashflow-gap-summary.json",
        "v3-dart-fcf-research-preview.json",
    )
    return tuple(json.loads((ROOT / "docs" / p).read_text(encoding="utf-8"))
                 for p in paths)


class FCFSourceAuditTests(unittest.TestCase):
    def setUp(self):
        positive_live_checks = {
            "test_current_published_source_counts_reconcile_without_issuing_scores",
            "test_no_unverified_issuer_or_money_values_are_republished",
            "test_modified_source_counts_dates_receipts_and_flags_fail_closed",
            "test_cli_uses_existing_public_files_only",
        }
        if (self._testMethodName in positive_live_checks and
                (self.observed.get("asof") != self.gap.get("asof") or
                 self.observed.get("asof") != self.preview.get("asof"))):
            self.skipTest("market_day_rolled_before_independent_fcf_sources")

    def test_older_fcf_sources_are_rejected_without_any_score(self):
        observed, gap, preview = (copy.deepcopy(self.observed),
                                  copy.deepcopy(self.gap),
                                  copy.deepcopy(self.preview))
        if observed.get("asof") == gap.get("asof") == preview.get("asof"):
            gap["asof"] = "2026-10-01"
        else:
            self.assertLess(gap["asof"], observed["asof"])
            self.assertLess(preview["asof"], observed["asof"])
        with self.assertRaisesRegex(FCFReconciliationError,
                                    "fcf_source_day_mismatch"):
            audit_fcf_sources(observed, gap, preview)

    @classmethod
    def setUpClass(cls):
        cls.observed, cls.gap, cls.preview = sources()

    def test_current_published_source_counts_reconcile_without_issuing_scores(self):
        r = audit_fcf_sources(self.observed, self.gap, self.preview)
        self.assertTrue(r["cashflow_snapshot_reconciled"])
        self.assertEqual(r["asof"], self.observed["asof"])
        self.assertEqual(r["attempted_company_years"],
                         self.gap["attempted_company_years"])
        self.assertEqual(r["fcf_observed_company_years"],
                         self.gap["observed_fcf_company_years"])
        self.assertEqual(r["fcf_missing_account_company_years"],
                         self.gap["missing_fcf_company_years"])
        self.assertEqual(r["missing_account_reason_counts"],
                         {k: v for k, v in self.gap["categories"].items()
                          if k not in ("fcf_observed_receipt_matched",
                                       "fcf_observed_receipt_unmatched")})
        self.assertEqual(r["financial_fcf4_observed_issuers"],
                         self.observed["fcf_observed_issuer_counts"]["financial4"])
        self.assertEqual(r["valuation_fcf5_observed_issuers"],
                         self.observed["fcf_observed_issuer_counts"]["valuation5"])
        self.assertFalse(r["approved_final_100point_scoring"])
        self.assertFalse(r["independent_dart_receipt_authentication"])
        self.assertFalse(r["historical_pit_or_corporate_action_certified"])
        self.assertIsNone(r["official_issuer_score"])
        self.assertEqual(r["provider_calls"], 0)
        self.assertTrue(r["read_only"])

    def test_no_unverified_issuer_or_money_values_are_republished(self):
        r = audit_fcf_sources(self.observed, self.gap, self.preview)
        text = json.dumps(r)
        self.assertNotIn(self.preview["rows"][0]["code"], text)
        self.assertNotIn("raw_observed_valuation_fcf_yield_pct", text)
        self.assertNotIn("rcept_no", text)
        self.assertNotIn("source_cashflow_sha256", text)
        self.assertFalse(r["raw_issuer_or_filing_rows_in_output"])

    def test_modified_source_counts_dates_receipts_and_flags_fail_closed(self):
        cases = [
            lambda o, g, p: g.update(asof="2026-10-26"),
            lambda o, g, p: g.update(source_cashflow_sha256="a" * 64),
            lambda o, g, p: g["categories"].update(
                missing_intangible_only=g["categories"]["missing_intangible_only"] + 1),
            lambda o, g, p: g["by_scope"]["OFS"].update(
                missing_both_capex=g["by_scope"]["OFS"]["missing_both_capex"] + 1),
            lambda o, g, p: g.update(missing_ifrs_account_not_interpreted_as_zero=False),
            lambda o, g, p: g["three_year_issuer_depth"].update(
                three_year_source_receipt_complete=1),
            lambda o, g, p: p.update(valuation_fcf5_research_candidate_issuer_count=0),
            lambda o, g, p: o["fcf_observed_issuer_counts"].update(financial4=0),
            lambda o, g, p: p["rows"][0].update(scoring_certified=True),
            lambda o, g, p: p["rows"][0].update(financial_fcf_assets_available_max=5),
            lambda o, g, p: p["rows"][0].update(valuation_fcf_yield_candidate_points=None),
            lambda o, g, p: p["rows"][0].update(code=p["rows"][1]["code"]),
            lambda o, g, p: p.update(real_fcf_assets_three_year_raw_issuer_count=None),
        ]
        for change in cases:
            with self.subTest(change=change):
                o, g, p = (copy.deepcopy(self.observed),
                           copy.deepcopy(self.gap),
                           copy.deepcopy(self.preview))
                change(o, g, p)
                with self.assertRaises(FCFReconciliationError) as cm:
                    audit_fcf_sources(o, g, p)
                self.assertRegex(str(cm.exception), r"^fcf_[a-z_]+$")

    def test_invalid_public_input_does_not_echo_source_payload(self):
        r = {"some_private_issuer": "SECRET"}
        with self.assertRaisesRegex(FCFReconciliationError, "fcf_public_inputs_invalid"):
            audit_fcf_sources(r, None, self.preview)

    def test_cli_uses_existing_public_files_only(self):
        with redirect_stdout(StringIO()) as buf:
            result = main(["--assert-unverified"])
        displayed = json.loads(buf.getvalue())
        self.assertEqual(result, displayed)
        self.assertEqual(result["schema"],
                         "v3-v30b-fcf-three-source-internal-reconciliation-0.1")
        self.assertEqual(result["provider_calls"], 0)
        self.assertIsNone(result["official_issuer_score"])


if __name__ == "__main__":
    unittest.main()
