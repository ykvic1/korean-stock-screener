"""No-network original filing retrieval, ZIP safety, privacy and resume tests."""
import base64
import gzip
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from v3_dart_historical_event_receipts import _bytes as canonical
import v3_private_dart_krx_filing_title_triage as triage
import v3_private_dart_original_evidence as original
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError


def zip_source(name="original.xml"):
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        z.writestr(name, "<DOCUMENT>source</DOCUMENT>")
    return out.getvalue()


def pair_source(*, empty=False):
    rows = [] if empty else [
        {"filing_receipt_no":"20161031000001", "filing_corp_code":"00123456",
         "title_keyword_hints":["merger_split"], "event_authentication":"not_verified"},
        {"filing_receipt_no":"20161031000001", "filing_corp_code":"00123456",
         "title_keyword_hints":["merger_split"], "event_authentication":"not_verified"},
        {"filing_receipt_no":"20161103000002", "filing_corp_code":"00123457",
         "title_keyword_hints":["bonus_issue"], "event_authentication":"not_verified"},
        {"filing_receipt_no":"20161103000002", "filing_corp_code":"00123457",
         "title_keyword_hints":[], "event_authentication":"not_verified"},
    ]
    obj = {"schema":triage.SCHEMA, "first_month":"2016-10", "second_month":"2016-11",
           "candidates":rows, "ticker_date_receipt_pairs":len(rows),
           "title_keyword_pairs":0 if empty else 3,
           "filing_title_or_candidate_is_event_proof":False, "scores_authorized":False}
    return gzip.compress(canonical(obj), mtime=0)


class Private:
    repository = original.PRIVATE_REPO

    def __init__(self, complete=True, empty=False):
        self.blobs = {
            original.TRIAGE_CHECKPOINT: canonical({
                **triage._empty(), "next_pair_index":int(complete),
                "last_file":triage._path(0) if complete else None,
                "last_digest":"a"*64 if complete else None}),
            triage._path(0): pair_source(empty=empty),
        }
        self.writes = 0

    def verify_private(self):
        return True

    def get_content(self, path):
        raw = self.blobs.get(path)
        return raw, sha256(raw).hexdigest() if raw is not None else None

    def put_new(self, path, value, message):
        if path in self.blobs:
            raise AssertionError("overwrite immutable")
        self.blobs[path] = value
        self.writes += 1

    def transport(self, method, path, payload=None):
        if method != "PUT" or not path.startswith("contents/"):
            raise AssertionError("unexpected transport")
        key = path.removeprefix("contents/")
        if sha256(self.blobs[key]).hexdigest() != payload.get("sha"):
            raise AssertionError("incorrect CAS")
        self.blobs[key] = base64.b64decode(payload["content"], validate=True)
        self.writes += 1


