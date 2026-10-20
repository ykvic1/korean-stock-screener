"""Offline tests for bounded KRX official private-Git history acquisition."""
import base64
import json
import unittest

import v3_krx_private_github_backfill as h
from test_v3_krx_history_collection import FakeClient


class SimulatedPrivateGithub:
    def __init__(self,private=True):
        self.private=private
        self.blobs={}
        self.read_count=0
        self.put_count=0
        self.token="synthetic-token"
    def transport(self,method,path,payload=None):
        if path=="":
            return {"private":self.private,
                    "full_name":"ykvic1/private-market-research"}
        assert path.startswith("contents/")
        name=path[9:]
        if method=="GET":
            self.read_count+=1
            raw=self.blobs.get(name)
            return {"type":"file","encoding":"base64",
                    "content":base64.b64encode(raw).decode(),
                    "sha":"synthetic-"+str(hash(raw))}
                    if raw is not None else None
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
    def test_five_days_ten_calls_and_only_private_zipped_source(self):
        gh=SimulatedPrivateGithub()
        fake=FakeClient()
        outcome=h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(outcome["durably_archived_pair_days"],5)
        self.assertEqual(outcome["new_official_market_calls"],10)
        self.assertEqual(len(fake.calls),10)
        self.assertEqual(gh.put_count,10) # five compressed day + five manifest
        self.assertFalse(outcome["market_raw_published"])
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
            "completed":[{"day":"2026-10-14","pair_sha256":"a"*64}]})
        fake=FakeClient()
        with self.assertRaisesRegex(h.PrivateHistoryError,"invalid_private_manifest"):
            h.run(storage=gh.store(),client=fake,dates=session_days())
        self.assertEqual(fake.calls,[])
        self.assertFalse(h._check_manifest(None,session_days())["completed"])

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

    def test_unknown_calendar_or_batch_does_not_collect(self):
        gh=SimulatedPrivateGithub();fake=FakeClient()
        for invalid in (0,6,True):
            with self.assertRaises(h.PrivateHistoryError):
                h.run(storage=gh.store(),client=fake,dates=session_days(),limit=invalid)
        self.assertEqual(fake.calls,[])


if __name__=="__main__":
    unittest.main()
