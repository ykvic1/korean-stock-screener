"""No-network official FY2025 owner profit probe contracts; no scored fake data."""
import copy
import json
from pathlib import Path
import tempfile
import tempfile
import unittest

import v3_dart_2025_owner_profit_probe as probe
from v3_dart_bulk_history import make_company_mapping_cache

DOCS=Path(__file__).resolve().parents[1]/"docs"


def inputs():
    def read(name):
        return json.loads((DOCS/name).read_text(encoding="utf8"))
    return (read("v22-market-catalog.json"),
            read("v3-dart-book-reference.json"),
            read("v3-near-100-evidence-priority.json"))


class Result:
    def __init__(self,payload):
        self.payload=payload
    def json(self):
        return self.payload
    def raise_for_status(self):
        return None


class Source:
    def __init__(self, payload):
        self.payload=payload
        self.calls=[]
    def get(self,uri,params,timeout):
        self.calls.append((uri,params))
        if "fnlttSinglAcntAll" not in uri:
            raise AssertionError("unexpected provider source")
        return Result(self.payload)


class OwnerProfitSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog,cls.book,cls.priority=inputs()

    def first(self):
        day,members,expected,_,selected=probe.plan(
            self.catalog,self.book,self.priority,limit=1)
        self.assertTrue(selected)
        code=selected[0]
        return day,members,expected[code]

    def row(self,base,amount="0"):
        scope=base["fs_div"]
        account=sorted(probe.PROFIT_IDS[scope])[0]
        return {
            "corp_code":"12345678","stock_code":base["code"],
            "bsns_year":"2025","reprt_code":"11011",
            "fs_div":scope,"sj_div":"CIS","account_detail":"-",
            "currency":"KRW","rcept_no":base["annual_receipt_id"],
            "account_id":account,"thstrm_amount":amount}

    def test_priority_matches_only_real_missing_2025_ownership_profit(self):
        day,members,expected,retained,target=probe.plan(
            self.catalog,self.book,self.priority,limit=20)
        self.assertEqual(day,self.priority["asof"])
        self.assertEqual(len(expected),
                         self.priority["official_2025_owners_profit_missing_priority_count"])
        self.assertEqual(len(target),min(20,len(expected)))
        self.assertFalse(retained)
        self.assertTrue(set(target)<=set(expected))
        self.assertEqual(len(set(target)),len(target))

    def test_source_observed_zero_or_negative_is_real_not_missing(self):
        day,members,base=self.first()
        for income in ("0","-20000000","100000000"):
            with self.subTest(profit=income):
                result=probe.parse_response(
                    {"status":"000","list":[self.row(base,income)]},
                    corp="12345678",base=base,on_day=day)
                self.assertEqual(result["owners_profit_krw"],int(income))
                self.assertEqual(result["status"],"observed_owner_profit")
                self.assertFalse(result["raw_provider_rows_published"])

    def test_no_standard_account_keeps_null_not_zero(self):
        day,members,base=self.first()
        for content in ({"status":"013"},
                        {"status":"000","list":[{
                            **self.row(base),"account_id":"nonstandard_account"}]}):
            with self.subTest(provider_status=content["status"]):
                result=probe.parse_response(
                    content,corp="12345678",base=base,on_day=day)
                self.assertEqual(result["status"],"missing_standard_account")
                self.assertIsNone(result["owners_profit_krw"])
                self.assertIsNone(result["source_account_id"])

    def test_wrong_year_corporation_scope_and_filing_are_not_scored(self):
        day,members,base=self.first()
        for key,value in (
            ("corp_code","87654321"),
            ("bsns_year","2024"),
            ("fs_div","OFS" if base["fs_div"]=="CFS" else "CFS"),
            ("rcept_no","20260301000001")):
            with self.subTest(field=key):
                altered=self.row(base,"500")
                altered[key]=value
                with self.assertRaises(probe.ProfitProbeError):
                    probe.parse_response(
                        {"status":"000","list":[altered]},
                        corp="12345678",base=base,on_day=day)
        fake=self.row(base,"500")
        fake["currency"]="USD"
        with self.assertRaisesRegex(probe.ProfitProbeError,
                                    "currency_not_krw"):
            probe.parse_response(
                {"status":"000","list":[fake]},
                corp="12345678",base=base,on_day=day)

    def test_conflicting_accounts_are_rejected_not_averaged(self):
        day,members,base=self.first()
        options=sorted(probe.PROFIT_IDS[base["fs_div"]])
        self.assertEqual(len(options),2)
        a=self.row(base,"100")
        b=self.row(base,"200")
        a["account_id"],b["account_id"]=options
        with self.assertRaisesRegex(probe.ProfitProbeError,"conflicting"):
            probe.parse_response(
                {"status":"000","list":[a,b]},
                corp="12345678",base=base,on_day=day)

    def test_exact_census_cached_identity_enables_one_real_provider_call(self):
        day,members,base=self.first()
        code=base["code"]
        cache=make_company_mapping_cache(
            {code:"12345678"},day,members)
        provider=Source({"status":"000","list":[self.row(base,"0")]})
        state,calls=probe.collect(
            self.catalog,self.book,self.priority,None,
            key="synthetic-test-key-not-credential",
            session=provider,cache=cache,limit=1)
        self.assertEqual(calls,1)
        self.assertEqual(len(provider.calls),1)
        self.assertEqual(state["observed_official_owner_profit_issuers"],1)
        self.assertEqual(state["records"][code]["owners_profit_krw"],0)
        self.assertTrue(probe.verify(
            state,self.catalog,self.book,self.priority))
        self.assertFalse(state["account_value_used_in_scores"])
        self.assertFalse(state["investment_grade_score_certified"])

    def test_prior_same_receipt_replay_and_tamper_fail_closed(self):
        day,members,base=self.first()
        code=base["code"]
        cache=make_company_mapping_cache({code:"12345678"},day,members)
        state,_=probe.collect(
            self.catalog,self.book,self.priority,None,
            key="synthetic-test-key-not-credential",
            session=Source({"status":"000","list":[self.row(base,"100")]}),
            cache=cache,limit=1)
        provider=Source({"status":"999"})
        later,calls=probe.collect(
            self.catalog,self.book,self.priority,state,
            key="synthetic-test-key-not-credential",
            session=provider,cache=cache,limit=1)
        self.assertEqual(calls,0)
        self.assertEqual(provider.calls,[])
        self.assertEqual(later,state)
        tampered=copy.deepcopy(state)
        tampered["records"][code]["owners_profit_krw"]=10**19
        with self.assertRaises(probe.ProfitProbeError):
            probe.verify(tampered,self.catalog,self.book,self.priority)
        changed=copy.deepcopy(state)
        changed["records"][code]["annual_receipt_id"]="20250301000001"
        with self.assertRaises(probe.ProfitProbeError):
            probe.verify(changed,self.catalog,self.book,self.priority)

    def test_unauthorized_score_and_modified_priority_fail_closed(self):
        day,members,base=self.first()
        broken=copy.deepcopy(self.priority)
        broken["official_2025_owners_profit_missing_priority_codes"]=[]
        with self.assertRaisesRegex(probe.ProfitProbeError,"tampered_owner_profit_priority"):
            probe.plan(self.catalog,self.book,broken)
        bogus=copy.deepcopy(self.priority)
        bogus["official_trade_score_certified"]=True
        with self.assertRaisesRegex(probe.ProfitProbeError,"misaligned_owner_profit_source_priority"):
            probe.plan(self.catalog,self.book,bogus)

    def test_actions_company_code_cache_is_read_from_repository_root(self):
        with tempfile.TemporaryDirectory() as td:
            cache=Path(td)/".cache"/"v3-dart-company-codes.json"
            cache.parent.mkdir()
            value={"schema":"synthetic-nonsecret-corp-map",
                   "issuer_codes":{"005930":"00123456"}}
            cache.write_text(json.dumps(value),encoding="utf8")
            self.assertEqual(probe._read_company_cache_from_root(cache),value)
            self.assertIsNone(
                probe._read_company_cache_from_root(cache.with_name("missing.json")))
            cache.write_text("{broken-json",encoding="utf8")
            with self.assertRaisesRegex(probe.ProfitProbeError,
                                       "untrusted_official_corp_identity_cache"):
                probe._read_company_cache_from_root(cache)

    def test_cli_dry_run_no_network_or_score_publication(self):
        # Uses only saved source JSON; official secret deliberately absent.
        probe.main(["--docs",str(DOCS),"--mode","dry-run","--limit","5"])


if __name__=="__main__":
    unittest.main()
