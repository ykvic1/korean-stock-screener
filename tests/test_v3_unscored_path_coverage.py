"""Offline-only coverage report contract: no new points, no raw OHLCV."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from v3_unscored_path_observations import observe
from v3_unscored_path_coverage import PathCoverageError, summarize

DAY = "2026-10-16"
ROOT = Path(__file__).resolve().parents[1]


def evidence():
    last = date.fromisoformat(DAY)
    days = [last-timedelta(days=120-i) for i in range(121)]
    closes = [100*(1.003**i) for i in range(121)]
    volume = [100.0]*121
    volume[105] = 300.0
    return observe(closes, volume, asof=DAY, session_days=days)


def daily():
    obs = evidence()
    assert obs is not None
    return {
        "status": "success", "date": DAY,
        "eligible_count": 3, "examined_count": 3, "candidate_count": 2,
        "history_scan_complete": False,
        "stocks": [
            {"code": "000001", "price_date": DAY,
             "v3_unscored_path_observations": obs},
            {"code": "000002", "price_date": DAY,
             "v3_unscored_path_observations": None},
        ],
        "v3_unscored_path_source": {
            "schema": "v3-unscored-price-path-evidence-0.1",
            "observed_issuer_count": 1, "unavailable_issuer_count": 1,
            "additional_volume_or_momentum_points_authorized": 0,
            "raw_ohlcv_exported": False,
            "source_exchange_and_adjustments_independently_verified": False,
        },
    }


class PricePathCoverageTests(unittest.TestCase):
    def test_partial_daily_counts_are_explicitly_not_a_score(self):
        result = summarize(daily())
        self.assertEqual(result["observed_121_session_paths"], 1)
        self.assertEqual(len(result["daily_source_sha256"]), 64)
        modified = daily()
        modified["stocks"][0]["reference_price"] = 123.4
        self.assertNotEqual(
            summarize(modified)["daily_source_sha256"],
            result["daily_source_sha256"],
            "unchanged coverage counts cannot authenticate another price file")
        self.assertEqual(result["unavailable_121_session_paths"], 1)
        self.assertEqual(result["path_coverage_pct_of_current"], 50.0)
        self.assertFalse(result["market_scan_complete"])
        self.assertEqual(result["additional_volume_or_momentum_points_authorized"], 0)
        self.assertFalse(result["investment_grade_score_certified"])
        self.assertFalse(result["corporate_actions_adjusted_verified"])
        self.assertEqual(result["up_days20_majority_issuer_count"], 1)

    def test_new_rejection_diagnostics_preserve_old_receipt_compatibility(self):
        old=daily()
        old_summary=summarize(old)
        self.assertNotIn("unavailable_path_reason_counts",old_summary)
        current=copy.deepcopy(old)
        current["stocks"][0]["v3_unscored_path_unavailable_reason"]=None
        current["stocks"][1]["v3_unscored_path_unavailable_reason"]="insufficient_121_sessions"
        report=summarize(current)
        self.assertEqual(report["unavailable_path_reason_counts"],
                         {"insufficient_121_sessions":1})
        self.assertEqual(report["unavailable_121_session_paths"],1)
        self.assertEqual(report["reason_diagnostics_scored_points"],0)
        self.assertFalse(report["reason_diagnostics_independently_verified"])
        self.assertNotEqual(report["daily_source_sha256"],old_summary["daily_source_sha256"])
        partial=copy.deepcopy(current)
        partial["stocks"][1].pop("v3_unscored_path_unavailable_reason")
        with self.assertRaisesRegex(PathCoverageError,"partially_recorded"):
            summarize(partial)
        forged=copy.deepcopy(current)
        forged["stocks"][1]["v3_unscored_path_unavailable_reason"]="API_KEY"
        with self.assertRaisesRegex(PathCoverageError,"invalid_path_unavailability"):
            summarize(forged)
        forged=copy.deepcopy(current)
        forged["stocks"][0]["v3_unscored_path_unavailable_reason"]="insufficient_121_sessions"
        with self.assertRaises(PathCoverageError):
            summarize(forged)

    def test_coverage_receipt_rejects_forged_score_mismatch_and_stale_day(self):
        baseline = daily()
        changes = [
            ("v3_unscored_path_source", "observed_issuer_count", 2),
            ("v3_unscored_path_source", "additional_volume_or_momentum_points_authorized", 4),
            ("v3_unscored_path_source", "raw_ohlcv_exported", True),
            ("v3_unscored_path_source", "source_exchange_and_adjustments_independently_verified", True),
        ]
        for parent, key, value in changes:
            with self.subTest(key=key, value=value):
                item = copy.deepcopy(baseline)
                item[parent][key] = value
                with self.assertRaises(PathCoverageError):
                    summarize(item)
        forged = copy.deepcopy(baseline)
        forged["stocks"][0]["v3_unscored_path_observations"]["new_v3_available_points"] = 4
        with self.assertRaises(PathCoverageError):
            summarize(forged)
        stale = copy.deepcopy(baseline)
        stale["stocks"][0]["price_date"] = "2026-10-15"
        with self.assertRaises(PathCoverageError):
            summarize(stale)
        duplicate = copy.deepcopy(baseline)
        duplicate["stocks"][1]["code"] = "000001"
        with self.assertRaises(PathCoverageError):
            summarize(duplicate)
        incomplete = copy.deepcopy(baseline)
        incomplete["candidate_count"] = 3
        with self.assertRaises(PathCoverageError):
            summarize(incomplete)

    def test_cli_write_then_verify_rejects_stale_published_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "data.json"
            target = Path(folder) / "coverage.json"
            source.write_text(json.dumps(daily()), encoding="utf-8")
            command = [sys.executable, str(ROOT / "v3_unscored_path_coverage.py"),
                       "--daily", str(source), "--output", str(target)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(target.read_text())["observed_121_session_paths"], 1)
            subprocess.run(command + ["--verify"], check=True,
                           capture_output=True, text=True)
            manipulated = json.loads(target.read_text())
            manipulated["additional_volume_or_momentum_points_authorized"] = 4
            target.write_text(json.dumps(manipulated), encoding="utf-8")
            failure = subprocess.run(command + ["--verify"], capture_output=True, text=True)
            self.assertNotEqual(failure.returncode, 0)
            self.assertIn("price_path_coverage_out_of_sync", failure.stderr)

    def test_scheduled_publisher_generates_and_commits_small_report(self):
        flow = (ROOT / ".github/workflows/full-market-batches.yml").read_text(encoding="utf-8")
        self.assertIn("python v3_unscored_path_coverage.py", flow)
        self.assertIn("--daily docs/data.json --output docs/v3-unscored-path-coverage.json", flow)
        self.assertIn("python v3_unscored_path_coverage.py --verify", flow)
        self.assertIn("git add docs/data.json", flow)
        self.assertIn("docs/v3-unscored-path-coverage.json", flow)
        self.assertIn("cron: '30 9 * * 1-5'", flow)


if __name__ == "__main__":
    unittest.main()
