"""Official DART past filing metadata research: synthetic, no-network CI."""
import base64
from hashlib import sha256
import gzip
import json
from pathlib import Path
import unittest

from v3_dart_historical_event_receipts import (
    CATEGORIES, CHECKPOINT, DARTEventReceiptError, MONTHS, _decode, _path,
    parse_official_response, run, PRIVATE_REPO, MAX_API_CALLS,
)


class FakePrivate:
    repository = PRIVATE_REPO

    def __init__(self):
        self.files = {}
        self.puts = 0
        self.fail_once_on_checkpoint = False

    def verify_private(self):
        return True

    def get_content(self, path):
        value = self.files.get(path)
        return value, sha256(value).hexdigest() if value is not None else None

    def put_new(self, path, raw, message):
        if path in self.files:
            raise AssertionError("immutable private file overwritten")
        if self.fail_once_on_checkpoint and path == CHECKPOINT:
            self.fail_once_on_checkpoint = False
            raise RuntimeError("synthetic GitHub checkpoint interruption")
        self.files[path] = raw
        self.puts += 1

    def transport(self, method, path, payload):
        if method != "PUT" or not path.startswith("contents/"):
            raise AssertionError("unexpected private mutation")
        key = path[len("contents/"):]
        if (key != CHECKPOINT or key not in self.files
            or payload.get("sha") != sha256(self.files[key]).hexdigest()):
            raise AssertionError("not a conditional checkpoint update")
        self.files[key] = base64.b64decode(payload["content"], validate=True)
        self.puts += 1


def row(receipt, dt="20161017"):
    return {"rcept_no": receipt, "corp_code": "00123456",
            "stock_code": "005930", "rcept_dt": dt,
            "report_nm": "주요사항보고서(합병 결정)"}


