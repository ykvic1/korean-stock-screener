"""Official DART past filing metadata research: synthetic, no-network CI."""
import base64
from hashlib import sha256
import gzip
import json
import os
from unittest.mock import patch
from pathlib import Path
import unittest

from v3_dart_historical_event_receipts import (
    CATEGORIES, CHECKPOINT, DARTEventReceiptError, MONTHS, _decode, _path,
    parse_official_response, run, PRIVATE_REPO, MAX_API_CALLS,
    public_progress_complete, public_failure_code, main,
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
    def test_public_dart_failure_codes_are_fixed_and_never_leak_secrets(self):
        self.assertEqual(
            public_failure_code(DARTEventReceiptError("dart_source_transport_failed")),
            "dart_source_transport_failed")
        self.assertEqual(
            public_failure_code(DARTEventReceiptError("dart_provider_rejected_or_unavailable")),
            "dart_provider_rejected_or_unavailable")
        fallback="private_dart_collection_validation_or_transport_failed"
        for problem in (
            RuntimeError("private-token=SYNTHETIC_SECRET"),
            DARTEventReceiptError("provider response: SYNTHETIC_SECRET"),
            DARTEventReceiptError("dart_source_transport_failed\nSYNTHETIC_SECRET"),
            ValueError("url=https://example.invalid/SYNTHETIC_SECRET"),
        ):
            self.assertEqual(public_failure_code(problem),fallback)
        env={"V3_PRIVATE_ARCHIVE_REPO":PRIVATE_REPO,
             "V3_PRIVATE_ARCHIVE_TOKEN":"T"*25,
             "DART_API_KEY":"K"*40}
        with patch.dict(os.environ,env), \
             patch("v3_dart_historical_event_receipts.PrivateGitStorage"), \
             patch("v3_dart_historical_event_receipts.run",
                   side_effect=RuntimeError("private-token=SYNTHETIC_SECRET")):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(str(caught.exception),"blocked: "+fallback)
        self.assertNotIn("SYNTHETIC_SECRET",str(caught.exception))

    def test_direct_fastlane_completion_gate_never_promotes_scores(self):
        done = {
            "schema": "v3-public-dart-historical-receipt-progress-v1",
            "filing_months_total": 120, "filing_months_checkpointed": 120,
            "filing_months_remaining": 0, "filing_months_checkpointed_ratio": 1,
            "last_checkpointed_filing_month": "2026-09",
            "current_collection_filing_month": None,
            "filing_category_scope": ["B", "C", "E", "I"],
            "private_tail_digest_and_schema_verified": True,
            "private_checkpoint_stable_during_read": True,
            "provider_api_calls": 0,
            "public_issuer_receipts_or_source_raw_published": False,
            "v3_trade_scores_promoted": False,
            "full_historical_archive_independently_audited": False,
        }
        self.assertTrue(public_progress_complete(done))
        for key, invalid in [
            ("filing_months_checkpointed", 119),
            ("filing_months_remaining", 1),
            ("current_collection_filing_month", "2026-09"),
            ("last_checkpointed_filing_month", "2026-08"),
            ("filing_category_scope", ["B", "C"]),
            ("provider_api_calls", 1),
            ("private_tail_digest_and_schema_verified", False),
            ("private_checkpoint_stable_during_read", False),
            ("v3_trade_scores_promoted", True),
        ]:
            self.assertFalse(public_progress_complete({**done, key: invalid}),
                "malformed or incomplete public counters cannot stop retrieval")
        self.assertFalse(public_progress_complete(None))

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

    def test_seventytwo_page_capped_api_budget_and_exact_resume(self):
        """No pagination gaps or unbounded provider calls at the larger cap."""
        self.assertEqual(MAX_API_CALLS, 72)
        private = FakePrivate()
        requested = []
        total = (MAX_API_CALLS + 1) * 100
        def fetch(month, category, page):
            requested.append((month, category, page))
            self.assertEqual(month, "2016-10")
            self.assertEqual(category, "B")
            return response(
                page, total,
                [row(f"{(page-1)*100+i:014d}") for i in range(1, 101)])
        first = run(private, fetch, max_calls=MAX_API_CALLS)
        self.assertEqual(first["provider_calls"], MAX_API_CALLS)
        self.assertEqual(first["new_pages"], MAX_API_CALLS)
        self.assertEqual(first["cumulative_pages"], MAX_API_CALLS)
        self.assertEqual(first["cumulative_receipts"], MAX_API_CALLS*100)
        self.assertFalse(first["historical_adjusted_prices_certified"])
        self.assertFalse(first["public_issuer_data_published"])
        cursor = json.loads(private.files[CHECKPOINT])
        self.assertEqual(cursor["page"], MAX_API_CALLS + 1)
        self.assertEqual(cursor["category_index"], 0)
        self.assertEqual(len(requested), MAX_API_CALLS)
        self.assertEqual(len({p for _,_,p in requested}), MAX_API_CALLS)
        second = run(private, fetch, max_calls=1)
        self.assertEqual(second["provider_calls"], 1)
        self.assertEqual(requested[-1], ("2016-10", "B", MAX_API_CALLS+1))
        self.assertEqual(second["cumulative_receipts"], total)
        cursor = json.loads(private.files[CHECKPOINT])
        self.assertEqual(cursor["category_index"], 1)
        self.assertEqual(cursor["page"], 1)
        self.assertEqual(private.puts, 2 * (MAX_API_CALLS+1))
        with self.assertRaises(DARTEventReceiptError):
            run(private, fetch, max_calls=MAX_API_CALLS + 1)

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

    def test_adjacent_pages_reject_snapshot_drift_or_duplicate_without_orphan(self):
        """A valid single page cannot silently corrupt a multi-page result."""
        first_rows = [row(f"{i:014d}") for i in range(1, 101)]
        valid_second = [row(f"{i:014d}") for i in range(101, 201)]
        issues = {
            "changed_total": response(2, 202, valid_second),
            "overlapping_receipt": response(
                2, 201, [row(f"{i:014d}") for i in [100, *range(101, 200)]]),
        }
        for label, bad_second in issues.items():
            with self.subTest(problem=label):
                private = FakePrivate()
                run(private, lambda *_: response(1, 201, first_rows),
                    max_calls=1)
                with self.assertRaisesRegex(
                        DARTEventReceiptError, "inconsistent_dart_page_sequence"):
                    run(private, lambda *_: bad_second, max_calls=1)
                # The rejected response never enters immutable private storage;
                # after a corrected source response the original cursor resumes.
                self.assertNotIn(_path("2016-10", "B", 2), private.files)
                self.assertEqual(json.loads(private.files[CHECKPOINT])["page"], 2)
                healthy = run(
                    private, lambda *_: response(2, 201, valid_second),
                    max_calls=1)
                self.assertEqual(healthy["provider_calls"], 1)
                self.assertEqual(healthy["cumulative_pages"], 2)
                self.assertEqual(healthy["cumulative_receipts"], 200)
                self.assertFalse(healthy["historical_adjusted_prices_certified"])

    def test_mid_category_cursor_must_point_to_immediately_previous_page(self):
        private = FakePrivate()
        first = [row(f"{i:014d}") for i in range(1, 101)]
        run(private, lambda *_: response(1, 201, first), max_calls=1)
        cur = json.loads(private.files[CHECKPOINT])
        # An internally valid SHA and stored blob are insufficient when a
        # tampered cursor points to a different category's previous page.
        wrong_path = _path("2016-10", "C", 1)
        private.files[wrong_path] = private.files[cur["last_page_file"]]
        cur["last_page_file"] = wrong_path
        private.files[CHECKPOINT] = json.dumps(cur).encode("utf-8")
        fetches = []
        with self.assertRaisesRegex(
                DARTEventReceiptError, "private_dart_receipt_cursor_chain_gap"):
            run(private, lambda *args: fetches.append(args), max_calls=1)
        self.assertEqual(fetches, [])
        self.assertNotIn(_path("2016-10", "B", 2), private.files)

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
        # Code pushes still run offline checks but do not silently spend
        # another 72 provider calls beyond the twelve scheduled daily budgets.
        self.assertIn(
            "if: ${{ github.event_name == 'schedule' || "
            "(github.event_name == 'workflow_dispatch' && inputs.mode == 'live') }}",
            wf)
        self.assertNotIn("github.event_name == 'push' ||", wf)
        self.assertIn("DART_API_KEY: ${{ secrets.DART_API_KEY }}", wf)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN: ${{ secrets.V3_PRIVATE_ARCHIVE_TOKEN }}", wf)
        self.assertIn("v3_dart_historical_event_receipts.py", wf)
        self.assertIn("Install private Git storage import dependencies", wf)
        self.assertIn("requests>=2.31,<3", wf)
        self.assertIn("group: v3-dart-private-historical-event-receipts", wf)
        self.assertIn("max 72 official DART calls", wf)
        self.assertEqual(wf.count("    - cron:"), 12)
        for hour in range(0, 24, 2):
            self.assertIn("cron: '19 %d * * *'" % hour, wf)
        self.assertIn("timeout-minutes: 17", wf)
        self.assertIn("cancel-in-progress: false", wf)
        self.assertIn("contents: read", wf)
        self.assertNotIn("contents: write", wf)
        self.assertIn("persist-credentials: false", wf)
        self.assertNotIn("git push", wf)
        direct = (base / ".github/workflows/v3-dart-historical-direct-once.yml").read_text()
        self.assertIn("paths:", direct)
        self.assertIn("v3-dart-historical-direct-once.yml", direct)
        self.assertIn("startsWith(github.event.head_commit.message", direct)
        self.assertIn("group: v3-dart-private-historical-event-receipts", direct)
        self.assertIn("max 72 calls", direct)
        self.assertIn("python v3_dart_historical_event_receipts.py", direct)
        self.assertNotIn("schedule:", direct)
        fast = (base / ".github/workflows/v3-dart-historical-direct-fastlane.yml").read_text()
        self.assertIn("cron: '31 * * * *'", fast)
        self.assertIn("default: dry_run", fast)
        self.assertIn("startsWith(github.event.head_commit.message", fast)
        self.assertIn("group: v3-dart-private-historical-event-receipts", fast)
        self.assertIn("python v3_dart_continuous_catchup.py", fast)
        self.assertIn("test_v3_dart_continuous_catchup.py", fast)
        self.assertIn("Each segment is limited to 72", fast)
        self.assertIn("timeout-minutes: 350", fast)
        self.assertIn("steps.completion.outputs.done == 'false'", fast)
        self.assertIn("public_progress_complete", fast)
        self.assertIn("contents: read", fast)
        self.assertIn("persist-credentials: false", fast)
        self.assertNotIn("contents: write", fast)
        self.assertNotIn("git push", fast)
        self.assertNotIn("upload-artifact", fast)
        self.assertIn("cron: '30 9 * * 1-5'",
                      (base / ".github/workflows/full-market-batches.yml").read_text())


if __name__ == "__main__":
    unittest.main()
