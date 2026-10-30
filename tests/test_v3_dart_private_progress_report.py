"""Synthetic-only public aggregate progress; never publish private DART rows."""
from hashlib import sha256
import json
from pathlib import Path
import unittest

import v3_dart_private_progress_report as p
from v3_dart_historical_event_receipts import (
    CATEGORIES, CHECKPOINT, MONTHS, _empty, _path,
    parse_official_response,
)


class FakePrivate:
    repository = p.PRIVATE_REPO

    def __init__(self):
        self.files = {}
        self.verify_calls = 0
        self.source_reads = 0
        self.change_checkpoint_on_second_read = False

    def verify_private(self):
        self.verify_calls += 1

    def get_content(self, path):
        blob = self.files.get(path)
        if path == CHECKPOINT:
            self.source_reads += 1
            if self.change_checkpoint_on_second_read and self.source_reads == 2:
                blob = b"changed-private-cursor"
        return blob, sha256(blob).hexdigest() if blob is not None else None


def fixture():
    storage = FakePrivate()
    end_month = MONTHS[0]
    tail_path = _path(end_month, CATEGORIES[-1], 1)
    tail = parse_official_response({"status": "013"}, end_month, CATEGORIES[-1], 1)
    cursor = _empty()
    cursor.update({
        "month_index": 1, "category_index": 0, "page": 1,
        "saved_pages": 4, "saved_receipts": 1,
        "last_page_file": tail_path, "last_page_sha256": sha256(tail).hexdigest(),
    })
    storage.files[CHECKPOINT] = json.dumps(cursor).encode("utf-8")
    storage.files[tail_path] = tail
    return storage


def fixture_mid_category():
    storage = FakePrivate()
    month = MONTHS[0]
    tail_path = _path(month, CATEGORIES[0], 1)
    entries = [{
        "rcept_no": f"{i:014d}", "corp_code": "00123456",
        "stock_code": "005930", "rcept_dt": "20161017",
        "report_nm": "synthetic private filing",
    } for i in range(1, 101)]
    tail = parse_official_response({
        "status": "000", "total_count": "201", "total_page": "3",
        "page_no": "1", "page_count": "100", "list": entries,
    }, month, CATEGORIES[0], 1)
    cursor = _empty()
    cursor.update({
        "month_index": 0, "category_index": 0, "page": 2,
        "saved_pages": 1, "saved_receipts": 100,
        "last_page_file": tail_path, "last_page_sha256": sha256(tail).hexdigest(),
    })
    storage.files[CHECKPOINT] = json.dumps(cursor).encode("utf-8")
    storage.files[tail_path] = tail
    return storage


