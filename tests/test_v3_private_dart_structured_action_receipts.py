"""Exact-match OpenDART JSON categories, private checkpoint and fail-closed tests."""
import json
from contextlib import redirect_stdout
from io import StringIO
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

    def test_one_transient_dart_timeout_retries_within_six_attempt_budget(self):
        requests = []
        def fetch(endpoint, corp, start, end):
            requests.append(endpoint)
            if len(requests) == 1:
                raise a.StructuredActionError(
                    "official_structured_action_transport_or_json_failed")
            return official_rows(endpoint, exact=True)
        with patch.object(a.time, "sleep") as sleep:
            out = self.process(fetch)
        self.assertEqual(out["provider_calls"], 4)
        self.assertEqual(requests, [a.ENDPOINTS[0], *a.ENDPOINTS])
        self.assertTrue(sleep.called)
        self.assertEqual(out["processed_candidates"], 1)
        self.assertEqual(out["scores_promoted"], 0)
        self.assertIn(a._path(0, RECORD["rcept_no"]), self.archive.blobs)

    def test_retry_consumes_budget_and_resumes_next_candidate_without_extra_calls(self):
        second = {**RECORD, "rcept_no": "20161103000002"}
        queue = [RECORD, second]
        attempts = []
        def fetch(endpoint, corp, start, end):
            attempts.append(endpoint)
            if len(attempts) == 1:
                raise a.StructuredActionError(
                    "official_structured_action_transport_or_json_failed")
            return official_rows(endpoint)
        with patch.object(a, "triage_cursor", return_value={"next_pair_index": 1}):
            with patch.object(a, "_candidate_queue",
                              return_value=(queue, QUEUE_HASH, 2)):
                with patch.object(a.time, "sleep"):
                    out = a.run(self.archive, fetch)
        self.assertEqual(out["provider_calls"], 4)
        self.assertEqual(out["processed_candidates"], 1)
        self.assertNotIn(a._path(0, second["rcept_no"]), self.archive.blobs)
        cursor = a._cursor(self.archive.blobs[a.CHECKPOINT])
        self.assertEqual(cursor["next_candidate_index"], 1)
        self.assertFalse(cursor["scores_authorized"])

        next_calls = []
        def update_private_cursor(method, path, payload):
            self.assertEqual(method, "PUT")
            self.assertEqual(path, "contents/" + a.CHECKPOINT)
            # FakePrivate uses a symbolic content SHA rather than Git's blob SHA.
            self.assertEqual(payload["sha"], "sha")
            self.archive.blobs[a.CHECKPOINT] = a.base64.b64decode(payload["content"])

        with patch.object(self.archive, "transport", create=True,
                          side_effect=update_private_cursor):
            with patch.object(a, "triage_cursor", return_value={"next_pair_index": 1}):
                with patch.object(a, "_candidate_queue",
                                  return_value=(queue, QUEUE_HASH, 2)):
                    out2 = a.run(self.archive,
                                 lambda endpoint, *rest:
                                     (next_calls.append(endpoint),
                                      official_rows(endpoint))[1])
        self.assertEqual(next_calls, list(a.ENDPOINTS))
        self.assertEqual(out2["provider_calls"], 3)
        self.assertEqual(out2["previously_saved_reports_reused"], 0)
        self.assertEqual(out2["processed_candidates"], 1)

    def test_no_retry_of_unverified_provider_status_and_no_false_report(self):
        calls = []
        def fetch(*args):
            calls.append(args)
            return {"status": "020"}
        with self.assertRaisesRegex(
                a.StructuredActionError, "structured_action_provider_rejected"):
            self.process(fetch)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(a._path(0, RECORD["rcept_no"]), self.archive.blobs)
        self.assertNotIn(a.CHECKPOINT, self.archive.blobs)

    def test_two_transient_timeouts_abort_without_checkpoint_or_score(self):
        calls = []
        def fetch(*args):
            calls.append(args)
            raise a.StructuredActionError(
                "official_structured_action_transport_or_json_failed")
        with patch.object(a.time, "sleep"):
            with self.assertRaisesRegex(
                    a.StructuredActionError,
                    "official_structured_action_transport_or_json_failed"):
                self.process(fetch)
        self.assertEqual(len(calls), 2)
        self.assertNotIn(a.CHECKPOINT, self.archive.blobs)
        self.assertNotIn(a._path(0, RECORD["rcept_no"]), self.archive.blobs)

    def test_second_candidate_transient_cannot_exceed_six_request_budget(self):
        second = {**RECORD, "rcept_no": "20161103000002"}
        queue = [RECORD, second]
        calls = []
        def fetch(endpoint, corp, start, end):
            calls.append(endpoint)
            if len(calls) == 4:
                raise a.StructuredActionError(
                    "official_structured_action_transport_or_json_failed")
            return official_rows(endpoint)
        with patch.object(a, "triage_cursor", return_value={"next_pair_index": 1}):
            with patch.object(a, "_candidate_queue",
                              return_value=(queue, QUEUE_HASH, 2)):
                with patch.object(a.time, "sleep") as sleep:
                    with self.assertRaisesRegex(
                            a.StructuredActionError,
                            "official_structured_action_transport_or_json_failed"):
                        a.run(self.archive, fetch)
        self.assertEqual(len(calls), 4)
        self.assertFalse(sleep.called)
        self.assertIn(a._path(0, RECORD["rcept_no"]), self.archive.blobs)
        self.assertNotIn(a._path(0, second["rcept_no"]), self.archive.blobs)
        self.assertNotIn(a.CHECKPOINT, self.archive.blobs)

    def test_second_run_reuses_immutable_report_no_extra_dart_calls(self):
        self.process(lambda ep, corp, start, end: official_rows(ep, exact=True))
        report_path = a._path(0, RECORD["rcept_no"])
        self.assertIn(report_path, self.archive.blobs)
        calls = []
        result = self.process(lambda *args: calls.append(args))
        # Cursor is already at next pair, so the job is safely waiting.
        self.assertEqual(result["status"], "waiting_for_private_title_shortlist")
        self.assertEqual(calls, [])

    def test_transient_private_write_failure_retries_without_extra_dart_calls(self):
        path = a._path(0, RECORD["rcept_no"])
        class FailedFirstPut(FakePrivate):
            failed = False
            def put_new(self, key, raw, comment):
                if key == path and not self.failed:
                    self.failed = True
                    raise a.PrivateHistoryError("private_storage_api_failed")
                return super().put_new(key, raw, comment)

        archive = FailedFirstPut()
        archive.blobs.update(self.archive.blobs)
        calls = []
        def fetch(endpoint, corp, start, end):
            calls.append(endpoint)
            return official_rows(endpoint)
        with patch.object(a.time, "sleep") as sleep:
            result = self.process(fetch, archive=archive)
        self.assertEqual(result["provider_calls"], len(a.ENDPOINTS))
        self.assertEqual(calls, list(a.ENDPOINTS))
        self.assertTrue(sleep.called)
        self.assertEqual(archive.posts, 2)  # one immutable receipt, one cursor
        self.assertIn(path, archive.blobs)
        self.assertIn(a.CHECKPOINT, archive.blobs)

    def test_transient_private_cursor_put_retries_without_provider_refetch(self):
        self.archive.blobs[a.CHECKPOINT] = a.canonical(a._empty())
        provider_calls, cursor_attempts = [], []
        def put(method, path, payload):
            cursor_attempts.append((method, path))
            if len(cursor_attempts) == 1:
                raise a.PrivateHistoryError("private_storage_api_failed")
            self.archive.blobs[a.CHECKPOINT] = a.base64.b64decode(payload["content"])
        def fetch(endpoint, *args):
            provider_calls.append(endpoint)
            return official_rows(endpoint)
        with patch.object(self.archive, "transport", create=True, side_effect=put):
            with patch.object(a.time, "sleep") as sleep:
                out = self.process(fetch)
        self.assertTrue(sleep.called)
        self.assertEqual(len(cursor_attempts), 2)
        self.assertEqual(provider_calls, list(a.ENDPOINTS))
        self.assertEqual(out["provider_calls"], 3)
        self.assertIn(a._path(0, RECORD["rcept_no"]), self.archive.blobs)
        self.assertFalse(a._cursor(self.archive.blobs[a.CHECKPOINT])["scores_authorized"])

    def test_cursor_put_lost_response_adopts_existing_bytes_without_overwrite(self):
        self.archive.blobs[a.CHECKPOINT] = a.canonical(a._empty())
        calls, put_attempts = [], []
        def put(method, path, payload):
            put_attempts.append(path)
            if len(put_attempts) > 1:
                self.fail("already committed checkpoint must not be rewritten")
            self.archive.blobs[a.CHECKPOINT] = a.base64.b64decode(payload["content"])
            raise a.PrivateHistoryError("private_storage_api_failed")
        with patch.object(self.archive, "transport", create=True, side_effect=put):
            with patch.object(a.time, "sleep"):
                out = self.process(lambda ep, *args:
                                   (calls.append(ep), official_rows(ep))[1])
        self.assertEqual(len(put_attempts), 1)
        self.assertEqual(calls, list(a.ENDPOINTS))
        self.assertEqual(out["provider_calls"], 3)
        self.assertEqual(a._cursor(self.archive.blobs[a.CHECKPOINT])
                         ["next_pair_index"], 1)

    def test_concurrent_cursor_change_is_never_overwritten_after_transport_error(self):
        self.archive.blobs[a.CHECKPOINT] = a.canonical(a._empty())
        writer_calls, provider_calls = [], []
        changed = b"another-private-writer-checkpoint"
        def put(method, path, payload):
            writer_calls.append(path)
            self.archive.blobs[a.CHECKPOINT] = changed
            raise a.PrivateHistoryError("private_storage_api_failed")
        with patch.object(self.archive, "transport", create=True, side_effect=put):
            with patch.object(a.time, "sleep"):
                with self.assertRaisesRegex(
                        a.StructuredActionError,
                        "private_structured_action_checkpoint_changed"):
                    self.process(lambda ep, *args:
                                 (provider_calls.append(ep), official_rows(ep))[1])
        self.assertEqual(len(writer_calls), 1)
        self.assertEqual(provider_calls, list(a.ENDPOINTS))
        self.assertEqual(self.archive.blobs[a.CHECKPOINT], changed)

    def test_new_cursor_commit_then_lost_response_is_reused_without_duplicate(self):
        class CommittedCursorThenError(FakePrivate):
            failed = False
            def put_new(self, path, blob, message):
                result = super().put_new(path, blob, message)
                if path == a.CHECKPOINT and not self.failed:
                    self.failed = True
                    raise a.PrivateHistoryError("private_storage_api_failed")
                return result

        archive = CommittedCursorThenError()
        archive.blobs.update(self.archive.blobs)
        calls = []
        with patch.object(a.time, "sleep"):
            out = self.process(lambda ep, *args:
                               (calls.append(ep), official_rows(ep))[1],
                               archive=archive)
        self.assertEqual(calls, list(a.ENDPOINTS))
        self.assertEqual(out["provider_calls"], 3)
        self.assertEqual(archive.posts, 2)
        self.assertEqual(a._cursor(archive.blobs[a.CHECKPOINT])
                         ["next_pair_index"], 1)

    def test_lost_write_response_adopts_exact_saved_bytes_not_overwrite(self):
        path = a._path(0, RECORD["rcept_no"])
        class CommittedButErrored(FakePrivate):
            failed = False
            def put_new(self, key, raw, comment):
                outcome = super().put_new(key, raw, comment)
                if key == path and not self.failed:
                    self.failed = True
                    raise a.PrivateHistoryError("private_storage_api_failed")
                return outcome

        archive = CommittedButErrored()
        archive.blobs.update(self.archive.blobs)
        calls = []
        with patch.object(a.time, "sleep"):
            result = self.process(
                lambda ep, corp, start, end:
                    (calls.append(ep), official_rows(ep))[1],
                archive=archive)
        self.assertEqual(calls, list(a.ENDPOINTS))
        self.assertEqual(result["provider_calls"], 3)
        self.assertEqual(archive.posts, 2)
        self.assertEqual(
            a._validate_report(archive.blobs[path], 0, RECORD)["scores_authorized"],
            False)

    def test_conflicting_immutable_report_never_overwritten_or_checkpointed(self):
        path = a._path(0, RECORD["rcept_no"])
        class ConflictingPrivate(FakePrivate):
            def put_new(self, key, raw, comment):
                if key == path:
                    self.blobs[key] = b"conflicting private content"
                    raise a.PrivateHistoryError("private_storage_api_failed")
                return super().put_new(key, raw, comment)

        archive = ConflictingPrivate()
        archive.blobs.update(self.archive.blobs)
        calls = []
        with patch.object(a.time, "sleep"):
            with self.assertRaisesRegex(
                    a.StructuredActionError, "private_structured_action_write_conflict"):
                self.process(
                    lambda ep, corp, start, end:
                        (calls.append(ep), official_rows(ep))[1],
                    archive=archive)
        self.assertEqual(calls, list(a.ENDPOINTS))
        self.assertEqual(archive.blobs[path], b"conflicting private content")
        self.assertNotIn(a.CHECKPOINT, archive.blobs)

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

    def test_official_schedule_fields_are_private_candidates_not_effective_dates(self):
        body = {"status": "000", "list": [
            {"rcept_no": RECORD["rcept_no"], "corp_code": RECORD["corp_code"],
             "mgsc_mgdt": "20261105", "bddd": "2026-10-20"},
            {"rcept_no": "20161101000003", "corp_code": RECORD["corp_code"],
             "mgsc_mgdt": "2020-01-01"},
        ]}
        result = a.inspect_response(body, "cmpMgDecsn",
                                    RECORD["corp_code"], RECORD["rcept_no"])
        self.assertEqual(result["reported_schedule_date_candidates"],
                         [{"field": "mgsc_mgdt", "reported_date": "2026-11-05"}])
        self.assertTrue(result["exact_receipt_match"])
        details = [result] + [
            {"endpoint": name, "provider_status": "013", "list_rows": 0,
             "exact_receipt_match": False}
            for name in a.ENDPOINTS[1:]]
        saved = a._report(0, RECORD, details)
        self.assertEqual(a._validate_report(a.canonical(saved), 0, RECORD), saved)
        self.assertFalse(saved["effective_date_authenticated"])
        self.assertFalse(saved["share_adjustment_authenticated"])
        self.assertFalse(saved["scores_authorized"])
        self.assertEqual(a._valid_schedule_candidates(result, "cmpMgDecsn"), True)
        # Existing immutable reports without the new optional field still load.
        legacy = a._report(0, RECORD, [
            {k: v for k, v in item.items()
             if k != "reported_schedule_date_candidates"} for item in details])
        self.assertEqual(a._validate_report(a.canonical(legacy), 0, RECORD), legacy)

    def test_planned_new_share_listing_dates_are_private_not_actual_listings(self):
        # Official DS005 response keys are additional planned dates, not
        # evidence that the new shares were ever listed or prices adjusted.
        cases = {
            "cmpDvDecsn": ("dvdt", "abcr_nstklstprd"),
            "fricDecsn": ("nstk_asstd", "nstk_lstprd"),
        }
        for endpoint, fields in cases.items():
            with self.subTest(endpoint=endpoint):
                exact_row = {
                    "rcept_no": RECORD["rcept_no"],
                    "corp_code": RECORD["corp_code"],
                    fields[0]: "20261105",
                    fields[1]: "20261119",
                    "bddd": "20261020",
                }
                observed = a.inspect_response(
                    {"status": "000", "list": [exact_row]},
                    endpoint, RECORD["corp_code"], RECORD["rcept_no"])
                self.assertEqual(
                    observed["reported_schedule_date_candidates"],
                    [{"field": fields[0], "reported_date": "2026-11-05"},
                     {"field": fields[1], "reported_date": "2026-11-19"}])
                self.assertTrue(a._valid_schedule_candidates(observed, endpoint))
                self.assertFalse(a._valid_schedule_candidates(
                    {**observed,
                     "reported_schedule_date_candidates":
                         list(reversed(observed["reported_schedule_date_candidates"]))},
                    endpoint))
                self.assertFalse(a._valid_schedule_candidates(
                    {**observed, "exact_receipt_match": False}, endpoint))
                unlisted = a.inspect_response(
                    {"status": "000", "list": [
                        {**exact_row, "rcept_no": "20161101000003"}]},
                    endpoint, RECORD["corp_code"], RECORD["rcept_no"])
                self.assertEqual(unlisted["reported_schedule_date_candidates"], [])

    def test_schedule_day_rejects_unknown_invalid_and_nonmatching_source(self):
        self.assertIsNone(a._schedule_day("2026-02-30"))
        self.assertIsNone(a._schedule_day("2026년 10월 29일"))
        self.assertIsNone(a._schedule_day("2099-13-01"))
        self.assertIsNone(a._schedule_day(20261029))
        self.assertIsNone(a._schedule_day("2014-12-31"))
        self.assertIsNone(a._schedule_day("2101-01-01"))
        unmatched = a.inspect_response(
            {"status": "000", "list": [
                {"rcept_no": "20161101000003", "corp_code": RECORD["corp_code"],
                 "mgsc_mgdt": "20261105"}]},
            "cmpMgDecsn", RECORD["corp_code"], RECORD["rcept_no"])
        self.assertEqual(unmatched["reported_schedule_date_candidates"], [])
        nodata = a.inspect_response(
            {"status": "013"}, "cmpMgDecsn",
            RECORD["corp_code"], RECORD["rcept_no"])
        self.assertEqual(nodata["reported_schedule_date_candidates"], [])
        self.assertFalse(nodata["exact_receipt_match"])
        supported = {"endpoint": "fricDecsn", "provider_status": "000",
                     "list_rows": 1, "exact_receipt_match": True,
                     "reported_schedule_date_candidates": [
                         {"field": "nstk_asstd", "reported_date": "2026-11-05"}]}
        self.assertTrue(a._valid_schedule_candidates(supported, "fricDecsn"))
        for bad in (
            [{**supported["reported_schedule_date_candidates"][0],
              "reported_date": "2026-02-30"}],
            [{"field": "bddd", "reported_date": "2026-11-05"}],
            [{"field": "nstk_asstd", "reported_date": "2026-11-05"},
             {"field": "nstk_asstd", "reported_date": "2026-11-05"}],
        ):
            self.assertFalse(a._valid_schedule_candidates(
                {**supported, "reported_schedule_date_candidates": bad},
                "fricDecsn"))
        self.assertFalse(a._valid_schedule_candidates(
            {**supported, "exact_receipt_match": False}, "fricDecsn"))

    def test_private_failure_stage_is_static_and_never_logs_source_payload(self):
        class Broken(FakePrivate):
            def get_content(self, path):
                if path == a.TRIAGE_CHECKPOINT:
                    raise RuntimeError("private-corp-code-00121932-and-secret")
                return super().get_content(path)

        store = Broken()
        marks = []
        with self.assertRaises(RuntimeError):
            a.run(store, lambda *args: self.fail("no provider request"),
                  stage_hook=marks.append)
        self.assertEqual(marks, ["private_auth", "triage_checkpoint_read"])
        out = StringIO()
        with (patch.dict(a.os.environ,
                         {"V3_PRIVATE_ARCHIVE_REPO": a.PRIVATE_REPO,
                          "V3_PRIVATE_ARCHIVE_TOKEN": "t" * 40,
                          "DART_API_KEY": "A" * 40}),
              patch.object(a, "PrivateGitStorage", return_value=store),
              redirect_stdout(out)):
            result = a.main()
        self.assertEqual(result, 1)
        observed = json.loads(out.getvalue())
        self.assertEqual(observed["status"], "blocked")
        self.assertEqual(observed["phase"], "triage_checkpoint_read")
        self.assertEqual(observed["error_category"], "unclassified_internal_error")
        self.assertFalse(observed["provider_source_values_exposed"])
        self.assertNotIn("00121932", out.getvalue())
        self.assertNotIn("secret", out.getvalue())
        self.assertNotIn("DART_API_KEY", out.getvalue())

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
