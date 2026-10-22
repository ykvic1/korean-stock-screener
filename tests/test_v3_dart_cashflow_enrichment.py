"""Offline contract tests for cash flow source acquisition. Zero live calls."""
import copy
import unittest
from unittest.mock import patch

from test_v3_dart_rolling_book_reference import fixture, DAY
from test_v3_dart_bulk_history import CODES, CORPS, mapping_zip
import v3_dart_cashflow_enrichment as cf


def live_inputs():
    market,catalog=fixture()
    entries={}
    for code in CODES:
        years={}
        for year in range(2022,2026):
            years[str(year)]={"CFS":{"receipt_id":f"{year+1}0318000001",
                "values":{"revenue":100000000+year,"operating_income":10000000}}}
        entries[code]={"years":years}
    bulk={"schema":"v3-dart-multi-company-four-year-major-account-0.1",
          "asof":DAY,"listed_total":5,"fiscal_years":[2022,2023,2024,2025],
          "historical_PIT_source_certified":False,"records":entries}
    return market,catalog,bulk


def rows(year,code,*,skip_intangible=False,mixed=False,wrong_year=False):
    receipt=f"{year+1}0318000001"
    values=[
        ("ifrs-full_CashFlowsFromUsedInOperatingActivities","1200000000"),
        ("ifrs-full_PurchaseOfPropertyPlantAndEquipment","300000000"),
        ("ifrs-full_PurchaseOfIntangibleAssets","100000000"),
    ]
    ans=[]
    for key,amount in values:
        if skip_intangible and "Intangible" in key:
            continue
        ans.append({"corp_code":CORPS[code],"stock_code":code,
                    "bsns_year":str(year-1 if wrong_year else year),
                    "reprt_code":"11011","sj_div":"CF",
                    "account_detail":"-","fs_div":"CFS",
                    "rcept_no":f"{year+1}0325000002" if mixed and key.endswith("IntangibleAssets")
                    else receipt,"currency":"KRW",
                    "account_id":key,"thstrm_amount":amount})
    return ans


class FakeResponse:
    def __init__(self,body=None,content=None):
        self.body=body
        self.content=content
    def json(self):
        return self.body
    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self,fail_year=None,missing_capex=None):
        self.calls=[]
        self.fail_year=fail_year
        self.missing_capex=missing_capex
    def get(self,url,params,timeout):
        self.calls.append((url,params))
        if url.endswith("corpCode.xml"):
            return FakeResponse(content=mapping_zip())
        year=int(params["bsns_year"])
        if year==self.fail_year:
            raise ConnectionError("synthetic API key must never leak")
        corp=params["corp_code"]
        code={v:k for k,v in CORPS.items()}[corp]
        return FakeResponse({"status":"000","list":rows(
            year,code,skip_intangible=(year==self.missing_capex))})


