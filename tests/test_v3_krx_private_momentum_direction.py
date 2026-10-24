"""No invented score from privacy-safe real KRX direction observations."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import unittest

import v3_krx_private_momentum_direction as direction
import v3_krx_derived_bridges as bridge
from test_v3_krx_derived_rolling_returns import DIGEST,setup_factors


class PrivateDirectionTests(unittest.TestCase):
    def test_20_60_120_intervals_are_exactly_buckets_plus_bridges(self):
        storage,days=setup_factors(cohorts=25)
        for h in (20,60,120):
            data,packed=direction.derive_window(storage,days,h,25)
            self.assertEqual(data["total_close_to_close_intervals"],h-1)
            self.assertEqual(data["source_cross_cohort_links"],h//5-1)
            self.assertEqual(data["vendor_api_calls"],0)
            self.assertFalse(data["live_momentum_extra_seven_points_approved"])
            self.assertEqual(data["complete_issuer_count"],8)
            self.assertEqual(
                direction.validate_saved(packed,h,data["window_start"],
                                         data["window_end"]),data)
            for row in data["issuer_market_private_direction_counts"].values():
                self.assertEqual(
                    row["rising_close_intervals"]+
                    row["flat_or_down_quantized_intervals"],h-1)
                if h==120:
                    mid=row["rising_close_intervals"]-(
                        row["early_60session_rising_intervals"]+
                        row["late_60session_rising_intervals"])
                    self.assertIn(mid,(0,1))

    def test_rise_count_matches_only_real_4_interval_and_boundary_signs(self):
        storage,days=setup_factors(cohorts=25)
        obs,_=direction.derive_window(storage,days,20,25)
        code="KOSPI:005000"
        self.assertIn(code,obs["issuer_market_private_direction_counts"])
        blocks=[direction._read_factor(storage,days,i)[code]
                for i in range(21,25)]
        links=[direction._read_bridge(storage,days,i)[code]
               for i in range(22,25)]
        actual=sum(x["rising_close_intervals"] for x in blocks)+sum(
            x["cross_bucket_log_return_e9"]>0 for x in links)
        self.assertEqual(
            obs["issuer_market_private_direction_counts"][code]
               ["rising_close_intervals"],actual)
        self.assertNotEqual(obs["total_close_to_close_intervals"],20,
                            "20 sessions have 19 close-to-close intervals")

    def test_exact_private_tail_resume_requires_no_krx_or_rewriting(self):
        storage,days=setup_factors(cohorts=25)
        before=storage.posts
        outcome=direction.run(storage=storage,dates=days,calendar_hash=DIGEST)
        self.assertEqual(outcome["provider_api_calls"],0)
        self.assertFalse(outcome["public_issuer_values_written"])
        self.assertEqual(outcome["approved_extra_momentum_points"],0)
        self.assertTrue(all(x["private_immutable_created"] for x
                            in outcome["horizons"].values()))
        after=storage.posts
        self.assertEqual(after-before,3)
        repeat=direction.run(storage=storage,dates=days,calendar_hash=DIGEST)
        self.assertTrue(all(not x["private_immutable_created"] for x
                            in repeat["horizons"].values()))
        self.assertEqual(storage.posts,after)

    def test_corrupt_positive_counts_block_before_private_upload(self):
        storage,days=setup_factors(cohorts=25)
        first=days[5*21];last=days[5*22-1]
        path=f"derived/five-session/{first}_{last}.json.gz"
        doc=json.loads(gzip.decompress(storage.blobs[path]))
        first_code=next(iter(doc["derived_issuer_factors"]))
        doc["derived_issuer_factors"][first_code]["rising_close_intervals"]=5
        storage.blobs[path]=gzip.compress(json.dumps(doc).encode("utf8"))
        before=storage.posts
        with self.assertRaises(direction.DirectionEvidenceError):
            direction.run(storage=storage,dates=days,calendar_hash=DIGEST,
                          horizons=[20])
        self.assertEqual(storage.posts,before)

    def test_fake_approval_or_issuer_count_is_rejected(self):
        storage,days=setup_factors(cohorts=25)
        report,blob=direction.derive_window(storage,days,20,25)
        report["live_momentum_extra_seven_points_approved"]=True
        fake=gzip.compress(json.dumps(report).encode("utf8"))
        with self.assertRaises(direction.DirectionEvidenceError):
            direction.validate_saved(fake,20,report["window_start"],
                                     report["window_end"])
        report["live_momentum_extra_seven_points_approved"]=False
        report["complete_issuer_count"]+=1
        fake=gzip.compress(json.dumps(report).encode("utf8"))
        with self.assertRaises(direction.DirectionEvidenceError):
            direction.validate_saved(fake,20,report["window_start"],
                                     report["window_end"])
        self.assertNotIn(b'TDD_CLSPRC',blob)

    def test_missing_bridge_checkpoint_does_not_fabricate(self):
        storage,days=setup_factors(cohorts=25)
        storage.blobs.pop(bridge.CHECKPOINT)
        output=direction.run(storage=storage,dates=days,calendar_hash=DIGEST)
        self.assertEqual(output["approved_extra_momentum_points"],0)
        self.assertEqual(output["status"],"waiting_for_verified_private_bridges")

    def test_reject_non_private_store_and_unverified_run_dispatch(self):
        storage,days=setup_factors(cohorts=25)
        storage.repository="somebody/public"
        with self.assertRaises(direction.DirectionEvidenceError):
            direction.run(storage=storage,dates=days,calendar_hash=DIGEST)
        yml=(Path(__file__).resolve().parents[1]/
             ".github/workflows/v3-krx-private-momentum-direction.yml").read_text(
                  encoding="utf8")
        self.assertIn("V3 KRX Private Derived-Only 20/60/120/250-Session Return Research",yml)
        self.assertIn("github.event.workflow_run.conclusion == 'success'",yml)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",yml)
        self.assertIn("inputs.mode == 'live'",yml)
        self.assertNotIn("KRX_API_KEY",yml)
        self.assertNotIn("DART_API_KEY",yml)
        self.assertIn("cancel-in-progress: false",yml)
        self.assertIn("python -m unittest discover",yml)


if __name__=="__main__":
    unittest.main()
