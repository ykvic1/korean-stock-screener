"""Synthetic source-shaped tests for non-reconstructive 5-session join links."""
import base64
from datetime import date,timedelta
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

import v3_krx_derived_bridges as bridge
import v3_krx_derived_backfill as backfill
from v3_krx_derived_5day import store_five_day_factors
from test_v3_krx_derived_5day import FakeKRX,FakePrivate


def days(n=15):
    current=date(2026,10,12)
    result=[]
    while len(result)<n:
        if current.weekday()<5:
            result.append(current.isoformat())
        current+=timedelta(days=1)
    return result


HASH="f"*64


class PrivateFake(FakePrivate):
    def transport(self,method,path,payload=None):
        if (method!="PUT" or path!="contents/derived/boundaries-checkpoint.json"
            or path.removeprefix("contents/") not in self.blobs
            or type(payload.get("sha")) is not str):
            raise RuntimeError("wrong private conditional write")
        self.blobs["derived/boundaries-checkpoint.json"]=base64.b64decode(
            payload["content"])
        self.posts+=1


def fixture(complete=3):
    p=PrivateFake()
    calendar=days(15)
    for i in range(complete):
        window=calendar[5*i:5*(i+1)]
        store_five_day_factors(p,FakeKRX(),window)
    checkpoint=backfill._empty(HASH)
    checkpoint["next_window_index"]=complete
    checkpoint["full_issuer_bucket_records"]=complete*8
    checkpoint["compressed_factor_bytes"]=sum(
        len(raw) for k,raw in p.blobs.items()
        if k.startswith("derived/five-session/"))
    if complete:
        last=calendar[(complete-1)*5:complete*5]
        path=f"derived/five-session/{last[0]}_{last[-1]}.json.gz"
        checkpoint["last_window_file_sha256"]=sha256(p.blobs[path]).hexdigest()
    p.blobs[backfill.CHECKPOINT]=backfill._canonical(checkpoint)
    return p,calendar


