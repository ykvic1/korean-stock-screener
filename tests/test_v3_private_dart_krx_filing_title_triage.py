"""Offline regressions for private-only, title-only DART/KRX research queue."""
import base64
from datetime import date, timedelta
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_dart_historical_event_receipts as dart
import v3_private_dart_krx_event_crosscheck as cross
import v3_private_dart_krx_adjacent_month as adjacent
import v3_private_dart_krx_filing_title_triage as triage


class Private:
    repository = triage.PRIVATE_REPO

    def __init__(self, months=2, monthly=2, pairs=1):
        self.blobs = {
            triage.DART_CHECKPOINT: dart._bytes({
                **dart._empty(), "month_index": months}),
            triage.MONTHLY_CHECKPOINT: dart._bytes({
                **cross._empty(), "next_month_index": monthly,
                "last_month_file": f"{cross.OUT}/{cross.MONTHS[monthly-1]}.json"
                    if monthly else None,
                "last_month_digest": "a"*64 if monthly else None}),
            triage.ADJACENT_CHECKPOINT: dart._bytes({
                **adjacent._empty(), "next_pair_index": pairs,
                "last_pair_file": adjacent._pair_path(pairs-1)
                    if pairs else None,
                "last_pair_digest": "b"*64 if pairs else None}),
            triage.SOURCE_CHECKPOINT: b"source",
            triage.RISK_CHECKPOINT: b"risk",
        }
        self.puts = 0

    def verify_private(self):
        return True

    def get_content(self, path):
        raw = self.blobs.get(path)
        return raw, sha256(raw).hexdigest() if raw is not None else None

    def put_new(self, path, raw, message):
        if path in self.blobs:
            raise AssertionError("private immutable output overwritten")
        self.blobs[path] = raw
        self.puts += 1

    def transport(self, method, path, payload=None):
        if method != "PUT" or not path.startswith("contents/"):
            raise AssertionError("unexpected private request")
        key = path.removeprefix("contents/")
        if payload.get("sha") != sha256(self.blobs[key]).hexdigest():
            raise AssertionError("conditional cursor SHA mismatch")
        self.blobs[key] = base64.b64decode(payload["content"], validate=True)
        self.puts += 1


def risk_events():
    days = [(date(2016, 10, 17) + timedelta(days=x)).isoformat()
            for x in range(45)]
    return [
        {"issuer_market": "KOSPI:005930", "first_day": "2016-10-27",
         "last_day": "2016-10-31", "type": "large_four_interval_bucket_change"},
        {"issuer_market": "KOSPI:005930", "first_day": "2016-11-02",
         "last_day": "2016-11-03", "type": "cohort_boundary_change"},
    ], days, {"next_cohort_index": 9}, b"source", b"risk"


def rows(storage, month):
    if month == "2016-10":
        return [{"rcept_no": "20161031000001", "corp_code": "00123456",
                 "stock_code": "005930", "rcept_dt": "20161031",
                 "report_nm": "정기 보고서"}]
    return [
        {"rcept_no": "20161103000002", "corp_code": "00123456",
         "stock_code": "005930", "rcept_dt": "20161103",
         "report_nm": "주요사항보고서 (회사 분할 합병 결정)"},
        {"rcept_no": "20161103000003", "corp_code": "00888888",
         "stock_code": "000660", "rcept_dt": "20161103",
         "report_nm": "무상증자 결정"}]


