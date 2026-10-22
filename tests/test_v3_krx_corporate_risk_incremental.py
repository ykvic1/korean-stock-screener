"""Append-only KRX derived action-risk private cursor and no-repeat test."""
import base64
import gzip
import json
import unittest

import v3_krx_derived_corporate_action_risk as baseline
import v3_krx_corporate_risk_incremental as inc
from test_v3_krx_derived_rolling_returns import setup_factors,DIGEST


def archive(cohorts=9):
    p,days=setup_factors(cohorts)
    # Simulate GitHub's existing-Contents conditional PUT, no API network.
    def update(method,path,payload=None):
        key=path.removeprefix("contents/")
        if (method!="PUT" or key!=inc.CHECKPOINT or key not in p.blobs
            or type(payload.get("sha")) is not str):
            raise RuntimeError("private conditional PUT contract violated")
        p.blobs[key]=base64.b64decode(payload["content"])
        p.posts+=1
    p.transport=update
    return p,days


def seed(p,days):
    # Existing real deployment already has this from first 2016 nine cohorts;
    # tests create a synthetic analog so public CI never reads real private data.
    tmp,dates=archive(9)
    baseline.collect(storage=tmp,dates=dates,checksum=DIGEST)
    name=baseline._file_end(dates,9)
    p.blobs[name]=tmp.blobs[name]


class IncrementalCorporateActionRiskTests(unittest.TestCase):
    def test_sixteen_derived_cohorts_per_run_and_partial_followup(self):
        p,days=archive(27)
        seed(p,days)
        inc.run(storage=p,days=days,digest=DIGEST)
        first=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(first["new_verified_cohorts_examined"],16)
        self.assertEqual(first["completed_quality_cohorts"],25)
        self.assertEqual(first["source_provider_calls"],0)
        self.assertEqual(first["raw_prices_persisted"],0)
        followup=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(followup["new_verified_cohorts_examined"],2)
        self.assertEqual(followup["completed_quality_cohorts"],27)
        self.assertEqual(followup["source_provider_calls"],0)
        self.assertEqual(followup["raw_prices_persisted"],0)
        with self.assertRaisesRegex(inc.IncrementalRiskError,
                                    "invalid_private_incremental_quality_request"):
            inc.run(storage=p,days=days,digest=DIGEST,limit=17)

    def test_adopt_real_shaped_nine_cohort_baseline_zero_api_and_no_new_chunk(self):
        p,days=archive(9)
        seed(p,days)
        result=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(result["status"],
                         "verified_baseline_adopted_without_provider_requests")
        self.assertEqual(result["cohorts_examined"],9)
        self.assertEqual(result["source_provider_calls"],0)
        self.assertEqual(result["new_quality_files"],0)
        self.assertIn(inc.CHECKPOINT,p.blobs)
        progress=json.loads(p.blobs[inc.CHECKPOINT])
        self.assertEqual(progress["next_cohort_index"],9)
        self.assertEqual(progress["cumulative_issuer_buckets_examined"],72)
        self.assertEqual(progress["cumulative_issuer_bridges_examined"],64)

    def test_new_tenth_cohort_reads_only_new_sources_and_idempotent_replay(self):
        p,days=archive(10)
        seed(p,days)
        inc.run(storage=p,days=days,digest=DIGEST)
        first=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(first["status"],"incremental_price_discontinuity_screen_saved")
        self.assertEqual(first["new_verified_cohorts_examined"],1)
        self.assertEqual(first["completed_quality_cohorts"],10)
        self.assertEqual(first["source_provider_calls"],0)
        self.assertEqual(first["raw_prices_persisted"],0)
        self.assertEqual(first["new_quality_files"],1)
        chunk=inc._delta_path(days,9,10)
        report=inc._validate_delta(p.blobs[chunk],days=days,start=9,end=10)
        self.assertEqual(report["issuer_bucket_factors_examined"],8)
        self.assertEqual(report["issuer_cross_boundary_factors_examined"],8)
        posts=p.posts
        next_=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(next_["status"],"waiting_for_new_verified_derived_cohorts")
        self.assertEqual(p.posts,posts)

    def test_new_big_change_is_flagged_but_action_not_certified(self):
        p,days=archive(10)
        seed(p,days)
        sourcepath=f"derived/five-session/{days[45]}_{days[49]}.json.gz"
        obj=json.loads(gzip.decompress(p.blobs[sourcepath]))
        obj["derived_issuer_factors"]["KOSPI:005000"][
            "intrabucket_four_interval_return_bp"]=6500
        p.blobs[sourcepath]=gzip.compress(json.dumps(obj).encode(),mtime=0)
        inc.run(storage=p,days=days,digest=DIGEST)
        result=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(result["new_flags"],1)
        v=inc._validate_delta(p.blobs[inc._delta_path(days,9,10)],
                              days=days,start=9,end=10)
        self.assertEqual(v["flags"][0]["type"],"four_interval_change")
        self.assertFalse(v["corporate_action_certified"])
        self.assertFalse(v["v3_trade_score_certified"])

    def test_incomplete_bridge_stops_at_last_verified_cohort_without_fabricating(self):
        p,days=archive(10)
        seed(p,days)
        # Bridge evidence covers only the first 9, even though 10 factor bins.
        b=json.loads(p.blobs["derived/boundaries-checkpoint.json"])
        b["next_boundary_index"]=9
        previous=days[39],days[40]
        from hashlib import sha256
        from v3_krx_derived_bridges import _link_path
        b["last_boundary_sha256"]=sha256(p.blobs[_link_path(*previous)]).hexdigest()
        p.blobs["derived/boundaries-checkpoint.json"]=json.dumps(b).encode()
        adopted=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(adopted["cohorts_examined"],9)
        paused=inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(paused["status"],"waiting_for_new_verified_derived_cohorts")
        self.assertFalse(any(k.startswith("derived/quality/batches/") for k in p.blobs))

    def test_mutated_quality_checkpoint_rejected_without_overwrite(self):
        p,days=archive(9)
        seed(p,days)
        inc.run(storage=p,days=days,digest=DIGEST)
        progress=json.loads(p.blobs[inc.CHECKPOINT])
        progress["calendar_candidate_hash"]="f"*64
        p.blobs[inc.CHECKPOINT]=json.dumps(progress).encode()
        old_posts=p.posts
        with self.assertRaisesRegex(inc.IncrementalRiskError,
                                    "invalid_private_incremental_quality_checkpoint"):
            inc.run(storage=p,days=days,digest=DIGEST)
        self.assertEqual(p.posts,old_posts)

    def test_missing_baseline_never_invents_nine_existing_cohorts(self):
        p,days=archive(9)
        with self.assertRaisesRegex(inc.IncrementalRiskError,
                                    "missing_verified_nine_cohort_baseline"):
            inc.run(storage=p,days=days,digest=DIGEST)
        self.assertFalse(any(k.startswith("derived/quality/") for k in p.blobs))


if __name__=="__main__":
    unittest.main()