class OriginalEvidenceTests(unittest.TestCase):
    def test_private_http_failures_are_safe_and_actionable_without_path_or_token(self):
        # The previous production failure flattened every transport error to
        # private_storage_api_failed. Preserve that stable error identifier,
        # while distinguishing permission, rate limit and concurrent writes.
        store = PrivateGitStorage(original.PRIVATE_REPO, "s" * 24)
        cases = (
            ("GET", "", 401, {}, "read_repository_metadata", "http_401"),
            ("GET", "contents/secret-private-source.json", 403, {},
             "read_private_content", "http_403"),
            ("GET", "contents/secret-private-source.json", 403,
             {"X-RateLimit-Remaining": "0"},
             "read_private_content", "http_403_rate_limited"),
            ("PUT", "contents/secret-private-source.json", 409, {},
             "write_private_content", "http_409"),
            ("PUT", "contents/secret-private-source.json", 422, {},
             "write_private_content", "http_422"),
            ("GET", "", 404, {}, "read_repository_metadata", "http_404"),
        )
        for method, path, code, headers, operation, status in cases:
            with self.subTest(code=code, method=method, status=status):
                denial = HTTPError(
                    "https://secret-private-url.test/source", code,
                    "secret-denial", headers, None)
                with patch("v3_krx_private_github_backfill.request.urlopen",
                           side_effect=denial):
                    with self.assertRaises(PrivateHistoryError) as caught:
                        store._http(method, path, {"test": True} if method == "PUT" else None)
                exc = caught.exception
                self.assertEqual(str(exc), "private_storage_api_failed")
                self.assertEqual(exc.safe_http_operation, operation)
                self.assertEqual(exc.safe_http_status, status)
                public = original._private_storage_failure_message(exc)
                self.assertIn("operation=" + operation, public)
                self.assertIn("http_status=" + status, public)
                for secret in ("secret", "private-url", original.PRIVATE_REPO, "s" * 24):
                    self.assertNotIn(secret, public)
        # A missing content file remains normal 'not yet saved' evidence.
        with patch("v3_krx_private_github_backfill.request.urlopen",
                   side_effect=HTTPError("https://hidden", 404, "secret", {}, None)):
            self.assertIsNone(store._http("GET", "contents/new-source.json"))

    def test_private_error_diagnostics_reject_injected_or_unknown_values(self):
        exc = PrivateHistoryError("private_storage_api_failed")
        exc.safe_http_operation = "read_private_content;receipt=123456789"
        exc.safe_http_status = "http_403;token=secret"
        public = original._private_storage_failure_message(exc)
        self.assertEqual(public,
                         "blocked: private original source archive "
                         "[storage_reason=private_storage_api_failed]")
        self.assertNotIn("secret", public)
        self.assertNotIn("123456789", public)

    def test_zip_failure_stage_is_fixed_and_never_exposes_file_identity(self):
        # Only allowlisted phase labels are safe for public Actions logs.
        # An unsafe member stays rejected; diagnostics do not relax ZIP rules.
        for source, expected_stage in (
            (b"PKunsafe-corrupted-secret", "zip_structure"),
            (zip_source("../private-receipt-secret.xml"),
             "member_structure_or_security"),
            (zip_source("opaque-provider-source.bin"),
             "document_extension"),
        ):
            with self.assertRaisesRegex(
                original.OriginalEvidenceError,
                "^invalid_or_unsafe_official_document_zip$"
            ) as failed:
                original._check_zip(source)
            self.assertEqual(failed.exception.safe_zip_validation_stage,
                             expected_stage)
            self.assertIn(expected_stage,
                          original.SAFE_ZIP_VALIDATION_STAGES)
            self.assertNotIn("secret", str(failed.exception))
            self.assertNotIn("receipt", str(failed.exception))
        self.assertEqual(
            original._check_zip(zip_source())["archive_structure_validated_only"],
            True)

    def test_dart_member_rejection_exposes_only_fixed_reason_never_file_identity(self):
        variants=[(zip_source("../private-990011-secret.xml"),"unsafe_member_path"),
                  (zip_source("x\\secret.xml"),"unsafe_member_path")]
        blank=BytesIO()
        with ZipFile(blank,"w",ZIP_DEFLATED) as z:
            z.writestr("confidential-empty-member.xml","")
        variants.append((blank.getvalue(),"invalid_regular_member_size"))
        for raw,expected in variants:
            with self.assertRaises(original.OriginalEvidenceError) as caught:
                original._check_zip(raw)
            err=caught.exception
            self.assertEqual(err.safe_zip_validation_stage,
                             "member_structure_or_security")
            self.assertEqual(err.safe_zip_member_issue,expected)
            self.assertIn(expected,original.SAFE_ZIP_MEMBER_ISSUES)
            self.assertEqual(str(err),"invalid_or_unsafe_official_document_zip")
            self.assertNotIn("secret",str(err))
            self.assertNotIn("confidential",str(err))


    def test_unsafe_path_failure_is_still_blocked_with_only_fixed_path_class(self):
        samples=(
            ("folder\\document.xml","backslash_path_separator"),
            ("/absolute/secret.xml","absolute_or_drive_path"),
            ("../outside.xml","parent_directory_component"),
            ("folder/../outside.xml","parent_directory_component"),
        )
        for name,expected in samples:
            with self.assertRaises(original.OriginalEvidenceError) as caught:
                original._check_zip(zip_source(name))
            err=caught.exception
            self.assertEqual(err.safe_zip_validation_stage,
                             "member_structure_or_security")
            self.assertEqual(err.safe_zip_member_issue,"unsafe_member_path")
            self.assertEqual(err.safe_zip_path_issue,expected)
            self.assertIn(expected,original.SAFE_ZIP_PATH_ISSUES)
            self.assertNotIn(name,str(err))


    def test_safe_directory_entries_are_allowed_but_traversal_stays_blocked(self):
        out = BytesIO()
        with ZipFile(out, "w", ZIP_DEFLATED) as z:
            z.writestr("docs/", "")
            z.writestr("docs/original.xml", "<DOCUMENT>source</DOCUMENT>")
        self.assertEqual(original._check_zip(out.getvalue())["zip_member_count"], 2)
        out = BytesIO()
        with ZipFile(out, "w", ZIP_DEFLATED) as z:
            z.writestr("../unsafe/", "")
            z.writestr("original.xml", "<DOCUMENT>source</DOCUMENT>")
        with self.assertRaises(original.OriginalEvidenceError):
            original._check_zip(out.getvalue())

    def test_zip_and_provider_no_data_are_unverified_and_resumable(self):
        store = Private()
        calls = []
        def provider(no):
            calls.append(no)
            return zip_source() if no.endswith("000001") else (
                b"<result><status>013</status></result>")
        first = original.run(store, provider, max_calls=1)
        self.assertEqual(first["provider_calls"], 1)
        self.assertEqual(first["new_original_zips_archived"], 1)
        self.assertEqual(first["remaining_receipts_in_pair"], 1)
        self.assertEqual(first["scores_promoted"], 0)
        raw = store.blobs[original._report_path(0, calls[0])]
        data = json.loads(raw)
        self.assertEqual(data["source_status"], "zip_archived_unparsed")
        self.assertFalse(data["corporate_action_authenticated"])
        self.assertFalse(data["effective_date_authenticated"])
        self.assertEqual(data["zip_metadata"]["sha256"], sha256(zip_source()).hexdigest())
        self.assertNotIn("20161031000001", json.dumps(first))
        second = original.run(store, provider)
        self.assertEqual(second["provider_calls"], 1)
        self.assertEqual(second["source_unavailable_receipts"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(store.blobs[original.CHECKPOINT])["next_pair_index"],1)
        self.assertEqual(original.run(store, lambda _: self.fail("unexpected fetch"))
                         ["status"], "waiting_for_private_title_shortlist")

    def test_orphan_zip_and_immutable_report_reuse_no_double_fetch(self):
        store = Private()
        receipt = "20161031000001"
        store.blobs[original._zip_path(0, receipt)] = zip_source()
        first = original.run(store, lambda _: self.fail("new source called"), max_calls=1)
        self.assertEqual(first["provider_calls"], 0)
        self.assertEqual(first["new_original_zips_archived"], 0)
        store.blobs[original.CHECKPOINT] = canonical(original._empty())
        again = original.run(store, lambda _: self.fail("source re-fetched"), max_calls=1)
        self.assertEqual(again["already_archived_receipts_reused"],1)
        self.assertEqual(again["provider_calls"],0)

    def test_empty_queue_advances_without_provider_and_waits_when_incomplete(self):
        s = Private(empty=True)
        result = original.run(s, lambda _: self.fail("no title evidence"))
        self.assertEqual(result["provider_calls"],0)
        self.assertEqual(json.loads(s.blobs[original.CHECKPOINT])["next_pair_index"],1)
        self.assertIsNone(json.loads(s.blobs[original.CHECKPOINT])["last_report_file"])
        waiting = original.run(Private(complete=False),lambda _: self.fail("no pair"))
        self.assertEqual(waiting["status"],"waiting_for_private_title_shortlist")

    def test_provider_oversize_is_unreviewed_not_event_rejection(self):
        store = Private()
        def oversize(_):
            raise original.OriginalOversizedError("official_original_zip_exceeds_private_cap")
        result = original.run(store, oversize, max_calls=1)
        self.assertEqual(result["source_oversize_receipts"],1)
        self.assertEqual(result["provider_calls"],1)
        self.assertEqual(result["scores_promoted"],0)
        self.assertNotIn(original._zip_path(0,"20161031000001"),store.blobs)

    def test_unsafe_official_zip_is_quarantined_and_does_not_block_next_receipt(self):
        store = Private()
        seen = []
        def provider(receipt):
            seen.append(receipt)
            return (zip_source("/unsafe/absolute-original.xml")
                    if receipt.endswith("000001") else zip_source())
        first = original.run(store, provider, max_calls=1)
        self.assertEqual(first["provider_calls"], 1)
        self.assertEqual(first["unsafe_original_zip_receipts_quarantined"], 1)
        self.assertEqual(first["new_original_zips_archived"], 0)
        self.assertEqual(first["scores_promoted"], 0)
        self.assertNotIn(original._zip_path(0, seen[0]), store.blobs)
        report = json.loads(store.blobs[original._report_path(0, seen[0])])
        self.assertEqual(report["source_status"],
                         "unsafe_zip_quarantined_no_negative_inference")
        self.assertIsNone(report["source_zip_path"])
        self.assertIsNone(report["zip_metadata"])
        self.assertFalse(report["corporate_action_authenticated"])
        self.assertFalse(report["effective_date_authenticated"])
        self.assertFalse(report["adjusted_prices_authorized"])
        second = original.run(store, provider, max_calls=1)
        self.assertEqual(second["new_original_zips_archived"], 1)
        self.assertEqual(second["unsafe_original_zip_receipts_quarantined"], 0)
        self.assertEqual(json.loads(store.blobs[original.CHECKPOINT])["next_pair_index"], 1)
        self.assertEqual(len(seen), 2)
        self.assertNotIn("20161031000001", json.dumps(first))

    def test_quarantined_receipt_reuse_never_refetches_or_claims_negative_evidence(self):
        store = Private()
        receipt = "20161031000001"
        first = original.run(store, lambda _: zip_source("../private.xml"), max_calls=1)
        self.assertEqual(first["unsafe_original_zip_receipts_quarantined"], 1)
        store.blobs[original.CHECKPOINT] = canonical(original._empty())
        replay = original.run(store, lambda _: self.fail("quarantined ZIP re-fetched"),
                              max_calls=1)
        self.assertEqual(replay["already_archived_receipts_reused"], 1)
        self.assertEqual(replay["provider_calls"], 0)
        self.assertNotIn(original._zip_path(0, receipt), store.blobs)
        self.assertEqual(replay["unsafe_original_zip_receipts_quarantined"], 1)

    def test_unknown_document_response_still_aborts_without_cursor_or_quarantine(self):
        store = Private()
        with self.assertRaisesRegex(original.OriginalEvidenceError,
                                    "^dart_document_provider_rejected$"):
            original.run(store,
                lambda _: b"<result><status>020</status></result>", max_calls=1)
        self.assertEqual(store.writes, 0)
        self.assertNotIn(original.CHECKPOINT, store.blobs)
        # Even a misleading transport exception containing the parser's
        # static unsafe-ZIP label must not skip the source-fetch boundary.
        with self.assertRaises(original.OriginalEvidenceError):
            original.run(store, lambda _: (_ for _ in ()).throw(
                original.OriginalEvidenceError("invalid_or_unsafe_official_document_zip")),
                max_calls=1)
        self.assertEqual(store.writes, 0)

    def test_private_storage_failures_only_expose_allowlisted_reason(self):
        env = {"DART_API_KEY":"K"*40,
               "V3_PRIVATE_ARCHIVE_REPO": original.PRIVATE_REPO,
               "V3_PRIVATE_ARCHIVE_TOKEN":"T"*30}
        with patch.dict("os.environ", env):
            for code, expected in (
                ("private_storage_api_failed", "private_storage_api_failed"),
                ("opaque-secret-403", "unclassified_private_storage_error"),
            ):
                with patch.object(original, "PrivateGitStorage", side_effect=
                                  original.PrivateHistoryError(code)):
                    with self.assertRaises(SystemExit) as caught:
                        original.main()
                self.assertIn("storage_reason="+expected, str(caught.exception))
                self.assertNotIn("opaque-secret", str(caught.exception))

    def test_corrupt_zip_path_traversal_and_quota_status_fail_closed(self):
        for name in ("../escape.xml", "bad\\folder.xml", "x/../../evil.xml"):
            with self.assertRaises(original.OriginalEvidenceError):
                original._check_zip(zip_source(name))
        for status in (b"<result><status>020</status></result>",
                       b"<status>900</status>",b"not-xml",
                       b'<!DOCTYPE x><status>013</status>'):
            with self.assertRaises(original.OriginalEvidenceError):
                original._interpret_response(status)
        store = Private()
        store.repository = "ykvic1/korean-stock-screener"
        with self.assertRaises(original.OriginalEvidenceError):
            original.run(store, lambda _: zip_source())
        store = Private()
        source = json.loads(gzip.decompress(store.blobs[triage._path(0)]))
        source["scores_authorized"] = True
        store.blobs[triage._path(0)] = gzip.compress(canonical(source),mtime=0)
        with self.assertRaises(original.OriginalEvidenceError):
            original.run(store, lambda _: self.fail("invalid queue"))

    def test_private_workflow_two_call_cap_and_legacy_market_schedule(self):
        wf = Path(".github/workflows/v3-private-dart-original-evidence.yml").read_text()
        self.assertIn("v3_private_dart_original_evidence.py", wf)
        self.assertIn("test_v3_private_dart_original_evidence.py", wf)
        self.assertIn("DART_API_KEY:", wf)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", wf)
        self.assertIn("group: v3-dart-private-historical-event-receipts", wf)
        self.assertIn("contents: read", wf)
        self.assertIn("persist-credentials: false", wf)
        self.assertIn("default: dry_run", wf)
        self.assertNotIn("KRX_API_KEY:", wf)
        self.assertNotIn("git push", wf)
        self.assertIn("30 9 * * 1-5",
            Path(".github/workflows/full-market-batches.yml").read_text())


if __name__ == "__main__":
    unittest.main()