class FilingTitleTriageTests(unittest.TestCase):
    def test_private_title_queue_is_ranked_zero_provider_and_no_certification(self):
        store = Private()
        with (patch.object(triage, "_source_events", return_value=risk_events()),
              patch.object(triage, "_month_receipts", side_effect=rows)):
            result = triage.run(store)
        self.assertEqual(result["status"], "private_title_keyword_shortlist_completed")
        self.assertEqual(result["ticker_date_receipt_pairs"], 4)
        self.assertEqual(result["title_keyword_pairs"], 2)
        self.assertEqual(result["other_title_pairs"], 2)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["scores_promoted"], 0)
        data = json.loads(gzip.decompress(store.blobs[triage._path(0)]))
        self.assertEqual(data["first_month"], "2016-10")
        self.assertEqual(data["second_month"], "2016-11")
        self.assertEqual(len(data["candidates"]), 4)
        self.assertEqual(data["candidates"][0]["title_keyword_hints"], ["merger_split"])
        self.assertTrue(any(x["opposite_calendar_month"] for x in data["candidates"]))
        self.assertFalse(data["filing_title_or_candidate_is_event_proof"])
        self.assertFalse(data["actual_effective_date_verified"])
        self.assertFalse(data["historical_issuer_identity_verified"])
        self.assertFalse(data["scores_authorized"])
        self.assertEqual(store.puts, 2)
        # Sensitive data is inside the compressed PRIVATE file only.
        self.assertNotIn("005930", json.dumps(result))
        self.assertNotIn("20161103000002", json.dumps(result))
        with patch.object(triage, "_source_events") as src:
            result2 = triage.run(store)
        src.assert_not_called()
        self.assertEqual(result2["status"], "waiting_for_two_approved_complete_months")

    def test_missing_upstream_month_and_prior_adjacency_are_not_negative_evidence(self):
        for opts in ({"months": 1}, {"monthly": 1}, {"pairs": 0}):
            store = Private(**opts)
            with patch.object(triage, "_source_events") as src:
                result = triage.run(store)
            src.assert_not_called()
            self.assertEqual(result["provider_calls"], 0)
            self.assertEqual(store.puts, 0)
            self.assertEqual(result["confirmed_corporate_actions"], 0)

    def test_unverified_title_category_and_no_match_are_not_negative_proof(self):
        self.assertEqual(triage._hint("정정(주식 병합 결정 철회)"),
                         ["stock_split_consolidation"])
        self.assertEqual(triage._hint("통상의 정기보고서"), [])
        # The immutable first nine KRX cohorts use distinct, equally valid
        # historical kind names. A six-character nonnumeric KRX code is valid
        # but cannot be joined to the numeric-only DART ticker and stays absent.
        mixed, matches = triage._shortlist(
            risk_events()[0] + [{
                "issuer_market": "KOSPI:12A345",
                "first_day": "2016-10-27", "last_day": "2016-10-31",
                "type": "large_one_interval_cohort_boundary"}],
            rows(None, "2016-10") + rows(None, "2016-11"),
            "2016-10", "2016-11",
        )
        self.assertEqual(mixed, 3)
        self.assertEqual(len(matches), 4)
        events = [{"issuer_market": "KOSDAQ:000660",
                   "first_day": "2016-11-01",
                   "last_day": "2016-11-03",
                   "type": "four_interval_change"}]
        examined, result = triage._shortlist(events, rows(None, "2016-10"), 
                                             "2016-10", "2016-11")
        self.assertEqual(examined, 1)
        self.assertEqual(result, [])
        # A filing 15 days away is excluded, even with a highly suggestive title.
        distant = [{"rcept_no": "20161120000001", "corp_code": "00123456",
                    "stock_code": "005930", "rcept_dt": "20161120",
                    "report_nm": "회사합병 결정"}]
        examined, result = triage._shortlist(risk_events()[0], distant,
                                             "2016-10", "2016-11")
        self.assertEqual(examined, 2)
        self.assertEqual(result, [])

    def test_fail_closed_on_public_store_corrupt_source_and_mutation(self):
        store = Private()
        store.repository = "ykvic1/korean-stock-screener"
        with self.assertRaises(triage.TitleTriageError):
            triage.run(store)
        with self.assertRaises(triage.TitleTriageError):
            triage._cursor(b'{"schema":"junk","next_pair_index":true}')
        for invalid in ("20161301", "2016-02-30", "notadate"):
            with self.assertRaises(triage.TitleTriageError):
                triage._day(invalid)
        with self.assertRaises(triage.TitleTriageError):
            triage._shortlist(risk_events()[0],
                             [{"stock_code": "005930", "rcept_no": "not-receipt"}],
                             "2016-10", "2016-11")
        store = Private()
        with (patch.object(triage, "_source_events", return_value=(
                risk_events()[0], risk_events()[1],
                {"next_cohort_index": 5}, b"source", b"risk")),
              patch.object(triage, "_month_receipts") as receipts):
            response = triage.run(store)
        self.assertEqual(response["status"], "waiting_for_complete_second_month_krx_review")
        receipts.assert_not_called()
        self.assertEqual(store.puts, 0)

    def test_schedule_private_isolation_and_legacy_1830(self):
        wf = Path(".github/workflows/v3-private-dart-krx-event-crosscheck.yml").read_text()
        legacy = Path(".github/workflows/full-market-batches.yml").read_text()
        self.assertIn("v3_private_dart_krx_filing_title_triage.py", wf)
        self.assertIn("test_v3_private_dart_krx_filing_title_triage.py", wf)
        self.assertIn("python v3_private_dart_krx_filing_title_triage.py", wf)
        self.assertIn("group: v3-dart-private-historical-event-receipts", wf)
        self.assertIn("contents: read", wf)
        self.assertIn("persist-credentials: false", wf)
        self.assertNotIn("DART_API_KEY:", wf)
        self.assertNotIn("KRX_API_KEY:", wf)
        self.assertIn("30 9 * * 1-5", legacy)


if __name__ == "__main__":
    unittest.main()
