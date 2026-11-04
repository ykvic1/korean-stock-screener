"""Offline guardrails for continuous private DART catch-up orchestration."""
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from v3_dart_continuous_catchup import (
    MAX_BATCHES, MAX_PROVIDER_CALLS, continuous_catchup,
    QuotaWaitingPrivateGitStorage,
)
from v3_dart_historical_event_receipts import DARTEventReceiptError
import v3_krx_private_github_backfill as transport


def response(months, calls, pages=None, reused=0):
    return {"months_completed": months, "provider_calls": calls,
            "new_pages": calls if pages is None else pages,
            "previously_saved_pages_reused": reused}


class ContinuousDARTCatchupTests(unittest.TestCase):
    def quota_store(self, deadline=10000):
        ticks = [0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            ticks[0] += seconds
        store = QuotaWaitingPrivateGitStorage(
            "ykvic1/private-market-research", "synthetic-token-at-least-20",
            deadline=deadline, clock=lambda: ticks[0], sleeper=sleep)
        return store, ticks, sleeps

    def test_primary_quota_read_waits_then_resumes_exact_request(self):
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, limit): return b'{"private":true}'
        denied = HTTPError("https://hidden/path", 403, "hidden body", {
            "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1060"}, None)
        store, ticks, sleeps = self.quota_store()
        with patch.object(transport.time, "time", return_value=1000), \
             patch.object(transport.request, "urlopen", side_effect=[denied, Reply()]) as get:
            self.assertEqual(store._http("GET", "contents/private-cursor"), {"private": True})
        self.assertEqual(ticks[0], 65)
        self.assertTrue(all(0 < delay <= 30 for delay in sleeps))
        self.assertEqual(store.quota_pauses, 1)
        self.assertEqual(get.call_args_list[0].args[0].full_url,
                         get.call_args_list[1].args[0].full_url)

    def test_quota_wait_never_retries_writes_or_permission_errors(self):
        for method, status, headers in [
            ("PUT", 429, {"Retry-After": "60"}),
            ("PUT", 403, {"X-RateLimit-Remaining": "0", "Retry-After": "60"}),
            ("GET", 403, {}), ("GET", 401, {"Retry-After": "60"}),
        ]:
            with self.subTest(method=method, status=status):
                store, _, sleeps = self.quota_store()
                denied = HTTPError("https://hidden", status, "secret", headers, None)
                with patch.object(transport.request, "urlopen", side_effect=denied) as call:
                    with self.assertRaises(transport.PrivateHistoryError):
                        store._http(method, "contents/private", {"data": "secret"} if method == "PUT" else None)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(sleeps, [])

    def test_secondary_limit_uses_retry_after_instead_of_unused_primary_reset(self):
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, limit): return b'{}'
        for remaining, expected in [("20", 15), ("0", 65)]:
            with self.subTest(remaining=remaining):
                denied = HTTPError("https://hidden", 403, "secret", {
                    "Retry-After": "10", "X-RateLimit-Remaining": remaining,
                    "X-RateLimit-Reset": "1060"}, None)
                store, ticks, _ = self.quota_store()
                with patch.object(transport.time, "time", return_value=1000), \
                     patch.object(transport.request, "urlopen", side_effect=[denied, Reply()]):
                    self.assertEqual(store._http("GET", ""), {})
                self.assertEqual(ticks[0], expected)

    def test_quota_delay_must_fit_deadline_and_have_bounded_numeric_header(self):
        for header, deadline in [("60", 90), ("3601", 10000),
                                 ("hidden-token", 10000), ("-1", 10000),
                                 ("9999999999999999", 10000), (None, 10000)]:
            with self.subTest(header=header, deadline=deadline):
                store, _, sleeps = self.quota_store(deadline)
                headers = {} if header is None else {"Retry-After": header}
                denied = HTTPError("https://hidden", 429, "secret", headers, None)
                with patch.object(transport.request, "urlopen", side_effect=denied) as call:
                    with self.assertRaises(transport.PrivateHistoryError):
                        store._http("GET", "contents/private")
                self.assertEqual(call.call_count, 1)
                self.assertEqual(sleeps, [])

    def test_repeated_quota_blocks_stop_after_three_cooldowns(self):
        store, ticks, _ = self.quota_store()
        denied = HTTPError("https://hidden", 429, "secret", {"Retry-After": "1"}, None)
        with patch.object(transport.request, "urlopen", side_effect=denied) as call:
            with self.assertRaises(transport.PrivateHistoryError):
                store._http("GET", "contents/private")
        self.assertEqual(call.call_count, 4)
        self.assertEqual(store.quota_pauses, 3)
        self.assertEqual(ticks[0], 18)

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


    def test_trusted_short_collector_completion_starts_long_catchup(self):
        fast = Path(".github/workflows/v3-dart-historical-direct-fastlane.yml").read_text(
            encoding="utf-8")
        short = Path(".github/workflows/v3-dart-historical-event-receipts.yml").read_text(
            encoding="utf-8")
        self.assertIn("V3 Private Historical OpenDART Filing Receipts (Research Only)", fast)
        self.assertIn("workflow_run:", fast)
        self.assertIn("types: [completed]", fast)
        self.assertIn("branches: [main]", fast)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", fast)
        self.assertIn("github.event.workflow_run.event == 'schedule'", fast)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", fast)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", fast)
        self.assertIn("group: v3-dart-private-historical-event-receipts", fast)
        self.assertIn("queue: max", fast)
        self.assertIn("cancel-in-progress: false", fast)
        self.assertIn("python v3_dart_continuous_catchup.py", fast)
        # A one-way completion trigger must not recursively spawn short runs.
        self.assertNotIn("workflow_run:", short)


if __name__ == "__main__":
    unittest.main()
