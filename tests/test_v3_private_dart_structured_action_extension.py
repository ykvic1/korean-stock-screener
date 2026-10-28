"""Offline supplemental official DART category evidence without trading claims."""
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_private_dart_structured_action_extension as s
from test_v3_krx_derived_5day import FakePrivate


class Private(FakePrivate):
    def transport(self, method, path, payload=None):
        if method != "PUT" or not path.startswith("contents/"):
            raise AssertionError("unexpected transport")
        location = path.removeprefix("contents/")
        if location not in self.blobs or payload.get("sha") != "sha":
            raise AssertionError("non-conditional checkpoint update")
        self.blobs[location] = base64.b64decode(payload["content"], validate=True)
        self.posts += 1


def record(receipt, hints):
    return {"rcept_no": receipt, "corp_code": "00121932", "title_hints": hints}


RECORDS = [
    record("20161031000001", ["rights_issue", "bonus_issue", "capital_reduction",
                                 "merger_split", "share_exchange"]),
    record("20161103000002", ["stock_split_consolidation", "stock_dividend"]),
]


class SupplementalActionTests(unittest.TestCase):
    def setUp(self):
        self.store = Private()
        self.store.blobs[s.TRIAGE_CHECKPOINT] = b"private-triage-cursor"
        self.store.blobs[s.triage_path(0)] = b"private verified queue"

    def process(self, fetch, *, limit=2):
        with (patch.object(s, "triage_cursor", return_value={"next_pair_index": 1}),
              patch.object(s, "_candidate_queue", return_value=(RECORDS, "a"*64, 2))):
            return s.run(self.store, fetch, limit=limit)

    def test_five_official_supplements_queried_only_for_supported_hints(self):
        calls = []
        def provider(ep, corp, begin, end):
            calls.append((ep, corp, begin, end))
            return {"status": "000", "list": [{
                "rcept_no": RECORDS[0]["rcept_no"] if ep == "crDecsn"
                else "20161101000009", "corp_code": corp}]}
        result = self.process(provider)
        self.assertEqual(result["provider_calls"], 5)
        self.assertEqual(result["processed_candidates"], 2)
        self.assertEqual(result["new_exact_receipt_category_matches"], 1)
        self.assertEqual(result["candidates_without_supplemental_endpoint"], 1)
        self.assertEqual([x[0] for x in calls], list(s.SUPPLEMENTAL_ENDPOINTS))
        self.assertTrue(all(x[2:] == ("20161001", "20161130") for x in calls))
        data = s._validate_report(self.store.blobs[
            s._report_path(0, RECORDS[0]["rcept_no"])], 0, RECORDS[0])
        self.assertEqual(data["exact_receipt_event_categories"], ["crDecsn"])
        self.assertFalse(data["effective_dates_certified"])
        self.assertFalse(data["adjusted_prices_authorized"])
        self.assertFalse(data["scores_authorized"])
        self.assertEqual(result["scores_promoted"], 0)
        self.assertNotIn("20161031000001", json.dumps(result))
        self.assertFalse(any("dart-original-evidence" in p for p in self.store.blobs))

    def test_partial_cursor_resumes_and_reuses_immutable_report(self):
        calls = []
        def provider(ep, corp, begin, end):
            calls.append(ep)
            return {"status": "013"}
        first = self.process(provider, limit=1)
        self.assertEqual(first["provider_calls"], 5)
        self.assertEqual(json.loads(self.store.blobs[s.CHECKPOINT])["next_candidate_index"], 1)
        second = self.process(provider, limit=1)
        self.assertEqual(second["provider_calls"], 0)
        self.assertEqual(json.loads(self.store.blobs[s.CHECKPOINT])["next_pair_index"], 1)
        self.assertEqual(len(calls), 5)
        waiting = self.process(lambda *args: self.fail("no replay after cursor complete"))
        self.assertEqual(waiting["status"], "waiting_for_private_title_queue")

    def test_four_candidate_default_budget_and_private_remaining_accounting(self):
        queue = [record(f"201611030000{n:02d}", ["rights_issue", "bonus_issue",
                 "capital_reduction", "merger_split", "share_exchange"])
                 for n in range(1, 6)]
        calls = []
        def provider(endpoint, corp, begin, end):
            calls.append(endpoint)
            return {"status": "013"}
        with (patch.object(s, "triage_cursor", return_value={"next_pair_index": 1}),
              patch.object(s, "_candidate_queue", return_value=(queue, "a" * 64, 5))):
            first = s.run(self.store, provider)
            self.assertEqual(first["processed_candidates"], 4)
            self.assertEqual(first["provider_calls"], 20)
            self.assertEqual(first["source_pair_candidate_count"], 5)
            self.assertEqual(first["source_pair_next_candidate_index"], 4)
            self.assertEqual(first["source_pair_remaining_candidates"], 1)
            self.assertEqual(first["candidate_budget_per_run"], 4)
            self.assertEqual(first["provider_call_budget_per_run"], 20)
            self.assertEqual(first["next_pair_index"], 0)
            second = s.run(self.store, provider)
            self.assertEqual(second["processed_candidates"], 1)
            self.assertEqual(second["provider_calls"], 5)
            self.assertEqual(second["source_pair_remaining_candidates"], 0)
            self.assertEqual(second["next_pair_index"], 1)
            self.assertEqual(len(calls), 25)
            self.assertEqual(self.store.posts, 7)  # five reports + two cursor writes
            self.assertEqual(second["scores_promoted"], 0)
            self.assertNotIn(queue[0]["rcept_no"], json.dumps(second))

    def test_old_main_three_endpoint_ledger_is_untouched(self):
        self.process(lambda ep, corp, begin, end: {"status": "013"})
        self.assertFalse(any(p.startswith("derived/dart-structured-action-receipts/")
                             for p in self.store.blobs))
        self.assertNotIn("DART_API_KEY", json.dumps(self.store.blobs, default=str))

    def test_unsupported_hints_do_not_promote_negative_corporate_event(self):
        result = s._report(0, RECORDS[1], [])
        self.assertEqual(result["queried_endpoint_scope"], [])
        self.assertFalse(result["complete_corporate_action_universe_checked"])
        self.assertEqual(result["title_hints_without_endpoint"],
                         ["stock_dividend", "stock_split_consolidation"])
        self.assertEqual(s.endpoints_for_hints(RECORDS[1]["title_hints"]), ())

    def test_invalid_quota_or_identity_blocks_before_private_write(self):
        for payload in ({"status": "020"}, {"status": "800"},
                        {"status": "000", "list": [{
                            "corp_code": "00000000", "rcept_no": "20161031000001"}]}):
            with self.assertRaises(s.StructuredActionError):
                self.process(lambda *args: payload)
            self.assertEqual(self.store.posts, 0)

    def test_private_report_tampering_never_certifies_adjusted_price_or_pit(self):
        rows = [{"endpoint": ep, "provider_status": "013", "list_rows": 0,
                 "exact_receipt_match": False}
                for ep in s.SUPPLEMENTAL_ENDPOINTS]
        obj = s._report(0, RECORDS[0], rows)
        for field in ("effective_dates_certified", "scores_authorized",
                      "adjusted_prices_authorized", "historical_issuer_identity_certified"):
            bad = {**obj, field: True}
            with self.assertRaises(s.ExtensionError):
                s._validate_report(s.canonical(bad), 0, RECORDS[0])
        self.assertEqual(s._validate_report(s.canonical(obj), 0, RECORDS[0]), obj)

    def test_supplement_checkpoint_tail_cannot_skip_candidate_or_point_future(self):
        wrong = s._report_path(0, "20161103000009")
        content = b"valid-tail-sha-but-wrong-receipt"
        state = {**s._empty(), "next_pair_index": 0, "next_candidate_index": 1,
                 "queue_sha256": "a" * 64, "last_report_file": wrong,
                 "last_report_sha256": s.sha256(content).hexdigest()}
        self.store.blobs[s.CHECKPOINT] = s.canonical(state)
        self.store.blobs[wrong] = content
        with self.assertRaisesRegex(s.ExtensionError,
                                    "checkpoint_queue_tail_mismatch"):
            self.process(lambda *args: self.fail("must not query skipped receipt"))
        self.assertEqual(self.store.posts, 0)

        for forged in (
            {**state, "last_report_file": s._report_path(1, RECORDS[0]["rcept_no"])},
            {**state, "last_report_file": s.BASE +
             "/reports/201610--201611/../20161031000001.json"},
            {**state, "last_report_file": None, "last_report_sha256": None},
            {**state, "next_pair_index": 1, "next_candidate_index": 0,
             "queue_sha256": None,
             "last_report_file": s._report_path(1, RECORDS[0]["rcept_no"])},
        ):
            with self.assertRaisesRegex(s.ExtensionError,
                                        "invalid_private_extension_checkpoint"):
                s._cursor(s.canonical(forged))
        old = {**state, "next_pair_index": 2, "next_candidate_index": 0,
               "queue_sha256": None,
               "last_report_file": s._report_path(0, RECORDS[0]["rcept_no"])}
        self.assertEqual(s._cursor(s.canonical(old)), old)

    def test_destination_and_endpoint_restrictions(self):
        self.store.repository = "ykvic1/korean-stock-screener"
        with self.assertRaises(s.ExtensionError):
            self.process(lambda *args: self.fail("no external calls"))
        self.assertEqual(self.store.posts, 0)
        for endpoint in s.SUPPLEMENTAL_ENDPOINTS:
            s.inspect_response({"status": "013"}, endpoint, "00121932", "20161031000001")
        with self.assertRaises(s.StructuredActionError):
            s.inspect_response({"status": "013"}, "document.xml", "00121932", "20161031000001")

    def test_workflow_private_and_legacy_scheduler_unchanged(self):
        wf = Path(".github/workflows/v3-dart-structured-action-extension.yml").read_text()
        self.assertIn("v3_private_dart_structured_action_extension.py", wf)
        self.assertIn("group: v3-dart-private-historical-event-receipts", wf)
        self.assertIn("persist-credentials: false", wf)
        self.assertIn("contents: read", wf)
        self.assertIn("DART_API_KEY:", wf)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", wf)
        self.assertNotIn("KRX_API_KEY:", wf)
        self.assertNotIn("git push", wf)
        self.assertIn("30 9 * * 1-5",
                      Path(".github/workflows/full-market-batches.yml").read_text())

    def test_retry_after_private_readback_error_reuses_receipt_no_duplicate_calls(self):
        class Flaky(Private):
            transient = True
            def get_content(self, path):
                if (self.transient and path == s._report_path(0, RECORDS[0]["rcept_no"])
                    and path in self.blobs):
                    self.transient = False
                    raise s.PrivateHistoryError("private_storage_api_failed")
                return super().get_content(path)

        store = Flaky()
        store.blobs.update(self.store.blobs)
        log = io.StringIO()
        calls = []
        def official(*args):
            calls.append(args[0])
            return {"status": "013"}
        with (patch.dict(s.os.environ, {"V3_PRIVATE_ARCHIVE_REPO": s.PRIVATE_REPO,
                                       "V3_PRIVATE_ARCHIVE_TOKEN": "t" * 40,
                                       "DART_API_KEY": "A" * 40}),
              patch.object(s, "PrivateGitStorage", return_value=store),
              patch.object(s, "triage_cursor", return_value={"next_pair_index": 1}),
              patch.object(s, "_candidate_queue", return_value=(RECORDS, "a" * 64, 2)),
              patch.object(s, "official_fetch", side_effect=official),
              patch.object(s.time, "sleep") as sleep,
              redirect_stdout(log)):
            code = s.main()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 5)
        self.assertEqual(store.posts, 3)  # two private reports, then checkpoint
        self.assertTrue(sleep.called)
        self.assertTrue(json.loads(log.getvalue())["previous_reports_reused"] == 1)
        self.assertNotIn(RECORDS[0]["rcept_no"], log.getvalue())

    def test_storage_authentication_or_shape_failure_not_retried(self):
        class InvalidPrivate(Private):
            calls = 0
            def get_content(self, path):
                self.calls += 1
                raise s.PrivateHistoryError("untrusted_private_blob")

        store = InvalidPrivate()
        log = io.StringIO()
        with (patch.dict(s.os.environ, {"V3_PRIVATE_ARCHIVE_REPO": s.PRIVATE_REPO,
                                       "V3_PRIVATE_ARCHIVE_TOKEN": "t" * 40,
                                       "DART_API_KEY": "A" * 40}),
              patch.object(s, "PrivateGitStorage", return_value=store),
              patch.object(s.time, "sleep") as sleep,
              redirect_stdout(log)):
            code = s.main()
        self.assertEqual(code, 1)
        self.assertEqual(store.calls, 1)
        sleep.assert_not_called()
        self.assertEqual(json.loads(log.getvalue())["reason"],
                         "private_extension_storage_unavailable")


if __name__ == "__main__":
    unittest.main()
