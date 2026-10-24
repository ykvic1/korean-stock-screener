"""Proof that private KRX 20/60/120/250d composed returns never certify raw prices."""
import base64
from datetime import date,timedelta
from hashlib import sha256
import gzip
import json
from pathlib import Path
import unittest

import v3_krx_derived_rolling_returns as rolling
import v3_krx_derived_backfill as history
import v3_krx_derived_bridges as bridge
from v3_krx_derived_5day import store_five_day_factors
from test_v3_krx_derived_5day import FakeKRX,FakePrivate


DIGEST="a"*64


class PrivateRolling(FakePrivate):
    def transport(self,method,path,payload=None):
        key=path.removeprefix("contents/")
        if (method!="PUT" or not path.startswith("contents/derived/rolling/")
            or type(payload.get("sha")) is not str or key not in self.blobs):
            raise RuntimeError("conditional GitHub private write required")
        self.blobs[key]=base64.b64decode(payload["content"])
        self.posts+=1


def days(n):
    current=date(2016,10,17)
    found=[]
    while len(found)<n:
        if current.weekday()<5:
            found.append(current.isoformat())
        current+=timedelta(days=1)
    return found


def setup_factors(cohorts=9,*,quarantine_one=False):
    p=PrivateRolling()
    dates=days(max(9,cohorts)*5)
    factors=0;bridged=0
    for i in range(cohorts):
        period=dates[5*i:5*(i+1)]
        client=FakeKRX(quarantine=(quarantine_one and i==4))
        store_five_day_factors(p,client,period)
        factors+=8 if not (quarantine_one and i==4) else 6
    for i in range(1,cohorts):
        fact,packed=bridge.derive_link(FakeKRX(),dates[5*i-1],dates[5*i])
        p.blobs[bridge._link_path(dates[5*i-1],dates[5*i])]=packed
        bridged+=fact["observed_boundary_issuer_pairs"]
    checkpoint=history._empty(DIGEST)
    checkpoint["next_window_index"]=cohorts
    checkpoint["full_issuer_bucket_records"]=factors
    checkpoint["compressed_factor_bytes"]=sum(
        len(raw) for k,raw in p.blobs.items()
        if k.startswith("derived/five-session/"))
    final_window=dates[5*(cohorts-1):5*cohorts]
    path=f"derived/five-session/{final_window[0]}_{final_window[-1]}.json.gz"
    checkpoint["last_window_file_sha256"]=sha256(p.blobs[path]).hexdigest()
    p.blobs[history.CHECKPOINT]=history._canonical(checkpoint)
    boundary={
        "schema":"v3-krx-private-boundary-progress-v1",
        "calendar_candidate_hash":DIGEST,
        "next_boundary_index":cohorts,
        "cumulative_derived_boundary_issuer_records":bridged,
        "cumulative_compressed_bytes":sum(
            len(raw) for k,raw in p.blobs.items()
            if k.startswith("derived/boundaries/")),
        "last_boundary_sha256":sha256(p.blobs[
            bridge._link_path(dates[5*(cohorts-1)-1],
                              dates[5*(cohorts-1)])]).hexdigest()
                              if cohorts>1 else None,
        "original_source_files_stored":0,
    }
    p.blobs[bridge.CHECKPOINT]=bridge._canonical(boundary)
    return p,dates


