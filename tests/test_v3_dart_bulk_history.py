"""No-network official fnlttMultiAcnt 4-fiscal-year full-listed contract."""
import copy
from io import BytesIO
from zipfile import ZipFile
import unittest

import v3_dart_bulk_history as bulk
from test_v3_dart_rolling_book_reference import fixture, DAY

CODES=("005930","247540","300003","300004","300005")
CORPS={code:f"{i+100000:08d}" for i,code in enumerate(CODES)}

def payload_rows(year, codes=CODES):
    rows=[]
    for i,code in enumerate(codes):
        for name,group,value in (
            ("매출액","IS",100_000_000_000+(year-2022)*20_000_000_000+i*100),
            ("영업이익","IS",10_000_000_000+i*500),
            ("자산총계","BS",90_000_000_000),
            ("부채총계","BS",40_000_000_000),
            ("자본총계","BS",50_000_000_000)):
            rows.append({"stock_code":code,"bsns_year":str(year),
                         "reprt_code":"11011","fs_div":"CFS",
                         "sj_div":group,"account_nm":name,
                         "thstrm_amount":str(value),"currency":"KRW",
                         "rcept_no":f"{year+1}0318000001"})
    return rows

def mapping_zip():
    result=BytesIO()
    with ZipFile(result,"w") as z:
        z.writestr("CORPCODE.xml","<result>"+
          "".join(f"<list><stock_code>{code}</stock_code>"
                  f"<corp_code>{corp}</corp_code></list>"
                  for code,corp in CORPS.items())+"</result>")
    return result.getvalue()

class FakeResponse:
    def __init__(self,body,content=None):
        self.body=body
        self.content=content
    def raise_for_status(self):
        return None
    def json(self):
        return self.body

class FakeDART:
    def __init__(self,fail_year=None,blank_year=None):
        self.calls=[]
        self.fail_year=fail_year
        self.blank_year=blank_year
    def get(self,url,params,timeout):
        self.calls.append((url,params))
        if url.endswith("corpCode.xml"):
            return FakeResponse(None,content=mapping_zip())
        year=int(params["bsns_year"])
        if year==self.fail_year:
            return FakeResponse({"status":"020","message":"no fake quota override"})
        if year==self.blank_year:
            return FakeResponse({"status":"013"})
        return FakeResponse({"status":"000","list":payload_rows(year)})

