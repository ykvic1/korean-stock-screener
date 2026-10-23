"""Synthetic KRX historical five-session derived-only resume regression."""
import hashlib
import base64
import json
import unittest
from unittest.mock import patch

from v3_krx_derived_backfill import (collect,DerivedBackfillError,
    _manifest,SCHEMA,_safe_source_failure_diagnostics)
from test_v3_krx_derived_5day import FakeKRX,FakePrivate


DATES=["2026-10-12","2026-10-13","2026-10-14",
       "2026-10-15","2026-10-16",
       "2026-10-19","2026-10-20","2026-10-21",
       "2026-10-22","2026-10-23",
       "2026-10-26","2026-10-27"]
DIGEST="1"*64


class FakeHistoryPrivate(FakePrivate):
    def transport(self,method,path,payload=None):
        if method!="PUT" or path!="contents/derived/historical-checkpoint.json":
            raise RuntimeError("wrong private write")
        raw=base64.b64decode(payload["content"])
        if (path.removeprefix("contents/") not in self.blobs
            or payload.get("sha") is None):
            raise RuntimeError("must be existing version")
        self.blobs["derived/historical-checkpoint.json"]=raw
        self.posts+=1


class HistoricalDerivedTests(unittest.TestCase):
    def test_failure_context_is_allowlisted_without_vendor_payload(self):
        from v3_krx_derived_5day import DerivedSourceError
        err=DerivedSourceError("official_market_window_or_validation_failed")
        err.safe_failure_phase="transport"
        err.safe_source_day="2026-05-11"
        err.safe_source_market="KOSPI"
        err.safe_completed_market_calls=0
        err.safe_krx_reason="empty_market_rows"
        err.safe_krx_http_status=200
        report=_safe_source_failure_diagnostics(err)
        self.assertEqual(report["failed_source_day"],"2026-05-11")
        self.assertEqual(report["failed_source_market"],"KOSPI")
        self.assertEqual(report["fully_validated_market_calls_before_failure"],0)
        self.assertEqual(report["krx_allowlisted_failure_reason"],"empty_market_rows")
        self.assertEqual(report["http_status_only"],200)
        forged=DerivedSourceError("official_market_window_or_validation_failed")
        forged.safe_failure_phase="transport"
        forged.safe_source_day="2026-05-11-RAW_SECRET"
        forged.safe_source_market="KOSPI_SECRET"
        forged.safe_completed_market_calls=-1
        forged.safe_krx_reason="RAW_SECRET"
        forged.safe_krx_http_status=999
        report=_safe_source_failure_diagnostics(forged)
        self.assertEqual(report,{"opaque_failure_phase":"transport"})

    def test_real_source_shaped_two_5day_windows_20_calls_and_no_raw(self):
        repo=FakeHistoryPrivate()
        provider=FakeKRX()
        first=collect(storage=repo,client=provider,dates=DATES,
                      checksum=DIGEST,windows=1)
        self.assertEqual(first["completed_five_session_cohorts"],1)
        self.assertEqual(first["current_run_api_calls"],10)
        self.assertEqual(first["historical_session_candidates_covered"],5)
        self.assertFalse(first["verified_20_60_120_250_day_factors"])
        self.assertEqual(first["private_raw_source_files"],0)
        second=collect(storage=repo,client=provider,dates=DATES,
                       checksum=DIGEST,windows=3)
        self.assertEqual(second["current_run_api_calls"],10)
        self.assertEqual(second["completed_five_session_cohorts"],2)
        self.assertEqual(len(provider.calls),20)
        self.assertEqual(len(repo.blobs),3)  # two transformed + tiny checkpoint
        self.assertLess(len(repo.blobs["derived/historical-checkpoint.json"]),1200)
        self.assertEqual(second["source_candidate_sessions"],12)
        self.assertEqual(second["historical_session_candidates_covered"],10)
        self.assertEqual(second["cumulative_stock_cohort_factor_records"],16)
        self.assertTrue(all(k.startswith("derived/") for k in repo.blobs))
        third=collect(storage=repo,client=FakeKRX(),dates=DATES,
                      checksum=DIGEST,windows=2)
        self.assertEqual(third["current_run_api_calls"],0)
        self.assertEqual(third["completed_five_session_cohorts"],2)

    def test_completed_orphan_file_adopted_without_repeat_provider_call(self):
        repo=FakePrivate()
        from v3_krx_derived_5day import store_five_day_factors
        already=store_five_day_factors(repo,FakeKRX(),DATES[:5])
        self.assertEqual(already["source_api_calls"],10)
        client=FakeKRX()
        report=collect(storage=repo,client=client,dates=DATES,
                       checksum=DIGEST,windows=1)
        self.assertEqual(report["current_run_api_calls"],0)
        self.assertEqual(report["current_run_resumed_existing_windows"],1)
        self.assertEqual(client.calls,[])
        self.assertEqual(report["completed_five_session_cohorts"],1)

    def test_deleted_or_mutated_checkpoint_or_wrong_calendar_fails_without_calls(self):
        repo=FakePrivate()
        collect(storage=repo,client=FakeKRX(),dates=DATES,
                checksum=DIGEST,windows=1)
        provider=FakeKRX()
        with self.assertRaises(DerivedBackfillError):
            collect(storage=repo,client=provider,dates=DATES,
                    checksum="2"*64,windows=1)
        self.assertEqual(provider.calls,[])
        raw=repo.blobs["derived/five-session/2026-10-12_2026-10-16.json.gz"]
        repo.blobs["derived/five-session/2026-10-12_2026-10-16.json.gz"]=raw+b"tampered"
        with self.assertRaisesRegex(DerivedBackfillError,"previous_private_derived"):
            collect(storage=repo,client=provider,dates=DATES,
                    checksum=DIGEST,windows=1)
        self.assertEqual(provider.calls,[])

    def test_partial_failed_provider_preserves_last_checkpoint_and_next_run_resumes(self):
        repo=FakeHistoryPrivate()
        provider=FakeKRX(fail_market=("KOSDAQ","2026-10-21"))
        with self.assertRaises(Exception):
            collect(storage=repo,client=provider,dates=DATES,
                    checksum=DIGEST,windows=2)
        self.assertIn("derived/historical-checkpoint.json",repo.blobs)
        self.assertEqual(json.loads(repo.blobs[
            "derived/historical-checkpoint.json"])["next_window_index"],1)
        self.assertIn("derived/five-session/2026-10-12_2026-10-16.json.gz",repo.blobs)
        fine=FakeKRX()
        report=collect(storage=repo,client=fine,dates=DATES,
                       checksum=DIGEST,windows=2)
        self.assertEqual(report["completed_five_session_cohorts"],2)
        self.assertEqual(report["current_run_resumed_existing_windows"],0)
        self.assertEqual(report["current_run_api_calls"],10)
        self.assertEqual(len(fine.calls),10)

    def test_three_window_batch_commits_each_verified_window_before_source_failure(self):
        expanded=DATES[:10]+["2026-10-26","2026-10-27","2026-10-28",
                             "2026-10-29","2026-10-30"]
        repo=FakeHistoryPrivate()
        broken=FakeKRX(fail_market=("KOSDAQ","2026-10-28"))
        with self.assertRaisesRegex(Exception,
                                    "official_market_window_or_validation_failed"):
            collect(storage=repo,client=broken,dates=expanded,
                    checksum=DIGEST,windows=3)
        checkpoint=json.loads(repo.blobs["derived/historical-checkpoint.json"])
        self.assertEqual(checkpoint["next_window_index"],2)
        self.assertEqual(checkpoint["raw_source_file_count"],0)
        self.assertIn(
            "derived/five-session/2026-10-19_2026-10-23.json.gz",
            repo.blobs)
        self.assertNotIn(
            "derived/five-session/2026-10-26_2026-10-30.json.gz",
            repo.blobs)
        resumed=FakeKRX()
        completed=collect(storage=repo,client=resumed,dates=expanded,
                          checksum=DIGEST,windows=2)
        self.assertEqual(completed["completed_five_session_cohorts"],3)
        self.assertEqual(completed["current_run_api_calls"],10)
        self.assertEqual(len(resumed.calls),10)
        self.assertEqual(repo.posts,6)  # 3 transformed + 3 checkpoint writes

    def test_failure_before_any_call_for_unexpected_repository_or_bad_schedule(self):
        private=FakeHistoryPrivate()
        private.repository="ykvic1/korean-stock-screener"
        client=FakeKRX()
        with self.assertRaises(DerivedBackfillError):
            collect(storage=private,client=client,dates=DATES,
                    checksum=DIGEST,windows=1)
        self.assertEqual(client.calls,[])
        good=FakeHistoryPrivate()
        for value in (0,17,True):
            with self.assertRaises(DerivedBackfillError):
                collect(storage=good,client=client,dates=DATES,
                        checksum=DIGEST,windows=value)
        self.assertEqual(client.calls,[])


    def test_checkpoint_put_then_delayed_get_recovers_without_second_krx(self):
        repo=FakeHistoryPrivate()
        old_get=repo.get_content
        misses={"remaining":2}
        def pending(path):
            if path=="derived/historical-checkpoint.json" and path in repo.blobs and misses["remaining"]:
                misses["remaining"]-=1
                return None,None
            return old_get(path)
        repo.get_content=pending
        source=FakeKRX()
        with patch("v3_krx_derived_backfill.retry_clock.sleep",return_value=None) as waits:
            out=collect(storage=repo,client=source,dates=DATES,
                        checksum=DIGEST,windows=1)
        self.assertEqual(out["completed_five_session_cohorts"],1)
        self.assertEqual(out["current_run_api_calls"],10)
        self.assertEqual(len(source.calls),10)
        self.assertEqual(repo.posts,2)  # exactly one derived blob + one checkpoint
        self.assertEqual(waits.call_count,2)
        self.assertEqual(misses["remaining"],0)

    def test_checkpoint_invisible_four_gets_fail_then_next_run_reuses_data(self):
        repo=FakeHistoryPrivate()
        prior=repo.get_content
        def never_visible(path):
            if path=="derived/historical-checkpoint.json" and path in repo.blobs:
                return None,None
            return prior(path)
        repo.get_content=never_visible
        source=FakeKRX()
        with patch("v3_krx_derived_backfill.retry_clock.sleep",return_value=None) as wait:
            with self.assertRaisesRegex(DerivedBackfillError,
                                        "derived_checkpoint_readback_failed"):
                collect(storage=repo,client=source,dates=DATES,
                        checksum=DIGEST,windows=1)
        self.assertEqual(wait.call_count,3)
        self.assertEqual(len(source.calls),10)
        self.assertEqual(repo.posts,2)
        # The successful checkpoint PUT stays in Private storage even when
        # readback timed out. Later run validates and resumes at next cohort,
        # making ZERO additional KRX calls for the first already-done window.
        repo.get_content=prior
        resumed=FakeKRX()
        out=collect(storage=repo,client=resumed,dates=DATES,
                    checksum=DIGEST,windows=1)
        self.assertEqual(out["completed_five_session_cohorts"],2)
        self.assertEqual(out["current_run_api_calls"],10)
        self.assertEqual(len(resumed.calls),10)
        self.assertEqual(repo.posts,4)

    def test_stale_or_wrong_checkpoint_readback_must_not_false_succeed(self):
        repo=FakeHistoryPrivate()
        first=collect(storage=repo,client=FakeKRX(),dates=DATES,
                      checksum=DIGEST,windows=1)
        self.assertEqual(first["completed_five_session_cohorts"],1)
        previous=repo.blobs["derived/historical-checkpoint.json"]
        original=repo.get_content
        misses={"remaining":2}
        def stale_then_fresh(path):
            if (path=="derived/historical-checkpoint.json" and
                repo.blobs.get(path)!=previous and misses["remaining"]):
                misses["remaining"]-=1
                return previous,"sha"
            return original(path)
        repo.get_content=stale_then_fresh
        source=FakeKRX()
        with patch("v3_krx_derived_backfill.retry_clock.sleep",return_value=None) as wait:
            out=collect(storage=repo,client=source,dates=DATES,
                        checksum=DIGEST,windows=1)
        self.assertEqual(out["completed_five_session_cohorts"],2)
        self.assertEqual(wait.call_count,2)
        self.assertEqual(len(source.calls),10)
        self.assertEqual(repo.posts,4)
        repo2=FakeHistoryPrivate()
        actual=repo2.get_content
        def corrupted(path):
            if path=="derived/historical-checkpoint.json" and path in repo2.blobs:
                return b"unexpected checkpoint","sha"
            return actual(path)
        repo2.get_content=corrupted
        with patch("v3_krx_derived_backfill.retry_clock.sleep",return_value=None):
            with self.assertRaisesRegex(DerivedBackfillError,
                                        "derived_checkpoint_readback_failed"):
                collect(storage=repo2,client=FakeKRX(),dates=DATES,
                        checksum=DIGEST,windows=1)
        self.assertEqual(repo2.posts,2)


if __name__=="__main__":
    unittest.main()