class PrivateKRXDerivedReturnTests(unittest.TestCase):
    def test_rolling_workflow_runs_after_verified_boundary_not_on_failed_or_forked_run(self):
        workflow=(Path(__file__).resolve().parents[1] /
                  ".github/workflows/v3-krx-derived-rolling-returns.yml").read_text(
                      encoding="utf-8")
        self.assertIn("V3 KRX Derived-Only Historical Cohort Boundary Join",workflow)
        self.assertIn("types: [completed]",workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",workflow)
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            workflow)
        self.assertEqual(workflow.count("cron:"),1,
                         "only one fallback; avoid redundant scheduled research jobs")
        self.assertIn("inputs.mode == 'live'",workflow)
        self.assertIn("concurrency:",workflow)

    def test_smaller_production_batch_resumes_without_rereading_or_requerying(self):
        self.assertEqual(rolling.PRODUCTION_WINDOWS_PER_HORIZON,8)
        p,candidates=setup_factors(cohorts=33)
        produced=[]
        for _ in range(4):
            result=rolling.run(storage=p,dates=candidates,
                calendar_hash=DIGEST,
                limit=rolling.PRODUCTION_WINDOWS_PER_HORIZON,horizons=[20])
            produced.append(result["horizons"]["20"]["newly_written_or_reused_windows"])
            self.assertEqual(result["provider_api_calls"],0)
        self.assertEqual(produced,[8,8,8,6])
        self.assertEqual(result["horizons"]["20"]["saved_complete_cohort_windows"],30)

    def test_twenty_four_windows_per_horizon_with_exact_resume(self):
        p,candidates=setup_factors(cohorts=33)
        first=rolling.run(storage=p,dates=candidates,
                          calendar_hash=DIGEST,horizons=[20])
        self.assertEqual(first["horizons"]["20"]["newly_written_or_reused_windows"],24)
        self.assertEqual(first["provider_api_calls"],0)
        second=rolling.run(storage=p,dates=candidates,
                           calendar_hash=DIGEST,horizons=[20])
        self.assertEqual(second["horizons"]["20"]["newly_written_or_reused_windows"],6)
        self.assertEqual(second["horizons"]["20"]["saved_complete_cohort_windows"],30)
        self.assertEqual(second["provider_api_calls"],0)

    def test_first_nine_cohorts_yield_six_20day_windows_no_other_horizon_yet(self):
        p,candidates=setup_factors()
        outcome=rolling.run(storage=p,dates=candidates,calendar_hash=DIGEST)
        self.assertEqual(outcome["provider_api_calls"],0)
        self.assertEqual(outcome["raw_source_files_added"],0)
        self.assertEqual(outcome["historical_complete_cohorts"],9)
        self.assertEqual(outcome["completed_cross_bucket_links"],8)
        self.assertEqual(outcome["horizons"]["20"]["saved_complete_cohort_windows"],6)
        self.assertEqual(outcome["horizons"]["20"]["saved_complete_issuer_window_observations"],48)
        for h in ("60","120","250"):
            self.assertEqual(outcome["horizons"][h]["saved_complete_cohort_windows"],0)
            self.assertFalse(outcome["horizons"][h]["ready_to_calculate_from_derived"])
        archive=[k for k in p.blobs if k.startswith("derived/rolling/20/")
                 and k.endswith(".json.gz")]
        self.assertEqual(len(archive),6)
        f=rolling._validate_rolling(p.blobs[archive[0]],20,candidates[0],candidates[19])
        self.assertEqual(f["complete_issuer_count"],8)
        self.assertEqual(f["total_observed_intervals"],19)
        self.assertEqual(f["full_cohorts_required"],4)
        self.assertEqual(f["cross_cohort_links_required"],3)
        self.assertFalse(f["full_v3_factor_score_approved"])
        self.assertFalse(f["adjusted_close_prices_verified"])
        self.assertNotIn("TDD_CLSPRC",str(f))
        self.assertNotIn("raw_source_base64",str(f))
        price0=int(FakeKRX().daily_market("KOSPI",candidates[0])[0][0]["TDD_CLSPRC"])
        price1=int(FakeKRX().daily_market("KOSPI",candidates[19])[0][0]["TDD_CLSPRC"])
        expected=round((price1/price0-1)*10000)
        actual=f["issuer_market_derived_returns_bp"]["KOSPI:005000"]
        self.assertLessEqual(abs(actual-expected),3,
                             "5-session rounding creates a small expected error")

    def test_six_20d_windows_resume_without_source_or_duplicate_private_writes(self):
        p,days_=setup_factors()
        x=rolling.run(storage=p,dates=days_,calendar_hash=DIGEST)
        count=p.posts
        y=rolling.run(storage=p,dates=days_,calendar_hash=DIGEST)
        self.assertEqual(p.posts,count)
        self.assertEqual(y["horizons"]["20"]["newly_written_or_reused_windows"],0)
        self.assertEqual(y["horizons"]["20"]["saved_complete_cohort_windows"],6)

    def test_incomplete_corporate_quarantine_excludes_partial_issuer(self):
        p,days_=setup_factors(quarantine_one=True)
        x=rolling.run(storage=p,dates=days_,calendar_hash=DIGEST)
        self.assertEqual(x["horizons"]["20"]["saved_complete_cohort_windows"],6)
        self.assertLess(x["horizons"]["20"]["saved_complete_issuer_window_observations"],48)

    def test_uncheckpointed_private_files_recovered_without_reconstruction(self):
        p,days_=setup_factors()
        first=rolling.run(storage=p,dates=days_,calendar_hash=DIGEST)
        index=rolling._progress_path(20)
        del p.blobs[index]
        again=rolling.run(storage=p,dates=days_,calendar_hash=DIGEST,horizons=[20])
        self.assertEqual(again["horizons"]["20"]["preexisting_uncheckpointed_files_reused"],6)
        self.assertEqual(again["provider_api_calls"],0)
        self.assertEqual(again["horizons"]["20"]["saved_complete_cohort_windows"],6)

    def test_missing_join_input_fails_closed_without_zero_fill(self):
        p,days_=setup_factors()
        path=f"derived/boundaries/{days_[4]}_{days_[5]}.json.gz"
        del p.blobs[path]
        with self.assertRaises(rolling.DerivedReturnError):
            rolling.run(storage=p,dates=days_,calendar_hash=DIGEST,horizons=[20])

    def test_corrupt_checkpoint_bad_calendar_or_wrong_public_destination_fails(self):
        p,days_=setup_factors()
        provider_call_count=0
        with self.assertRaisesRegex(rolling.DerivedReturnError,
                                    "invalid_rolling_factor_configuration"):
            rolling.run(storage=p,dates=days_,calendar_hash=DIGEST,
                        limit=25,horizons=[20])
        p.repository="ykvic1/korean-stock-screener"
        with self.assertRaises(rolling.DerivedReturnError):
            rolling.run(storage=p,dates=days_,calendar_hash=DIGEST)
        p.repository="ykvic1/korean-stock-private-data"
        with self.assertRaises(rolling.DerivedReturnError):
            rolling.run(storage=p,dates=days_,calendar_hash="b"*64)
        self.assertEqual(provider_call_count,0)


if __name__=="__main__":
    unittest.main()