class DerivedHistoricalBridgeTests(unittest.TestCase):
    def test_two_links_eight_real_shaped_calls_are_derived_only(self):
        p,calendar=fixture()
        source=FakeKRX()
        result=bridge.run(storage=p,client=source,dates=calendar,
                          calendar_hash=HASH)
        self.assertEqual(result["cumulative_completed_derived_boundaries"],2)
        self.assertEqual(result["new_market_api_calls"],8)
        self.assertEqual(len(source.calls),8)
        self.assertEqual(result["cumulative_boundary_issuer_observations"],16)
        self.assertFalse(result["exact_adjusted_20_60_120_250_returns_verified"])
        self.assertEqual(result["raw_provider_files_saved"],0)
        keys=[k for k in p.blobs if k.startswith("derived/boundaries/")]
        self.assertEqual(len(keys),2)
        content=bridge._read_link(p.blobs[keys[0]],calendar[4],calendar[5])
        self.assertIn("KOSPI:005000",content["derived_logreturn_by_market_issuer"])
        self.assertNotIn("TDD_CLSPRC",str(content))
        self.assertNotIn("ACC_TRDVOL",str(content))
        self.assertNotIn("raw_source_base64",str(content))
        self.assertTrue(all(k.startswith("derived/") for k in p.blobs))
        self.assertLess(len(p.blobs[bridge.CHECKPOINT]),1000)

    def test_eventually_visible_private_blob_does_not_repeat_source_or_put(self):
        p,calendar=fixture(complete=2)
        p.write_delays={}
        old_put=p.put_new
        old_get=p.get_content

        def stuttering_put(path,blob,message):
            old_put(path,blob,message)
            if path.startswith("derived/boundaries/") or path==bridge.CHECKPOINT:
                p.write_delays[path]=1

        def stuttering_read(path):
            if p.write_delays.get(path,0)>0:
                p.write_delays[path]-=1
                return None,None
            return old_get(path)

        p.put_new=stuttering_put
        p.get_content=stuttering_read
        api=FakeKRX()
        with patch.object(bridge.time,"sleep",return_value=None):
            result=bridge.run(storage=p,client=api,dates=calendar,
                              calendar_hash=HASH,max_links=1)
        self.assertEqual(result["new_market_api_calls"],4)
        self.assertEqual(len(api.calls),4)
        self.assertEqual(result["cumulative_completed_derived_boundaries"],1)
        self.assertEqual(sum(k.startswith("derived/boundaries/")
                             for k in p.blobs),1)

    def test_existing_links_are_reused_without_krx_calls(self):
        p,calendar=fixture()
        bridge.run(storage=p,client=FakeKRX(),dates=calendar,
                   calendar_hash=HASH)
        provider=FakeKRX()
        next_=bridge.run(storage=p,client=provider,dates=calendar,
                         calendar_hash=HASH)
        self.assertEqual(next_["new_market_api_calls"],0)
        self.assertEqual(next_["cumulative_completed_derived_boundaries"],2)
        self.assertEqual(provider.calls,[])

    def test_orphan_after_network_failure_is_repaired_without_any_api(self):
        p,calendar=fixture(complete=2)
        a,b=bridge.derive_link(FakeKRX(),calendar[4],calendar[5])
        p.blobs[bridge._link_path(calendar[4],calendar[5])]=b
        provider=FakeKRX()
        x=bridge.run(storage=p,client=provider,dates=calendar,
                     calendar_hash=HASH)
        self.assertEqual(x["existing_boundaries_reused"],1)
        self.assertEqual(x["new_market_api_calls"],0)
        self.assertEqual(provider.calls,[])

    def test_source_cohort_missing_raises_without_external_call(self):
        p,calendar=fixture(complete=2)
        del p.blobs[f"derived/five-session/{calendar[5]}_{calendar[9]}.json.gz"]
        provider=FakeKRX()
        with self.assertRaisesRegex(bridge.BoundaryError,
                                    "missing_source_derived_cohort_file"):
            bridge.run(storage=p,client=provider,dates=calendar,
                       calendar_hash=HASH)
        self.assertFalse(provider.calls)

    def test_tampering_checkpoint_or_last_link_block_without_market_calls(self):
        p,calendar=fixture()
        bridge.run(storage=p,client=FakeKRX(),dates=calendar,
                   calendar_hash=HASH)
        provider=FakeKRX()
        with self.assertRaises(bridge.BoundaryError):
            bridge.run(storage=p,client=provider,dates=calendar,
                       calendar_hash="e"*64)
        self.assertFalse(provider.calls)
        key=bridge._link_path(calendar[9],calendar[10])
        p.blobs[key]+=b"tampered"
        with self.assertRaises(bridge.BoundaryError):
            bridge.run(storage=p,client=provider,dates=calendar,
                       calendar_hash=HASH)
        self.assertFalse(provider.calls)

    def test_missing_issuer_quarantined_not_zero_return(self):
        data=FakeKRX(quarantine=True)
        output,compressed=bridge.derive_link(data,days()[4],days()[5])
        self.assertEqual(len(data.calls),4)
        self.assertNotIn("KOSPI:005003",output["derived_logreturn_by_market_issuer"])
        self.assertEqual(output["observed_boundary_issuer_pairs"],6)
        self.assertFalse(output["corporate_actions_adjusted"])

    def test_missing_second_cohort_waits_without_calls(self):
        p,calendar=fixture(complete=1)
        client=FakeKRX()
        x=bridge.run(storage=p,client=client,dates=calendar,
                     calendar_hash=HASH)
        self.assertEqual(x["api_calls"],0)
        self.assertEqual(x["source_cohorts_available"],1)
        self.assertEqual(client.calls,[])

    def test_invalid_destination_and_oversized_request_before_any_api(self):
        p,calendar=fixture()
        p.repository="ykvic1/korean-stock-screener"
        client=FakeKRX()
        with self.assertRaises(bridge.BoundaryError):
            bridge.run(storage=p,client=client,dates=calendar,
                       calendar_hash=HASH)
        p.repository="ykvic1/korean-stock-private-data"
        for max_links in (0,9,True):
            with self.subTest(max_links=max_links),self.assertRaises(bridge.BoundaryError):
                bridge.run(storage=p,client=client,dates=calendar,
                           calendar_hash=HASH,max_links=max_links)
        self.assertEqual(client.calls,[])

    def test_boundary_provider_failure_does_not_save_derived(self):
        p,calendar=fixture(complete=2)
        client=FakeKRX(fail_market=("KOSDAQ",calendar[5]))
        with self.assertRaisesRegex(bridge.BoundaryError,
                                    "official_boundary_session_unavailable_or_invalid"):
            bridge.run(storage=p,client=client,dates=calendar,
                       calendar_hash=HASH)
        self.assertFalse(any(k.startswith("derived/boundaries/") for k in p.blobs))
        self.assertNotIn(bridge.CHECKPOINT,p.blobs)


if __name__=="__main__":
    unittest.main()
