"""Fail-closed original/successor KRX CLOSE continuity; not official intraday LOW."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

import v3_historical_official_holder_daily_path as holder


class HistoricalOfficialDailyHolderPathTests(TestCase):
    def test_market_and_issuer_identity_share_and_close_mismatch_rejected(self):
        on = date(2026, 3, 27)
        public = {
            "329180": ("KOSPI", "Successor", 100, 490000, 500000, 470000, 480000, 20)
        }
        official = {
            "329180": ["KOSPI", "Successor", 480000, 100, None, True]
        }
        targets = [("010620", "329180", Decimal("0.4059146"))]
        known = holder.observe(public, official, on, targets)
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0][:3],
                         ("010620", on, "official_close_and_holder_units_match"))
        self.assertEqual(known[0][-1], Decimal("194839.0080"))

        cases = (
            ("wrong_close", lambda p, o: o["329180"].__setitem__(2, 480001),
             "official_close_mismatch"),
            ("wrong_name", lambda p, o: o["329180"].__setitem__(1, "Other"),
             "issuer_or_market_mismatch"),
            ("wrong_shares", lambda p, o: o["329180"].__setitem__(3, 101),
             "listed_shares_mismatch"),
            ("wrong_market", lambda p, o: o["329180"].__setitem__(0, "KOSDAQ"),
             "issuer_or_market_mismatch"),
            ("untraded_official", lambda p, o: o["329180"].__setitem__(5, False),
             "trade_status_mismatch"),
        )
        for case, mutate, expected in cases:
            p, o = deepcopy(public), deepcopy(official)
            mutate(p, o)
            with self.subTest(case=case):
                self.assertEqual(holder.observe(p, o, on, targets)[0][2], expected)

    def test_no_trades_or_missing_archive_rows_never_forward_filled(self):
        on = date(2025, 11, 19)
        target = [("000010", "000010", Decimal(1))]
        p = {"000010": ("KOSPI", "Suspended", 100, None, None, None, None, 0)}
        o = {"000010": ["KOSPI", "Suspended", None, 100, None, False]}
        row = holder.observe(p, o, on, target)[0]
        self.assertEqual(row[2], "untraded_in_both")
        self.assertIsNone(row[3])
        self.assertEqual(holder.observe({}, {}, on, target)[0][2], "missing_in_both")
        self.assertEqual(holder.observe(p, {}, on, target)[0][2], "missing_one_source")

    @staticmethod
    def miniature(early_close_breach=False):
        a, b = date(2025, 11, 14), date(2025, 11, 17)
        origins = [f"{i:06d}" for i in range(17)]
        p = {
            "days": [a, b],
            "reference": {
                c: {
                    "entry_open_krw": "10",
                    "reference_line_krw": "9",
                    "first_observed_touch_on": b.isoformat() if c == origins[0] else "",
                } for c in origins
            },
            "horizon": {
                (c, h): {
                    "eligible_sessions": "2", "observed_trade_bars": "2",
                    "unpriced_sessions": "0",
                    "maximum_adverse_excursion_observed_pct": "20.0000",
                    "maximum_favorable_excursion_observed_pct": "20.0000",
                }
                for c in origins for h in ("1", "2", "3", "6", "12")
            },
            "cutoff": {
                (c, h): b for c in origins for h in ("1", "2", "3", "6", "12")
            },
            "by_day": {
                a: [(c, c, Decimal(1)) for c in origins],
                b: [(c, c, Decimal(1)) for c in origins],
            },
            "original_share_days_planned": 34,
            "successor_share_days_planned": 0,
        }
        observations = [
            (c, day, "official_close_and_holder_units_match",
             Decimal("8.50") if early_close_breach and day == a and c == origins[0]
             else Decimal("10.00"))
            for c in origins for day in (a, b)
        ]
        return p, observations

    def test_synthetic_full_daily_horizon_check_and_close_stop_bound(self):
        p, observations = self.miniature()
        report = holder.summarize(p, observations)
        self.assertEqual(report["horizon_rows"], 85)
        self.assertEqual(report["frozen_horizon_observation_counts_reproduced"], 85)
        self.assertEqual(report["frozen_horizon_intraday_extremes_bound_official_close"], 85)
        self.assertEqual(report["close_does_not_precede_frozen_first_low_touch"], 85)
        self.assertEqual(report["horizon_traded_close_observations_replayed"], 170)
        self.assertTrue(report["daily_close_and_holder_schedule_matched"])
        self.assertFalse(report["performance_validated"])
        self.assertFalse(report["complete_adjusted_total_return_history_certified"])
        # Tiny synthetic fixture is not a frozen 245-day production report.
        self.assertFalse(holder.valid_summary(report))

        p, observations = self.miniature(early_close_breach=True)
        report = holder.summarize(p, observations)
        self.assertEqual(
            report["incompatible_horizons"][
                "official_close_before_frozen_first_observed_low_touch"], 5)
        self.assertFalse(report["daily_close_and_holder_schedule_matched"])

    def test_missing_or_duplicate_observations_rejected(self):
        p, observations = self.miniature()
        truncated = holder.summarize(p, observations[:-1])
        self.assertFalse(truncated["daily_close_and_holder_schedule_matched"])
        self.assertEqual(truncated["frozen_horizon_observation_counts_reproduced"], 80)
        with self.assertRaises(ValueError):
            holder.summarize(p, observations + [observations[0]])

    def test_existing_archive_watches_official_daily_holder_without_sensitive_rows(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "v3_historical_close_archive_audit.py").read_text(encoding="utf8")
        workflow = (root / ".github/workflows/v3-historical-close-archive-audit.yml").read_text(encoding="utf8")
        self.assertIn("holder_daily_official_close_path", source)
        self.assertIn("valid_holder_daily", source)
        self.assertIn("v3_historical_official_holder_daily_path.py", workflow)
        self.assertIn("tests/test_v3_historical_official_holder_daily_path.py", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)


if __name__ == "__main__":
    import unittest
    unittest.main()
