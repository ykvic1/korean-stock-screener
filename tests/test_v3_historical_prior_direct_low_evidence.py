"""Freeze actual KRX direct LOW proof for all 35 frozen adverse-extreme dates."""
import json
from copy import deepcopy
from pathlib import Path
from unittest import TestCase

import v3_historical_prior_direct_low_evidence as audit


class HistoricalAllDirectLowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = json.loads(audit.EVIDENCE.read_text(encoding="utf-8"))
        cls.baseline = json.loads(audit.BASELINE_FILE.read_text(encoding="utf-8"))
        cls.later = json.loads(audit.LATER_FILE.read_text(encoding="utf-8"))

    def test_verified_8_plus_27_dates_cover_exactly_53_security_days_and_85_horizons(self):
        result = audit.build(self.saved, self.baseline, self.later)
        self.assertEqual(result["prior_marketwide_only_dates_now_exact_security_checked"], 8)
        self.assertEqual(result["previously_exact_security_checked_dates"], 27)
        self.assertEqual(result["all_35_extreme_dates_direct_security_checked"], 35)
        self.assertEqual(result["prior_direct_trading_security_date_pairs"], 20)
        self.assertEqual(result["earlier_direct_trading_security_date_pairs"], 33)
        self.assertEqual(result["all_extreme_trading_security_date_pairs"], 53)
        self.assertEqual(result["all_85_horizon_extreme_dates_directly_checked"], 85)
        self.assertEqual(result["provider_requests_by_readonly_audit"], 0)
        self.assertEqual(result["prior_8_marketwide_issuer_date_pairs"],
                         result["prior_8_marketwide_exact_traded_ohlc"] +
                         result["prior_8_marketwide_untraded_on_both"])
        for flag in ("regular_only_volume_verified", "historical_adjusted_path_certified",
                     "point_in_time_scores_verified", "performance_validated",
                     "v30b_release_ready"):
            self.assertIs(result[flag], False)

    def test_any_original_successor_ohlc_field_mismatch_rejected(self):
        first = self.saved["batches"][0]["result"]
        day = first["requested_sessions"][0]
        for field in ("open", "high", "low", "close"):
            modified = deepcopy(self.saved)
            modified["batches"][0]["result"]["sessions"][day][
                "actual_extreme_trading_symbols"]["different_price_by_field"][field] = 1
            with self.subTest(field=field), self.assertRaises(audit.PriorDirectLowError):
                audit.build(modified, self.baseline, self.later)

    def test_source_reuse_artifact_change_missing_dates_and_false_certification_rejected(self):
        changed = []
        clone = deepcopy(self.saved)
        clone["batches"][0]["artifact_id"] += 1
        changed.append(clone)
        clone = deepcopy(self.saved)
        clone["batches"][0]["result"]["requested_sessions"].pop()
        changed.append(clone)
        clone = deepcopy(self.saved)
        clone["batches"][0]["result"]["sessions"][
            clone["batches"][0]["result"]["requested_sessions"][0]
        ]["official_source_sha256"]["KOSDAQ"] = (
            clone["batches"][0]["result"]["sessions"][
                clone["batches"][0]["result"]["requested_sessions"][1]
            ]["official_source_sha256"]["KOSDAQ"])
        changed.append(clone)
        clone = deepcopy(self.saved)
        clone["batches"][0]["result"]["sessions"][
            clone["batches"][0]["result"]["requested_sessions"][0]
        ]["turnover"]["fields"]["amount"]["matched"] -= 1
        changed.append(clone)
        clone = deepcopy(self.saved)
        clone["historical_adjusted_path_certified"] = True
        changed.append(clone)
        clone = deepcopy(self.saved)
        clone["v30b_release_ready"] = True
        changed.append(clone)
        clone = deepcopy(self.saved)
        clone["batches"].pop()
        changed.append(clone)
        for index, candidate in enumerate(changed):
            with self.subTest(index=index), self.assertRaises(
                    (audit.PriorDirectLowError, ValueError)):
                audit.build(candidate, self.baseline, self.later)

    def test_marketwide_replay_cannot_replace_security_direct_low(self):
        modified = deepcopy(self.saved)
        row = modified["batches"][1]["result"]["sessions"][
            modified["batches"][1]["result"]["requested_sessions"][0]]
        row["actual_extreme_trading_symbols"]["exact_ohlc"] -= 1
        with self.assertRaises(audit.PriorDirectLowError):
            audit.build(modified, self.baseline, self.later)

    def test_existing_27_direct_low_proof_cannot_be_overstated(self):
        later = deepcopy(self.later)
        later["batches"][0]["result"]["actual_extreme_symbol_days_exact_ohlc"] += 1
        with self.assertRaises(ValueError):
            audit.build(self.saved, self.baseline, later)

    def test_archived_audit_requires_new_proof_without_provider_requeries(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/v3-historical-close-archive-audit.yml")
        content = workflow.read_text(encoding="utf-8")
        for changed_source in (
                "v3_historical_prior_direct_low_evidence.py",
                "ops/v3-historical-prior-extreme-direct-low-20261122.json",
                "tests/test_v3_historical_prior_direct_low_evidence.py"):
            self.assertIn("- " + changed_source, content)
        self.assertIn("python v3_historical_prior_direct_low_evidence.py >", content)
        self.assertIn("historical-all-extreme-direct-low-coverage.json", content)
        self.assertIn(".all_35_extreme_dates_direct_security_checked == 35", content)
        self.assertIn(".all_85_horizon_extreme_dates_directly_checked == 85", content)
        self.assertIn(".historical_adjusted_path_certified == false", content)
        self.assertIn(".v30b_release_ready == false", content)
        self.assertIn("contents: read", content)
        self.assertNotIn("contents: write", content)

    def test_no_private_issuer_rows_or_price_values_published(self):
        for batch in self.saved["batches"]:
            result = batch["result"]
            for detail in result["sessions"].values():
                self.assertEqual(set(detail), {
                    "official_source_sha256", "turnover", "market_wide",
                    "present_pilot_anchor_issuers", "pilot_anchor_absent_from_both",
                    "later_delisted_pilot_issuers", "actual_extreme_trading_symbols",
                })
            self.assertNotIn('"code":', json.dumps(result))
            self.assertNotIn('"raw_official_rows":', json.dumps(result))


if __name__ == "__main__":
    import unittest
    unittest.main()
