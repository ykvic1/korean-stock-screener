"""No-network adjacent-calendar-month filing/price anomaly regression."""
import base64
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_dart_historical_event_receipts as dart
import v3_private_dart_krx_adjacent_month as adjacent


class Private:
    repository = adjacent.PRIVATE_REPO

    def __init__(self, complete_months=2):
        self.blobs = {
            adjacent.DART_CHECKPOINT: dart._bytes({
                **dart._empty(), "month_index": complete_months
            }),
            adjacent.SOURCE_CHECKPOINT: b"synthetic-source",
            adjacent.RISK_CHECKPOINT: b"synthetic-risk",
        }
        self.puts = 0

    def verify_private(self):
        return True

    def get_content(self, path):
        body = self.blobs.get(path)
        return (body, sha256(body).hexdigest() if body is not None else None)

    def put_new(self, path, body, message):
        if path in self.blobs:
            raise AssertionError("immutable private overwrite")
        self.blobs[path] = body
        self.puts += 1

    def transport(self, method, path, payload=None):
        if method != "PUT" or not path.startswith("contents/"):
            raise AssertionError("unexpected private API")
        key = path.removeprefix("contents/")
        if payload.get("sha") != sha256(self.blobs[key]).hexdigest():
            raise AssertionError("nonconditional cursor mutation")
        self.blobs[key] = base64.b64decode(payload["content"], validate=True)
        self.puts += 1


def history():
    days = [(date(2016, 10, 17) + timedelta(days=i)).isoformat()
            for i in range(45)]
    # Both events have an opposite-month receipt within three calendar days.
    flags = [
        {"issuer_market": "KOSPI:005930", "last_day": "2016-10-31"},
        {"issuer_market": "KOSPI:005930", "last_day": "2016-11-03"},
        {"issuer_market": "KOSDAQ:000660", "last_day": "2016-10-20"},
    ]
    return flags, days, {"next_cohort_index": 9}, b"synthetic-source", b"synthetic-risk"


def receipts(_storage, month):
    return ([{"stock_code": "005930", "rcept_dt": "20161031"}]
            if month == "2016-10" else
            [{"stock_code": "005930", "rcept_dt": "20161103"}])


