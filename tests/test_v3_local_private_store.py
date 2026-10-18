"""No-API filesystem tests for private KRX paired-market atomic archival."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from v3_local_private_store import (
    PrivateStoreError, store_pair, inspect_private_store
)

DAY = "2026-10-16"
RECEIPT = DAY + "T07:10:00Z"


class PrivateLocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "v3-private"
        self.db = self.folder / "snapshots.sqlite3"

    def save(self, *, day=DAY, kpi=b'{"synthetic_kospi":1}',
             kdq=b'{"synthetic_kosdaq":2}', receipt=RECEIPT):
        return store_pair(
            database_path=self.db, asof=day,
            kospi_raw=kpi, kosdaq_raw=kdq,
            kospi_received_at_utc=receipt,
            kosdaq_received_at_utc=receipt)

    def test_pair_insert_rehash_and_no_release(self):
        report = self.save()
        self.assertEqual(report["status"], "private_pair_added")
        self.assertEqual(report["archived_days"], 1)
        self.assertTrue(report["raw_sources_rehashed_against_manifest"])
        self.assertFalse(report["third_party_provenance_authenticated"])
        self.assertFalse(report["private_at_rest_encryption_provided"])
        self.assertFalse(report["contractual_storage_permission_verified"])
        self.assertFalse(report["authorized_for_v3_score"])
        self.assertFalse(report["confirmed_buy"])
        second = inspect_private_store(database_path=self.db)
        self.assertEqual(second["archived_days"], 1)
        self.assertEqual(second["last_chain_digest_unanchored"],
                         report["last_chain_digest_unanchored"])
        self.assertNotIn("synthetic_kospi", json.dumps(report))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_private_permissions_enforced(self):
        self.save()
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        os.chmod(self.db, 0o644)
        with self.assertRaisesRegex(PrivateStoreError, "permissions_too_open"):
            inspect_private_store(database_path=self.db)

    def test_idempotent_replay_never_adds_duplicate(self):
        a = self.save()
        b = self.save()
        self.assertEqual(b["status"], "private_pair_already_present_unchanged")
        self.assertEqual(b["archived_days"], 1)
        self.assertEqual(a["last_chain_digest_unanchored"],
                         b["last_chain_digest_unanchored"])

    def test_changed_same_day_raw_source_rolls_back_and_preserves_original(self):
        original = self.save()
        with self.assertRaisesRegex(PrivateStoreError, "transaction_rejected"):
            self.save(kpi=b'{"synthetic_kospi":99}')
        observed = inspect_private_store(database_path=self.db)
        self.assertEqual(observed["archived_days"], 1)
        self.assertEqual(observed["last_chain_digest_unanchored"],
                         original["last_chain_digest_unanchored"])

    def test_second_day_links_and_old_date_insertion_fails(self):
        self.save()
        other = self.save(
            day="2026-10-19", receipt="2026-10-19T07:10:00Z",
            kpi=b'{"kospi":3}', kdq=b'{"kosdaq":4}')
        self.assertEqual(other["archived_days"], 2)
        before = other["last_chain_digest_unanchored"]
        with self.assertRaises(PrivateStoreError):
            self.save(day="2026-10-17", receipt="2026-10-17T07:10:00Z")
        self.assertEqual(inspect_private_store(
            database_path=self.db)["last_chain_digest_unanchored"], before)

    def test_one_missing_market_does_not_create_database(self):
        for invalid in (None, b"", "not bytes", True):
            with self.subTest(value=str(type(invalid))):
                with self.assertRaises(PrivateStoreError):
                    self.save(kdq=invalid)
                self.assertFalse(self.db.exists())

    def test_same_raw_response_reused_across_markets_cannot_be_stored(self):
        with self.assertRaises(PrivateStoreError):
            self.save(kpi=b"same raw response", kdq=b"same raw response")
        # Failure may create an empty new DB, but no partial market pair.
        if self.db.exists():
            self.assertEqual(inspect_private_store(
                database_path=self.db)["archived_days"], 0)

    def test_db_tamper_to_raw_bytes_is_detected_without_raw_output(self):
        self.save()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE source_pairs SET kospi_raw=? WHERE asof=?",
                     (b"compromised-private-raw", DAY))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(PrivateStoreError, "raw_bytes_and_manifest_disagree"):
            inspect_private_store(database_path=self.db)

    def test_db_tamper_to_manifest_detected(self):
        self.save()
        conn = sqlite3.connect(self.db)
        item = json.loads(conn.execute(
            "SELECT chain_entry_json FROM source_pairs WHERE asof=?",
            (DAY,)).fetchone()[0])
        item["prev_hash"] = "f" * 64
        conn.execute("UPDATE source_pairs SET chain_entry_json=? WHERE asof=?",
                     (json.dumps(item), DAY))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(PrivateStoreError, "chain_inconsistent"):
            inspect_private_store(database_path=self.db)

    def test_cannot_create_database_within_public_repository(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "unsafe.sqlite3"
        with self.assertRaisesRegex(PrivateStoreError, "public_repo_storage_forbidden"):
            store_pair(database_path=path, asof=DAY,
                       kospi_raw=b"a", kosdaq_raw=b"b",
                       kospi_received_at_utc=RECEIPT,
                       kosdaq_received_at_utc=RECEIPT)
        self.assertFalse(path.exists())

    def test_bad_receipt_or_different_timestamp_replay_is_blocked(self):
        first = self.save()
        with self.assertRaises(PrivateStoreError):
            self.save(receipt="2026-10-16T06:00:00Z")
        with self.assertRaises(PrivateStoreError):
            self.save(receipt="2026-10-16T08:00:00Z")
        self.assertEqual(inspect_private_store(
            database_path=self.db)["last_chain_digest_unanchored"],
            first["last_chain_digest_unanchored"])

    def test_inspect_nonexistent_is_read_only_no_new_artifact(self):
        with self.assertRaisesRegex(PrivateStoreError, "private_store_not_found"):
            inspect_private_store(database_path=self.db)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.folder.exists())


if __name__ == "__main__":
    unittest.main()
