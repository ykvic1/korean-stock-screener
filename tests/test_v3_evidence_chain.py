"""Offline synthetic paired-market chain tests; no file, credential or API use."""
from __future__ import annotations

import copy
import unittest

from v3_evidence_chain import (
    EvidenceChainError, ZERO_HASH, SCHEMA, empty_manifest,
    append_market_pair, verify_manifest
)


def pair(day="2026-10-16", received=None):
    timestamp = received or f"{day}T07:40:00Z"
    return {
        "asof": day,
        "kospi": {"raw_sha256": "a" * 64, "byte_count": 100,
                  "received_at_utc": timestamp},
        "kosdaq": {"raw_sha256": "b" * 64, "byte_count": 200,
                   "received_at_utc": timestamp},
    }


def append(manifest, day="2026-10-16", received=None):
    return append_market_pair(manifest, **pair(day, received))


class PrivateEvidenceChainContractTests(unittest.TestCase):
    def test_two_market_complete_pair_hash_link_and_blocked_release(self):
        empty = empty_manifest()
        result = append(empty)
        self.assertEqual(result["outcome"], "new_private_manifest_entry_prepared")
        self.assertEqual(result["manifest"]["schema"], SCHEMA)
        self.assertEqual(result["manifest"]["entries"][0]["prev_hash"], ZERO_HASH)
        audit = verify_manifest(result["manifest"])
        self.assertEqual(audit["records"], 1)
        self.assertEqual(len(audit["head_digest_unanchored"]), 64)
        self.assertFalse(audit["independent_head_anchor_verified"])
        self.assertFalse(audit["persistent_private_storage_ready"])
        self.assertFalse(audit["regular_session_and_pit_verified"])
        self.assertFalse(audit["v3_release_ready"])
        self.assertIsNone(audit["confirmed_v3_score"])
        self.assertEqual(empty["entries"], [])

    def test_second_date_has_previous_head_and_paired_atomic_record(self):
        first = append(empty_manifest())
        second = append(first["manifest"], "2026-10-19")
        entries = second["manifest"]["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1]["prev_hash"], entries[0]["entry_hash"])
        self.assertEqual(set(entries[1]["markets"]), {"KOSPI", "KOSDAQ"})
        self.assertEqual(second["audit"]["records"], 2)
        self.assertFalse(second["audit"]["source_raw_bytes_verified"])

    def test_identical_day_is_idempotent_but_changed_payload_conflicts(self):
        a = append(empty_manifest())
        b = append(a["manifest"])
        self.assertEqual(b["outcome"], "already_recorded_unchanged")
        self.assertEqual(a["manifest"], b["manifest"])
        change = pair()
        change["kospi"]["raw_sha256"] = "c" * 64
        with self.assertRaisesRegex(EvidenceChainError, "existing_day_immutable_conflict"):
            append_market_pair(a["manifest"], **change)

    def test_tamper_history_detected_even_when_new_record_is_valid_shape(self):
        original = append(empty_manifest())
        second = append(original["manifest"], "2026-10-19")
        altered = copy.deepcopy(second["manifest"])
        altered["entries"][0]["markets"]["KOSPI"]["byte_count"] += 1
        with self.assertRaisesRegex(EvidenceChainError, "manifest_entry_digest_mismatch"):
            verify_manifest(altered)
        altered = copy.deepcopy(second["manifest"])
        altered["entries"][1]["prev_hash"] = ZERO_HASH
        with self.assertRaisesRegex(EvidenceChainError, "broken_manifest_hash_link"):
            verify_manifest(altered)
        altered = copy.deepcopy(second["manifest"])
        altered["entries"][1]["asof"] = "2026-10-16"
        with self.assertRaises(EvidenceChainError):
            verify_manifest(altered)

    def test_manifest_fake_certification_flags_always_forbidden(self):
        for flag in ("independent_head_anchor_verified", "raw_source_bytes_authenticated",
                     "real_private_persistent_storage_ready", "v3_release_ready"):
            with self.subTest(flag=flag):
                inp = empty_manifest()
                inp[flag] = True
                with self.assertRaises(EvidenceChainError):
                    verify_manifest(inp)

    def test_backfill_must_be_ordered_and_no_future_data_rewrite(self):
        a = append(empty_manifest(), "2026-10-19")
        with self.assertRaisesRegex(EvidenceChainError, "out_of_order_backfill"):
            append(a["manifest"], "2026-10-16")

    def test_bad_receipts_sources_and_cross_market_duplicates_are_blocked(self):
        base = pair()
        changes = {
            "naive": lambda x: x["kospi"].update(received_at_utc="2026-10-16T07:30:00"),
            "offset": lambda x: x["kospi"].update(received_at_utc="2026-10-16T16:30:00+09:00"),
            "too_early": lambda x: x["kospi"].update(received_at_utc="2026-10-16T06:29:59Z"),
            "before_date": lambda x: x["kospi"].update(received_at_utc="2026-10-15T23:00:00Z"),
            "bad_hash": lambda x: x["kosdaq"].update(raw_sha256="wrong"),
            "source_reuse": lambda x: x["kosdaq"].update(raw_sha256="a" * 64),
            "no_bytes": lambda x: x["kospi"].update(byte_count=0),
            "bool_bytes": lambda x: x["kospi"].update(byte_count=True),
            "huge_bytes": lambda x: x["kospi"].update(byte_count=10**12),
            "unexpected_field": lambda x: x["kospi"].update(verified=True),
            "missing_market": lambda x: x.update(kosdaq=None),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                args = copy.deepcopy(base)
                change(args)
                with self.assertRaises(EvidenceChainError):
                    append_market_pair(empty_manifest(), **args)

    def test_unverified_same_day_retrieval_is_not_pit_evidence(self):
        a = append(empty_manifest())
        self.assertFalse(a["manifest"]["v3_release_ready"])
        self.assertFalse(a["manifest"]["full_pit_history_verified"])
        self.assertFalse(a["audit"]["independent_head_anchor_verified"])
        self.assertFalse(a["audit"]["regular_session_and_pit_verified"])


if __name__ == "__main__":
    unittest.main()
