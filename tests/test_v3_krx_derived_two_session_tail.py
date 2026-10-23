"""Two-session KRX partial tail: no raw OHLCV, no fake five-day evidence."""
import gzip
import json
import unittest
from unittest.mock import patch

import v3_krx_derived_two_session_tail as two
from test_v3_krx_derived_5day import FakeKRX, FakePrivate


DAYS = ["2026-10-15", "2026-10-16"]
SESSIONS = ["2026-10-06", "2026-10-07", "2026-10-08",
            "2026-10-13", "2026-10-14"] + DAYS
DIGEST = "a" * 64


class TwoSessionTailTests(unittest.TestCase):
    def test_one_observed_interval_no_volatility_or_pseudo_five_day(self):
        source = FakeKRX()
        report, blob = two.derive(source, DAYS)
        self.assertEqual(len(source.calls), 4)
        self.assertEqual(report["source_api_calls"], 4)
        self.assertEqual(report["observed_two_session_issuers"], 8)
        self.assertFalse(report["valid_complete_five_day_cohort"])
        self.assertFalse(report["join_20_60_120_250_allowed"])
        self.assertFalse(report["v3_six_pillar_score_certified"])
        self.assertEqual(two.validate(blob, DAYS)["observed_two_session_issuers"], 8)
        for item in report["per_issuer_partial_factors"].values():
            self.assertEqual(item["observed_return_intervals"], 1)
            self.assertTrue(item["volatility_not_estimable_from_one_interval"])
            self.assertTrue(item["obv_trend_not_estimable_from_one_interval"])
            self.assertFalse(item["eligible_for_five_session_chain"])
        source_text = json.dumps(report)
        for unsafe in ("TDD_CLSPRC", "ACC_TRDVOL", "raw_source_base64"):
            self.assertNotIn(unsafe, source_text)

    def test_remainder_zero_four_or_three_never_calls_krx(self):
        for days in (SESSIONS[:5], SESSIONS[:5] + DAYS + ["2026-10-19"],
                     SESSIONS[:5] + DAYS + ["2026-10-19", "2026-10-20"]):
            self.assertIsNone(two.select_tail(days))
            archive = FakePrivate()
            provider = FakeKRX()
            result = two.store(archive, provider, days, DIGEST)
            self.assertEqual(result["provider_api_calls"], 0)
            self.assertFalse(provider.calls)

    def test_private_readback_reuse_never_requeries_official_api(self):
        private = FakePrivate()
        first = FakeKRX()
        with patch.object(two, "_source_completed", return_value=True):
            stored = two.store(private, first, SESSIONS, DIGEST)
        self.assertEqual(stored["status"], "verified_saved_two_session_tail")
        self.assertEqual(stored["provider_api_calls"], 4)
        self.assertEqual(private.posts, 1)
        self.assertEqual(len(first.calls), 4)
        self.assertEqual(len(private.blobs), 1)
        self.assertTrue(next(iter(private.blobs)).startswith("derived/partial-tail/"))
        repeated = FakeKRX()
        with patch.object(two, "_source_completed", return_value=True):
            result = two.store(private, repeated, SESSIONS, DIGEST)
        self.assertEqual(result["status"], "reused_verified_two_session_tail")
        self.assertEqual(result["provider_api_calls"], 0)
        self.assertFalse(repeated.calls)
        self.assertEqual(private.posts, 1)

    def test_reject_absent_five_session_checkpoint_without_source_calls(self):
        archive = FakePrivate()
        provider = FakeKRX()
        with patch.object(two, "_source_completed", return_value=False):
            result = two.store(archive, provider, SESSIONS, DIGEST)
        self.assertEqual(result["status"], "awaiting_verified_five_session_history")
        self.assertFalse(provider.calls)
        self.assertEqual(archive.posts, 0)

    def test_failed_market_pair_never_writes_and_never_leaks_provider_exception(self):
        private = FakePrivate()
        provider = FakeKRX(fail_market=("KOSDAQ", "2026-10-15"))
        with patch.object(two, "_source_completed", return_value=True):
            with self.assertRaisesRegex(two.TwoSessionTailError,
                                        "^two_session_official_source_or_integrity_failure$") as err:
                two.store(private, provider, SESSIONS, DIGEST)
        self.assertNotIn("untrusted-api-token", str(err.exception))
        self.assertEqual(private.posts, 0)

    def test_private_only_gate_precedes_provider_calls(self):
        archive = FakePrivate()
        archive.repository = "ykvic1/korean-stock-screener"
        provider = FakeKRX()
        with self.assertRaisesRegex(two.TwoSessionTailError,
                                    "private_derived_destination_required"):
            two.store(archive, provider, SESSIONS, DIGEST)
        self.assertFalse(provider.calls)

    def test_corrupt_existing_file_fails_closed_without_provider_retries(self):
        archive = FakePrivate()
        path = "derived/partial-tail/2026-10-15_2026-10-16.two-session.json.gz"
        archive.blobs[path] = b"corrupt"
        provider = FakeKRX()
        with patch.object(two, "_source_completed", return_value=True):
            with self.assertRaisesRegex(two.TwoSessionTailError,
                                        "private_two_session_derived_integrity_failure"):
                two.store(archive, provider, SESSIONS, DIGEST)
        self.assertFalse(provider.calls)
        self.assertEqual(archive.posts, 0)

    def test_tampered_score_certification_never_validates(self):
        _, blob = two.derive(FakeKRX(), DAYS)
        data = json.loads(gzip.decompress(blob))
        data["v3_six_pillar_score_certified"] = True
        bad = gzip.compress(two._canonical(data), mtime=0)
        with self.assertRaises(two.TwoSessionTailError):
            two.validate(bad, DAYS)

    def test_eventual_readback_retries_get_only(self):
        archive = FakePrivate()
        original = archive.get_content
        lag = {"remaining": 2}
        def delayed(path):
            if path in archive.blobs and lag["remaining"]:
                lag["remaining"] -= 1
                return None, None
            return original(path)
        archive.get_content = delayed
        provider = FakeKRX()
        with patch.object(two, "_source_completed", return_value=True):
            with patch.object(two.time, "sleep", return_value=None) as sleeper:
                result = two.store(archive, provider, SESSIONS, DIGEST)
        self.assertEqual(result["provider_api_calls"], 4)
        self.assertEqual(archive.posts, 1)
        self.assertEqual(sleeper.call_count, 2)


if __name__ == "__main__":
    unittest.main()
