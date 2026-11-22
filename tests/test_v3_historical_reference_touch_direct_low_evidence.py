"""Frozen first-reference-touch official KRX LOW verification has strictly limited scope."""
import json
from copy import deepcopy
from pathlib import Path
from unittest import TestCase

import v3_historical_reference_touch_direct_low_evidence as audit


class ReferenceTouchOfficialLowEvidenceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = json.loads(audit.EVIDENCE.read_text(encoding="utf8"))
        cls.baseline = json.loads(audit.BASELINE_PATH.read_text(encoding="utf8"))
        cls.prior = json.loads(audit.PRIOR_PATH.read_text(encoding="utf8"))
        cls.later = json.loads(audit.LATER_PATH.read_text(encoding="utf8"))

    def assert_fail_closed(self, changed, prior=None, later=None, baseline=None):
        with self.assertRaises((ValueError, TypeError, KeyError, IndexError)):
            audit.build(changed, baseline or self.baseline,
                        prior or self.prior, later or self.later)

    def test_11_specific_holder_low_and_open_values_officially_match(self):
        result = audit.build(self.saved, self.baseline, self.prior, self.later)
        self.assertEqual(result["frozen_first_touch_records"], 14)
        self.assertEqual(result["prior_first_touch_security_days_on_direct_extreme_low_dates"], 3)
        self.assertEqual(result["newly_verified_first_touch_security_days"], 11)
        self.assertEqual(result["unique_new_official_first_touch_dates"], 10)
        self.assertEqual(result["all_reported_first_touch_security_days_with_exact_ohlc"], 14)
        self.assertEqual(result["new_official_marketwide_issuer_day_pairs"],
                         result["new_official_exact_traded_ohlc"] +
                         result["new_official_no_positive_trade"])
        self.assertEqual(result["new_provider_calls_in_readonly_replay"], 0)
        for flag in ("all_prior_sessions_prove_first_touch_was_earliest",
                     "first_touch_is_earliest_trade_proven",
                     "pre_touch_complete_adjusted_path_verified",
                     "regular_only_volume_verified",
                     "historical_point_in_time_scores_certified",
                     "performance_validated",
                     "v30b_release_ready"):
            self.assertIs(result[flag], False)

    def test_cannot_substitute_quote_day_or_original_artifact(self):
        for case in ("artifact_id", "digest", "target_date", "holder_low"):
            modified = deepcopy(self.saved)
            group = modified["groups"][0]
            if case == "artifact_id":
                group["artifact_id"] += 1
            elif case == "digest":
                group["canonical_report_sha256"] = "0" * 64
            else:
                report = audit._unpack(group)
                day = report["requested_sessions"][0]
                if case == "target_date":
                    report["requested_sessions"][0] = "2024-01-01"
                else:
                    report["sessions"][day]["touch_holder_open_low_exact"] = 0
                # Recompress a valid but altered report. The pinned hash MUST
                # reject this independently of aggregate counts and source hash.
                import base64
                import gzip
                canonical = json.dumps(
                    report, sort_keys=True, separators=(",", ":")).encode("utf8")
                group["data"] = base64.b64encode(gzip.compress(
                    canonical, mtime=0)).decode("ascii")
            with self.subTest(case=case):
                self.assert_fail_closed(modified)

    def test_missing_replayed_groups_and_release_promotion_rejected(self):
        for case in ("groups", "same_group", "certified", "retired_source"):
            changed = deepcopy(self.saved)
            if case == "groups":
                changed["groups"].pop()
            elif case == "same_group":
                changed["groups"][1]["group"] = 0
            elif case == "certified":
                changed["performance_validated"] = True
            else:
                changed["source_git_blob_sha1"][
                    "ops/v3-performance-downside-reference-20251113.csv"
                ] = "0" * 40
            with self.subTest(case=case):
                self.assert_fail_closed(changed)

    def test_same_day_kospi_digest_cannot_be_reused_for_kosdaq(self):
        from unittest.mock import patch
        original_unpack = audit._unpack
        def altered_unpack(group):
            report = original_unpack(group)
            if group["group"] == 0:
                report = deepcopy(report)
                day = report["requested_sessions"][0]
                digests = report["sessions"][day]["official_source_sha256"]
                digests["KOSDAQ"] = digests["KOSPI"]
            return report
        with patch.object(audit, "_unpack", side_effect=altered_unpack):
            self.assert_fail_closed(self.saved)

    def test_official_archive_watch_requires_readonly_frozen_touch_validation(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/v3-historical-close-archive-audit.yml")
        content = workflow.read_text(encoding="utf8")
        for path in (
            "v3_historical_reference_touch_direct_probe.py",
            "v3_historical_reference_touch_direct_low_evidence.py",
            "ops/v3-historical-first-touch-official-low-20261122.json",
            "tests/test_v3_historical_reference_touch_direct_probe.py",
            "tests/test_v3_historical_reference_touch_direct_low_evidence.py",
        ):
            self.assertIn("- " + path, content)
        self.assertIn("python v3_historical_reference_touch_direct_low_evidence.py >", content)
        self.assertIn("historical-reference-touch-direct-low-coverage.json", content)
        self.assertIn(".newly_verified_first_touch_security_days == 11", content)
        self.assertIn(".first_touch_is_earliest_trade_proven == false", content)
        self.assertIn(".pre_touch_complete_adjusted_path_verified == false", content)
        self.assertIn(".v30b_release_ready == false", content)
        self.assertIn("contents: read", content)
        self.assertNotIn("contents: write", content)

    def test_stored_proofs_do_not_disclose_issuer_rows(self):
        for group in self.saved["groups"]:
            raw = audit._unpack(group)
            for row in raw["sessions"].values():
                self.assertEqual(set(row), {
                    "schema", "official_source_sha256", "market_wide",
                    "turnover", "actual_touch_security_ohlc",
                    "touch_holder_open_low_exact",
                    "reported_touch_security_days",
                    "first_touch_is_earliest_trade_proven",
                    "pre_touch_complete_adjusted_path_verified",
                    "regular_only_volume_verified",
                    "performance_validated", "v30b_release_ready",
                })
                self.assertNotIn('"code":', json.dumps(row))


if __name__ == "__main__":
    import unittest
    unittest.main()
