"""Offline guardrails for continuous private DART catch-up orchestration."""
import unittest

from v3_dart_continuous_catchup import (
    MAX_BATCHES, MAX_PROVIDER_CALLS, continuous_catchup,
)
from v3_dart_historical_event_receipts import DARTEventReceiptError


def response(months, calls, pages=None, reused=0):
    return {"months_completed": months, "provider_calls": calls,
            "new_pages": calls if pages is None else pages,
            "previously_saved_pages_reused": reused}


class ContinuousDARTCatchupTests(unittest.TestCase):
    def test_completes_120_months_using_separate_bounded_segments(self):
        sequence = iter([response(118, 72), response(119, 72),
                         response(120, 9)])
        ceilings = []

        def collector(ceiling):
            ceilings.append(ceiling)
            return next(sequence)

        report = continuous_catchup(collector, clock=lambda: 0)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["months_completed"], 120)
        self.assertEqual(report["provider_calls"], 153)
        self.assertEqual(ceilings, [72, 72, 72])
        self.assertFalse(report["investment_scores_promoted"])

    def test_provider_budget_cannot_be_exceeded(self):
        ceilings = []

        def collector(ceiling):
            ceilings.append(ceiling)
            return response(85, ceiling)

        report = continuous_catchup(
            collector, clock=lambda: 0, max_provider_calls=75)
        self.assertEqual(ceilings, [72, 3])
        self.assertEqual(report["provider_calls"], 75)
        self.assertEqual(report["status"], "provider_budget")

    def test_time_limit_preserves_last_completed_batch(self):
        ticks = iter([0, 0, 101])
        report = continuous_catchup(
            lambda ceiling: response(86, ceiling),
            clock=lambda: next(ticks), max_seconds=100)
        self.assertEqual(report["status"], "time_limit")
        self.assertEqual(report["segments"], 1)

    def test_no_work_is_done_after_private_cursor_says_complete(self):
        report = continuous_catchup(
            lambda ceiling: response(120, 0),
            clock=lambda: 0)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["segments"], 1)

    def test_unchanged_cursor_stops_without_spinning(self):
        report = continuous_catchup(
            lambda ceiling: response(90, 0, 0),
            clock=lambda: 0)
        self.assertEqual(report["status"], "stalled_no_source_progress")
        self.assertEqual(report["segments"], 1)

    def test_recovered_orphans_are_not_mistaken_for_stall(self):
        seen = iter([response(90, 0, 0, reused=2), response(91, 2)])
        report = continuous_catchup(
            lambda ceiling: next(seen), clock=lambda: 0, max_batches=2)
        self.assertEqual(report["status"], "batch_limit")
        self.assertEqual(report["orphan_pages_recovered"], 2)

    def test_rejects_unbounded_settings_and_untrusted_reports(self):
        for settings in [
            {"max_batches": MAX_BATCHES + 1},
            {"max_provider_calls": MAX_PROVIDER_CALLS + 1},
            {"max_seconds": 18001},
            {"max_provider_calls": 0},
            {"max_batches": True},
        ]:
            with self.subTest(settings=settings), self.assertRaises(
                    DARTEventReceiptError):
                continuous_catchup(
                    lambda ceiling: response(1, 1),
                    clock=lambda: 0, **settings)
        with self.assertRaises(DARTEventReceiptError):
            continuous_catchup(
                lambda ceiling: response(20, ceiling + 1),
                clock=lambda: 0)

    def test_monotonic_month_progress_enforced(self):
        steps = iter([response(91, 3), response(90, 4)])
        with self.assertRaises(DARTEventReceiptError):
            continuous_catchup(
                lambda ceiling: next(steps), clock=lambda: 0,
                max_batches=2)


if __name__ == "__main__":
    unittest.main()
