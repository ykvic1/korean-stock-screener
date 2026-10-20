"""KRX corporate-action risk: observed price discontinuity ≠ verified action."""
import gzip
import json
import unittest
from unittest.mock import patch

import v3_krx_derived_corporate_action_risk as risk
from test_v3_krx_derived_rolling_returns import setup_factors,DIGEST


def rewrite_gzip(obj):
    return gzip.compress(json.dumps(obj,sort_keys=True,separators=(",",":"),
                                    allow_nan=False).encode(),mtime=0)


class RealShapedCorporateActionRiskTests(unittest.TestCase):
    def test_contiguous_nine_cohorts_offline_zero_krx_and_private_result(self):
        store,days=setup_factors()
        original_posts=store.posts
        out=risk.collect(storage=store,dates=days,checksum=DIGEST)
        self.assertEqual(out["source_cohorts"],9)
        self.assertEqual(out["reviewed_cross_bucket_links"],8)
        self.assertEqual(out["observed_issuer_bucket_factors"],72)
        self.assertEqual(out["observed_issuer_boundary_links"],64)
        self.assertEqual(out["source_provider_calls"],0)
        self.assertEqual(out["raw_prices_saved"],0)
        self.assertFalse(out["corporate_actions_confirmed"])
        self.assertFalse(out["v3_score_certified"])
        self.assertEqual(store.posts,original_posts+1)
        path=risk._file_end(days,9)
        saved,_=store.get_content(path)
        checked=risk._read_existing(saved,days[44],9)
        self.assertFalse(checked["corporate_actions_verified"])
        self.assertFalse(checked["unflagged_stocks_action_free_certified"])
        self.assertNotIn("TDD_CLSPRC",str(checked))
        self.assertNotIn("ACC_TRDVOL",str(checked))

    def test_injected_large_bucket_move_is_flag_not_split_diagnosis(self):
        store,days=setup_factors()
        path=f"derived/five-session/{days[0]}_{days[4]}.json.gz"
        original=json.loads(gzip.decompress(store.blobs[path]))
        original["derived_issuer_factors"]["KOSPI:005000"][
            "intrabucket_four_interval_return_bp"]=-4200
        store.blobs[path]=rewrite_gzip(original)
        out=risk.collect(storage=store,dates=days,checksum=DIGEST)
        self.assertEqual(out["risk_flags"],1)
        observed=risk._read_existing(store.blobs[risk._file_end(days,9)],days[44],9)
        self.assertEqual(observed["flagged_issuer_periods"][0]["kind"],
                         "large_four_interval_bucket_change")
        self.assertFalse(observed["flagged_issuer_periods"][0][
            "exchange_corporate_event_verified"])

    def test_injected_large_boundary_logmove_is_not_zero_filled(self):
        store,days=setup_factors()
        first=risk._link_path(days[4],days[5])
        obj=json.loads(gzip.decompress(store.blobs[first]))
        obj["derived_logreturn_by_market_issuer"]["KOSDAQ:015000"][
            "cross_bucket_log_return_e9"]=330_000_000
        store.blobs[first]=rewrite_gzip(obj)
        result=risk.collect(storage=store,dates=days,checksum=DIGEST)
        self.assertEqual(result["risk_flags"],1)
        f=risk._read_existing(store.blobs[risk._file_end(days,9)],days[44],9)
        self.assertEqual(f["flagged_issuer_periods"][0]["kind"],
                         "large_one_interval_cohort_boundary")
        self.assertEqual(f["cohorts_touching_one_or_more_price_flags"],2)

    def test_repeat_after_success_reads_existing_and_creates_no_file(self):
        store,days=setup_factors()
        risk.collect(storage=store,dates=days,checksum=DIGEST)
        posts=store.posts
        second=risk.collect(storage=store,dates=days,checksum=DIGEST)
        self.assertEqual(second["status"],"reused_existing_private_quality_audit")
        self.assertEqual(second["source_provider_calls"],0)
        self.assertEqual(store.posts,posts)

    def test_missing_link_aborts_before_bogus_all_clear(self):
        store,days=setup_factors()
        del store.blobs[risk._link_path(days[4],days[5])]
        with self.assertRaisesRegex(risk.CorporateRiskError,
                                    "missing_private_derived_cross_boundary"):
            risk.collect(storage=store,dates=days,checksum=DIGEST)
        self.assertFalse(any("/quality/" in k for k in store.blobs))

    def test_bad_calendar_or_public_repo_never_mutates_evidence(self):
        store,days=setup_factors()
        store.repository="ykvic1/korean-stock-screener"
        with self.assertRaises(risk.CorporateRiskError):
            risk.collect(storage=store,dates=days,checksum=DIGEST)
        store.repository="ykvic1/korean-stock-private-data"
        with self.assertRaisesRegex(risk.CorporateRiskError,
                                    "historical_derived_checkpoint_untrusted"):
            risk.collect(storage=store,dates=days,checksum="f"*64)
        self.assertFalse(any("/quality/" in k for k in store.blobs))

    def test_corrupted_existing_audit_is_never_overwritten(self):
        store,days=setup_factors()
        store.blobs[risk._file_end(days,9)]=b"garbage"
        total=store.posts
        with self.assertRaisesRegex(risk.CorporateRiskError,
                                    "private_quality_evidence_invalid"):
            risk.collect(storage=store,dates=days,checksum=DIGEST)
        self.assertEqual(store.posts,total)


if __name__=="__main__":
    unittest.main()
