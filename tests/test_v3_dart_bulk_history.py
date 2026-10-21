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
        for key,value,reason in (
                ("stock_code","999999","unrequested_or_blank_stock_code"),
                ("bsns_year","2024","fiscal_year_mismatch"),
                ("reprt_code","11012","annual_report_code_mismatch"),
                ("rcept_no","20271019009999","invalid_or_future_receipt"),
                ("currency","USD","unconfirmed_krw_currency"),
                ("fs_div","BAD","unknown_accounting_scope"),
                ("corp_code","00199999","corp_code_mismatch")):
            bad=copy.deepcopy(rows)
            bad[0][key]=value
            reasons={}
            with self.subTest(key=key):
                accepted=bulk._normalize_year(bad,2025,DAY,set(CODES),
                        {corp:code for code,corp in CORPS.items()},
                        diagnostics=reasons)
                self.assertEqual(reasons["quarantine_reasons"],{reason:1})
                self.assertEqual(reasons["trusted_row_count"],len(rows)-1)
                self.assertIsNone(
                    accepted["005930"]["CFS"]["values"]["revenue"])
                self.assertEqual(
                    accepted["247540"]["CFS"]["values"]["revenue"],
                    160_000_000_100)

    def test_untrusted_issuer_isolation_and_global_schema_fail_closed(self):
        rows=payload_rows(2025)
        lookup={corp:code for code,corp in CORPS.items()}
        bad=copy.deepcopy(rows)
        # One company's fiscal report revision cannot contaminate another.
        bad.append(dict(bad[0],rcept_no="20260412000001"))
        reasons={}
        matched=bulk._normalize_year(bad,2025,DAY,set(CODES),
                        lookup,diagnostics=reasons)
        self.assertNotIn("CFS",matched["005930"])
        self.assertEqual(matched["247540"]["CFS"]["values"]["revenue"],
                         160_000_000_100)
        self.assertEqual(
            reasons["quarantine_reasons"]["mixed_receipts_same_company_scope"],1)
        self.assertEqual(reasons["quarantined_company_scope_count"],1)
        # Do not accept one valid row amid a wholesale changed/invalid API.
        severe=[dict(bad[1],stock_code="999999") for _ in range(100)]
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                     "no_trustworthy_source"):
            bulk._normalize_year(severe+[rows[0]],2025,DAY,set(CODES),
                                 lookup)
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                     "no_trustworthy_source"):
            bulk._normalize_year([dict(rows[0],currency="USD")],
                                 2025,DAY,set(CODES),lookup)
        self.assertEqual(bulk._normalize_year([],2025,DAY,set(CODES),
                                                lookup)["005930"],{})



    def test_reuse_all_five_real_annual_book_latest_year_before_bulk_http(self):
        market,catalog=fixture()
        book={"asof":DAY,"market_listed_total":5,
              "records":[{
                 "code":code,"status":"observed",
                 "annual":{"year":2025,"fs_div":"CFS",
                           "receipt_id":"20260318000001","currency":"KRW",
                           "values":{"revenue":150_000_000_000+i*1000,
                                     "operating_income":15_000_000_000,
                                     "equity":50_000_000_000,
                                     "liabilities":20_000_000_000}}}
                for i,code in enumerate(CODES)]}
        ready=bulk.verified_current_annual_book_year(
            market,catalog,book,None)
        self.assertEqual(set(ready),set(CODES))
        self.assertIsNone(ready["005930"]["2025"]["CFS"]["values"]["assets"])
        self.assertEqual(ready["005930"]["2025"]["CFS"]["source_origin"],
                         "previously_observed_dart_annual_book")
        session=FakeDART()
        saved=[]
        state,calls=bulk.collect(
            market,catalog,None,key="synthetic-key-not-secret-123",
            session=session,groups=1,annual_book=book,
            on_progress=lambda snapshot,year:saved.append(
                (year,copy.deepcopy(snapshot))))
        self.assertEqual(calls,4) # mapping + only 2022,2023,2024
        self.assertEqual([v["bsns_year"] for u,v in session.calls[1:]],
                         ["2022","2023","2024"])
        self.assertEqual(saved[0][0],2025)
        self.assertEqual(saved[0][1]["completed_source_company_years"],5)
        self.assertEqual(state["four_year_income_observed_count"],5)
        self.assertTrue(all(
            state["records"][c]["years"]["2025"]["CFS"]["values"]["assets"]
            is None for c in CODES))
        self.assertEqual(len(saved),4)
        with self.assertRaisesRegex(bulk.BulkHistoryError,"seed_asof"):
            bulk.verified_current_annual_book_year(
                market,catalog,{**book,"asof":"1999-01-01"},None)
        altered=copy.deepcopy(book)
        altered["records"][0]["annual"]["receipt_id"]="20271018000001"
        self.assertNotIn("005930",
            bulk.verified_current_annual_book_year(
                market,catalog,altered,None))
        swapped=copy.deepcopy(book)
        swapped["records"][0]["annual"]["currency"]="USD"
        self.assertNotIn("005930",
            bulk.verified_current_annual_book_year(
                market,catalog,swapped,None))

    def test_25_issuer_response_limit_and_resume_preserves_first_chunk(self):
        codes=[f"{i:06d}" for i in range(1,31)]
        corpcodes={code:f"{i+10000:08d}" for i,code in enumerate(codes)}
        reverse={v:k for k,v in corpcodes.items()}
        class BatchSource:
            def __init__(self,fail_after=None):
                self.requested=[]
                self.fail_after=fail_after
            def get(self,url,params,timeout):
                chunk=params["corp_code"].split(",")
                self.requested.append(chunk)
                if self.fail_after is not None and (
                        len(self.requested)>self.fail_after):
                    raise ConnectionError("never log source error text")
                rows=[]
                for corp in chunk:
                    code=reverse[corp]
                    for nm,sj,val in (
                            ("매출액","IS","1000000000"),
                            ("영업이익","IS","100000000"),
                            ("자산총계","BS","2000000000"),
                            ("부채총계","BS","500000000"),
                            ("자본총계","BS","1500000000")):
                        rows.append({
                            "stock_code":code,"corp_code":corp,
                            "bsns_year":"2025","reprt_code":"11011",
                            "fs_div":"CFS","sj_div":sj,"account_nm":nm,
                            "thstrm_amount":val,"currency":"KRW",
                            "rcept_no":"20260318000001"})
                return FakeResponse({"status":"000","list":rows})
        chunks=[]
        one=BatchSource()
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                     "safe_source_call_budget_reached"):
            bulk.fetch_group(
                one,"mock",DAY,[2025],codes,corpcodes,
                max_requests=1,
                on_year=lambda batch,year:chunks.append(copy.deepcopy(batch)))
        self.assertEqual(len(one.requested),1)
        self.assertEqual(len(one.requested[0]),25)
        self.assertEqual(len(chunks),1)
        self.assertEqual(len(chunks[0]),25)
        self.assertEqual(sum(len(v) for v in chunks[0].values()),25)
        prior={code:years for code,years in chunks[0].items()}
        remainder=BatchSource()
        finished,calls=bulk.fetch_group(
            remainder,"mock",DAY,[2025],codes,corpcodes,prior_years=prior)
        self.assertEqual(calls,1)
        self.assertEqual(len(remainder.requested[0]),5)
        self.assertEqual(len(finished),30)
        self.assertTrue(all("2025" in v for v in finished.values()))
        self.assertEqual(finished[codes[0]],prior[codes[0]])
        errors=BatchSource(fail_after=1)
        preserved=[]
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                     "dart_bulk_http_connection_failed"):
            bulk.fetch_group(errors,"mock",DAY,[2025],codes,corpcodes,
                on_year=lambda batch,year:preserved.append(batch))
        self.assertEqual(len(preserved),1)
        self.assertEqual(len(preserved[0]),25)

    def test_live_http_failure_preserves_last_valid_year_and_resumes_without_refetch(self):
        market,catalog=fixture()
        saved=[]
        class Outage(FakeDART):
            def get(self,url,params,timeout):
                if url.endswith("fnlttMultiAcnt.json") and params["bsns_year"]=="2023":
                    self.calls.append((url,params))
                    raise ConnectionError("untrusted-secret-in-provider-error")
                return super().get(url,params,timeout)
        first=Outage()
        def checkpoint(snapshot,year):
            saved.append((year,copy.deepcopy(snapshot)))
        with self.assertRaisesRegex(bulk.BulkHistoryError,
                                     "dart_bulk_http_connection_failed"):
            bulk.collect(market,catalog,None,key="synthetic-key-not-secret-123",
                         session=first,groups=1,on_progress=checkpoint)
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0][0],2022)
        self.assertEqual(saved[0][1]["attempted_company_count"],0)
        self.assertEqual(saved[0][1]["completed_source_company_years"],5)
        self.assertEqual(saved[0][1]["records"]["005930"]["years"].keys(),
                         {"2022"})
        recovered=FakeDART()
        state,requests_made=bulk.collect(
            market,catalog,saved[0][1],key="synthetic-key-not-secret-123",
            session=recovered,groups=1,on_progress=checkpoint)
        self.assertEqual(requests_made,4) # mapping + 2023/2024/2025
        self.assertEqual([v["bsns_year"] for u,v in recovered.calls[1:]],
                         ["2023","2024","2025"])
        self.assertEqual(state["four_year_income_observed_count"],5)
        self.assertEqual(state["attempted_company_count"],5)
        self.assertEqual(len(saved),4) # previous 2022 + next 3 checkpoints
        self.assertEqual(saved[-1][1]["completed_source_company_years"],20)
        self.assertFalse(state["four_year_complete_financial15_certified"])

    def test_checkpoint_rejects_cross_company_wrong_year_and_changes(self):
        market,catalog=fixture()
        good={CODES[0]:{"2022":{"CFS":{
            "receipt_id":"20230318000001",
            "values":{"revenue":2000},"ambiguous":[]}}}}
        first=bulk.partial_year_checkpoint(market,catalog,None,good)
        self.assertEqual(first["completed_source_company_years"],1)
        self.assertEqual(first["four_year_income_observed_count"],0)
        self.assertEqual(first["attempted_company_count"],0)
        self.assertEqual(bulk.partial_year_checkpoint(
            market,catalog,first,good)["records"],first["records"])
        for change,wrong in (
            ("wrong_code",{"999999":{"2022":{}}}),
            ("wrong_year",{CODES[0]:{"2027":{}}}),
            ("wrong_scope",{CODES[0]:{"2023":{"UNKNOWN":{}}}})):
            with self.subTest(change=change),self.assertRaises(bulk.BulkHistoryError):
                bulk.partial_year_checkpoint(market,catalog,first,wrong)
        with self.assertRaisesRegex(bulk.BulkHistoryError,"conflicting_saved"):
            bulk.partial_year_checkpoint(market,catalog,first,{
                CODES[0]:{"2022":{"CFS":{"receipt_id":"20230318000002"}}}})

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
