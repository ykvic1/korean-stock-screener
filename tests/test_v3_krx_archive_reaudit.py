"""All synthetic, no real KRX requests, no credentials, no vendor uploads."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from v3_krx_archive_reaudit import (audit_private_archive, ArchiveReauditError,
                                    main)
from v3_krx_local_resume_batch import run_one_local_batch, LocalBatchError
from v3_local_private_store import store_pair


DAY0 = "2026-10-15"
DAY1 = "2026-10-16"


def row(code, day, *, opened="100", high="110", low="90",
        closed="103", volume="100"):
    return {"ISU_CD": code, "BAS_DD": day.replace("-", ""),
            "TDD_OPNPRC": opened, "TDD_HGPRC": high,
            "TDD_LWPRC": low, "TDD_CLSPRC": closed,
            "ACC_TRDVOL": volume}


def raw(*rows):
    return json.dumps({"OutBlock_1": list(rows)}, sort_keys=True).encode()


class CallRecorder:
    def __init__(self):
        self.calls = []

    def daily_market(self, market, day):
        self.calls.append((market, day))
        code = "005930" if market == "KOSPI" else "247540"
        rows = [row(code, day)]
        return rows, raw(*rows)


class PrivateArchiveReauditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "market.sqlite3"

    def record(self, day=DAY0, *, kpi=None, kdq=None):
        if kpi is None:
            kpi = raw(row("005930", day),
                      row("00088K", day, opened="0", high="0", low="0",
                          closed="107", volume="0"))
        if kdq is None:
            kdq = raw(row("247540", day))
        return store_pair(
            database_path=self.path, asof=day,
            kospi_raw=kpi, kosdaq_raw=kdq,
            kospi_received_at_utc=day + "T07:20:00Z",
            kosdaq_received_at_utc=day + "T07:21:00Z")

    def check(self, **kwargs):
        return audit_private_archive(
            start=kwargs.pop("start", DAY0),
            end=kwargs.pop("end", DAY1),
            private_db=kwargs.pop("private_db", self.path),
            calendar_source=kwargs.pop("calendar_source", "weekdays"),
            **kwargs)

    def test_missing_archive_is_read_only_and_not_a_session_validation(self):
        result = self.check()
        self.assertFalse(self.path.exists())
        self.assertEqual(result["actual_api_calls"], 0)
        self.assertEqual(result["archive_paired_dates"], 0)
        self.assertEqual(result["next_batch_dates"], [DAY0, DAY1])
        self.assertFalse(result["internal_chain_and_source_shape_consistent"])
        self.assertFalse(result["historical_250_sessions_verified"])

    def test_reaudit_counts_quarantine_and_drift_with_no_codes_or_prices(self):
        self.record()
        self.record(DAY1, kpi=raw(row("005930", DAY1),
                                 row("00088L", DAY1)),
                    kdq=raw(row("247540", DAY1)))
        result = self.check()
        self.assertEqual(result["status"], "internally_reaudited_unverified_source")
        self.assertEqual(result["archive_paired_dates"], 2)
        self.assertEqual(result["next_batch_dates"], [])
        self.assertEqual(result["by_market"]["KOSPI"]["source_rows"], 4)
        self.assertEqual(result["by_market"]["KOSPI"]["valid_numeric_rows"], 3)
        self.assertEqual(result["by_market"]["KOSPI"]["quarantined_rows"], 1)
        self.assertEqual(result["by_market"]["KOSPI"]["new_codes_vs_prior_day"], 1)
        self.assertEqual(result["by_market"]["KOSPI"]["missing_codes_vs_prior_day"], 1)
        self.assertEqual(result["by_market"]["KOSDAQ"]["source_rows"], 2)
        for key in ("official_exchange_calendar_authenticated",
                    "independent_archive_head_anchor_verified",
                    "corporate_action_and_adjusted_prices_verified",
                    "historic_issuer_master_and_delistings_verified",
                    "regular_session_only_volume_verified",
                    "historical_250_sessions_verified",
                    "confirmed_scoring_allowed", "confirmed_buy"):
            self.assertFalse(result[key], key)
        rendered = json.dumps(result)
        for sensitive in ("005930", "247540", "00088K", "00088L",
                          "107", "TEST_SECRET", str(self.path)):
            self.assertNotIn(sensitive, rendered)

    def test_hash_valid_wrong_date_source_fails_semantic_check(self):
        self.record(kpi=raw(row("005930", DAY1)))
        with self.assertRaisesRegex(ArchiveReauditError,
                                    "archive_market_date_or_ohlcv_rejected"):
            self.check()

    def test_hash_valid_invalid_code_or_bad_price_fails(self):
        for bad in (row("BAD?", DAY0),
                    row("005930", DAY0, low="150")):
            with self.subTest(bad=bad["ISU_CD"]):
                with tempfile.TemporaryDirectory() as location:
                    db = Path(location) / "safe" / "stock.sqlite3"
                    store_pair(database_path=db, asof=DAY0,
                               kospi_raw=raw(bad),
                               kosdaq_raw=raw(row("247540", DAY0)),
                               kospi_received_at_utc=DAY0 + "T07:30:00Z",
                               kosdaq_received_at_utc=DAY0 + "T07:30:00Z")
                    with self.assertRaisesRegex(
                            ArchiveReauditError,
                            "archive_market_date_or_ohlcv_rejected"):
                        self.check(private_db=db)

    def test_cross_market_ticker_collision_fails(self):
        # Different raw hashes so the storage layer accepts the pair, but
        # the semantic re-audit still detects one ticker in both markets.
        self.record(kpi=raw(row("005930", DAY0)),
                    kdq=raw(row("005930", DAY0, opened="101", high="113")))
        with self.assertRaisesRegex(ArchiveReauditError,
                                    "cross_market_duplicate_short_code"):
            self.check()

    def test_changed_raw_after_insert_detected_before_semantic_audit(self):
        self.record()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE source_pairs SET kosdaq_raw=? WHERE asof=?",
                (raw(row("999999", DAY0)), DAY0))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ArchiveReauditError,
                                    "archive_raw_digest_or_length_mismatch"):
            self.check()

    def test_xkrx_excluded_or_historical_gap_is_not_silently_skipped(self):
        self.record("2026-09-24",
                    kpi=raw(row("005930", "2026-09-24")),
                    kdq=raw(row("247540", "2026-09-24")))
        with self.assertRaisesRegex(ArchiveReauditError,
                                    "archive_not_candidate_prefix"):
            self.check(start="2026-09-23", end="2026-09-28",
                       calendar_source="xkrx")
        self.assertTrue(self.path.exists())

    def test_cli_never_discloses_malformed_data_or_paths(self):
        self.record(kpi=b"CONFIDENTIAL_RAW_MARKET_KEY_123456789")
        output = StringIO()
        with redirect_stdout(output):
            rc = main(["--start", DAY0, "--end", DAY1,
                       "--private-db", str(self.path),
                       "--calendar-source", "weekdays"])
        self.assertEqual(rc, 1)
        printed = output.getvalue()
        self.assertIn('"status": "blocked"', printed)
        self.assertNotIn("CONFIDENTIAL_RAW", printed)
        self.assertNotIn(str(self.path), printed)
        self.assertNotIn("005930", printed)

    def test_bad_explicit_path_or_calendar_fails_closed(self):
        for path in (None, "", "/bad.extension", Path(__file__).resolve().parents[1]
                     / "THIS_IS_NOT_A_PRIVATE_ARCHIVE.sqlite3"):
            with self.subTest(path=str(path)):
                with self.assertRaises(ArchiveReauditError):
                    self.check(private_db=path)
        self.assertFalse(self.path.exists())

    def test_existing_semantically_invalid_archive_blocks_next_live_call(self):
        self.record(kpi=raw(row("005930", DAY1)))
        fake = CallRecorder()
        with self.assertRaisesRegex(LocalBatchError,
                                    "existing_private_archive_semantic_reaudit_failed"):
            run_one_local_batch(start=DAY0, end=DAY1,
                                database_path=self.path, calendar_source="weekdays",
                                live=True, consent=True, key="SECRET", client=fake)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.path.exists())

    def test_existing_valid_archive_allows_only_next_missing_pair(self):
        self.record(kpi=raw(row("005930", DAY0)))
        fake = CallRecorder()
        outcome = run_one_local_batch(
            start=DAY0, end=DAY1, database_path=self.path,
            calendar_source="weekdays",
            live=True, consent=True, key="SYNTHETIC", client=fake)
        self.assertEqual(fake.calls, [("KOSPI", DAY1), ("KOSDAQ", DAY1)])
        self.assertEqual(outcome["total_archive_paired_days_integrity_checked"], 2)
        self.assertFalse(outcome["v3_release_ready"])


if __name__ == "__main__":
    unittest.main()
