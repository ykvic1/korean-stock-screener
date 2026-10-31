"""Offline tests for bounded KRX official private-Git history acquisition."""
import base64
import json
import gzip
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import v3_krx_private_github_backfill as h
from test_v3_krx_history_collection import FakeClient


class SimulatedPrivateGithub:
    def __init__(self,private=True):
        self.private=private
        self.blobs={}
        self.read_count=0
        self.put_count=0
        self.token="synthetic-token"
        self.repo_size_kib=0
    def transport(self,method,path,payload=None):
        if path=="":
            return {"private":self.private,
                    "full_name":"ykvic1/private-market-research",
                    "size":self.repo_size_kib}
        assert path.startswith("contents/")
        name=path[9:]
        if method=="GET":
            self.read_count+=1
            raw=self.blobs.get(name)

            if raw is None:
                return None
            return {"type":"file","encoding":"base64",
                    "content":base64.b64encode(raw).decode(),
                    "sha":"synthetic-"+str(hash(raw))}
        if method=="PUT":
            raw=base64.b64decode(payload["content"])
            if name in self.blobs and "sha" not in payload:
                raise RuntimeError("overwrite must be forbidden")
            if name not in self.blobs and "sha" in payload:
                raise RuntimeError("manifest must exist to replace")
            self.put_count+=1
            self.blobs[name]=raw
            return {"content":{"sha":"synthetic"}}
        raise AssertionError(method)
    def store(self):
        return h.PrivateGitStorage("ykvic1/private-market-research",
                                   "fake_TOKEN_at_least_20_characters",
                                   transport=self.transport)


def session_days():
    return ["2026-10-12","2026-10-13","2026-10-14",
            "2026-10-15","2026-10-16","2026-10-19"]


