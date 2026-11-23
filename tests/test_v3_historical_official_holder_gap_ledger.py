"""Classification of non-price corporate-action intervals must never fill quotes."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import v3_historical_official_holder_gap_ledger as gap


class HistoricalOfficialHolderGapTests(TestCase):
    def test_old_original_missing_or_halted_is_not_a_tradeable_quote(self):
        session = date(2026, 1, 2)
        targets = [("000010", "exchange_effective_before_share_trading")]
        self.assertEqual(
            gap.observe_gap({}, {}, session, targets)[0][3],
            "old_security_absent_in_both_sources")
        public = {"000010": ("KOSPI", "Issuer", 100, None, None, None, 500, 0)}
        official = {"000010": ["KOSPI", "Issuer", 500, 100, None, False]}
        self.assertEqual(gap.observe_gap(public, official, session, targets)[0][3],
                         "old_security_untraded_in_both_sources")
        # A last-close-looking value during zero trading must never turn into
        # market-to-market or create an observed low.
        self.assertNotIn("close", gap.observe_gap(public, official, session, targets)[0][3])

    def test_any_original_post_last_trade_or_unilateral_presence_is_a_failure(self):
        session = date(2026, 1, 2)
        targets = [("000010", "exchange_effective_before_share_trading")]
        public = {"000010": ("KOSPI", "Issuer", 100, 10, 11, 9, 10, 1)}
        official = {"000010": ["KOSPI", "Issuer", 10, 100, None, True]}
        self.assertEqual(gap.observe_gap(public, official, session, targets)[0][3],
                         "original_trade_after_frozen_last_trade")
        official["000010"][5] = False
        self.assertEqual(gap.observe_gap(public, official, session, targets)[0][3],
                         "original_trade_after_frozen_last_trade")
        public["000010"] = ("KOSPI", "Issuer", 100, None, None, None, 10, 0)
        self.assertEqual(gap.observe_gap(public, {}, session, targets)[0][3],
                         "one_source_missing")
        official["000010"][3] = 101
        self.assertEqual(gap.observe_gap(public, official, session, targets)[0][3],
                         "old_security_listed_shares_mismatch")
        official["000010"][3] = 100
        official["000010"][1] = "Other"
        self.assertEqual(gap.observe_gap(public, official, session, targets)[0][3],
                         "old_security_identity_mismatch")

    @staticmethod
    def synthetic_full_report():
        session = date(2026, 1, 2)
        cases = []
        for cause, count in gap.FROZEN_CAUSES.items():
            cases.extend([cause] * count)
        assert len(cases) == 687
        identifiers = [f"{i:06d}" for i in range(687)]
        p = {
            "total": 687,
            "eligible_holder_day_cells": 4148,
            "traded_or_suspended_holder_rows": 3461,
            "causes": dict(gap.FROZEN_CAUSES),
            "by_day": {
                session: list(zip(identifiers, cases, strict=True))
            },
        }
        o = [(code, session, cause,
              ("old_security_absent_in_both_sources" if i < 506
               else "old_security_untraded_in_both_sources"))
             for i, (code, cause) in enumerate(zip(identifiers, cases, strict=True))]
        return p, o

    def test_only_complete_nontrading_687_day_partition_can_pass(self):
        plan, observations = self.synthetic_full_report()
        with patch.object(gap, "valid_daily", return_value=True):
            report = gap.summarize(plan, observations, {})
        self.assertTrue(report["all_corporate_action_gaps_accounted_for"])
        self.assertTrue(gap.valid_summary(report))
        self.assertEqual(report["eligible_holder_day_cells"], 4148)
        self.assertEqual(report["unpriced_corporate_gap_holder_date_cells"], 687)
        self.assertFalse(report["first_intraday_reference_touch_proven"])
        self.assertFalse(report["future_entitlement_mark_to_market_verified"])
        with patch.object(gap, "valid_daily", return_value=True):
            incomplete = gap.summarize(plan, observations[:-1], {})
        self.assertEqual(incomplete["missing_historical_gap_date_observations"], 1)
        self.assertFalse(gap.valid_summary(incomplete))
        with patch.object(gap, "valid_daily", return_value=True):
            invalid = gap.summarize(plan, observations[:-1] + [(
                observations[-1][0], observations[-1][1],
                observations[-1][2], "original_trade_after_frozen_last_trade")], {})
        self.assertFalse(invalid["all_corporate_action_gaps_accounted_for"])
        self.assertFalse(gap.valid_summary(invalid))
        with patch.object(gap, "valid_daily", return_value=True):
            with self.assertRaises(ValueError):
                gap.summarize(plan, observations + observations[:1], {})

    def test_no_false_release_certification_from_legal_gap_proof(self):
        p, rows = self.synthetic_full_report()
        with patch.object(gap, "valid_daily", return_value=True):
            report = gap.summarize(p, rows, {})
        for field in (
            "legal_effective_and_listing_dates_newly_authenticated",
            "future_entitlement_mark_to_market_verified",
            "continuous_adjusted_intraday_low_verified",
            "first_intraday_reference_touch_proven",
            "regular_only_volume_verified",
            "historical_pit_score_verified", "performance_validated", "v30b_release_ready",
        ):
            edited = deepcopy(report)
            edited[field] = True
            with self.subTest(field=field):
                self.assertFalse(gap.valid_summary(edited))

    def test_existing_official_archive_consumes_gap_ledger_without_new_requests(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "v3_historical_close_archive_audit.py").read_text()
        workflow = (root / ".github/workflows/v3-historical-close-archive-audit.yml").read_text()
        self.assertIn('holder_legal_unpriced_gap_ledger', source)
        self.assertIn('valid_legal_gap', source)
        self.assertIn("v3_historical_official_holder_gap_ledger.py", workflow)
        self.assertIn("test_v3_historical_official_holder_gap_ledger.py", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)


if __name__ == "__main__":
    import unittest
    unittest.main()
