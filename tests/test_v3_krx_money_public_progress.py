"""Offline guards for KRX actual-money aggregate progress publication."""
import hashlib
import json
import unittest
from unittest.mock import patch

from v3_krx_money_backfill import (
    SCHEMA as CHECKPOINT_SCHEMA, CHECKPOINT, _path, _cohort_at_recent_first,
)
from v3_krx_money_public_progress import collect, MoneyProgressError


class Storage:
    repository = "ykvic1/korean-stock-private-data"

    def __init__(self, contents):
        self.contents = contents
        self.reads = []
        self.change_on_second_checkpoint_read = False

    def verify_private(self):
        return True

    def get_content(self, path):
        self.reads.append(path)
        data = self.contents.get(path)
        if (path == CHECKPOINT
            and self.change_on_second_checkpoint_read
            and self.reads.count(path) > 1):
            return b'changed', 'different-version'
        return (data, hashlib.sha256(data).hexdigest() if data else None)


DATES = [f"2026-10-{day:02d}" for day in range(1, 21)]
DIGEST = "a" * 64


def make_storage(completed):
    obj = {
        "schema": CHECKPOINT_SCHEMA,
        "start": "2016-10-17",
        "end": "2026-10-16",
        "calendar_candidate_hash": DIGEST,
        "next_window_index": completed,
        "full_issuer_bucket_records": 250 * completed,
        "compressed_factor_bytes": 123 * completed,
        "last_window_file_sha256": None,
        "raw_source_file_count": 0,
    }
    files = {}
    if completed:
        tail = _cohort_at_recent_first(DATES, completed - 1)
        blob = b"fake-private-validated-derived-only-blob"
        obj["last_window_file_sha256"] = hashlib.sha256(blob).hexdigest()
        files[_path(tail)] = blob
    files[CHECKPOINT] = json.dumps(obj).encode()
    return Storage(files)


class PublicMoneyProgressTests(unittest.TestCase):
    def test_only_aggregate_never_approves_money_score(self):
        storage = make_storage(2)
        verified = {
            "complete_five_session_issuer_market_pairs": 100,
            "raw_vendor_source_retained": False,
        }
        with patch("v3_krx_money_public_progress.validate_money_blob",
                   return_value=verified) as validator:
            result = collect(storage, DATES, DIGEST)
        self.assertEqual(result["five_session_cohorts_completed"], 2)
        self.assertEqual(result["five_session_cohorts_remaining"], 2)
        self.assertEqual(result["candidate_sessions_in_completed_cohorts"], 10)
        self.assertFalse(result["money_factors_in_final_investment_scores"])
        self.assertFalse(result["full_v3_score_certified"])
        self.assertEqual(result["daily_horizon_research"]["20"]
                         ["possible_consecutive_five_session_endpoints"], 0)
        self.assertNotIn("000001", json.dumps(result))
        self.assertNotIn("ACC_TRDVAL", json.dumps(result.get("daily_horizon_research")))
        validator.assert_called_once()
        self.assertEqual(storage.reads.count(CHECKPOINT), 2)

    def test_digest_mismatch_cannot_be_published(self):
        storage = make_storage(1)
        tail = _cohort_at_recent_first(DATES, 0)
        storage.contents[_path(tail)] = b"mutated"
        with self.assertRaisesRegex(MoneyProgressError, "tail_missing_or_changed"):
            collect(storage, DATES, DIGEST)

    def test_concurrent_advance_is_not_a_valid_snapshot(self):
        storage = make_storage(1)
        storage.change_on_second_checkpoint_read = True
        with patch("v3_krx_money_public_progress.validate_money_blob",
                   return_value={
                       "complete_five_session_issuer_market_pairs": 10,
                       "raw_vendor_source_retained": False,
                   }):
            with self.assertRaisesRegex(MoneyProgressError, "changed_during_verification"):
                collect(storage, DATES, DIGEST)

    def test_checkpoint_invalid_fails_closed(self):
        storage = make_storage(0)
        data = json.loads(storage.contents[CHECKPOINT])
        data["raw_source_file_count"] = 1
        storage.contents[CHECKPOINT] = json.dumps(data).encode()
        with self.assertRaisesRegex(MoneyProgressError, "checkpoint_invalid"):
            collect(storage, DATES, DIGEST)


if __name__ == "__main__":
    unittest.main()
