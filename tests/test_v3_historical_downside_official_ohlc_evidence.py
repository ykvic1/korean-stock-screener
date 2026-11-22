"""Offline coverage audit: official market-wide OHLC is not a full path proof."""

import csv
from copy import deepcopy
import json
from unittest import TestCase

import v3_historical_downside_official_ohlc_evidence as audit


class SavedHistoricalDownsideOHLCProofTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(audit.EVIDENCE.read_text(encoding="utf-8"))
        with audit.EXTREMES.open(encoding="utf-8", newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_real_pinned_report_quantifies_partial_ohlc_proof(self):
        result = audit.build(self.report, self.rows)
        self.assertEqual(result["saved_comparison_sessions"], 14)
        self.assertEqual(result["saved_comparison_market_rows"], 38804)
        self.assertEqual(result["saved_comparison_traded_ohlc_exact"], 37288)
        self.assertEqual(result["saved_comparison_no_positive_trade"], 1516)
        self.assertEqual(result["frozen_downside_horizon_rows"], 85)
        self.assertEqual(result["frozen_downside_unique_extreme_dates"], 35)
        self.assertEqual(result["horizon_rows_with_saved_official_ohlc_day"], 41)
        self.assertEqual(result["horizon_rows_without_saved_official_ohlc_day"], 44)
        self.assertEqual(result["extreme_dates_with_saved_official_ohlc"], 8)
        self.assertEqual(result["extreme_dates_without_saved_official_ohlc"], 27)
        self.assertEqual(result["official_ohlc_extreme_dates"],
                         ["2025-11-19", "2025-12-08", "2025-12-09",
                          "2025-12-15", "2026-01-08", "2026-01-12",
                          "2026-01-13", "2026-02-13"])
        self.assertEqual(result["by_horizon"]["1"]["with_saved_official_ohlc"], 11)
        self.assertEqual(result["by_horizon"]["12"]["without_saved_official_ohlc"], 14)
        self.assertEqual(result["new_provider_calls"], 0)
        self.assertTrue(result["saved_ohlc_evidence_extended_without_new_api_calls"])
        self.assertEqual(result["additional_pinned_source_run_id"], 44611945936)
        for key in ("target_issuer_extreme_low_directly_authenticated",
                    "intraday_extreme_entire_holding_path_verified",
                    "historical_adjusted_prices_verified",
                    "regular_only_volume_verified",
                    "point_in_time_scores_verified",
                    "performance_validated", "v30b_release_ready"):
            self.assertIs(result[key], False, key)


    def test_pinned_extension_reuses_exact_unmodified_original_nine_sessions(self):
        original = json.loads((audit.ROOT /
            "ops/v3-historical-turnover-corroboration-20261117.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(self.report["batches"][:2], original["batches"])
        self.assertEqual([batch["batch"] for batch in self.report["batches"]],
                         ["anchor", "remaining", "downside_priority"])
        extension = self.report["batches"][2]
        self.assertEqual(extension["job_id"], 135870570056)
        self.assertEqual(extension["requested_sessions"], [
            "2025-11-19", "2025-12-08", "2025-12-09",
            "2026-01-08", "2026-01-12"])
        self.assertEqual(sum(
            item["market_wide"]["scope"] for item in extension["sessions"].values()), 13881)
        self.assertEqual(sum(
            item["market_wide"]["exact_ohlc"] for item in extension["sessions"].values()), 13380)

    def test_extended_evidence_rejects_reused_dates_and_provenance_corruption(self):
        for key, value in (
            ("additional_run_id", 44611945935),
            ("additional_job_id", 1),
            ("additional_artifact_id", 1),
            ("prior_report_blob_sha", "0" * 40),
            ("no_additional_provider_calls_in_this_consolidation", False),
        ):
            bad = deepcopy(self.report)
            bad["saved_official_batch_provenance"][key] = value
            with self.subTest(provenance_field=key), self.assertRaises(
                audit.HistoricalExtremeEvidenceError
            ):
                audit.build(bad, self.rows)
        removed = deepcopy(self.report)
        removed["batches"].pop()
        with self.assertRaises(audit.HistoricalExtremeEvidenceError):
            audit.build(removed, self.rows)
        reused = deepcopy(self.report)
        reused["batches"][2]["sessions"]["2025-11-19"] = deepcopy(
            reused["batches"][0]["sessions"]["2025-11-13"])
        with self.assertRaises(audit.HistoricalExtremeEvidenceError):
            audit.build(reused, self.rows)
        changed_low = deepcopy(self.report)
        changed_low["batches"][2]["sessions"]["2025-11-19"][
            "market_wide"]["different_price_by_field"]["low"] = 1
        with self.assertRaises(audit.HistoricalExtremeEvidenceError):
            audit.build(changed_low, self.rows)

    def test_tampered_market_ohlc_or_identity_fails_closed(self):
        for key, value in (("exact_ohlc", 0), ("positive_trade_on_both", 0),
                           ("listed_share_mismatch", 1),
                           ("positive_trade_on_official_only", 1),
                           ("absent_from_official", 1),
                           ("identity_mismatch", 1)):
            with self.subTest(field=key):
                mutated = deepcopy(self.report)
                first = mutated["batches"][0]["sessions"]["2025-11-13"]["market_wide"]
                first[key] = value
                with self.assertRaises(audit.HistoricalExtremeEvidenceError):
                    audit.build(mutated, self.rows)
        damaged = deepcopy(self.report)
        damaged["batches"][1]["sessions"]["2025-12-15"]["market_wide"][
            "different_price_by_field"]["low"] = 1
        with self.assertRaises(audit.HistoricalExtremeEvidenceError):
            audit.build(damaged, self.rows)

    def test_missing_or_replayed_source_evidence_fails_closed(self):
        mutants = []
        missing_hash = deepcopy(self.report)
        missing_hash["batches"][0]["sessions"]["2025-11-13"]["official_source_sha256"].pop("KOSPI")
        mutants.append(missing_hash)
        changed_digest = deepcopy(self.report)
        changed_digest["batches"][0]["public_panel_source_sha256"][0] = "0" * 64
        mutants.append(changed_digest)
        missing_date = deepcopy(self.report)
        missing_date["batches"][1]["sessions"].pop("2026-02-13")
        mutants.append(missing_date)
        duplicate_batch = deepcopy(self.report)
        duplicate_batch["batches"].append(deepcopy(duplicate_batch["batches"][0]))
        mutants.append(duplicate_batch)
        bad_summary = deepcopy(self.report)
        bad_summary["summary"]["rows"] += 1
        mutants.append(bad_summary)
        invented_certification = deepcopy(self.report)
        invented_certification["source_scope"]["regular_0900_1530_volume_verified"] = True
        mutants.append(invented_certification)
        for number, item in enumerate(mutants):
            with self.subTest(mutation=number), self.assertRaises(
                audit.HistoricalExtremeEvidenceError
            ):
                audit.build(item, self.rows)

    def test_extreme_roster_must_match_frozen_five_horizons(self):
        for changed in ("remove", "duplicate", "bad_day", "bad_horizon"):
            rows = deepcopy(self.rows)
            if changed == "remove":
                rows.pop()
            elif changed == "duplicate":
                rows[-1] = deepcopy(rows[0])
            elif changed == "bad_day":
                rows[0]["adverse_extreme_on"] = "2025-11-01"
            else:
                rows[0]["horizon_months"] = "13"
            with self.subTest(change=changed), self.assertRaises(
                audit.HistoricalExtremeEvidenceError
            ):
                audit.build(self.report, rows)
