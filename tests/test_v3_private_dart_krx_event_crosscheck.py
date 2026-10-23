"""No-network PIT-safe tests for private historical DART/KRX candidate join."""
import base64
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_private_dart_krx_event_crosscheck as cross
import v3_dart_historical_event_receipts as dart


class Private:
    repository = cross.PRIVATE_REPO
    def __init__(self):
        self.blobs = {}
        self.puts = 0
    def verify_private(self):
        return True
    def get_content(self, path):
        body = self.blobs.get(path)
        return body, sha256(body).hexdigest() if body is not None else None
    def put_new(self, path, content, message):
        if path in self.blobs:
            raise AssertionError("private immutable page rewritten")
        self.blobs[path] = content
        self.puts += 1
    def transport(self, method, path, payload=None):
        if method != "PUT" or not path.startswith("contents/"):
            raise AssertionError("unexpected transport")
        key = path.removeprefix("contents/")
        if (key not in self.blobs or payload.get("sha") !=
            sha256(self.blobs[key]).hexdigest()):
            raise AssertionError("private conditional write mismatch")
        self.blobs[key] = base64.b64decode(payload["content"], validate=True)
        self.puts += 1


def setup(*,completed=1,receipt=True):
    store = Private()
    month = cross.MONTHS[0]
    total = 0
    last = None
    for cat in cross.CATEGORIES:
        row = [{"rcept_no":"20161017000001", "corp_code":"00123456",
                "stock_code":"005930", "rcept_dt":"20161017",
                "report_nm":"주요사항보고서"}] if receipt and cat == "B" else []
        data = dart.parse_official_response(
            {"status":"000", "total_count":"1", "total_page":"1",
             "page_no":"1", "page_count":"100", "list":row}
            if row else {"status":"013"}, month, cat, 1)
        path = dart._path(month, cat, 1)
        store.blobs[path] = data
        total += len(row)
        last = path
    checkpoint = {**dart._empty(), "month_index":completed,
                  "category_index":0, "page":1, "saved_pages":4,
                  "saved_receipts":total, "last_page_file":last,
                  "last_page_sha256":sha256(store.blobs[last]).hexdigest()}
    store.blobs[dart.CHECKPOINT] = dart._bytes(checkpoint)
    store.blobs[cross.SOURCE_CHECKPOINT] = b"synthetic-source"
    store.blobs[cross.RISK_CHECKPOINT] = b"synthetic-risk"
    days = [(date(2016,10,17)+timedelta(days=i)).isoformat()
            for i in range(25)]
    event = {"issuer_market":"KOSPI:005930", "last_day":"2016-10-20",
             "first_day":"2016-10-17", "type":"four_interval_change"}
    review = {"next_cohort_index":4}
    return store, days, event, review


