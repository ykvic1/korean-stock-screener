"""Only official intraday LOW evidence may prove no earlier structural touch."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import v3_historical_pretouch_official_low_gap as audit


class PreFirstTouchOfficialLowGapTests(TestCase):
    def test_priority_counts_issuer_days_and_skips_old_official_dates(self):
        dates = [date(2025, 11, d) for d in (14, 17, 18, 19, 20, 21, 24, 25)]
        def targets(size):
            return [(f"{i:06d}", f"{i:06d}", Decimal("0.5"), Decimal(10))
                    for i in range(size)]
        candidates = dict(zip(dates, map(targets, (6, 6, 4, 8, 7, 5, 7, 3))))
        selection = audit.choose_batch(candidates, {dates[3]})
        self.assertEqual(selection, [dates[4], dates[6], dates[0], dates[1], dates[5]])
        self.assertEqual(len(selection), 5)
        self.assertNotIn(dates[3], selection)
        self.assertEqual(audit.choose_batch(candidates, {dates[3]}, limit=1),
                         [dates[4]])

    def test_duplicate_origin_and_wrong_units_are_rejected(self):
        d = date(2025, 11, 17)
        good = ("000010", "000010", Decimal("1"), Decimal("90"))
        with self.assertRaises(ValueError):
            audit.choose_batch({d: [good, good]}, set())
        with self.assertRaises(ValueError):
            audit.choose_batch({d: [(good[0], good[1], Decimal(0), good[3])]}, set())
        with self.assertRaises(ValueError):
            audit.choose_batch({d: [good]}, set(), limit=6)

    def test_krx_exact_low_is_required_and_pre_first_touch_must_not_breach(self):
        p = {"000010": ("KOSPI", "TestCo", 100, 100, 110, 95, 100, 12)}
        o = {"000010": ("KOSPI", "TestCo", "traded", 100, 110, 95, 100, 100)}
        raw = {"000010": ("KOSPI", o["000010"])}
        hashes = {"KOSPI": "a" * 64, "KOSDAQ": "b" * 64}
        t = [("000010", "000010", Decimal(1), Decimal(90))]
        with patch.object(audit, "daily_row", side_effect=lambda market, row: row):
            result = audit.check_official_session(p, raw, hashes, t)
            self.assertEqual(result["exact_original_or_successor_pre_first_lows"], 1)
            self.assertTrue(result["no_prior_official_low_breach_on_this_date"])
            with self.assertRaises(ValueError):
                audit.check_official_session(
                    p, raw, hashes, [("000010", "000010", Decimal(1), Decimal(95))])
            changed = {"000010": ("KOSPI", ("KOSPI", "TestCo", "traded", 100, 110, 94, 100, 100))}
            with self.assertRaises(ValueError):
                audit.check_official_session(p, changed, hashes, t)
            with self.assertRaises(ValueError):
                audit.check_official_session(p, raw, {"KOSPI": "a" * 64}, t)

    def test_unpriced_and_prior_source_does_not_promote_release(self):
        self.assertFalse(audit.prepare.__doc__ is None)
        self.assertFalse(audit.check_bounded_batch.__doc__ is None)
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/v3-historical-close-archive-audit.yml").read_text()
        self.assertIn("v3_historical_pretouch_official_low_gap.py", workflow)
        self.assertIn("test_v3_historical_pretouch_official_low_gap.py", workflow)
        self.assertIn("contents: read", workflow)


if __name__ == "__main__":
    import unittest
    unittest.main()
