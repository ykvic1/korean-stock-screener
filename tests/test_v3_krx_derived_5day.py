"""Bounded synthetic KRX provider and private Git factor archive regression."""
import json
import unittest
from unittest.mock import patch
from unittest.mock import patch

import v3_krx_derived_5day as d


def fake_rows(market,day,*,invalid=False):
    entries=[]
    for k in range(4):
        code=f"{k+5000:06d}" if market=="KOSPI" else f"{k+15000:06d}"
        date_n=int(day[-2:])
        close=(10000+k*230+date_n*10)
        entries.append({
            "BAS_DD":day.replace("-",""),"ISU_CD":code,
            "TDD_OPNPRC":str(close-10),
            "TDD_HGPRC":str(close+200),
            "TDD_LWPRC":str(close-200),
            "TDD_CLSPRC":str(close),
            "ACC_TRDVOL":str(10000+k*250+date_n*70),
        })
    if invalid:
        entries[3]["TDD_CLSPRC"]="0"
    return entries


class FakeKRX:
    def __init__(self,*,fail_market=None,quarantine=False):
        self.calls=[]
        self.fail_market=fail_market
        self.quarantine=quarantine

    def daily_market(self,market,day):
        self.calls.append((market,day))
        if self.fail_market == (market,day):
            raise RuntimeError("untrusted-api-token-MUST-NEVER-PRINT")
        rows=fake_rows(market,day,invalid=self.quarantine)
        return rows,json.dumps({"OutBlock_1":rows},separators=(",",":")).encode()


class FakePrivate:
    def __init__(self):
        self.repository="ykvic1/korean-stock-private-data"
        self.blobs={}
        self.posts=0
        self.checked=False
    def verify_private(self):
        self.checked=True
    def get_content(self,path):
        return self.blobs.get(path), "sha" if path in self.blobs else None
    def put_new(self,path,blob,message):
        if not self.checked or path in self.blobs:
            raise RuntimeError("private write denied")
        self.posts+=1
        self.blobs[path]=blob