class CashflowSourceTests(unittest.TestCase):
    def test_40_issuer_hourly_budget_and_exact_source_only_resumption(self):
        market,catalog,bulk=live_inputs()
        # Exercise the production upper bound without depending on the small
        # five-company fixture, provider credentials, or network access.
        codes=[f"{i:06d}" for i in range(1,41)]
        members={code:{"market":"KOSPI" if i%2 else "KOSDAQ"}
                 for i,code in enumerate(codes)}
        receipts={str(y):{"CFS":{"receipt_id":f"{y+1}0318000001",
                     "values":{"revenue":100000000,"operating_income":100000}}}
                  for y in range(2022,2026)}
        bulk["listed_total"]=40
        bulk["records"]={code:{"years":copy.deepcopy(receipts)} for code in codes}
        corp_map={code:f"{i+100000:08d}" for i,code in enumerate(codes)}

        class FortyCompanySession:
            def __init__(self):
                self.calls=[]
            def get(self,url,params,timeout):
                self.calls.append((url,params))
                year=int(params["bsns_year"])
                corp=params["corp_code"]
                code=next(c for c,v in corp_map.items() if v==corp)
                receipt=f"{year+1}0318000001"
                entries=[
                    ("ifrs-full_CashFlowsFromUsedInOperatingActivities","1200"),
                    ("ifrs-full_PurchaseOfPropertyPlantAndEquipment","300"),
                    ("ifrs-full_PurchaseOfIntangibleAssets","100"),
                ]
                return FakeResponse({"status":"000","list":[
                    {"corp_code":corp,"stock_code":code,"bsns_year":str(year),
                     "reprt_code":"11011","fs_div":"CFS","sj_div":"CF",
                     "account_detail":"-","rcept_no":receipt,"currency":"KRW",
                     "account_id":key,"thstrm_amount":amount}
                    for key,amount in entries]})

        with patch.object(cf,"asof_members",return_value=(DAY,members)), \
             patch.object(cf,"verified_company_mapping_cache",return_value=corp_map):
            with self.assertRaisesRegex(cf.CashflowError,"invalid_cashflow_max_issuers"):
                cf.plan(market,catalog,bulk,limit=41)
            source=FortyCompanySession()
            saved=[]
            state,calls=cf.collect(market,catalog,bulk,None,
                key="synthetic-test-dart-key-without-real-secrets",session=source,
                issuer_limit=40,on_progress=lambda snapshot:saved.append(snapshot))
            self.assertEqual(calls,120)
            self.assertEqual(len(source.calls),120)
            self.assertEqual(len(saved),120)
            self.assertEqual(state["metrics"]["attempted_company_years"],120)
            self.assertEqual(state["metrics"]["fcf_company_years"],120)
            self.assertEqual(state["metrics"]["three_year_fcf_receipt_matched_issuers"],40)
            self.assertTrue(cf.verify_state(state,market,catalog,bulk))
            no_repeat=FortyCompanySession()
            repeated,n=cf.collect(market,catalog,bulk,state,
                key="synthetic-test-dart-key-without-real-secrets",session=no_repeat,
                issuer_limit=40)
            self.assertEqual(n,0)
            self.assertEqual(no_repeat.calls,[])
            self.assertEqual(repeated,state)

    def test_same_receipt_three_year_full_statement_no_guessing_and_resume(self):
        market,catalog,bulk=live_inputs()
        prior=[]
        sess=FakeSession()
        snapshot,calls=cf.collect(
            market,catalog,bulk,None,key="synthetic-non-sensitive-DART-api-key",
            session=sess,issuer_limit=4,on_progress=lambda x:prior.append(copy.deepcopy(x)))
        self.assertEqual(calls,13) # mapping + 4 firms x 3 FYs
        self.assertEqual(snapshot["metrics"]["attempted_company_years"],12)
        self.assertEqual(snapshot["metrics"]["valid_cashflow_company_years"],12)
        self.assertEqual(snapshot["metrics"]["complete_capex_pair_company_years"],12)
        self.assertEqual(snapshot["metrics"]["fcf_company_years"],12)
        self.assertEqual(snapshot["metrics"]["three_year_fcf_receipt_matched_issuers"],4)
        self.assertEqual(snapshot["metrics"]["most_recent_year_receipt_matched_fcf_issuers"],4)
        self.assertEqual(snapshot["records"]["005930"]["years"]["2025"]["free_cashflow_krw"],
                         800000000)
        self.assertFalse(snapshot["three_year_fcf_financial15_certified"])
        self.assertFalse(snapshot["official_six_pillar_100pt_certified"])
        self.assertTrue(cf.verify_state(snapshot,market,catalog,bulk))
        self.assertEqual(len(prior),12)
        another=FakeSession()
        resumed,n=cf.collect(market,catalog,bulk,snapshot,
            key="synthetic-non-sensitive-DART-api-key",
            session=another,issuer_limit=3)
        self.assertEqual(n,4) # the fifth issuer's 3 fiscal years + master
        self.assertEqual(resumed["metrics"]["three_year_fcf_receipt_matched_issuers"],5)

    def test_new_trading_day_carries_only_unchanged_filing_evidence(self):
        market,catalog,bulk=live_inputs()
        earlier,_=cf.collect(
            market,catalog,bulk,None,
            key="synthetic-non-sensitive-DART-api-key",
            session=FakeSession(),issuer_limit=2)
        next_market=copy.deepcopy(market)
        next_catalog=copy.deepcopy(catalog)
        next_bulk=copy.deepcopy(bulk)
        next_day="2026-10-20"
        next_market["date"]=next_day
        for item in next_market["stocks"]:
            item["price_date"]=next_day
        next_catalog["date"]=next_day
        next_bulk["asof"]=next_day
        preserved=cf.retained_current_market_cashflow(
            next_market,next_catalog,next_bulk,earlier)
        self.assertEqual(preserved["asof"],next_day)
        self.assertEqual(preserved["metrics"],earlier["metrics"])
        self.assertEqual(preserved["records"],earlier["records"])
        self.assertTrue(cf.verify_state(
            preserved,next_market,next_catalog,next_bulk))
        _,_,previous_records,targets=cf.plan(
            next_market,next_catalog,next_bulk,earlier,limit=2)
        self.assertEqual(previous_records,earlier["records"])
        self.assertTrue(all(t[1] not in earlier["records"] for t in targets))
        session=FakeSession()
        continued,_=cf.collect(
            next_market,next_catalog,next_bulk,earlier,
            key="synthetic-non-sensitive-DART-api-key",
            session=session,issuer_limit=2)
        self.assertEqual(continued["asof"],next_day)
        self.assertEqual(continued["metrics"]["attempted_company_years"],12)
        self.assertEqual(len(session.calls),7)  # new master plus 2x3 FY
        altered=copy.deepcopy(next_bulk)
        altered["records"]["005930"]["years"]["2024"]["CFS"]["receipt_id"]=(
            "20250318000002")
        blocked=FakeSession()
        with self.assertRaisesRegex(
            cf.CashflowError,"cashflow_major_receipt_changed"):
            cf.collect(next_market,next_catalog,altered,earlier,
                key="synthetic-non-sensitive-DART-api-key",
                session=blocked,issuer_limit=2)
        self.assertEqual(blocked.calls,[])
        tampered=copy.deepcopy(earlier)
        tampered["metrics"]["fcf_company_years"]+=1
        with self.assertRaisesRegex(
            cf.CashflowError,"untrusted_previous_cashflow_source"):
            cf.retained_current_market_cashflow(
                next_market,next_catalog,next_bulk,tampered)

    def test_major_archive_growth_reuses_receipts_but_revision_fails_closed(self):
        market,catalog,bulk=live_inputs()
        saved,_=cf.collect(
            market,catalog,bulk,None,
            key="synthetic-non-sensitive-DART-api-key",
            session=FakeSession(),issuer_limit=2)
        grown=copy.deepcopy(bulk)
        # A new independent FY report observation should not invalidate
        # historical cashflow already tied to its own genuine receipt.
        grown["records"]["300005"]["years"]["2022"]["CFS"]["values"]["revenue"]+=3
        self.assertTrue(cf.verify_state(
            saved,market,catalog,grown,strict_fingerprint=False))
        with self.assertRaisesRegex(cf.CashflowError,
                                    "cashflow_source_changed_after_rebase"):
            cf.verify_state(saved,market,catalog,grown)
        changed=copy.deepcopy(grown)
        changed["records"]["005930"]["years"]["2025"]["CFS"]["receipt_id"]=(
            "20260318000002")
        with self.assertRaisesRegex(cf.CashflowError,
                                    "cashflow_major_receipt_changed"):
            cf.verify_state(saved,market,catalog,changed,
                            strict_fingerprint=False)
        source=FakeSession()
        with self.assertRaisesRegex(cf.CashflowError,
                                    "cashflow_major_receipt_changed"):
            cf.collect(market,catalog,changed,saved,
                key="synthetic-non-sensitive-DART-api-key",session=source)
        self.assertEqual(source.calls,[], "reject conflicting source before HTTP")

    def test_partial_checkpoint_after_real_http_failure_and_no_retry_of_saved_year(self):
        market,catalog,bulk=live_inputs()
        snapshots=[]
        with self.assertRaisesRegex(cf.CashflowError,"cashflow_provider_http_or_json_failure"):
            cf.collect(market,catalog,bulk,None,
                 key="synthetic-non-sensitive-DART-api-key",
                 session=FakeSession(fail_year=2024),
                 issuer_limit=2,on_progress=lambda x:snapshots.append(copy.deepcopy(x)))
        self.assertEqual(len(snapshots),1)
        self.assertEqual(snapshots[0]["metrics"]["attempted_company_years"],1)
        session=FakeSession()
        resumed,n=cf.collect(market,catalog,bulk,snapshots[0],
            key="synthetic-non-sensitive-DART-api-key",
            session=session,issuer_limit=2)
        self.assertEqual(n,6) # master + missing five company-year requests
        self.assertEqual(len(session.calls),6)
        self.assertEqual(resumed["metrics"]["three_year_fcf_receipt_matched_issuers"],2)

    def test_strict_missing_intangible_never_zero_capex(self):
        market,catalog,bulk=live_inputs()
        data,calls=cf.collect(market,catalog,bulk,None,
            key="synthetic-non-sensitive-DART-api-key",
            session=FakeSession(missing_capex=2024),issuer_limit=2)
        self.assertEqual(calls,7)
        self.assertEqual(data["metrics"]["valid_cashflow_company_years"],6)
        self.assertEqual(data["metrics"]["complete_capex_pair_company_years"],4)
        self.assertEqual(data["metrics"]["fcf_company_years"],4)
        self.assertEqual(data["metrics"]["three_year_fcf_receipt_matched_issuers"],0)
        value=data["records"]["005930"]["years"]["2024"]
        self.assertIsNone(value["free_cashflow_krw"])
        self.assertIsNone(value["total_capex_krw"])
        self.assertEqual(value["ppe_capex_krw"],300000000)
        self.assertIsNone(value["intangible_capex_krw"])

    def test_provider_identity_future_receipt_mixed_revision_wrong_cash_sign_rejected(self):
        y=2025
        args={"code":"005930","corp":CORPS["005930"],"year":y,"scope":"CFS",
              "asof":DAY,"major_receipt":"20260318000001"}
        data=rows(y,"005930")
        with self.assertRaisesRegex(cf.CashflowError,"invalid_cashflow_receipt"):
            cf.normalize_year([dict(data[0],rcept_no="20271118000001")]+data[1:],**args)
        with self.assertRaisesRegex(cf.CashflowError,"single_full_statement_issuer"):
            cf.normalize_year([dict(data[0],corp_code="00000001")]+data[1:],**args)
        with self.assertRaisesRegex(cf.CashflowError,"mixed_cashflow_receipt"):
            cf.normalize_year(rows(y,"005930",mixed=True),**args)
        with self.assertRaisesRegex(cf.CashflowError,"non_krw"):
            cf.normalize_year([dict(data[0],currency="USD")]+data[1:],**args)
        negative=[dict(t) for t in data]
        negative[1]["thstrm_amount"]="-300000000"
        result=cf.normalize_year(negative,**args)
        self.assertIsNone(result["free_cashflow_krw"])
        self.assertIsNone(result["total_capex_krw"])
        self.assertEqual(result["operating_cashflow_krw"],1200000000)
        ambiguous=data+[dict(data[1],thstrm_amount="310000000")]
        result=cf.normalize_year(ambiguous,**args)
        self.assertIsNone(result["ppe_capex_krw"])
        self.assertIsNone(result["free_cashflow_krw"])

    def test_prior_summary_and_cashflow_max_points_never_promoted(self):
        market,catalog,bulk=live_inputs()
        s,n=cf.collect(market,catalog,bulk,None,
            key="synthetic-non-sensitive-DART-api-key",
            session=FakeSession(),issuer_limit=2)
        fake=copy.deepcopy(s)
        fake["metrics"]["fcf_company_years"]+=1
        with self.assertRaisesRegex(cf.CashflowError,"cashflow_summary_or_snapshot_tampered"):
            cf.verify_state(fake,market,catalog,bulk)
        fake=copy.deepcopy(s)
        fake["records"]["005930"]["years"]["2025"]["free_cashflow_krw"]+=1
        with self.assertRaisesRegex(cf.CashflowError,"untrusted_cashflow_fcf_formula"):
            cf.verify_state(fake,market,catalog,bulk)
        fake=copy.deepcopy(s)
        fake["official_six_pillar_100pt_certified"]=True
        with self.assertRaisesRegex(cf.CashflowError,"invalid_cashflow_snapshot_identity"):
            cf.verify_state(fake,market,catalog,bulk)
        changed=copy.deepcopy(bulk)
        changed["records"]["005930"]["years"]["2025"]["CFS"]["values"]["revenue"]+=1
        with self.assertRaisesRegex(cf.CashflowError,"cashflow_source_changed_after_rebase"):
            cf.verify_state(s,market,catalog,changed)


if __name__=="__main__":
    unittest.main()
