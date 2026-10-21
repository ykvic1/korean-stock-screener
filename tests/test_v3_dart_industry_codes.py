"""Offline official company.json industry classification and rollover guards."""
import copy
import json
import unittest

import v3_dart_industry_codes as c
from v3_dart_bulk_history import make_company_mapping_cache
from test_v3_dart_rolling_book_reference import fixture, DAY

class FakeResp:
    def __init__(self,record):
        self.record=record
    def raise_for_status(self): pass
    def json(self): return self.record

class FakeProfile:
    def __init__(self,code_to_corp,code_to_market,missing=None,poison=None):
        self.calls=[]
        self.inverse={v:k for k,v in code_to_corp.items()}
        self.markets=code_to_market
        self.missing=missing
        self.poison=poison
    def get(self,url,params,timeout):
        self.calls.append(url)
        if not url.endswith("/api/company.json"):
            raise AssertionError("Unexpected source endpoint")
        code=self.inverse[params["corp_code"]]
        if self.poison=="quota":
            return FakeResp({"status":"020"})
        body={"status":"000","stock_code":code,
              "corp_cls":"Y" if self.markets[code]=="KOSPI" else "K",
              "induty_code":None if code==self.missing else "26222"}
        if self.poison=="spoof":
            body["stock_code"]="999999"
        return FakeResp(body)

class FlakyProfile(FakeProfile):
    """Synthetic transport loss, not an HTTP/quota or identity error."""
    def __init__(self,*args,failed_code,fail_times=1,**kwargs):
        super().__init__(*args,**kwargs)
        self.failed_code=failed_code
        self.fail_times=fail_times

    def get(self,url,params,timeout):
        code=self.inverse[params["corp_code"]]
        if code==self.failed_code and self.fail_times:
            self.calls.append(url)
            self.fail_times-=1
            raise TimeoutError("synthetic_transport_outage")
        return super().get(url,params,timeout)


