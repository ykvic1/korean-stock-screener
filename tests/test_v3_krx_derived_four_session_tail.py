"""KRX real-shaped four-session historical remainder, never a fabricated 5d."""
import unittest
from unittest.mock import patch
import v3_krx_derived_four_session_tail as tail
from test_v3_krx_derived_5day import FakeKRX,FakePrivate


DAYS=["2026-10-13","2026-10-14","2026-10-15","2026-10-16"]
WHOLE=["2026-10-12"]+DAYS


class PrivateTail(FakePrivate):
    pass


class FourSessionTests(unittest.TestCase):
    def test_missing_four_not_five_is_separate_partial_record(self):
        provider=FakeKRX()
        obj,blob=tail.derive(provider,DAYS)
        self.assertEqual(len(provider.calls),8)
        self.assertEqual(obj["observed_four_session_issuers"],8)
        self.assertEqual(obj["source_api_calls"],8)
        self.assertEqual(obj["observed_market_date_pairs"],8)
        self.assertEqual(obj["valid_complete_five_day_cohort"],False)
        self.assertFalse(obj["join_20_60_120_250_allowed"])
        self.assertEqual(tail.validate(blob,DAYS)["observed_four_session_issuers"],8)
        self.assertNotIn("TDD_CLSPRC",str(obj))
        self.assertNotIn("ACC_TRDVOL",str(obj))
        self.assertNotIn("raw_source_base64",str(obj))
        self.assertTrue(all(x["observed_return_intervals"]==3
                            for x in obj["per_issuer_partial_factors"].values()))

    def test_real_five_session_aligned_calendar_needs_no_derived_four(self):
        p=PrivateTail()
        provider=FakeKRX()
        out=tail.store(p,provider,WHOLE)
        self.assertEqual(out["status"],"no_four_session_remainder")
        self.assertEqual(out["provider_api_calls"],0)
        self.assertEqual(p.blobs,{})
        self.assertEqual(provider.calls,[])

    def test_private_only_once_repeated_run_no_krx_call(self):
        p=PrivateTail()
        api=FakeKRX()
        # 1 complete 5d segment, followed by 4 historical remainder days.
        dates=["2026-10-06","2026-10-07","2026-10-08",
               "2026-10-09","2026-10-12"]+DAYS
        result=tail.store(p,api,dates)
        self.assertEqual(result["status"],"verified_saved_four_session_tail")
        self.assertEqual(result["provider_api_calls"],8)
        self.assertEqual(result["observed_four_session_issuers"],8)
        self.assertEqual(len(p.blobs),1)
        self.assertTrue(next(iter(p.blobs)).startswith("derived/partial-tail/"))
        self.assertEqual(len(api.calls),8)
        second=FakeKRX()
        repeated=tail.store(p,second,dates)
        self.assertEqual(repeated["status"],"reused_verified_four_session_tail")
        self.assertEqual(repeated["provider_api_calls"],0)
        self.assertEqual(second.calls,[])
        self.assertEqual(p.posts,1)

    def test_provider_failure_never_saves_partial_source(self):
        p=PrivateTail()
        source=FakeKRX(fail_market=("KOSDAQ",DAYS[2]))
        dates=["2026-10-06","2026-10-07","2026-10-08",
               "2026-10-09","2026-10-12"]+DAYS
        with self.assertRaisesRegex(tail.PartialTailError,
                                    "four_session_official_source_or_integrity_failure"):
            tail.store(p,source,dates)
        self.assertEqual(p.posts,0)

    def test_corrupt_old_source_not_recollected(self):
        p=PrivateTail()
        dates=["2026-10-06","2026-10-07","2026-10-08",
               "2026-10-09","2026-10-12"]+DAYS
        p.blobs["derived/partial-tail/2026-10-13_2026-10-16.json.gz"]=b"not gzip"
        provider=FakeKRX()
        with self.assertRaisesRegex(tail.PartialTailError,
                                    "private_four_session_derived_integrity_failure"):
            tail.store(p,provider,dates)
        self.assertEqual(provider.calls,[])

    def test_never_upload_to_public_repo(self):
        p=PrivateTail()
        p.repository="ykvic1/korean-stock-screener"
        provider=FakeKRX()
        dates=["2026-10-06","2026-10-07","2026-10-08",
               "2026-10-09","2026-10-12"]+DAYS
        with self.assertRaises(tail.PartialTailError):
            tail.store(p,provider,dates)
        self.assertEqual(provider.calls,[])

    def test_different_remainder_size_fails_no_misleading_four_day_file(self):
        for n in (6,7,8):
            p=PrivateTail()
            x=FakeKRX()
            r=tail.store(p,x,WHOLE+["2026-10-19","2026-10-20","2026-10-21"][:n-5])
            self.assertEqual(r["status"],"no_four_session_remainder")
            self.assertEqual(x.calls,[])


if __name__=="__main__":
    unittest.main()
