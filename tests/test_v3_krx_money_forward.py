"""Offline tests for KRX real-money append-only forward tail over fixed history."""
import base64
from datetime import date, timedelta
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from v3_krx_money_backfill import _path
from official_probe.clients import KRXProbeError
from v3_krx_money_forward import (
    SCHEMA, CHECKPOINT, _manifest, collect, forward_candidates,
    ForwardMoneyError, RetryingMoneyClient,
)


def weekdays(start, n):
    current = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if current.weekday() < 5:
            out.append(current.isoformat())
        current += timedelta(days=1)
    return out


class FakeStore:
    repository = "ykvic1/korean-stock-private-data"

    def __init__(self):
        self.files = {}

    def verify_private(self):
        return True

    def get_content(self, path):
        value = self.files.get(path)
        return (value, sha256(value).hexdigest() if value is not None else None)

    def put_new(self, path, data, message):
        if path in self.files:
            raise AssertionError("overwriting private immutable path")
        self.files[path] = data

    def transport(self, method, path, payload):
        assert method == "PUT" and path.startswith("contents/")
        actual = path[len("contents/"):]
        assert payload["sha"] == sha256(self.files[actual]).hexdigest()
        self.files[actual] = base64.b64decode(payload["content"])
        return {}


class ForwardMoneyTests(unittest.TestCase):
    def test_calendar_first_cohort_includes_frozen_two_day_residue(self):
        old = weekdays("2026-10-08", 7)
        self.assertEqual(old[-1], "2026-10-16")
        later = weekdays("2026-10-19", 7)
        with patch("v3_krx_money_forward.history_candidates",
                   return_value=(old, "x"*64)), patch(
                   "v3_krx_money_forward.calendar_weekday_candidates",
                   return_value=later), patch(
                   "v3_krx_money_forward.candidate_dates",
                   return_value={"dates": later}):
            dates = forward_candidates("2026-11-03")
        self.assertEqual(dates[:2], old[-2:])
        self.assertEqual(dates[2:], later)
        self.assertEqual(dates[:5], old[-2:] + later[:3])

    def test_additional_calendar_days_never_reinterpret_prior_checkpoint(self):
        store = FakeStore()
        initial = weekdays("2026-10-15", 11)
        extended = weekdays("2026-10-15", 16)
        calls = []

        def save(store_obj, client, window):
            calls.append(list(window))
            data = ("derived-factor-" + window[0]).encode()
            store_obj.put_new(_path(window), data, "fake test")
            return {"status": "persisted_and_verified_money",
                    "source_api_calls": 10}

        with patch("v3_krx_money_forward.store_money_five_days", side_effect=save), patch(
            "v3_krx_money_forward.validate_money_blob",
            return_value={"complete_five_session_issuer_market_pairs": 2}):
            first = collect(store, object(), initial, windows=1)
            self.assertEqual(first["completed_forward_five_session_cohorts"], 1)
            self.assertEqual(first["current_run_api_calls"], 10)
            self.assertFalse(first["full_v3_score_certified"])
            second = collect(store, object(), extended, windows=8)
            self.assertEqual(second["completed_forward_five_session_cohorts"], 3)
            self.assertEqual(second["current_run_api_calls"], 20)
            again = collect(store, object(), extended, windows=8)
            self.assertEqual(again["current_run_api_calls"], 0)
            self.assertEqual(again["completed_forward_five_session_cohorts"], 3)
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(store.files[CHECKPOINT])["schema"], SCHEMA)
        self.assertEqual(len(store.files), 4)
        self.assertEqual(_manifest(store.files[CHECKPOINT], extended)
                         ["next_window_index"], 3)

    def test_calendar_insertion_into_committed_prefix_fails_closed(self):
        store = FakeStore()
        dates = weekdays("2026-10-15", 10)
        checkpoint = {
            "schema": SCHEMA,
            "historical_anchor_end": "2026-10-16",
            "next_window_index": 1,
            "completed_candidate_prefix_sha256": "x" * 64,
            "issuer_cohort_records": 2,
            "compressed_factor_bytes": 22,
            "last_window_file_sha256": "a" * 64,
            "raw_source_file_count": 0,
        }
        store.files[CHECKPOINT] = json.dumps(checkpoint).encode()
        with self.assertRaisesRegex(ForwardMoneyError, "invalid_forward_checkpoint"):
            collect(store, object(), dates)

    def test_transient_http_200_empty_rows_is_retried_with_bounded_budget(self):
        class Source:
            calls = 0
            def daily_market(self, market, day):
                self.calls += 1
                if self.calls < 3:
                    raise KRXProbeError(market, "empty_market_rows", 200)
                return ([{"validated": True}], b"valid market payload")

        source = Source()
        client = RetryingMoneyClient(source)
        with patch("v3_krx_money_forward.retry_clock.sleep") as delay:
            rows, payload = client.daily_market("KOSPI", "2026-11-10")
        self.assertEqual(rows, [{"validated": True}])
        self.assertEqual(payload, b"valid market payload")
        self.assertEqual(source.calls, 3)
        self.assertEqual(client.requests, 3)
        self.assertEqual([call.args[0] for call in delay.call_args_list], [1, 2])

    def test_persistent_empty_rows_and_forbidden_never_promote_unverified_cohort(self):
        class Source:
            calls = 0
            reason = "empty_market_rows"
            def daily_market(self, market, day):
                self.calls += 1
                raise KRXProbeError(market, self.reason, 200 if self.reason == "empty_market_rows" else 403)

        for reason, expected in (("empty_market_rows", 3), ("http_forbidden", 1)):
            with self.subTest(reason=reason):
                source = Source()
                source.reason = reason
                client = RetryingMoneyClient(source)
                with patch("v3_krx_money_forward.retry_clock.sleep"):
                    with self.assertRaises(KRXProbeError) as raised:
                        client.daily_market("KOSDAQ", "2026-11-10")
                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(client.requests, expected)
                self.assertEqual(source.calls, expected)

    def test_failed_source_does_not_advance_private_checkpoint(self):
        store = FakeStore()
        dates = weekdays("2026-10-15", 10)
        with patch("v3_krx_money_forward.store_money_five_days",
                   side_effect=ValueError("offline simulated failed source")):
            with self.assertRaises(ValueError):
                collect(store, object(), dates)
        self.assertNotIn(CHECKPOINT, store.files)
        self.assertFalse(any(path.startswith("derived/money-five-session/")
                             for path in store.files))


if __name__ == "__main__":
    unittest.main()
