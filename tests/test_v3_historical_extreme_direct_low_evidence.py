"""Pinned official KRX historical extreme LOW proof is strictly partial."""
import json
from copy import deepcopy
from unittest import TestCase

import v3_historical_extreme_direct_low_evidence as audit


class DirectExtremeLowEvidenceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(audit.EVIDENCE.read_text(encoding="utf-8"))
        cls.baseline = json.loads(audit.BASELINE_PATH.read_text(encoding="utf-8"))

    def test_real_successful_official_batches_have_only_bound_direct_low_scope(self):
        result = audit.build(self.report, self.baseline)
        self.assertEqual(result["saved_historical_marketwide_dates"], 14)
        self.assertEqual(result["new_direct_low_compared_dates"], 27)
        self.assertEqual(result["all_frozen_extreme_dates_with_official_marketwide_ohlc"], 35)
        self.assertEqual(
            result["frozen_85_horizons_whose_extreme_day_has_direct_low_check"], 44)
        self.assertEqual(
            result["prior_41_horizons_marketwide_day_only_not_directly_authenticated"], 41)
        self.assertEqual(result["new_provider_calls_by_this_readonly_validation"], 0)
        self.assertEqual(
            result["new_marketwide_issuer_date_pairs"],
            result["new_marketwide_exact_traded_ohlc"] +
            result["new_marketwide_untraded_on_both"])
        self.assertGreater(result["direct_low_trading_symbol_date_pairs"], 27)
        for key in ("historical_adjusted_path_certified",
                    "point_in_time_scores_verified", "regular_only_volume_verified",
                    "performance_validated", "v30b_release_ready"):
            self.assertIs(result[key], False)

    def test_tampering_with_actual_issuer_low_fails(self):
        for key, changed in (("low", 1), ("close", 1), ("high", 1), ("open", 1)):
            report = deepcopy(self.report)
            day = report["batches"][0]["result"]["requested_sessions"][0]
            report["batches"][0]["result"]["sessions"][day][
                "actual_extreme_trading_symbols"]["different_price_by_field"][key] = changed
            with self.subTest(field=key), self.assertRaises(audit.DirectLowEvidenceError):
                audit.build(report, self.baseline)

    def test_missing_or_replayed_evidence_fails_closed(self):
        mutants = []
        removed = deepcopy(self.report)
        removed["batches"].pop()
        mutants.append(removed)
        wrong_run = deepcopy(self.report)
        wrong_run["batches"][0]["run_id"] = 45012847984
        mutants.append(wrong_run)
        unknown_batch = deepcopy(self.report)
        unknown_batch["batches"][0]["batch"] = "anchor"
        mutants.append(unknown_batch)
        reused_day = deepcopy(self.report)
        reused_day["batches"][0]["result"]["sessions"][
            reused_day["batches"][0]["result"]["requested_sessions"][0]
        ]["official_source_sha256"]["KOSPI"] = "not-a-sha"
        mutants.append(reused_day)
        inflated = deepcopy(self.report)
        inflated["batches"][0]["result"]["actual_extreme_symbol_days_exact_ohlc"] += 1
        mutants.append(inflated)
        bad_money = deepcopy(self.report)
        first = bad_money["batches"][0]["result"]["requested_sessions"][0]
        bad_money["batches"][0]["result"]["sessions"][first][
            "turnover"]["fields"]["amount"]["matched"] -= 1
        mutants.append(bad_money)
        certification = deepcopy(self.report)
        certification["v30b_release_ready"] = True
        mutants.append(certification)
        for i, mutant in enumerate(mutants):
            with self.subTest(case=i), self.assertRaises(
                audit.DirectLowEvidenceError
            ):
                audit.build(mutant, self.baseline)

    def test_baseline_evidence_cannot_be_modified_to_expand_coverage(self):
        older = deepcopy(self.baseline)
        older["summary"]["rows"] += 1
        with self.assertRaises(ValueError):
            audit.build(self.report, older)

    def test_direct_scope_does_not_publish_private_security_rows(self):
        # The stored proof may expose only fixed known fields and aggregate
        # counts. There are no per-security identity entries.
        for batch in self.report["batches"]:
            self.assertEqual(batch["result"]["batch"], batch["batch"])
            for day, row in batch["result"]["sessions"].items():
                self.assertEqual(set(row), {
                    "market_wide", "present_pilot_anchor_issuers",
                    "pilot_anchor_absent_from_both", "later_delisted_pilot_issuers",
                    "actual_extreme_trading_symbols", "official_source_sha256", "turnover"})
                self.assertEqual(set(row["official_source_sha256"]), {"KOSPI", "KOSDAQ"})
