"""Synthetic KRX historical five-session derived-only resume regression."""
import hashlib
import base64
import json
import unittest

from v3_krx_derived_backfill import collect,DerivedBackfillError,_manifest,SCHEMA
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
        repo=FakePrivate()
        provider=FakeKRX(fail_market=("KOSDAQ","2026-10-21"))
        with self.assertRaises(Exception):
            collect(storage=repo,client=provider,dates=DATES,
                    checksum=DIGEST,windows=2)
        self.assertNotIn("derived/historical-checkpoint.json",repo.blobs)
        self.assertIn("derived/five-session/2026-10-12_2026-10-16.json.gz",repo.blobs)
        fine=FakeKRX()
        report=collect(storage=repo,client=fine,dates=DATES,
                       checksum=DIGEST,windows=2)
        self.assertEqual(report["completed_five_session_cohorts"],2)
        self.assertEqual(report["current_run_resumed_existing_windows"],1)
        self.assertEqual(report["current_run_api_calls"],10)
        self.assertEqual(len(fine.calls),10)

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


if __name__=="__main__":
    unittest.main()
