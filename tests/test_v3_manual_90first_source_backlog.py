"""Offline contracts for manual 90-first source dispatch (no provider calls)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v3_manual_90first_source_backlog import source_queue_counts, should_dispatch


FILES = (
    "v3-dart-bulk-history.json",
    "v3-dart-revenue-2022-supplement.json",
    "v3-dart-book-reference.json",
    "v3-near-100-evidence-priority.json",
    "v3-dart-owner-profit-supplement.json",
    "v3-operational-research-comparison.json",
    "v3-dart-reported-eps.json",
)


class ManualSourceBacklogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {"date": "2026-11-12", "listed_total": 2591}
        self.operational = {"asof": "2026-11-12", "listed_total": 2591}

    def test_verifiably_exhausted_receipt_queues_skip_redundant_workers(self):
        with tempfile.TemporaryDirectory() as d:
            for name in FILES:
                (Path(d) / name).write_text(json.dumps({"source": name}))
            with patch("v3_manual_90first_source_backlog.revenue_plan",
                       return_value=({"A": 1}, {"A": 1}, [])) as annual, patch(
                    "v3_manual_90first_source_backlog.profit_completion",
                    return_value=({}, {}, set(), {
                        "status": "all_listed_profit_source_queue",
                        "pending_all_listed_issuers": 0})) as profit:
                counts = source_queue_counts(d, self.catalog, self.operational)
            self.assertEqual(counts, {"annual": 0, "profit": 0})
            annual.assert_called_once()
            profit.assert_called_once()
            self.assertFalse(should_dispatch(94, counts["annual"]))
            self.assertFalse(should_dispatch(16, counts["profit"]))

    def test_unqueried_records_remain_eligible_even_if_point_ceiling_is_not_source(self):
        with tempfile.TemporaryDirectory() as d:
            for name in FILES:
                (Path(d) / name).write_text("{}")
            with patch("v3_manual_90first_source_backlog.revenue_plan",
                       return_value=({}, {}, ["000001"])), patch(
                    "v3_manual_90first_source_backlog.profit_completion",
                    return_value=({}, {}, set(), {
                        "status": "all_listed_profit_source_queue",
                        "pending_all_listed_issuers": 2})):
                counts = source_queue_counts(d, self.catalog, self.operational)
            self.assertEqual(counts, {"annual": 1, "profit": 2})
            self.assertTrue(should_dispatch(94, counts["annual"]))
            self.assertTrue(should_dispatch(16, counts["profit"]))

    def test_missing_invalid_or_stale_evidence_never_masquerades_as_zero_work(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(source_queue_counts(d, self.catalog, self.operational),
                             {"annual": None, "profit": None})
            self.assertTrue(should_dispatch(94, None))
            self.assertTrue(should_dispatch(16, None))
            self.assertFalse(should_dispatch(0, None))
            stale = dict(self.operational, asof="2026-11-11")
            self.assertEqual(source_queue_counts(d, self.catalog, stale),
                             {"annual": None, "profit": None})
            with self.assertRaises(ValueError):
                should_dispatch(3, -1)
            with self.assertRaises(ValueError):
                should_dispatch(-1, None)

    def test_invalid_source_plan_does_not_stop_other_source_families(self):
        with tempfile.TemporaryDirectory() as d:
            for name in FILES:
                (Path(d) / name).write_text("{}")
            with patch("v3_manual_90first_source_backlog.revenue_plan",
                       side_effect=ValueError("misaligned_receipt")), patch(
                    "v3_manual_90first_source_backlog.profit_completion",
                    return_value=({}, {}, set(), {
                        "status": "waiting_current_operational_snapshot",
                        "pending_all_listed_issuers": 0})):
                counts = source_queue_counts(d, self.catalog, self.operational)
            self.assertEqual(counts, {"annual": None, "profit": None})


if __name__ == "__main__":
    unittest.main()