class CrosscheckTests(unittest.TestCase):
    def test_partial_month_is_not_accepted_as_no_corporate_action(self):
        store,days,event,review = setup(completed=0)
        with patch.object(cross, "_source_events") as load:
            result = cross.run(store)
        load.assert_not_called()
        self.assertEqual(result["status"],
                         "waiting_for_complete_official_filing_month")
        self.assertEqual(result["confirmed_corporate_actions"],0)
        self.assertEqual(store.puts,0)

    def test_finished_month_matches_ticker_date_but_never_authenticates_event(self):
        store,days,event,review = setup()
        sources = ([event, {**event,"issuer_market":"KOSDAQ:123456"}],
                   days,review,b"synthetic-source",b"synthetic-risk")
        with patch.object(cross,"_source_events",return_value=sources):
            result = cross.run(store)
        self.assertEqual(result["status"],
                         "private_unverified_candidate_month_completed")
        self.assertEqual(result["filing_receipts_examined"],1)
        self.assertEqual(result["risk_events_within_completed_month"],2)
        self.assertEqual(result["ticker_date_nearby_event_count"],1)
        self.assertEqual(result["ticker_date_candidate_pairs"],1)
        self.assertEqual(result["confirmed_corporate_actions"],0)
        self.assertEqual(result["provider_calls"],0)
        month = cross.MONTHS[0]
        assert_path = f"{cross.OUT}/{month}.json"
        report = json.loads(store.blobs[assert_path])
        self.assertTrue(report["coverage_gaps_not_negative_evidence"])
        for key in ("identity_or_corporate_action_verified",
                    "split_adjustment_authorized", "historical_pit_verified",
                    "trading_score_authorized"):
            self.assertFalse(report[key])
        self.assertNotIn("005930",store.blobs[assert_path].decode())
        self.assertNotIn("00123456",store.blobs[assert_path].decode())
        self.assertNotIn("20161017000001",store.blobs[assert_path].decode())
        previous = store.puts
        with patch.object(cross,"_source_events") as load:
            resumed = cross.run(store)
        load.assert_not_called()
        self.assertEqual(store.puts,previous)
        self.assertEqual(resumed["crosschecked_months"],1)

    def test_cannot_publish_partial_dart_category_or_changed_checkpoint(self):
        store,days,event,review = setup()
        store.blobs.pop(dart._path("2016-10","C",1))
        sources=([event],days,review,b"synthetic-source",b"synthetic-risk")
        with patch.object(cross,"_source_events",return_value=sources):
            with self.assertRaises(cross.CrosscheckError):
                cross.run(store)
        self.assertNotIn(cross.CHECKPOINT,store.blobs)
        self.assertEqual(store.puts,0)
        store,days,event,review=setup()
        def mutated(st):
            st.blobs[dart.CHECKPOINT] = b"changed"
            return [event],days,review,b"synthetic-source",b"synthetic-risk"
        with patch.object(cross,"_source_events",side_effect=lambda s: mutated(s)):
            with self.assertRaisesRegex(
                    cross.CrosscheckError,"source_cursors_changed_during_match"):
                cross.run(store)
        self.assertEqual(store.puts,0)

    def test_krx_risk_month_must_be_complete_even_with_full_dart_month(self):
        store,days,event,review=setup()
        review["next_cohort_index"]=2
        with patch.object(cross,"_source_events",return_value=(
                [event],days,review,b"synthetic-source",b"synthetic-risk")):
            result=cross.run(store)
        self.assertEqual(result["status"],
                         "waiting_for_full_month_krx_risk_review")
        self.assertEqual(store.puts,0)

    def test_ticker_collision_cross_category_duplicate_and_date_filter(self):
        store,days,event,review=setup()
        month=cross.MONTHS[0]
        stamp=dart.parse_official_response({
            "status":"000","total_count":"1","total_page":"1",
            "page_no":"1","page_count":"100",
            "list":[{"rcept_no":"20161017000001","corp_code":"00123456",
                     "stock_code":"005930","rcept_dt":"20161017",
                     "report_nm":"실제 문서는 미검증"}]},month,"C",1)
        store.blobs[dart._path(month,"C",1)]=stamp
        with self.assertRaisesRegex(
                cross.CrosscheckError,"dart_receipt_duplicate_across_pages"):
            cross._month_receipts(store,month)
        event["last_day"]="2016-10-31"
        found=cross._crossmatch([event],[{
            "rcept_no":"20161017000001","corp_code":"00123456",
            "stock_code":"005930","rcept_dt":"20161017",
            "report_nm":"미검증"}],month)
        self.assertEqual(found["ticker_date_nearby_event_count"],1)
        event["last_day"]="2016-10-31"
        found=cross._crossmatch([event],[{
            "rcept_no":"20161001000001","corp_code":"00123456",
            "stock_code":"005930","rcept_dt":"20161001",
            "report_nm":"미검증"}],month)
        self.assertEqual(found["risk_events_within_completed_month"],1)
        self.assertEqual(found["ticker_date_nearby_event_count"],0)
        self.assertTrue(found["coverage_gaps_not_negative_evidence"])
        self.assertFalse(found["identity_or_corporate_action_verified"])

    def test_workflow_is_private_and_derived_only_with_untrusted_event_gate(self):
        base=Path(__file__).resolve().parents[1]
        workflow=(base/".github/workflows/v3-private-dart-krx-event-crosscheck.yml").read_text()
        receipts=(base/".github/workflows/v3-dart-historical-event-receipts.yml").read_text()
        reviewer=(base/".github/workflows/v3-krx-derived-corporate-risk-incremental.yml").read_text()
        for parent in (receipts,reviewer):
            name=parent.splitlines()[0].removeprefix("name: ")
            self.assertIn("      - "+name+"\n",workflow)
        self.assertIn("workflow_run.conclusion == 'success'",workflow)
        self.assertIn("workflow_run.head_branch == 'main'",workflow)
        self.assertIn("workflow_run.head_repository.full_name == github.repository",workflow)
        self.assertIn("group: v3-dart-private-historical-event-receipts",workflow)
        self.assertIn("cancel-in-progress: false",workflow)
        self.assertIn("contents: read",workflow)
        self.assertIn("persist-credentials: false",workflow)
        self.assertNotIn("DART_API_KEY:",workflow)
        self.assertNotIn("KRX_API_KEY:",workflow)
        self.assertNotIn("git push",workflow)
        self.assertIn("default: dry_run",workflow)
        self.assertIn("cron: '30 9 * * 1-5'",
                      (base/".github/workflows/full-market-batches.yml").read_text())


if __name__=="__main__":
    unittest.main()