class HistoricalPrivateGithubTests(unittest.TestCase):
    def test_private_storage_retries_only_transient_reads_not_permission_or_writes(self):
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self,limit): return b'{"private":true}'
        store=h.PrivateGitStorage("ykvic1/private-market-research",
                                  "fake_TOKEN_at_least_20_characters")
        def http_error(code):
            return HTTPError("https://api.github.com",code,"blocked",{},None)
        with patch.object(h.request,"urlopen",side_effect=[http_error(503),Reply()]) as get:
            with patch.object(h.time,"sleep") as delay:
                self.assertEqual(store._http("GET",""),{"private":True})
            self.assertEqual(get.call_count,2)
            delay.assert_called_once_with(0.25)
        with patch.object(h.request,"urlopen",side_effect=http_error(403)) as get:
            with patch.object(h.time,"sleep") as delay:
                with self.assertRaisesRegex(h.PrivateHistoryError,"private_storage_api_failed"):
                    store._http("GET","")
            self.assertEqual(get.call_count,1)
            delay.assert_not_called()
        with patch.object(h.request,"urlopen",side_effect=http_error(503)) as exhausted:
            with patch.object(h.time,"sleep") as delay:
                with self.assertRaisesRegex(h.PrivateHistoryError,"private_storage_api_failed"):
                    store._http("GET","")
            self.assertEqual(exhausted.call_count,3)
            self.assertEqual(delay.call_count,2)
        with patch.object(h.request,"urlopen",side_effect=http_error(503)) as put:
            with patch.object(h.time,"sleep") as delay:
                with self.assertRaisesRegex(h.PrivateHistoryError,"private_storage_api_failed"):
                    store._http("PUT","contents/derived/private.zip",{"content":"eA=="})
            self.assertEqual(put.call_count,1)
            delay.assert_not_called()
        with patch.object(h.request,"urlopen",side_effect=http_error(404)) as missing:
            self.assertIsNone(store._http("GET","contents/derived/absent.zip"))
            self.assertEqual(missing.call_count,1)
        class Oversized(Reply):
            def read(self,limit): return b" " * 2_000_001
        with patch.object(h.request,"urlopen",return_value=Oversized()) as huge:
            with self.assertRaisesRegex(h.PrivateHistoryError,"private_api_response_too_large"):
                store._http("GET","")
            self.assertEqual(huge.call_count,1)

    def test_five_days_ten_calls_and_only_private_zipped_source(self):
        gh=SimulatedPrivateGithub()
        fake=FakeClient()
        outcome=h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(outcome["durably_archived_pair_days"],5)
        self.assertEqual(outcome["new_official_market_calls"],10)
        self.assertEqual(len(fake.calls),10)
        self.assertEqual(gh.put_count,6) # five immutable gzip days + ONE tiny checkpoint
        self.assertFalse(outcome["market_raw_published"])
        self.assertEqual(outcome["manifest_entry_count"],1)
        self.assertGreater(outcome["source_compression_saving_pct"],0)
        self.assertLess(len(gh.blobs["krx/manifest.json"]),400)
        self.assertFalse(outcome["historical_adjusted_prices_certified"])
        self.assertIn("krx/2026/2026-10-12.json.gz",gh.blobs)
        self.assertNotIn("docs",''.join(gh.blobs))
        day_blob=gh.blobs["krx/2026/2026-10-12.json.gz"]
        self.assertTrue(day_blob.startswith(b'\x1f\x8b'))
        self.assertEqual(h._unpack_pair("2026-10-12",day_blob)
                         ["KOSPI"]["numeric_candidates"],1)
        next_result=h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(next_result["new_official_market_calls"],2)
        self.assertEqual(next_result["durably_archived_pair_days"],6)
        replay=h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(replay["new_official_market_calls"],0)
        self.assertEqual(replay["durably_archived_pair_days"],6)

    def test_private_repo_required_before_any_live_calls(self):
        gh=SimulatedPrivateGithub(private=False)
        fake=FakeClient()
        with self.assertRaisesRegex(h.PrivateHistoryError,
                                    "target_repo_not_confirmed_private"):
            h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertFalse(fake.calls)
        for invalid in ("ykvic1/korean-stock-screener","INVALID","random/repo"):
            with self.subTest(repo=invalid):
                with self.assertRaises(h.PrivateHistoryError):
                    h.PrivateGitStorage(invalid,"123")

    def test_orphaned_day_resumes_without_new_krx_call(self):
        gh=SimulatedPrivateGithub()
        first=h.run(storage=gh.store(),client=FakeClient(),
                    dates=session_days(),limit=1)
        self.assertEqual(first["durably_archived_pair_days"],1)
        d=session_days()[1]
        blob,_=h._pack_pair(d,{
            m:FakeClient().daily_market(m,d)[1] for m in ("KOSPI","KOSDAQ")})
        gh.blobs[h._pair_path(d)]=blob # simulated after PUT, before manifest
        fake=FakeClient()
        ret=h.run(storage=gh.store(),client=fake,dates=session_days(),limit=1)
        self.assertEqual(ret["orphaned_private_days_reconciled"],1)
        self.assertEqual(ret["new_official_market_calls"],0)
        self.assertEqual(fake.calls,[])

    def test_corrupt_or_future_manifest_fails_without_provider_request(self):
        gh=SimulatedPrivateGithub()
        gh.blobs["krx/manifest.json"]=h._canonical({
            "schema":h.SCHEMA,"start":h.START,"end":h.END,
            "next_index":1,"last_day":"2026-10-14",
            "last_pair_sha256":"a"*64,"compressed_bytes":100,"raw_bytes":1000})
        fake=FakeClient()
        with self.assertRaisesRegex(h.PrivateHistoryError,"invalid_or_legacy_private_checkpoint"):
            h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(fake.calls,[])
        self.assertEqual(h._check_manifest(None,session_days())["next_index"],0)

    def test_mismatched_or_changed_source_is_blocked(self):
        gh=SimulatedPrivateGithub()
        d=session_days()[0]
        blob,_=h._pack_pair(d,{
            m:FakeClient().daily_market(m,d)[1] for m in ("KOSPI","KOSDAQ")})
        gh.blobs[h._pair_path(d)]=blob+b"forged"
        fake=FakeClient()
        with self.assertRaises(h.PrivateHistoryError):
            h.run(storage=gh.store(),client=fake,dates=session_days(),limit=1)
        self.assertEqual(fake.calls,[])
        self.assertNotIn("krx/manifest.json",gh.blobs)

    def test_mixed_or_missing_market_fails_closed(self):
        day=session_days()[0]
        with self.assertRaises(h.PrivateHistoryError):
            h._pack_pair(day,{"KOSPI":b"placeholder"})
        bad=FakeClient(bad=("KOSDAQ",day))
        gh=SimulatedPrivateGithub()
        with self.assertRaisesRegex(h.PrivateHistoryError,"official_krx_market_request"):
            h.run(storage=gh.store(),client=bad,dates=session_days(),limit=1)
        self.assertNotIn("krx/manifest.json",gh.blobs)
        self.assertFalse(gh.blobs)

    def test_constant_size_checkpoint_across_many_batches(self):
        # Each five-day batch should mutate the tiny pointer ONCE rather than
        # rewriting ever-growing lists of previously stored day SHA hashes.
        gh=SimulatedPrivateGithub()
        from datetime import date,timedelta
        days=[(date(2026,1,5)+timedelta(days=i)).isoformat()
              for i in range(30) if (date(2026,1,5)+timedelta(days=i)).weekday()<5]
        self.assertEqual(len(days),22)
        lengths=[]
        fake=FakeClient()
        for n in range(5):
            result=h.run(storage=gh.store(),client=fake,dates=days)
            lengths.append(len(gh.blobs["krx/manifest.json"]))
            self.assertEqual(result["durably_archived_pair_days"],min(5*(n+1),22))
            self.assertLess(result["compressed_private_source_bytes"],
                            h.MAX_PRIVATE_PAYLOAD_BYTES)
        self.assertEqual(gh.put_count,22+5)
        self.assertLess(max(lengths)-min(lengths),50,
                        "checkpoint must be O(1) in number of completed days")
        self.assertEqual(len(fake.calls),44)

    def test_archive_requires_repo_size_and_rejects_near_budget(self):
        gh=SimulatedPrivateGithub()
        gh.repo_size_kib=h.MAX_REPO_SIZE_KIB
        fake=FakeClient()
        with self.assertRaisesRegex(h.PrivateHistoryError,
                                    "private_repository_budget_exceeded"):
            h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(fake.calls,[])
        gh.repo_size_kib=0
        gh.blobs["krx/manifest.json"]=h._canonical({
            "schema":h.SCHEMA,"start":h.START,"end":h.END,
            "next_index":0,"last_day":None,"last_pair_sha256":None,
            "compressed_bytes":h.MAX_PRIVATE_PAYLOAD_BYTES,
            "raw_bytes":h.MAX_PRIVATE_PAYLOAD_BYTES})
        with self.assertRaisesRegex(h.PrivateHistoryError,
                                    "private_compressed_budget_limit"):
            h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(fake.calls,[])

    def test_existing_checkpoint_tail_mutation_halts_before_api(self):
        gh=SimulatedPrivateGithub()
        first=h.run(storage=gh.store(),client=FakeClient(),dates=session_days(),limit=1)
        self.assertEqual(first["durably_archived_pair_days"],1)
        gh.blobs[h._pair_path(session_days()[0])]+=b"forged"
        fake=FakeClient()
        with self.assertRaisesRegex(h.PrivateHistoryError,
                                    "private_checkpoint_missing_or_mismatched_day"):
            h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertFalse(fake.calls)

    def test_legacy_growing_manifest_requires_explicit_migration(self):
        old=h._canonical({
            "schema":"v3-krx-private-git-history-0.1",
            "start":h.START,"end":h.END,
            "completed":[{"day":"2026-10-12","pair_sha256":"a"*64}]})
        with self.assertRaisesRegex(h.PrivateHistoryError,
                                    "invalid_or_legacy_private_checkpoint"):
            h._check_manifest(old,session_days())

    def test_benchmark_repetitive_market_json_vs_old_base64_gzip(self):
        day="2026-10-16"
        markets={}
        for market,count in (("KOSPI",1350),("KOSDAQ",1147)):
            rows=[{"BAS_DD":"20261016","ISU_CD":f"{i+10000:06d}",
                   "TDD_OPNPRC":str(10000+i),"TDD_HGPRC":str(10100+i),
                   "TDD_LWPRC":str(9900+i),"TDD_CLSPRC":str(10030+i),
                   "ACC_TRDVOL":str(100000+i*17)}
                  for i in range(count)]
            markets[market]=json.dumps({"OutBlock_1":rows},
                                       separators=(",",":")).encode()
        packed,_=h._pack_pair(day,markets)
        original_sum=sum(len(v) for v in markets.values())
        self.assertLess(len(packed),original_sum*.5)
        original_base64=h._canonical({
            "schema":"v3-private-krx-two-market-raw-0.1","asof":day,
            "raw_source_base64":{k:base64.b64encode(v).decode()
                                 for k,v in markets.items()}})
        old_packed=gzip.compress(original_base64,compresslevel=9,mtime=0)
        self.assertLess(len(packed),len(old_packed),
                        "binary frame must reduce private payload vs base64")
        self.assertEqual(h._unpack_pair(day,packed)["KOSPI"]["source_rows"],1350)

    def test_no_scheduled_action_minutes_before_private_opt_in(self):
        workflow=Path(".github/workflows/v3-krx-private-historical-backfill.yml").read_text()
        self.assertIn("github.event_name == 'workflow_dispatch' || (vars.V3_KRX_PRIVATE_STORAGE_AUTHORIZED == 'true'",workflow)
        self.assertIn("vars.V3_KRX_CLOUD_STORAGE_WRITTEN_APPROVED == 'true'",workflow)
        self.assertIn("vars.V3_KRX_APPROVAL_REFERENCE != ''",workflow)
        self.assertIn("default: dry_run",workflow)
        self.assertIn("permissions:",workflow)
        self.assertIn("  contents: read",workflow)

    def test_unknown_calendar_or_batch_does_not_collect(self):
        gh=SimulatedPrivateGithub();fake=FakeClient()
        for invalid in (0,6,True):
            with self.assertRaises(h.PrivateHistoryError):
                h.run(storage=gh.store(),client=fake,dates=session_days(),limit=invalid)
        self.assertEqual(fake.calls,[])


if __name__=="__main__":
    unittest.main()