class AdjacentMonthTests(unittest.TestCase):
    def test_adjacent_month_window_is_additive_private_and_resumable(self):
        private = Private()
        with (patch.object(adjacent, "_source_events", return_value=history()),
              patch.object(adjacent, "_month_receipts", side_effect=receipts)):
            result = adjacent.run(private)
        self.assertEqual(result["status"], "private_unverified_adjacent_month_pair_completed")
        self.assertEqual(result["opposite_month_nearby_event_count"], 2)
        self.assertEqual(result["opposite_month_receipt_candidate_pairs"], 2)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["confirmed_corporate_actions"], 0)
        self.assertEqual(result["scores_promoted"], 0)
        report = private.blobs[adjacent._pair_path(0)]
        data = json.loads(report)
        self.assertEqual(data["first_month"], "2016-10")
        self.assertEqual(data["second_month"], "2016-11")
        self.assertTrue(data["krx_source_begins_within_this_pair"])
        self.assertFalse(data["filing_kind_or_effective_date_verified"])
        self.assertFalse(data["adjusted_prices_certified"])
        self.assertFalse(data["trading_scores_authorized"])
        for sensitive in ("005930", "000660", "20161031", "20161103"):
            self.assertNotIn(sensitive, report.decode())
        self.assertEqual(private.puts, 2)
        with patch.object(adjacent, "_source_events") as source:
            resumed = adjacent.run(private)
        source.assert_not_called()
        self.assertEqual(resumed["status"], "waiting_for_two_complete_adjacent_dart_months")
        self.assertEqual(private.puts, 2)

    def test_no_neighboring_month_ever_means_no_negative_evidence(self):
        private = Private(complete_months=1)
        with patch.object(adjacent, "_source_events") as source:
            result = adjacent.run(private)
        source.assert_not_called()
        self.assertEqual(result["status"], "waiting_for_two_complete_adjacent_dart_months")
        self.assertEqual(result["confirmed_corporate_actions"], 0)
        self.assertEqual(private.puts, 0)

    def test_incomplete_krx_second_month_and_mutating_source_fail_closed(self):
        private = Private()
        flags, days, _, s, r = history()
        with patch.object(adjacent, "_source_events", return_value=(
            flags, days, {"next_cohort_index": 5}, s, r)):
            result = adjacent.run(private)
        self.assertEqual(result["status"], "waiting_for_full_second_month_krx_risk_review")
        self.assertEqual(private.puts, 0)
        with (patch.object(adjacent, "_source_events", return_value=(
                flags, days, {"next_cohort_index": 9}, b"mismatched-source", r)),
              patch.object(adjacent, "_month_receipts", side_effect=receipts)):
            with self.assertRaises(adjacent.AdjacentMonthError):
                adjacent.run(private)
        self.assertEqual(private.puts, 0)

    def test_only_opposite_month_receipts_are_compared_with_calendar_day_limit(self):
        flags = [
            {"issuer_market": "KOSPI:005930", "last_day": "2016-10-31"},
            {"issuer_market": "KOSPI:005930", "last_day": "2016-10-01"},
        ]
        result = adjacent._count_boundary_candidates(
            flags,
            [{"stock_code": "005930", "rcept_dt": "20161031"}],
            [{"stock_code": "005930", "rcept_dt": "20161103"}],
            "2016-10", "2016-11",
        )
        self.assertEqual(result["price_discontinuity_events_examined_in_pair"], 2)
        self.assertEqual(result["opposite_month_nearby_event_count"], 1)
        self.assertEqual(result["opposite_month_receipt_candidate_pairs"], 1)
        self.assertFalse(result["ticker_history_identity_verified"])

    def test_corrupt_checkpoint_private_destination_and_tail_are_rejected(self):
        private = Private()
        private.repository = "ykvic1/korean-stock-screener"
        with self.assertRaises(adjacent.AdjacentMonthError):
            adjacent.run(private)
        self.assertRaises(adjacent.AdjacentMonthError,
                          adjacent._checkpoint, b'{"schema":"fake","next_pair_index":1}')
        self.assertRaises(adjacent.AdjacentMonthError,
                          adjacent._checkpoint, b'{"schema":"v3-private-dart-krx-adjacent-month-date-candidates-v1","next_pair_index":true}')

    def test_cli_exposes_only_vetted_static_validation_codes(self):
        # A normal source-integrity failure must reveal its class so a
        # repeated scheduled failure can be diagnosed without raw evidence.
        with (patch.dict(os.environ, {
                "V3_PRIVATE_ARCHIVE_REPO": adjacent.PRIVATE_REPO,
                "V3_PRIVATE_ARCHIVE_TOKEN": "x" * 30}),
              patch.object(adjacent, "PrivateGitStorage", return_value=Private()),
              patch.object(adjacent, "run",
                           side_effect=adjacent.AdjacentMonthError(
                               "invalid_private_dart_receipt_source"))):
            with self.assertRaises(SystemExit) as caught:
                adjacent.main()
            self.assertIn("invalid_private_dart_receipt_source",
                          str(caught.exception))
        # Arbitrary exception text is never a loggable reason, even when
        # raised using the domain error class by an untrusted component.
        for exc in (
            adjacent.AdjacentMonthError("secret-private-company-identifier"),
            RuntimeError("secret-private-transport-token"),
        ):
            with (patch.dict(os.environ, {
                    "V3_PRIVATE_ARCHIVE_REPO": adjacent.PRIVATE_REPO,
                    "V3_PRIVATE_ARCHIVE_TOKEN": "x" * 30}),
                  patch.object(adjacent, "PrivateGitStorage", return_value=Private()),
                  patch.object(adjacent, "run", side_effect=exc)):
                with self.assertRaises(SystemExit) as caught:
                    adjacent.main()
                self.assertNotIn("secret", str(caught.exception))
                self.assertIn("blocked: private adjacent-month evidence",
                              str(caught.exception))

    def test_schedules_privacy_and_legacy_market_publication_remain_separate(self):
        wf = Path(".github/workflows/v3-private-dart-krx-event-crosscheck.yml").read_text()
        old = Path(".github/workflows/full-market-batches.yml").read_text()
        self.assertIn("v3_private_dart_krx_adjacent_month.py", wf)
        self.assertIn("test_v3_private_dart_krx_adjacent_month.py", wf)
        self.assertIn("python v3_private_dart_krx_adjacent_month.py", wf)
        self.assertIn("contents: read", wf)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", wf)
        self.assertIn("group: v3-dart-private-historical-event-receipts", wf)
        self.assertIn("30 9 * * 1-5", old)
        self.assertNotIn("DART_API_KEY:", wf)
        self.assertNotIn("KRX_API_KEY:", wf)


if __name__ == "__main__":
    unittest.main()