class OfficialIndustryCodeTests(unittest.TestCase):
    def setUp(self):
        self.market,self.catalog=fixture()
        self.asof,self.members=c._inputs(self.market,self.catalog)
        self.mapping={code:f"{10000000+i:08d}"
                      for i,code in enumerate(sorted(self.members))}
        self.cache=make_company_mapping_cache(self.mapping,self.asof,self.members)
        self.market_names={code:r["market"] for code,r in self.members.items()}
        self.key="SYNTHETIC-NO-SECRET-FOR-TESTS-123456"

    def session(self,**kwargs):
        return FakeProfile(self.mapping,self.market_names,**kwargs)

    def test_real_source_identity_and_96_cap_without_any_pit_claim(self):
        fake=self.session()
        saved=[]
        result,calls=c.collect(self.market,self.catalog,None,key=self.key,
            session=fake,cache=self.cache,limit=3,checked_on=DAY,
            on_progress=lambda x:saved.append(x))
        self.assertEqual(calls,3)
        self.assertEqual(len(fake.calls),3)
        self.assertEqual(result["attempted_company_profiles"],3)
        self.assertEqual(result["observed_industry_codes"],3)
        self.assertEqual(result["not_yet_attempted"],len(self.members)-3)
        self.assertFalse(result["industry_5433_points_authorized"])
        self.assertFalse(result["past_constituent_membership_pit_verified"])
        self.assertFalse(result["official_krx_sector_membership_verified"])
        self.assertFalse(result["source_company_profiles_published"])
        for code,r in result["records"].items():
            self.assertEqual(r["industry_code"],"26222")
            self.assertEqual(r["market"],self.members[code]["market"])
            self.assertEqual(len(r["corporation_identity_digest"]),64)
        self.assertNotIn(self.key,json.dumps(result))
        self.assertNotIn("10000000",json.dumps(result))
        second,calls=c.collect(self.market,self.catalog,result,key=self.key,
            session=self.session(),cache=self.cache,checked_on=DAY)
        self.assertEqual(second["attempted_company_profiles"],len(self.members))
        self.assertEqual(calls,len(self.members)-3)
        self.assertEqual(c.choose(self.members,second["records"],self.mapping),[])

    def test_transient_retry_once_and_global_call_budget(self):
        from unittest.mock import patch
        first=sorted(self.members)[0]
        fake=FlakyProfile(self.mapping,self.market_names,failed_code=first)
        with patch.object(c.time,"sleep",return_value=None) as sleepy:
            result,calls=c.collect(self.market,self.catalog,None,key=self.key,
                session=fake,cache=self.cache,checked_on=DAY)
        self.assertEqual(result["attempted_company_profiles"],len(self.members))
        self.assertEqual(calls,len(self.members)+1)
        self.assertEqual(len(fake.calls),calls)
        self.assertEqual(sleepy.call_count,1)
        self.assertFalse(result["industry_5433_points_authorized"])

    def test_retry_exhaustion_checkpoints_previously_verified_rows(self):
        from unittest.mock import patch
        second=sorted(self.members)[1]
        flaky=FlakyProfile(self.mapping,self.market_names,
                           failed_code=second,fail_times=2)
        snapshots=[]
        with patch.object(c.time,"sleep",return_value=None):
            with self.assertRaisesRegex(c.IndustrySourceError,
                                        "company_profile_transport_unavailable"):
                c.collect(self.market,self.catalog,None,key=self.key,
                    session=flaky,cache=self.cache,limit=3,checked_on=DAY,
                    on_progress=snapshots.append)
        self.assertEqual(len(flaky.calls),3)
        self.assertEqual(len(snapshots),1)
        self.assertEqual(snapshots[0]["attempted_company_profiles"],1)
        self.assertEqual(snapshots[0]["last_run_attempted"],1)
        self.assertEqual(list(snapshots[0]["records"]),[sorted(self.members)[0]])
        self.assertEqual(snapshots[0]["observed_industry_codes"],1)
        self.assertFalse(snapshots[0]["industry_5433_points_authorized"])

    def test_missing_code_is_absent_not_zero_scoring(self):
        fake=self.session(missing=sorted(self.members)[0])
        r,_=c.collect(self.market,self.catalog,None,key=self.key,
            session=fake,cache=self.cache,checked_on=DAY)
        self.assertEqual(r["missing_industry_codes"],1)
        self.assertEqual(r["observed_industry_codes"],len(self.members)-1)
        self.assertEqual(r["records"][sorted(self.members)[0]]["industry_code"],None)
        self.assertEqual(r["records"][sorted(self.members)[0]]["status"],
                         "industry_unavailable")
        self.assertFalse(r["industry_5433_points_authorized"])

    def test_duplicate_cache_cross_company_spoof_and_quota_block(self):
        x=self.session(poison="spoof")
        with self.assertRaises(c.IndustrySourceError):
            c.collect(self.market,self.catalog,None,key=self.key,
                      session=x,cache=self.cache,checked_on=DAY)
        q=self.session(poison="quota")
        with self.assertRaises(c.IndustrySourceError):
            c.collect(self.market,self.catalog,None,key=self.key,
                      session=q,cache=self.cache,checked_on=DAY)
        self.assertEqual(calls_bound:=len(q.calls),1)
        self.assertLessEqual(calls_bound,c.MAX_PER_RUN)
        bad_cache=copy.deepcopy(self.cache)
        bad_cache["issuer_codes"][sorted(self.members)[0]]="00000000"
        with self.assertRaises(Exception):
            c.collect(self.market,self.catalog,None,key=self.key,
                session=self.session(),cache=bad_cache,checked_on=DAY)

    def test_corrupt_saved_digest_market_or_industry_code_rejected(self):
        r,_=c.collect(self.market,self.catalog,None,key=self.key,
            session=self.session(),cache=self.cache,checked_on=DAY)
        wrong=copy.deepcopy(r)
        first=sorted(wrong["records"])[0]
        wrong["records"][first]["industry_code"]="FAKE"
        with self.assertRaises(c.IndustrySourceError):
            c.prior_records(wrong,self.asof,self.members)
        wrong=copy.deepcopy(r)
        wrong["records"][first]["market"]="NYSE"
        wrong["source_records_digest"]=c._stable_digest(wrong["records"])
        with self.assertRaises(c.IndustrySourceError):
            c.prior_records(wrong,self.asof,self.members)
        wrong=copy.deepcopy(r)
        wrong["industry_5433_points_authorized"]=True
        with self.assertRaises(c.IndustrySourceError):
            c.prior_records(wrong,self.asof,self.members)

    def test_cohort_is_market_balanced_and_not_price_filtered(self):
        r,_=c.collect(self.market,self.catalog,None,key=self.key,
            session=self.session(),cache=self.cache,checked_on=DAY)
        self.assertEqual(set(r["records"]),set(self.members))
        self.assertEqual(r["listed_total"],len(self.members))
        self.assertFalse(any("score" in rec for rec in r["records"].values()))

if __name__=="__main__":
    unittest.main()