class BulkHistoryTests(unittest.TestCase):
    def test_one_mapping_four_year_requests_cover_every_listed_even_unpriced(self):
        market,catalog=fixture()
        session=FakeDART()
        state,calls=bulk.collect(market,catalog,None,
                                  key="synthetic-key-not-secret-123",
                                  session=session,groups=1)
        self.assertEqual(calls,5)  # mapping once + one call per FY
        self.assertEqual(state["listed_total"],5)
        self.assertEqual(state["attempted_company_count"],5)
        self.assertEqual(state["four_year_income_observed_count"],5)
        self.assertEqual(state["unattempted_company_count"],0)
        self.assertEqual(state["fiscal_years"],[2022,2023,2024,2025])
        self.assertEqual(len(session.calls),5)
        self.assertEqual(session.calls[1][1]["corp_code"].count(","),4)
        self.assertIn("300005",state["records"])  # unpriced NOT skipped
        self.assertFalse(state["historical_PIT_source_certified"])
        self.assertFalse(state["four_year_complete_financial15_certified"])
        self.assertFalse(state["cfo_capex_and_net_debt_from_bulk_api_available"])
        self.assertFalse(state["raw_source_rows_published"])
        again=FakeDART()
        same,replayed=bulk.collect(market,catalog,state,
                      key="synthetic-key-not-secret-123",session=again,groups=3)
        self.assertEqual(replayed,0)
        self.assertEqual(again.calls,[])
        self.assertEqual(same["attempted_company_count"],5)

    def test_missing_annual_does_not_invent_zero_or_full_score(self):
        market,catalog=fixture()
        data,n=bulk.collect(market,catalog,None,
            key="synthetic-key-not-secret-123",session=FakeDART(blank_year=2023))
        self.assertEqual(n,5)
        self.assertEqual(data["four_year_income_observed_count"],0)
        yr=data["records"]["005930"]["years"]["2023"]
        self.assertEqual(yr,{})
        self.assertIsNone(bulk._best_income_window(
                          data["records"]["005930"],data["fiscal_years"]))
        blank_book={"asof":DAY,"market_listed_total":5,
                    "records":[],"observations":[]}
        scored=bulk.research_parts(data,catalog,blank_book,minimum_peers=8)
        self.assertEqual(scored["companies_with_computable_4y_income_scores"],0)
        self.assertEqual(scored["rows"],[])
        self.assertEqual(scored["model_financial_full_points"],15)
        self.assertEqual(scored["financial_partial_max_possible"],8)
        self.assertEqual(scored["valuation_partial_max_possible"],10)

    def test_stock_code_only_response_as_official_docs_and_strict_identity(self):
        rows=payload_rows(2025)
        parsed=bulk._normalize_year(rows,2025,DAY,set(CODES),
                {corp:code for code,corp in CORPS.items()})
        self.assertEqual(parsed["005930"]["CFS"]["values"]["operating_income"],
                         10_000_000_000)
        dupe=copy.deepcopy(rows)
        dupe.append(dict(dupe[0],thstrm_amount="99999"))
        changed=bulk._normalize_year(dupe,2025,DAY,set(CODES),
                {corp:code for code,corp in CORPS.items()})
        self.assertIsNone(changed["005930"]["CFS"]["values"]["revenue"])
        for key,value in (("stock_code","999999"),
                          ("bsns_year","2024"),
                          ("rcept_no","20271019009999"),
                          ("currency","USD"),
                          ("fs_div","BAD")):
            bad=copy.deepcopy(rows)
            bad[0][key]=value
            with self.subTest(key=key),self.assertRaises(bulk.BulkHistoryError):
                bulk._normalize_year(bad,2025,DAY,set(CODES),
                    {corp:code for code,corp in CORPS.items()})

    def test_current_dart_company_master_is_safely_size_bounded_and_identified(self):
        self.assertEqual(bulk._mapping(mapping_zip(),set(CODES)),CORPS)
        with self.assertRaisesRegex(bulk.BulkHistoryError,"not_zip_bytes"):
            bulk._mapping(b"",set(CODES))
        with self.assertRaisesRegex(bulk.BulkHistoryError,"zip_over_30mb"):
            bulk._mapping(b"x"*30_000_001,set(CODES))
        with self.assertRaisesRegex(bulk.BulkHistoryError,"not_parseable_zip_xml"):
            bulk._mapping(b"not a zip",set(CODES))
        corrupt=BytesIO()
        with ZipFile(corrupt,"w") as z:
            z.writestr("CORPCODE.xml",("<result><list><stock_code>005930</stock_code>"
               "<corp_code>00100000</corp_code></list>"
               "<list><stock_code>005930</stock_code>"
               "<corp_code>00100001</corp_code></list></result>"))
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                    "duplicate_stock_identity"):
            bulk._mapping(corrupt.getvalue(),set(CODES))

    def test_provider_stop_and_incomplete_response_do_not_update_snapshot(self):
        market,catalog=fixture()
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                    "dart_bulk_provider_stop"):
            bulk.collect(market,catalog,None,key="synthetic-key-not-secret-123",
                         session=FakeDART(fail_year=2024))
        _,_,_,years,cohorts=bulk.plan(market,catalog)
        with self.assertRaisesRegex(bulk.BulkHistoryError,"partial_or_unexpected"):
            bulk.combine(market,catalog,None,{},groups=3)
        with self.assertRaisesRegex(bulk.BulkHistoryError,"missing_bulk_fiscal"):
            bulk.combine(market,catalog,None,
                         {c:{"2025":{}} for c in CODES},groups=1)
        self.assertEqual(years,[2022,2023,2024,2025])
        self.assertEqual(len(cohorts[0]),5)

    def test_market_peer_partial_financial_and_valuation_never_claim_100(self):
        n=40
        years=[2022,2023,2024,2025]
        records={}
        rows=[]
        observed=[]
        annual=[]
        for i in range(n):
            code=f"{i:06d}"
            market="KOSPI" if i<20 else "KOSDAQ"
            rows.append({"code":code,"market":market,
                         "reference_market_cap_eok":2000+i})
            record={"years":{}}
            for year in years:
                record["years"][str(year)]={"CFS":{
                    "receipt_id":f"{year+1}0318000001",
                    "ambiguous":[],
                    "values":{"revenue":1000000*(10+i+year-2022),
                              "operating_income":100000+i*500+year-2022,
                              "assets":4000000,"liabilities":2000000,"equity":2000000}}}
            records[code]=record
            annual.append({"code":code,"annual":{
                "receipt_id":"20260318000001","fs_div":"CFS","year":2025,
                "currency":"KRW","values":{"owners_profit":3000000000+i*1000000}}})
            observed.append({"code":code,"receipt_id":"20260318000001",
                 "fs_div":"CFS","year":2025,
                 "market_cap_reference_day":DAY,
                 "pbr_simple_annual_reference":0.5+i/20,
                 "official_exchange_pbr_verified":False})
        data={"schema":bulk.SCHEMA,"asof":DAY,"listed_total":n,
              "fiscal_years":years,"four_year_income_observed_count":n,
              "records":records}
        catalog={"date":DAY,"listed_total":n,"rows":rows}
        book={"asof":DAY,"market_listed_total":n,"records":annual,
              "observations":observed}
        score=bulk.research_parts(data,catalog,book,minimum_peers=8)
        self.assertEqual(score["companies_with_computable_4y_income_scores"],n)
        self.assertEqual(score["companies_with_computable_pb_scores"],n)
        self.assertEqual(len(score["rows"]),n)
        self.assertEqual(score["companies_with_computable_earnings_yield_scores"],n)
        self.assertTrue(all(x["financial_available_max"]==8 and
                            x["valuation_available_max"]==10 and
                            x["combined_available_max"]==18 and
                            x["confirmed_v3_score"] is None and
                            not x["scoring_certified"]
                            for x in score["rows"]))
        self.assertEqual(score["fully_verified_six_pillar_count"],0)
        self.assertTrue(score["all_scores_provisional_unverified"])

if __name__=="__main__":
    unittest.main()