class HistoricalReceiptProgressTests(unittest.TestCase):
    def test_verified_private_tail_yields_anonymous_no_provider_aggregate(self):
        storage = fixture()
        result = p.collect(storage)
        self.assertEqual(storage.verify_calls, 1)
        self.assertEqual(storage.source_reads, 2)
        self.assertEqual(result["filing_months_checkpointed"], 1)
        self.assertEqual(result["filing_months_total"], 120)
        self.assertEqual(result["filing_months_remaining"], 119)
        self.assertEqual(result["private_receipt_pages_checkpointed"], 4)
        self.assertEqual(result["private_filing_receipts_checkpointed"], 1)
        self.assertEqual(result["last_checkpointed_filing_month"], MONTHS[0])
        self.assertEqual(result["current_collection_filing_month"], MONTHS[1])
        self.assertEqual(result["filing_category_scope"], list(CATEGORIES))
        self.assertTrue(result["private_tail_digest_and_schema_verified"])
        self.assertTrue(result["private_checkpoint_stable_during_read"])
        self.assertFalse(result["full_historical_archive_independently_audited"])
        self.assertFalse(result["corporate_actions_or_effective_dates_certified"])
        self.assertFalse(result["historical_issuer_universe_pit_certified"])
        self.assertFalse(result["historical_adjusted_prices_certified"])
        self.assertFalse(result["v3_trade_scores_promoted"])
        self.assertFalse(result["public_issuer_receipts_or_source_raw_published"])
        self.assertEqual(result["provider_api_calls"], 0)
        rendered = json.dumps(result)
        for hidden in ("00123456", "005930", "20161017000001",
                       "derived/dart-filing-receipts/", "private_archive_token"):
            self.assertNotIn(hidden, rendered)

    def test_in_category_cursor_checks_immediately_previous_page(self):
        storage = fixture_mid_category()
        result = p.collect(storage)
        self.assertEqual(result["filing_months_checkpointed"], 0)
        self.assertEqual(result["private_receipt_pages_checkpointed"], 1)
        self.assertTrue(result["private_tail_digest_and_schema_verified"])

        cursor = json.loads(storage.files[CHECKPOINT])
        cursor["last_page_file"] = _path(MONTHS[0], CATEGORIES[1], 1)
        storage.files[CHECKPOINT] = json.dumps(cursor).encode()
        with self.assertRaises(p.DARTProgressError):
            p.collect(storage)

    def test_corrupt_missing_tail_and_impossible_counts_fail_closed(self):
        storage = fixture()
        last_path = json.loads(storage.files[CHECKPOINT])["last_page_file"]
        storage.files[last_path] = b"tampered"
        with self.assertRaisesRegex(
                p.DARTProgressError, "private_dart_progress_tail_changed"):
            p.collect(storage)
        storage = fixture()
        cursor = json.loads(storage.files[CHECKPOINT])
        cursor["saved_receipts"] = 9999
        storage.files[CHECKPOINT] = json.dumps(cursor).encode()
        with self.assertRaisesRegex(
                p.DARTProgressError, "inconsistent_private_dart_receipt_progress"):
            p.collect(storage)
        storage = fixture()
        storage.files.pop(CHECKPOINT)
        with self.assertRaisesRegex(
                p.DARTProgressError, "missing_private_dart_receipt_checkpoint"):
            p.collect(storage)

    def test_concurrent_checkpoint_change_must_not_be_published(self):
        storage = fixture()
        storage.change_checkpoint_on_second_read = True
        with self.assertRaisesRegex(
                p.DARTProgressError, "private_dart_progress_changed_during_read"):
            p.collect(storage)

    def test_cannot_read_public_repo_or_promote_from_empty_cursor(self):
        storage = fixture()
        storage.repository = "ykvic1/korean-stock-screener"
        with self.assertRaisesRegex(
                p.DARTProgressError, "untrusted_private_dart_progress_source"):
            p.collect(storage)
        storage = FakePrivate()
        storage.files[CHECKPOINT] = json.dumps(_empty()).encode()
        report = p.collect(storage)
        self.assertEqual(report["filing_months_checkpointed"], 0)
        self.assertEqual(report["private_receipt_pages_checkpointed"], 0)
        self.assertIsNone(report["last_checkpointed_filing_month"])
        self.assertFalse(report["private_tail_digest_and_schema_verified"])

    def test_workflow_is_aggregate_only_and_does_not_call_dart(self):
        base = Path(__file__).resolve().parents[1]
        wf = (base / ".github/workflows/v3-dart-public-receipt-progress.yml").read_text()
        self.assertIn("V3 Private Historical OpenDART Filing Receipts (Research Only)", wf)
        self.assertIn("workflow_run:", wf)
        self.assertIn("v3_dart_private_progress_report.py --mode write", wf)
        self.assertIn("v3_dart_private_progress_report.py --mode verify", wf)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN: ${{ secrets.V3_PRIVATE_ARCHIVE_TOKEN }}", wf)
        self.assertIn("git add docs/v3-dart-historical-receipt-progress.json", wf)
        self.assertIn("options: [verify, write]", wf)
        self.assertIn("default: verify", wf)
        self.assertIn("cancel-in-progress: false", wf)
        self.assertNotIn("DART_API_KEY:", wf)
        self.assertNotIn("KRX_API_KEY:", wf)
        self.assertNotIn("python v3_dart_historical_event_receipts.py", wf)
        self.assertIn("cron: '30 9 * * 1-5'",
            (base / ".github/workflows/full-market-batches.yml").read_text())


if __name__ == "__main__":
    unittest.main()
