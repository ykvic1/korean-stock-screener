"""Synthetic, offline full-archive checks; never request or print provider data."""
import copy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import v3_dart_historical_event_receipts as receipts
import v3_dart_receipt_archive_audit as audit


def fixture(root):
    for month in receipts.MONTHS:
        for category in receipts.CATEGORIES:
            folder = root / month / category
            folder.mkdir(parents=True)
            raw = receipts.parse_official_response({"status": "013"}, month, category, 1)
            (folder / "0001.json.gz").write_bytes(raw)
    cursor = {**receipts._empty(), "month_index": len(receipts.MONTHS),
              "saved_pages": len(receipts.MONTHS) * len(receipts.CATEGORIES),
              "last_page_file": receipts._path(receipts.MONTHS[-1], receipts.CATEGORIES[-1], 1),
              "last_page_sha256": sha256(raw).hexdigest()}
    (root / "checkpoint.json").write_bytes(receipts._bytes(cursor))
    return cursor


def page(root, month, receipt):
    raw = receipts.parse_official_response({
        "status": "000", "total_count": "1", "total_page": "1", "page_no": "1", "page_count": "100",
        "list": [{"rcept_no": receipt, "corp_code": "00123456", "stock_code": "0013V0",
                  "rcept_dt": month.replace("-", "") + "01", "report_nm": "PRIVATE_SOURCE_TITLE"}],
    }, month, "B", 1)
    (root / month / "B" / "0001.json.gz").write_bytes(raw)


class ReceiptArchiveAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private-receipts"
        self.cursor = fixture(self.root)

    def write_cursor(self):
        raw = receipts._bytes(self.cursor)
        (self.root / "checkpoint.json").write_bytes(raw)
        return raw

    def test_full_120_month_archive_is_read_only_anonymous_and_not_certified(self):
        page(self.root, "2016-10", "20161001000001")
        self.cursor["saved_receipts"] = 1
        raw = self.write_cursor()
        before = {p.relative_to(self.root): sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        report = audit.audit(self.root)
        self.assertEqual(report["months_scanned"], 120)
        self.assertEqual(report["pages_scanned"], 480)
        self.assertEqual(report["unique_receipts"], 1)
        self.assertTrue(audit.audit_matches_checkpoint(report, raw))
        self.assertTrue(all(report[key] is False for key in audit.FALSE_FIELDS))
        self.assertEqual(report["provider_api_calls"], 0)
        self.assertNotIn("20161001000001", json.dumps(report))
        self.assertNotIn("PRIVATE_SOURCE_TITLE", json.dumps(report))
        self.assertNotIn("00123456", json.dumps(report))
        after = {p.relative_to(self.root): sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_partial_cursor_cannot_pass_full_archive_gate(self):
        self.cursor["month_index"] = 119
        self.write_cursor()
        with self.assertRaisesRegex(audit.ReceiptArchiveAuditError, "not_complete"):
            audit.audit(self.root)

    def test_missing_page_or_unexpected_file_cannot_be_silently_skipped(self):
        target = self.root / "2016-10" / "B" / "0001.json.gz"
        saved = target.read_bytes()
        target.unlink()
        with self.assertRaises(audit.ReceiptArchiveAuditError):
            audit.audit(self.root)
        target.write_bytes(saved)
        (target.parent / "0002.json.gz").write_bytes(saved)
        with self.assertRaises(audit.ReceiptArchiveAuditError):
            audit.audit(self.root)

    def test_month_and_category_inventory_are_exact(self):
        (self.root / "2016-10" / "unexpected-category").mkdir()
        with self.assertRaises(audit.ReceiptArchiveAuditError):
            audit.audit(self.root)

    def test_multiple_pages_are_counted_and_missing_later_page_is_rejected(self):
        folder = self.root / "2016-10" / "B"
        for number, count in ((1, 100), (2, 1)):
            rows = [{"rcept_no": f"20161001{(number - 1) * 100 + index:06d}",
                     "corp_code": "00123456", "stock_code": "0013V0",
                     "rcept_dt": "20161001", "report_nm": "PRIVATE_SOURCE_TITLE"}
                    for index in range(count)]
            raw = receipts.parse_official_response({
                "status": "000", "total_count": "101", "total_page": "2",
                "page_no": str(number), "page_count": "100", "list": rows,
            }, "2016-10", "B", number)
            (folder / f"{number:04d}.json.gz").write_bytes(raw)
        self.cursor.update(saved_pages=481, saved_receipts=101)
        self.write_cursor()
        report = audit.audit(self.root)
        self.assertEqual((report["pages_scanned"], report["unique_receipts"]), (481, 101))
        (folder / "0002.json.gz").unlink()
        with self.assertRaisesRegex(audit.ReceiptArchiveAuditError, "inventory_invalid"):
            audit.audit(self.root)

    def test_duplicate_receipt_across_different_months_fails_closed(self):
        page(self.root, "2016-10", "20161001000001")
        page(self.root, "2016-11", "20161001000001")
        self.cursor["saved_receipts"] = 2
        self.write_cursor()
        with self.assertRaisesRegex(audit.ReceiptArchiveAuditError, "duplicate_receipt"):
            audit.audit(self.root)

    def test_changed_tail_or_cursor_total_is_rejected(self):
        for field, value, message in (
            ("saved_pages", 481, "totals_mismatch"),
            ("saved_receipts", 1, "totals_mismatch"),
            ("last_page_sha256", "0" * 64, "tail_mismatch"),
        ):
            original = self.cursor[field]
            self.cursor[field] = value
            self.write_cursor()
            with self.subTest(field=field), self.assertRaisesRegex(audit.ReceiptArchiveAuditError, message):
                audit.audit(self.root)
            self.cursor[field] = original

    def test_symlink_source_cannot_escape_the_private_snapshot(self):
        target = self.root / "2016-10" / "B" / "0001.json.gz"
        outside = Path(self.temp.name) / "outside.gz"
        target.rename(outside)
        target.symlink_to(outside)
        with self.assertRaises(audit.ReceiptArchiveAuditError):
            audit.audit(self.root)

    def test_report_cannot_overwrite_private_input(self):
        target = self.root / "checkpoint.json"
        before = target.read_bytes()
        with self.assertRaises(audit.ReceiptArchiveAuditError):
            audit.main(["--private-root", str(self.root), "--output", str(target)])
        self.assertEqual(target.read_bytes(), before)

    def test_cached_audit_requires_exact_source_counts_and_false_authentication(self):
        raw = self.write_cursor()
        report = audit.audit(self.root)
        for key, value in (("v3_trade_scores_promoted", True), ("unique_receipts", 10),
                           ("provider_api_calls", 1), ("source_checkpoint_sha256", "0" * 64),
                           ("archive_structure_sha256", "not-a-digest"), ("source_text", "private")):
            bad = copy.deepcopy(report)
            bad[key] = value
            self.assertFalse(audit.audit_matches_checkpoint(bad, raw), key)
        self.assertFalse(audit.audit_matches_checkpoint(report, b"invalid"))


class ArchiveAuditWorkflowTests(unittest.TestCase):
    def test_snapshot_job_is_separate_pinned_and_does_not_publish_private_files(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/v3-dart-receipt-archive-audit.yml").read_text()
        offline, live = workflow.split("  private-snapshot-audit:", 1)
        self.assertNotIn("concurrency:", offline)
        self.assertNotIn("secrets.", offline)
        self.assertNotIn("v3-dart-private-historical-event-receipts", live)
        self.assertIn("public_progress_complete(progress)", live)
        self.assertIn("completed_cursor(raw)", live)
        self.assertIn("head_repository.full_name == github.repository", live)
        self.assertIn("audit_matches_checkpoint", live)
        self.assertIn("os.environ.get('FORCE_REAUDIT') != 'true'", live)
        self.assertIn("ref: ${{ steps.snapshot.outputs.commit }}", live)
        self.assertIn("sparse-checkout: /derived/dart-filing-receipts/", live)
        self.assertIn("persist-credentials: false", live)
        self.assertIn("shutil.rmtree(path)", live)
        self.assertIn("git add docs/v3-dart-receipt-archive-audit.json", live)
        self.assertNotIn("git add .", live)
        self.assertNotIn("upload-artifact", workflow)

    def test_embedded_python_compiles_and_step_outputs_have_real_newlines(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/v3-dart-receipt-archive-audit.yml").read_text()
        blocks = workflow.split("python - <<'PY'\n")[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            body = block.split("          PY", 1)[0]
            compile("\n".join(line[10:] for line in body.splitlines()), "workflow", "exec")
        self.assertIn("value + '\\n')", workflow)
        self.assertNotIn("value + '\\\\n')", workflow)


if __name__ == "__main__":
    unittest.main()
