"""Synthetic safety checks for post-120 fully-closed OpenDART receipt months."""
import base64
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from v3_dart_future_closed_months import (
    CHECKPOINT, PREFIX, collect_closed_months, due_months,
)
from v3_dart_historical_event_receipts import (
    DARTEventReceiptError, PRIVATE_REPO,
    CHECKPOINT as HISTORICAL_CHECKPOINT,
)


class FakePrivate:
    repository = PRIVATE_REPO

    def __init__(self):
        self.files = {}
        self.fail_checkpoint_once = False

    def verify_private(self):
        return True

    def get_content(self, path):
        blob = self.files.get(path)
        return blob, sha256(blob).hexdigest() if blob is not None else None

    def put_new(self, path, raw, message):
        if path in self.files:
            raise AssertionError("private immutable overwrite")
        if path == CHECKPOINT and self.fail_checkpoint_once:
            self.fail_checkpoint_once = False
            raise RuntimeError("interrupted before cursor save")
        self.files[path] = raw

    def transport(self, method, path, payload):
        self_key = path.removeprefix("contents/")
        if (method != "PUT" or self_key != CHECKPOINT
            or payload.get("sha") !=
                sha256(self.files[CHECKPOINT]).hexdigest()):
            raise AssertionError("not a private conditional checkpoint")
        self.files[CHECKPOINT] = base64.b64decode(
            payload["content"], validate=True)


def empty_response(month, category, page, allowed):
    if month not in allowed or page != 1:
        raise AssertionError("outside completed-month query contract")
    return {"status": "013"}


class FutureClosedMonthReceiptTests(unittest.TestCase):
    def test_verified_progress_completion_triggers_only_gated_closed_month_continuation(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / '.github/workflows/v3-dart-future-closed-months.yml').read_text()
        reporter = (root / '.github/workflows/v3-dart-public-receipt-progress.yml').read_text()
        self.assertIn('V3 Private OpenDART Historical Receipt Progress (Anonymous Public Aggregate)', source)
        self.assertIn('types: [completed]', source)
        self.assertIn('branches: [main]', source)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", source)
        self.assertIn('github.event.workflow_run.head_repository.full_name == github.repository', source)
        self.assertIn("if: ${{ steps.base.outputs.done == 'true' }}", source)
        self.assertIn('public_progress_complete(progress)', source)
        self.assertIn("cron: '37 0,6,12,18 * * *'", source)
        self.assertIn('group: v3-dart-private-post-120-closed-months', source)
        # Completion publication must not recursively listen to its consumer.
        self.assertNotIn('V3 Post-120 Private DART Closed-Month Receipt Continuation', reporter)

    def test_kst_month_rollover_excludes_partial_month(self):
        kst = ZoneInfo("Asia/Seoul")
        self.assertEqual(due_months(datetime(
            2026, 10, 31, 23, 59, tzinfo=kst)), ())
        self.assertEqual(due_months(datetime(
            2026, 11, 1, 0, 1, tzinfo=kst)), ("2026-10",))
        self.assertEqual(due_months(datetime(
            2027, 1, 1, 0, 1, tzinfo=kst)),
            ("2026-10", "2026-11", "2026-12"))
        with self.assertRaises(DARTEventReceiptError):
            due_months(datetime(2026, 11, 1))

    def test_october_archived_once_then_november_resumed_when_closed(self):
        store = FakePrivate()
        kst = ZoneInfo("Asia/Seoul")
        october = datetime(2026, 11, 4, tzinfo=kst)
        december = datetime(2026, 12, 1, tzinfo=kst)
        one = collect_closed_months(
            store, empty_response, now=october)
        self.assertEqual(one["closed_months_completed"], 1)
        self.assertEqual(one["source_provider_calls"], 4)
        self.assertEqual(one["new_pages"], 4)
        old_paths = set(store.files)
        two = collect_closed_months(
            store, empty_response, now=october)
        self.assertEqual(two["source_provider_calls"], 0)
        self.assertEqual(set(store.files), old_paths)
        three = collect_closed_months(
            store, empty_response, now=december)
        self.assertEqual(three["closed_months_completed"], 2)
        self.assertEqual(three["source_provider_calls"], 4)
        self.assertEqual(len([p for p in store.files if p.startswith(PREFIX)]), 9)
        self.assertNotIn(HISTORICAL_CHECKPOINT, store.files)
        self.assertFalse(three["correction_effective_dates_certified"])

    def test_orphan_page_is_reused_after_checkpoint_failure(self):
        store = FakePrivate()
        store.fail_checkpoint_once = True
        now = datetime(2026, 11, 4, tzinfo=ZoneInfo("Asia/Seoul"))
        with self.assertRaises(RuntimeError):
            collect_closed_months(store, empty_response, now=now)
        self.assertEqual(len(store.files), 1)
        replay = collect_closed_months(
            store, empty_response, now=now)
        self.assertEqual(replay["source_provider_calls"], 3)
        self.assertEqual(replay["reused_orphans"], 1)
        self.assertEqual(replay["closed_months_completed"], 1)

    def test_source_error_fails_closed_without_empty_receipt_inference(self):
        store = FakePrivate()
        def rejected(*args):
            return {"status": "020"}
        now = datetime(2026, 11, 4, tzinfo=ZoneInfo("Asia/Seoul"))
        with self.assertRaises(DARTEventReceiptError):
            collect_closed_months(store, rejected, now=now)
        self.assertEqual(store.files, {})

    def test_unsafe_request_ceiling_rejected(self):
        store = FakePrivate()
        now = datetime(2026, 11, 4, tzinfo=ZoneInfo("Asia/Seoul"))
        with self.assertRaises(DARTEventReceiptError):
            collect_closed_months(store, empty_response, now=now,
                                  max_calls=73)


if __name__ == "__main__":
    unittest.main()
