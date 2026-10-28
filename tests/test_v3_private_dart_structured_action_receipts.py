"""Exact-match OpenDART JSON categories, private checkpoint and fail-closed tests."""
import json
import unittest
from unittest.mock import patch

import v3_private_dart_structured_action_receipts as a
from test_v3_krx_derived_5day import FakePrivate


RECORD = {
    "rcept_no": "20161101000001", "corp_code": "00121932",
    "title_hints": ["합병"],
}
QUEUE_HASH = "b" * 64


def official_rows(endpoint, *, exact=False):
    return {
        "status": "000",
        "list": [{"rcept_no": RECORD["rcept_no"] if exact else "20161101000002",
                  "corp_code": RECORD["corp_code"]}],
    }


class StructuredActionTests(unittest.TestCase):
    def setUp(self):
        self.archive = FakePrivate()
        self.archive.blobs[a.TRIAGE_CHECKPOINT] = b"private triage cursor"
        self.queue_path = a.triage_path(0)
        self.archive.blobs[self.queue_path] = b"private title candidates"

    def process(self, fetch, *, archive=None):
        with patch.object(a, "triage_cursor", return_value={"next_pair_index": 1}):
            with patch.object(a, "_candidate_queue",
                              return_value=([RECORD], QUEUE_HASH, 1)):
                return a.run(archive or self.archive, fetch)

    def test_three_explicit_official_endpoints_and_exact_receipt_only(self):
        calls = []
        def fetch(endpoint, corp, start, end):
            calls.append((endpoint, corp, start, end))
            return official_rows(endpoint, exact=(endpoint == "cmpMgDecsn"))
        out = self.process(fetch)
        self.assertEqual(out["provider_calls"], 3)
        self.assertEqual(out["exact_receipt_categories_matched"], 1)
        self.assertEqual([x[0] for x in calls], list(a.ENDPOINTS))
        self.assertTrue(all(x[1] == RECORD["corp_code"] for x in calls))
        self.assertTrue(all(x[2] == "20161001" and x[3] == "20161130"
                            for x in calls))
        path = a._path(0, RECORD["rcept_no"])
        report = a._validate_report(self.archive.blobs[path], 0, RECORD)
        self.assertEqual(report["exact_receipt_event_categories"], ["cmpMgDecsn"])
        self.assertTrue(report["official_structured_category_match"])
        self.assertFalse(report["effective_date_authenticated"])
        self.assertFalse(report["share_adjustment_authenticated"])
        self.assertFalse(report["scores_authorized"])
        self.assertNotIn("raw_source_base64", str(report))
        self.assertEqual(out["scores_promoted"], 0)

    def test_other_filing_receipts_are_not_evidence_of_event(self):
        out = self.process(lambda ep, corp, start, end: official_rows(ep))
        self.assertEqual(out["exact_receipt_categories_matched"], 0)
        self.assertEqual(out["provider_calls"], 3)
        data = a._validate_report(self.archive.blobs[a._path(0, RECORD["rcept_no"])],
                                  0, RECORD)
        self.assertFalse(data["official_structured_category_match"])
        self.assertFalse(data["complete_corporate_action_universe_checked"])

    def test_provider_no_data_per_category_is_not_global_negative(self):
        result = a.inspect_response(
            {"status": "013", "message": "No data"},
            "cmpMgDecsn", RECORD["corp_code"], RECORD["rcept_no"])
        self.assertEqual(result["provider_status"], "013")
        self.assertFalse(result["exact_receipt_match"])

    def test_auth_quota_and_maintenance_abort_not_negative_evidence(self):
        for status in ("010", "011", "020", "800", "901"):
            with self.assertRaisesRegex(a.StructuredActionError,
                                        "structured_action_provider_rejected"):
                a.inspect_response({"status": status}, "cmpMgDecsn",
                                   RECORD["corp_code"], RECORD["rcept_no"])
        self.assertEqual(self.archive.posts, 0)

    def test_invalid_source_identity_aborts_before_private_commit(self):
        result = {"status": "000", "list": [{
            "rcept_no": RECORD["rcept_no"], "corp_code": "00000001"}]}
        with self.assertRaisesRegex(a.StructuredActionError,
                                    "invalid_structured_action_row_identity"):
            self.process(lambda *args: result)
        self.assertEqual(self.archive.posts, 0)

    def test_second_run_reuses_immutable_report_no_extra_dart_calls(self):
        self.process(lambda ep, corp, start, end: official_rows(ep, exact=True))
        report_path = a._path(0, RECORD["rcept_no"])
        self.assertIn(report_path, self.archive.blobs)
        calls = []
        result = self.process(lambda *args: calls.append(args))
        # Cursor is already at next pair, so the job is safely waiting.
        self.assertEqual(result["status"], "waiting_for_private_title_shortlist")
        self.assertEqual(calls, [])

    def test_invalid_existing_report_fails_without_fetch(self):
        self.archive.blobs[a._path(0, RECORD["rcept_no"])] = b"invalid"
        with self.assertRaisesRegex(a.StructuredActionError,
                                    "invalid_private_structured_action_report"):
            self.process(lambda *args: self.fail("should not call DART"))
        self.assertEqual(self.archive.posts, 0)

    def test_wrong_repo_never_accesses_source_or_credentials(self):
        self.archive.repository = "ykvic1/korean-stock-screener"
        with self.assertRaisesRegex(a.StructuredActionError,
                                    "invalid_private_structured_action_destination"):
            self.process(lambda *args: self.fail("provider should not be called"))

    def test_report_tamper_score_or_adjustment_fails_closed(self):
        details = [{"endpoint": name, "provider_status": "013",
                    "list_rows": 0, "exact_receipt_match": False}
                   for name in a.ENDPOINTS]
        report = a._report(0, RECORD, details)
        report["scores_authorized"] = True
        with self.assertRaises(a.StructuredActionError):
            a._validate_report(a.canonical(report), 0, RECORD)
        report["scores_authorized"] = False
        report["share_adjustment_authenticated"] = True
        with self.assertRaises(a.StructuredActionError):
            a._validate_report(a.canonical(report), 0, RECORD)

    def test_checkpoint_pointer_must_follow_real_receipt_order(self):
        stale = a._path(0, "20161101000009")
        content = b"verified-but-wrong-cursor-tail"
        state = {**a._empty(), "next_pair_index": 0,
                 "next_candidate_index": 1, "queue_sha256": QUEUE_HASH,
                 "last_report_file": stale,
                 "last_report_sha256": a.sha256(content).hexdigest()}
        self.archive.blobs[a.CHECKPOINT] = a.canonical(state)
        self.archive.blobs[stale] = content
        with self.assertRaisesRegex(
                a.StructuredActionError, "checkpoint_queue_tail_mismatch"):
            self.process(lambda *args: self.fail("no provider on skipped receipt"))
        self.assertEqual(self.archive.posts, 0)

        for forged in (
            {**state, "last_report_file": a._path(1, RECORD["rcept_no"])},
            {**state, "last_report_file":
             a.BASE + "/reports/201610--201611/../20161101000001.json"},
            {**state, "last_report_file": None, "last_report_sha256": None},
            {**state, "next_pair_index": 1, "next_candidate_index": 0,
             "queue_sha256": None,
             "last_report_file": a._path(1, RECORD["rcept_no"])},
        ):
            with self.assertRaisesRegex(
                    a.StructuredActionError, "invalid_private_structured_action_checkpoint"):
                a._cursor(a.canonical(forged))
        # A genuinely empty later month may legitimately retain an older tail.
        old = {**state, "next_pair_index": 2, "next_candidate_index": 0,
               "queue_sha256": None, "last_report_file": a._path(0, RECORD["rcept_no"])}
        self.assertEqual(a._cursor(a.canonical(old)), old)

    def test_request_allowlist_and_date_ranges(self):
        a._check_request("fricDecsn", RECORD["corp_code"], "20161001", "20161130")
        for args in (("document.xml", RECORD["corp_code"], "20161001", "20161130"),
                     ("cmpMgDecsn", "corp code", "20161001", "20161130"),
                     ("fricDecsn", RECORD["corp_code"], "20161201", "20161130")):
            with self.assertRaises(a.StructuredActionError):
                a._check_request(*args)

    def test_earlier_partial_zip_failure_does_not_gate_json_endpoint(self):
        # No original-ZIP checkpoint, ZIP bytes, filenames or fetches are used.
        self.process(lambda ep, corp, start, end: {"status": "013"})
        self.assertFalse(any("original-evidence" in p for p in self.archive.blobs))
        self.assertIn(a.CHECKPOINT, self.archive.blobs)


if __name__ == "__main__":
    unittest.main()
