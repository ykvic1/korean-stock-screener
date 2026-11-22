"""One-time historical reference-line touch proof must not imply first occurrence."""
import unittest
from copy import deepcopy
from decimal import Decimal

from v3_historical_reference_touch_direct_probe import (
    pending_first_touch_targets, audit_saved_first_touch_date,
    compare_bounded_batch,
)


class DirectReferenceTouchTests(unittest.TestCase):
    def test_gap_is_exactly_11_pairs_on_10_dates(self):
        targets = pending_first_touch_targets()
        self.assertEqual(len(targets), 10)
        self.assertEqual(sum(map(len, targets.values())), 11)
        self.assertEqual(len(targets["2025-11-18"]), 2)
        self.assertEqual(
            set(targets), {
                "2025-11-14", "2025-11-18", "2025-11-19",
                "2025-12-09", "2026-01-05", "2026-01-09",
                "2026-03-27", "2026-06-24", "2026-10-08", "2026-10-29",
            })

    def test_ratio_math_for_exact_official_low_and_open(self):
        raw = {"000001": ("KOSDAQ", {
            "ISU_NM": "Company", "ACC_TRDVOL": "20",
            "ACC_TRDVAL": "9600000", "LIST_SHRS": "100",
            "TDD_OPNPRC": "480000", "TDD_HGPRC": "500000",
            "TDD_LWPRC": "470000", "TDD_CLSPRC": "490000",
        })}
        public = {"000001": ("KOSDAQ", "Company", 100,
                              480000, 500000, 470000, 490000, 20)}
        turnover = {"000001": ("KOSDAQ", "Company", 20, 9600000)}
        ratio = Decimal("0.4059146")
        touch = {"000001": (ratio, Decimal("194839.0080"), Decimal("190779.8620"))}
        digest = {"KOSPI": "a" * 64, "KOSDAQ": "b" * 64}
        result = audit_saved_first_touch_date(public, turnover, raw, digest, touch)
        self.assertEqual(result["touch_holder_open_low_exact"], 1)
        self.assertFalse(result["first_touch_is_earliest_trade_proven"])
        self.assertFalse(result["pre_touch_complete_adjusted_path_verified"])
        self.assertFalse(result["performance_validated"])
        self.assertNotIn("000001", str(result))
        self.assertNotIn("480000", str(result))

        mutations = []
        candidate = deepcopy(raw)
        candidate["000001"][1]["TDD_LWPRC"] = "470001"
        mutations.append((candidate, public, touch))
        candidate = deepcopy(raw)
        candidate["000001"][1]["LIST_SHRS"] = "101"
        mutations.append((candidate, public, touch))
        candidate = deepcopy(raw)
        candidate["000001"][1]["ISU_NM"] = "Other Company"
        mutations.append((candidate, public, touch))
        for modified_raw, panel, expected in mutations:
            with self.assertRaises(ValueError):
                audit_saved_first_touch_date(panel, turnover, modified_raw,
                                             digest, expected)
        bad_touch = {"000001": (Decimal(1), touch["000001"][1], touch["000001"][2])}
        with self.assertRaises(ValueError):
            audit_saved_first_touch_date(public, turnover, raw, digest, bad_touch)

    def test_bounded_batches_require_present_distinct_dates(self):
        for invalid in ([], ["2025-11-14"] * 2, ["2020-01-02"],
                        ["2025-11-14"] * 6):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                compare_bounded_batch({}, {}, object(), invalid,
                                      pending_first_touch_targets())


if __name__ == "__main__":
    unittest.main()