class FiveDayDerivativeTests(unittest.TestCase):
    def test_complete_real_shape_ten_market_calls_no_raw_volume_or_ohlcv(self):
        client=FakeKRX()
        result,blob,total=d.derive(client)
        self.assertEqual(len(client.calls),10)
        self.assertEqual(result["source_api_calls"],10)
        self.assertEqual(result["complete_five_session_issuer_market_pairs"],8)
        self.assertEqual(len(result["market_date_coverage"]),10)
        self.assertEqual(len(result["derived_issuer_factors"]),8)
        self.assertGreater(total,len(blob))
        self.assertTrue(result["intrabucket_return_missing_four_intervals_across_boundaries"])
        self.assertTrue(result["not_full_20_60_120_250_session_history"])
        self.assertFalse(result["source_ohlcv_persisted"])
        self.assertFalse(result["v3_six_pillar_full_scoring_allowed"])
        self.assertNotIn("TDD_CLSPRC",json.dumps(result))
        self.assertNotIn("TDD_OPNPRC",json.dumps(result))
        self.assertNotIn("ACC_TRDVOL",json.dumps(result))
        self.assertNotIn("raw_source_base64",json.dumps(result))
        self.assertTrue(d.validate_derived_blob(blob,d.FIXED_FIRST_SAMPLE[0],
                                                  d.FIXED_FIRST_SAMPLE[-1]))
        f=result["derived_issuer_factors"]["KOSPI:005000"]
        self.assertEqual(f["observed_sessions"],5)
        self.assertIsNotNone(f["intrabucket_four_interval_return_bp"])

    def test_partial_data_quarantine_not_fabricated_five_day(self):
        client=FakeKRX(quarantine=True)
        result,blob,_=d.derive(client)
        self.assertEqual(result["complete_five_session_issuer_market_pairs"],6)
        self.assertTrue(all(x["unpriced_quarantined"]==1
                            for x in result["market_date_coverage"].values()))
        self.assertNotIn("KOSPI:005003",result["derived_issuer_factors"])
        self.assertNotIn("KOSDAQ:015003",result["derived_issuer_factors"])

    def test_private_immutable_upload_readback_once_no_second_market_fetch(self):
        private=FakePrivate()
        first=FakeKRX()
        report=d.store_five_day_factors(private,first)
        self.assertEqual(report["status"],"persisted_and_readback_verified_derived")
        self.assertEqual(report["source_api_calls"],10)
        self.assertFalse(report["raw_source_retained"])
        self.assertEqual(private.posts,1)
        self.assertEqual(len(private.blobs),1)
        self.assertTrue(next(iter(private.blobs)).startswith("derived/five-session/"))
        self.assertLess(report["derived_file_bytes"],report["raw_response_transient_bytes"])
        second=FakeKRX()
        rerun=d.store_five_day_factors(private,second)
        self.assertEqual(rerun["status"],"verified_existing_derived")
        self.assertEqual(rerun["source_api_calls"],0)
        self.assertEqual(second.calls,[])
        self.assertEqual(private.posts,1)

    def test_private_put_get_briefly_missing_never_recollects_or_rewrites(self):
        archive=FakePrivate()
        old_get=archive.get_content
        state={"after_put_misses":2}
        def delayed_get(path):
            if path in archive.blobs and state["after_put_misses"]>0:
                state["after_put_misses"]-=1
                return None,None
            return old_get(path)
        archive.get_content=delayed_get
        provider=FakeKRX()
        with patch.object(d.time,"sleep",return_value=None) as wait:
            result=d.store_five_day_factors(archive,provider)
        self.assertEqual(result["status"],"persisted_and_readback_verified_derived")
        self.assertEqual(len(provider.calls),10)
        self.assertEqual(archive.posts,1)
        self.assertEqual(wait.call_count,2)
        self.assertEqual(state["after_put_misses"],0)

    def test_readback_never_visible_fails_without_duplicate_api_or_put(self):
        archive=FakePrivate()
        prior=archive.get_content
        def never_visible(path):
            if path in archive.blobs:
                return None,None
            return prior(path)
        archive.get_content=never_visible
        provider=FakeKRX()
        with patch.object(d.time,"sleep",return_value=None):
            with self.assertRaisesRegex(d.DerivedSourceError,
                                        "^private_derived_archive_readback_failed$"):
                d.store_five_day_factors(archive,provider)
        self.assertEqual(len(provider.calls),10)
        self.assertEqual(archive.posts,1)
        # Next independent run (orphan source) will use the existing immutable
        # file rather than call official KRX again.
        archive.get_content=prior
        resumed=FakeKRX()
        saved=d.store_five_day_factors(archive,resumed)
        self.assertEqual(saved["status"],"verified_existing_derived")
        self.assertEqual(resumed.calls,[])
        self.assertEqual(archive.posts,1)

    def test_wrong_readback_bytes_are_not_accepted_as_success(self):
        archive=FakePrivate()
        prior=archive.get_content
        def wrong_bytes(path):
            if path in archive.blobs:
                return b"wrong", "sha"
            return prior(path)
        archive.get_content=wrong_bytes
        with patch.object(d.time,"sleep",return_value=None):
            with self.assertRaisesRegex(d.DerivedSourceError,
                                        "private_derived_archive_readback_failed"):
                d.store_five_day_factors(archive,FakeKRX())
        self.assertEqual(archive.posts,1)

    def test_bad_provider_never_uploads_or_reveals_error(self):
        private=FakePrivate()
        provider=FakeKRX(fail_market=("KOSDAQ","2026-10-14"))
        with self.assertRaisesRegex(d.DerivedSourceError,
                                    "^official_market_window_or_validation_failed$") as err:
            d.store_five_day_factors(private,provider)
        self.assertNotIn("untrusted-api-token",str(err.exception))
        self.assertEqual(private.posts,0)
        self.assertEqual(private.blobs,{})

    def test_no_public_repo_upload_even_before_request(self):
        p=FakePrivate()
        p.repository="ykvic1/korean-stock-screener"
        api=FakeKRX()
        with self.assertRaisesRegex(d.DerivedSourceError,
                                    "unexpected_destination"):
            d.store_five_day_factors(p,api)
        self.assertEqual(api.calls,[])

    def test_reject_corrupted_previous_derived_file_no_retry(self):
        p=FakePrivate()
        p.blobs["derived/five-session/2026-10-12_2026-10-16.json.gz"]=b"bad"
        api=FakeKRX()
        with self.assertRaisesRegex(d.DerivedSourceError,
                                    "existing_derived_archive_invalid"):
            d.store_five_day_factors(p,api)
        self.assertEqual(api.calls,[])

    def test_non_five_business_day_windows_never_call_provider(self):
        cases=[
            ["2026-10-12","2026-10-13","2026-10-14","2026-10-15"],
            ["2026-10-12"]*5,
            ["2026-10-10","2026-10-13","2026-10-14","2026-10-15","2026-10-16"]
        ]
        for bad in cases:
            client=FakeKRX()
            with self.assertRaises(d.DerivedSourceError):
                d.derive(client,bad)
            self.assertFalse(client.calls)


if __name__=="__main__":
    unittest.main()
