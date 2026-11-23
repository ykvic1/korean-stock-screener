"""Prove the limits of official closing support breaches versus intraday lows."""
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import v3_historical_official_close_stop_reference as audit

EXACT = "official_close_and_holder_units_match"


def scenario(with_early_close=False):
    sessions = [date(2025, 11, 14), date(2025, 11, 17),
                date(2025, 11, 18), date(2025, 11, 19)]
    ids = [f"{i:06d}" for i in range(17)]
    p = {
        "days": sessions,
        "reference": {},
        "by_day": {session: [(c, c, Decimal(1)) for c in ids]
                   for session in sessions},
        "horizon": {},
        "cutoff": {},
    }
    observed = []
    for i, c in enumerate(ids):
        low = sessions[1] if i < 14 else None
        p["reference"][c] = {
            "reference_line_krw": "9",
            "first_observed_touch_on": low.isoformat() if low else "",
        }
        for h in ("1", "2", "3", "6", "12"):
            p["horizon"][c, h] = {
                "first_observed_touch_on": low.isoformat() if low else "",
            }
            p["cutoff"][c, h] = sessions[-1]
        for d in sessions:
            # Each observed first public intraday LOW can close ABOVE support.
            value = Decimal(10)
            if i == 0 and d == sessions[2]:
                value = Decimal(8)
            if with_early_close and i == 0 and d == sessions[0]:
                value = Decimal(8)
            observed.append((c, d, EXACT, value))
    return p, observed


class HistoricalOfficialCloseStopReferenceTests(TestCase):
    def test_later_close_is_not_first_intraday_low_touch(self):
        p, observations = scenario()
        with patch.object(audit, "valid_holder", return_value=True), patch.object(
                audit, "valid_gaps", return_value=True):
            # A 4-day synthetic fixture is deliberately not a production proof.
            with patch.object(audit, "require",
                              side_effect=lambda ok, reason: None if ok else (
                                  None if reason == "official_close_stop_observation_roster_changed"
                                  else (_ for _ in ()).throw(ValueError(reason)))):
                report = audit.summarize(p, observations, {}, {})
        self.assertEqual(report["reported_public_first_low_touch_issuers"], 14)
        self.assertEqual(report["reported_no_public_low_touch_issuers"], 3)
        self.assertEqual(report["first_low_dates_whose_official_close_is_above_reference"], 14)
        self.assertEqual(report["origin_outcomes"][
            "official_close_breach_later_than_public_first_low"], 1)
        self.assertEqual(report["origin_outcomes"][
            "public_first_low_but_no_official_close_breach"], 13)
        self.assertEqual(report["horizon_outcomes"][
            "low_touch_with_official_closing_breach_by_horizon"], 5)
        self.assertTrue(report[
            "all_official_closing_breaches_consistent_with_frozen_public_first_lows"])
        self.assertFalse(audit.valid_summary(report))  # 68 != 3,461 holder dates.

    def test_closing_breach_prior_to_replayed_first_intraday_low_is_an_error(self):
        p, observations = scenario(with_early_close=True)
        with patch.object(audit, "valid_holder", return_value=True), patch.object(
                audit, "valid_gaps", return_value=True):
            with patch.object(audit, "require",
                              side_effect=lambda ok, reason: None if ok else (
                                  None if reason == "official_close_stop_observation_roster_changed"
                                  else (_ for _ in ()).throw(ValueError(reason)))):
                report = audit.summarize(p, observations, {}, {})
        self.assertEqual(report["origin_outcomes"][
            "official_close_breach_before_public_first_low"], 1)
        self.assertEqual(report["horizon_outcomes"][
            "closing_breach_precedes_reported_low_touch"], 5)
        self.assertFalse(report[
            "all_official_closing_breaches_consistent_with_frozen_public_first_lows"])
        self.assertFalse(audit.valid_summary(report))

    def test_prerequisite_and_duplicate_checks_fail_closed(self):
        p, observations = scenario()
        with self.assertRaises(ValueError):
            audit.summarize(p, observations, {}, {})
        # A synthetic fixture cannot pass the production calendar and 3,461 holder
        # observations, even when underlying evidence claims it is valid.
        with patch.object(audit, "valid_holder", return_value=True), patch.object(
                audit, "valid_gaps", return_value=True):
            with self.assertRaises(ValueError):
                audit.summarize(p, observations + observations[:1], {}, {})

    def test_release_flags_may_not_be_promoted(self):
        p, observations = scenario()
        with patch.object(audit, "valid_holder", return_value=True), patch.object(
                audit, "valid_gaps", return_value=True), patch.object(
                audit, "require",
                side_effect=lambda ok, reason: None if ok else (
                    None if reason == "official_close_stop_observation_roster_changed"
                    else (_ for _ in ()).throw(ValueError(reason)))):
            report = audit.summarize(p, observations, {}, {})
        for key in (
            "all_pre_touch_official_intraday_lows_verified",
            "first_intraday_reference_touch_certified",
            "continuous_adjusted_total_return_history_certified",
            "regular_only_volume_verified", "historical_pit_scores_verified",
            "performance_validated", "v30b_release_ready",
        ):
            bad = deepcopy(report)
            bad[key] = True
            self.assertFalse(audit.valid_summary(bad))

    def test_official_archive_reuses_only_existing_private_close_reads(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "v3_historical_close_archive_audit.py").read_text()
        workflow = (root / ".github/workflows/v3-historical-close-archive-audit.yml").read_text()
        self.assertIn("holder_close_reference_chronology", source)
        self.assertIn("valid_official_close_stop", source)
        self.assertIn("v3_historical_official_close_stop_reference.py", workflow)
        self.assertIn("test_v3_historical_official_close_stop_reference.py", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)


if __name__ == "__main__":
    import unittest
    unittest.main()