def response(page, total, rows):
    return {"status": "000", "total_count": str(total),
            "total_page": str((total + 99) // 100), "page_no": str(page),
            "page_count": "100", "list": rows}


class HistoricalDARTReceiptTests(unittest.TestCase):
    def test_paginated_official_source_and_private_only_resumable_archive(self):
        self.assertEqual(MONTHS[0], "2016-10")
        self.assertEqual(MONTHS[-1], "2026-09")
        self.assertEqual(CATEGORIES, ("B", "C", "E", "I"))
        private = FakePrivate()
        requests = []
        def fetch(month, category, page):
            requests.append((month, category, page))
            if (month, category, page) == ("2016-10", "B", 1):
                return response(1, 102,
                                [row(f"{i:014d}") for i in range(1, 101)])
            if (month, category, page) == ("2016-10", "B", 2):
                return response(2, 102, [row(f"{i:014d}")
                                          for i in (101, 102)])
            return {"status": "013"}
        result = run(private, fetch, max_calls=3)
        self.assertEqual(result["provider_calls"], 3)
        self.assertEqual(result["cumulative_pages"], 3)
        self.assertEqual(result["cumulative_receipts"], 102)
        self.assertEqual(result["months_completed"], 0)
        self.assertFalse(result["historical_adjusted_prices_certified"])
        self.assertFalse(result["public_issuer_data_published"])
        self.assertEqual(requests, [("2016-10", "B", 1),
                                     ("2016-10", "B", 2),
                                     ("2016-10", "C", 1)])
        self.assertFalse(any("docs/" in p for p in private.files))
        body = _decode(private.files[_path("2016-10", "B", 2)],
                       "2016-10", "B", 2)
        self.assertEqual(len(body["receipts"]), 2)
        self.assertFalse(body["split_or_merger_verified"])
        puts = private.puts
        again = run(private, fetch, max_calls=2)
        self.assertEqual(again["provider_calls"], 2)
        self.assertEqual(again["months_completed"], 1)
        self.assertEqual(private.puts, puts + 4)

    def test_failed_checkpoint_resumes_private_immutable_orphan_without_redownload(self):
        private = FakePrivate()
        private.fail_once_on_checkpoint = True
        count = []
        def fetch(month, category, page):
            count.append((month, category, page))
            return response(1, 1, [row("20261017000001")])
        with self.assertRaises(RuntimeError):
            run(private, fetch, max_calls=1)
        self.assertIn(_path("2016-10", "B", 1), private.files)
        self.assertNotIn(CHECKPOINT, private.files)
        result = run(private, fetch, max_calls=1)
        self.assertEqual(result["provider_calls"], 1)  # next category C only
        self.assertEqual(result["previously_saved_pages_reused"], 1)
        self.assertEqual(count, [("2016-10", "B", 1),
                                 ("2016-10", "C", 1)])

    def test_fails_closed_on_status_quota_duplicate_dates_tampered_counts(self):
        failures = [
            response(1, 2, [row("1" * 14), row("1" * 14)]),
            response(1, 1, [row("1" * 14, dt="20161101")]),
            response(1, 1, [row("1" * 14, dt="20161099")]),
            response(1, 1, [row("1" * 14, dt="20161017")]),
        ]
        # Last response above is valid: the first three must be rejected.
        for sample in failures[:3]:
            with self.subTest(sample=sample["list"][:1]):
                with self.assertRaises(DARTEventReceiptError):
                    parse_official_response(sample, "2016-10", "B", 1)
        valid = parse_official_response(failures[3], "2016-10", "B", 1)
        self.assertEqual(_decode(valid, "2016-10", "B", 1)["total_count"], 1)
        for status in ("020", "010", "800", "100", "900"):
            with self.subTest(status=status):
                with self.assertRaises(DARTEventReceiptError):
                    parse_official_response({"status": status},
                                            "2016-10", "B", 1)
        with self.assertRaises(DARTEventReceiptError):
            parse_official_response(response(2, 102, [row("2" * 14)]),
                                    "2016-10", "B", 1)
        with self.assertRaises(DARTEventReceiptError):
            parse_official_response({"status": "013"}, "2016-10", "B", 2)

    def test_private_tail_and_wrong_destination_never_fall_back_public(self):
        private = FakePrivate()
        run(private, lambda *_: {"status": "013"}, max_calls=1)
        cursor = json.loads(private.files[CHECKPOINT])
        private.files[cursor["last_page_file"]] = b"tampered"
        with self.assertRaises(DARTEventReceiptError):
            run(private, lambda *_: {"status": "013"}, max_calls=1)
        other = FakePrivate()
        other.repository = "ykvic1/korean-stock-screener"
        with self.assertRaises(DARTEventReceiptError):
            run(other, lambda *_: {"status": "013"}, max_calls=1)
        with self.assertRaises(DARTEventReceiptError):
            run(FakePrivate(), lambda *_: {"status": "013"},
                max_calls=MAX_API_CALLS + 1)

    def test_approved_workflow_keeps_legacy_market_cron_and_private_only_paths(self):
        base = Path(__file__).resolve().parents[1]
        wf = (base / ".github/workflows/v3-dart-historical-event-receipts.yml").read_text()
        self.assertIn("workflow_dispatch:", wf)
        self.assertIn("default: dry_run", wf)
        self.assertIn("DART_API_KEY: ${{ secrets.DART_API_KEY }}", wf)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN: ${{ secrets.V3_PRIVATE_ARCHIVE_TOKEN }}", wf)
        self.assertIn("v3_dart_historical_event_receipts.py", wf)
        self.assertIn("Install private Git storage import dependencies", wf)
        self.assertIn("requests>=2.31,<3", wf)
        self.assertIn("group: v3-dart-private-historical-event-receipts", wf)
        self.assertIn("cancel-in-progress: false", wf)
        self.assertIn("contents: read", wf)
        self.assertNotIn("contents: write", wf)
        self.assertIn("persist-credentials: false", wf)
        self.assertNotIn("git push", wf)
        self.assertIn("cron: '30 9 * * 1-5'",
                      (base / ".github/workflows/full-market-batches.yml").read_text())


if __name__ == "__main__":
    unittest.main()
