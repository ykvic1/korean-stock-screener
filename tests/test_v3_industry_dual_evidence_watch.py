"""Regression tests for source-joined CURRENT-industry observations only."""
import copy
import csv
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from v3_industry_dual_evidence_watch import (
    CrossSourceError, build, main, to_csv,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def snapshot():
    return [
        json.loads((DOCS / path).read_text(encoding="utf8")) for path in (
            "v3-industry-saved-profit-gap-summary.json",
            "v3-industry-observed-relative-strength.json",
        )
    ]


class DualEvidenceWatchTests(unittest.TestCase):
    def test_actual_saved_source_same_day_with_real_group_reconciliation(self):
        earnings, prices = snapshot()
        report = build(earnings, prices)
        self.assertEqual(report["listed_total"], earnings["listed_total"])
        self.assertEqual(report["asof"], earnings["asof"])
        self.assertEqual(report["current_company_code_market_groups"],
                         prices["groups_total"])
        self.assertEqual(report["price_relative20_60_verified_samples"],
                         prices["paired_20_60_observations"])
        self.assertEqual(report["two_year_profit_verified_samples"],
                         earnings["two_year_operating_profit_verified_any_revenue"])
        self.assertEqual(report["price_recovery_watch_groups"],
                         prices["groups_early_recovery_watch"])
        self.assertLessEqual(report["dual_evidence_watch_groups"],
                             report["price_recovery_watch_groups"])
        self.assertEqual(
            sum(row["observed_state"] == "dual_evidence_watch"
                for row in report["groups"]),
            report["dual_evidence_watch_groups"])
        self.assertFalse(report["historical_krx_sector_pit_verified"])
        self.assertFalse(report["official_industry15_points_authorized"])
        self.assertFalse(report["investable_stock_screener_score_authorized"])
        self.assertEqual(report["external_provider_calls"], 0)
        self.assertTrue(all(row["industry15_points"] is None
                            and row["investment_buy_signal"] is False
                            for row in report["groups"]))
        rendered = to_csv(report)
        self.assertTrue(rendered.startswith("\ufeff"))
        records = list(csv.DictReader(StringIO(rendered.removeprefix("\ufeff"))))
        self.assertEqual(len(records), report["current_company_code_market_groups"])
        self.assertTrue(all(row["과거KRX업종구성_PIT"] == "미확인"
                            and row["산업15점"] == "" for row in records))
        self.assertNotIn("ISU_CD", rendered)
        self.assertNotIn("TDD_CLSPRC", rendered)

    def test_mismatched_dates_group_sizes_or_original_roster_rejected(self):
        e, p = snapshot()
        bad = copy.deepcopy(p)
        bad["asof"] = "2026-10-22"
        with self.assertRaises(CrossSourceError):
            build(e, bad)
        bad = copy.deepcopy(p)
        bad["source_sha256"]["catalog"] = "0"*64
        with self.assertRaises(CrossSourceError):
            build(e, bad)
        bad = copy.deepcopy(p)
        bad["groups"][0]["current_listed_issuers"] += 1
        with self.assertRaises(CrossSourceError):
            build(e, bad)
        bad = copy.deepcopy(p)
        bad["groups"].append(copy.deepcopy(bad["groups"][0]))
        with self.assertRaises(CrossSourceError):
            build(e, bad)
        bad = copy.deepcopy(e)
        bad["groups"][0]["classified"] += 1
        with self.assertRaises(CrossSourceError):
            build(bad, p)

    def test_missing_proof_or_fake_industry_score_fails_closed(self):
        e, p = snapshot()
        bad = copy.deepcopy(e)
        bad["actual_industry_15_points_authorized"] = True
        with self.assertRaises(CrossSourceError):
            build(bad, p)
        bad = copy.deepcopy(p)
        bad["historical_krx_sector_membership_pit_verified"] = True
        with self.assertRaises(CrossSourceError):
            build(e, bad)
        bad = copy.deepcopy(p)
        bad["new_provider_calls"] = 1
        with self.assertRaises(CrossSourceError):
            build(e, bad)
        bad = copy.deepcopy(p)
        bad["groups"][0]["official_industry_15_points"] = 7
        with self.assertRaises(CrossSourceError):
            build(e, bad)

    def test_missing_sample_has_no_fabricated_recovery(self):
        e, p = snapshot()
        result = build(e, p)
        for row in result["groups"]:
            if row["verified_relative20_60_sample_issuers"] < 3:
                self.assertIsNone(row["market_relative20_median_sample_pct"])
                self.assertIsNone(row["market_relative60_median_sample_pct"])
                self.assertNotEqual(row["observed_state"], "dual_evidence_watch")
        altered = copy.deepcopy(p)
        index = next(i for i, row in enumerate(altered["groups"])
                     if row["early_rotation_research_state"]
                        == "early_relative_recovery_watch")
        altered["groups"][index]["early_rotation_research_state"] = (
            "no_early_recovery_pattern_observed"
        )
        with self.assertRaises(CrossSourceError):
            build(e, altered)

    def test_saved_file_verify_mode_tamper_detected(self):
        e, p = snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"v3-industry-saved-profit-gap-summary.json").write_text(
                json.dumps(e), encoding="utf8")
            (root/"v3-industry-observed-relative-strength.json").write_text(
                json.dumps(p), encoding="utf8")
            main(["--docs", str(root), "--mode", "write"])
            main(["--docs", str(root), "--mode", "verify"])
            file = root/"v3-industry-dual-evidence-watch.csv"
            file.write_text("modified", encoding="utf8")
            with self.assertRaises(CrossSourceError):
                main(["--docs", str(root), "--mode", "verify"])

    def test_workflow_no_api_no_forced_push_no_live_scoring(self):
        flow = (ROOT / ".github/workflows/v3-industry-dual-evidence-watch.yml").read_text(
            encoding="utf8")
        self.assertIn("v3_industry_dual_evidence_watch.py --mode write", flow)
        self.assertIn("v3_industry_dual_evidence_watch.py --mode verify", flow)
        self.assertIn("v3-industry-dual-evidence-watch.csv", flow)
        self.assertNotIn("DART_API_KEY", flow)
        self.assertNotIn("KRX_API_KEY", flow)
        self.assertNotIn("push --force", flow)


if __name__ == "__main__":
    unittest.main()
