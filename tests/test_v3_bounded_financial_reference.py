"""Bounded real DART optional research coverage, mocked; no keys or network."""
from __future__ import annotations
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from zipfile import ZipFile

import official_data

DAY="2026-10-16"
def make_market(n=140):
    return {"status":"success","version":"2.0","date":DAY,"stocks":[
        {"code":f"{100000+i:06d}","name":f"Synthetic {i}",
         "market":"KOSPI" if i%2 else "KOSDAQ",
         "price_date":DAY,"price":10000+i,"market_cap_eok":500+i}
        for i in range(n)
    ]}

def make_dossiers():
    return {"asof":DAY,"research_source_matched":True,
            "dossiers":[
                {"code":"100139","qualification":"needs_deeper_evidence"},
                {"code":"100138","qualification":"separate_caution_research_only"},
                {"code":"100137","qualification":"verified_recommendation"}
            ]}

class BoundedOfficialFinances(unittest.TestCase):
    def test_capped_top20_and_no_raw_entries_on_optional_daily_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            market=Path(tmp)/"data.json"
            reports=Path(tmp)/"recommendations.json"
            output=Path(tmp)/"official.json"
            market.write_text(json.dumps(make_market()),encoding="utf8")
            reports.write_text(json.dumps(make_dossiers()),encoding="utf8")
            observed=[{"code":f"{100000+i:06d}","status":"unconfirmed"}
                      for i in range(20)]
            observed[:3]=[{"code":code,"status":"observed","reports":[
                {"year":2025,"fs_div":"CFS","source":"OpenDART fnlttSinglAcntAll annual",
                 "values":{"revenue":12000000000,"operating_income":900000000},
                 "currency":"KRW","revision_history_verified":False}
            ]} for code in ("100139","100138","100137")]
            with patch.dict("os.environ",{"DART_API_KEY":"secret-research-test-key"},
                            clear=True):
                with patch.object(official_data,"dart_collect",return_value=observed) as coll:
                    res=official_data.collect(market,output,reports)
            args,kwargs=coll.call_args
            self.assertEqual(len(args[0]),20)
            self.assertEqual([x["code"] for x in args[0][:3]],
                             ["100139","100138","100137"])
            self.assertEqual(args[2],"secret-research-test-key")
            self.assertEqual(kwargs["budget_seconds"],360)
            self.assertEqual(res["dart"]["requested"],20)
            self.assertEqual(res["dart"]["universe"],140)
            self.assertEqual(res["dart"]["observed"],3)
            self.assertEqual(res["dart"]["status"],"observed")
            disk=output.read_text()
            self.assertNotIn("secret-research-test-key",disk)
            self.assertNotIn('"accounts"',disk)
            self.assertNotIn('"raw_ohlcv"',disk)
            self.assertTrue(res["financial_reference_not_automatically_scored"])

    def test_without_dart_key_keeps_null_without_fabricated_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            market=Path(tmp)/"data.json"
            market.write_text(json.dumps(make_market()),encoding="utf8")
            out=Path(tmp)/"official.json"
            with patch.dict("os.environ",{},clear=True):
                result=official_data.collect(market,out,Path(tmp)/"missing.json")
            self.assertEqual(result["dart"]["status"],"key_required")
            self.assertEqual(result["dart"]["requested"],20)
            self.assertFalse("stocks" in result["dart"])
            self.assertEqual(result["backtest_included"],False)

    def test_dart_collection_derives_accounts_and_hides_original_rows(self):
        xml=b"<result><list><corp_code>00126380</corp_code><stock_code>100139</stock_code></list></result>"
        out=BytesIO()
        with ZipFile(out,"w") as z:z.writestr("CORPCODE.xml",xml)
        response=Mock()
        response.content=out.getvalue()
        response.raise_for_status.return_value=None
        session=Mock();session.get.return_value=response
        rcp="20260331001234"
        annual=[
            {"account_id":"ifrs-full_Revenue","currency":"KRW",
             "thstrm_amount":"12000000000","rcept_no":rcp,"account_detail":"-"},
            {"account_id":"dart_OperatingIncomeLoss","currency":"KRW",
             "thstrm_amount":"900000000","rcept_no":rcp,"account_detail":"-"},
            {"account_id":"ifrs-full_CashFlowsFromUsedInOperatingActivities",
             "currency":"KRW","thstrm_amount":"1000000000","rcept_no":rcp,
             "account_detail":"-"},
            {"account_id":"ifrs-full_Liabilities","currency":"KRW",
             "thstrm_amount":"10000000000","rcept_no":rcp,"account_detail":"-"},
            {"account_id":"ifrs-full_Equity","currency":"KRW",
             "thstrm_amount":"5000000000","rcept_no":rcp,"account_detail":"-"},
        ]
        def fake_dart(s,endpoint,key,**params):
            return annual if params["bsns_year"]=="2025" and params["fs_div"]=="CFS" else []
        with patch("requests.Session",return_value=session),patch.object(
                official_data,"dart_json",side_effect=fake_dart),patch.object(
                official_data.time,"sleep"):
            result=official_data.dart_collect(
                [{"code":"100139","market_cap_eok":500}],DAY,"fake",years=3)
        self.assertEqual(result[0]["status"],"observed")
        report=result[0]["reports"][0]
        self.assertEqual(report["year"],2025)
        self.assertEqual(report["values"]["revenue"],12000000000)
        self.assertEqual(report["values"]["operating_income"],900000000)
        self.assertEqual(report["values"]["operating_cashflow"],1000000000)
        self.assertFalse(report["revision_history_verified"])
        self.assertNotIn("accounts",report)
        self.assertNotIn("crtfc_key",json.dumps(result))

    def test_exhausted_budget_marks_deferred_not_zero_or_fake(self):
        xml=b"<result><list><corp_code>00126380</corp_code><stock_code>100139</stock_code></list></result>"
        buf=BytesIO()
        with ZipFile(buf,"w") as z:z.writestr("CORPCODE.xml",xml)
        response=Mock(content=buf.getvalue())
        session=Mock();session.get.return_value=response
        with patch("requests.Session",return_value=session),patch.object(
                official_data.time,"monotonic",side_effect=[0,400]),patch.object(
                official_data,"dart_json") as call:
            result=official_data.dart_collect(
                [{"code":"100139","market_cap_eok":100}],DAY,"fake",
                budget_seconds=360)
        self.assertEqual(result[0]["status"],"deferred")
        call.assert_not_called()

    def test_user_cannot_turn_on_unbounded_2300_company_requests(self):
        with self.assertRaises(ValueError):
            official_data.dart_collect([{"code":f"{i:06d}"} for i in range(21)],
                                      DAY,"fake")
        with self.assertRaises(ValueError):
            official_data.dart_collect([{"code":"005930"}],DAY,"fake",
                                      budget_seconds=86400)

    def test_opendart_quota_account_and_service_errors_stop_immediately(self):
        # Provider often returns HTTP 200 with an application failure status.
        # These are not per-company missing statements (013).
        for status in ("010", "011", "012", "020", "800", "900", "901"):
            with self.subTest(status=status):
                session=Mock()
                response=Mock()
                response.json.return_value={"status":status,"message":"provider response"}
                response.raise_for_status.return_value=None
                session.get.return_value=response
                with self.assertRaisesRegex(official_data.DartStopCollectionError,status):
                    official_data.dart_json(session,"fnlttSinglAcntAll.json",
                                            "test-key",corp_code="00126380",
                                            bsns_year="2025",reprt_code="11011",
                                            fs_div="OFS")
                self.assertEqual(session.get.call_count,1)

        session=Mock()
        response=Mock()
        response.json.return_value={"status":"013"}
        response.raise_for_status.return_value=None
        session.get.return_value=response
        self.assertEqual(official_data.dart_json(
            session,"fnlttSinglAcntAll.json","test-key",
            corp_code="00126380",bsns_year="2025",
            reprt_code="11011",fs_div="OFS"),[])

    def test_quota_stop_does_not_continue_looping_through_20_issuers(self):
        xml=b"<result><list><corp_code>00126380</corp_code><stock_code>100139</stock_code></list><list><corp_code>00126381</corp_code><stock_code>100138</stock_code></list></result>"
        buf=BytesIO()
        with ZipFile(buf,"w") as z:
            z.writestr("CORPCODE.xml",xml)
        response=Mock(content=buf.getvalue())
        response.raise_for_status.return_value=None
        session=Mock()
        session.get.return_value=response
        with patch("requests.Session",return_value=session),patch.object(
            official_data,"dart_json",
            side_effect=official_data.DartStopCollectionError("OpenDART stop status 020")
        ) as dart:
            with self.assertRaisesRegex(official_data.DartStopCollectionError,"020"):
                official_data.dart_collect([
                    {"code":"100139","market_cap_eok":500},
                    {"code":"100138","market_cap_eok":500},
                ],DAY,"test-key",years=1,budget_seconds=300)
            self.assertEqual(dart.call_count,1,
                             "no repeated DART calls after provider quota failure")

if __name__=="__main__":
    unittest.main()
