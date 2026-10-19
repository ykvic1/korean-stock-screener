"""Offline complete listed-universe catalog tests, no API or GitHub state."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from v22_active_market_catalog import (
    ActiveCatalogError,build_active_catalog,write_active_catalog,
)


def fixture():
    rows=[
        {"code":"005930","name":"기업A","market":"KOSPI","price":70000,
         "price_date":"2026-10-16","flows_confirmed":True,"pbr":1.25,
         "flow_observations":20,"foreign_shares":18000,"inst_shares":-3500,
         "flow_start_date":"2026-09-15","flow_end_date":"2026-10-16",
         "flow_source":"public_reference_source","sector":"전기전자"},
        {"code":"247540","name":"기업B","market":"KOSDAQ","price":12000,
         "price_date":"2026-10-16","flows_confirmed":False,"pbr":None,
         "sector":None},
    ]
    snap={"version":"2.0","status":"success","date":"2026-10-16",
          "eligible_count":7,"examined_count":5,"history_deferred_count":2,
          "history_scan_complete":False,"candidate_count":2,
          "history_usable_count":2,"stocks":rows,"error_count":2,
          "collection_errors":[["300003","가격이력","TimeoutError"],
                               ["247540","기관·외국인 수급","Unavailable"]],
          "history_exclusions":[["300004","insufficient_valid_price_history"],
                                ["300005","insufficient_valid_price_history"]],
          "flow_confirmed_count":1,"fundamental_count":1}
    listing={}
    for code,name,market in [
        ("005930","기업A","KOSPI"),("247540","기업B","KOSDAQ"),
        ("300003","기업C","KOSPI"),("300004","기업D","KOSPI"),
        ("300005","기업E","KOSDAQ"),("300006","기업F","KOSDAQ"),
        ("300007","기업G","KOSPI"),
    ]:
        listing[code]={"code":code,"name":name,"market":market,
                       "market_cap_eok":1500,"trading_eok":2,
                       "pbr":None,"sector":None,"valuation_source":None}
    return snap,listing


class ActiveListedMarketCatalogTests(unittest.TestCase):
    def test_all_seven_symbols_appear_with_disjoint_status(self):
        snap,listing=fixture()
        c=build_active_catalog(snap,listing)
        self.assertEqual(c["date"],"2026-10-16")
        self.assertEqual(c["listed_total"],7)
        self.assertEqual(c["priced_count"],2)
        self.assertEqual(c["status_counts"],{
            "priced":2,"price_source_or_validation_error":1,
            "insufficient_history":2,"unattempted_batch":2})
        self.assertEqual(len(c["rows"]),7)
        self.assertEqual({x["code"] for x in c["rows"]},set(listing))
        self.assertEqual(c["pbr_observed_count"],1)
        self.assertEqual(c["flow_reference_count"],1)
        self.assertFalse(c["historical_delisted_stocks_included"])
        self.assertTrue(c["historical_backtest_has_survivorship_bias"])
        self.assertFalse(c["official_v3_six_pillar_score_ready"])
        entries={x["code"]:x for x in c["rows"]}
        self.assertEqual(entries["005930"]["published_price"],70000)
        self.assertEqual(entries["005930"]["pbr_observed"],1.25)
        self.assertEqual(entries["005930"]["flow_reference"]["foreign_20d_shares_observed"],18000)
        self.assertFalse(entries["005930"]["flow_reference"]["independently_verified"])
        self.assertEqual(entries["300003"]["price_history_status"],"price_source_or_validation_error")
        self.assertIsNone(entries["300003"]["published_price"])
        self.assertIsNone(entries["300003"]["flow_reference"])
        self.assertIsNone(entries["300004"]["fundamental_revenue"])

    def test_incomplete_flow_is_nullable_without_blocking_entire_market(self):
        snap,listing=fixture()
        snap["stocks"][0]["foreign_shares"]=None
        result=build_active_catalog(snap,listing)
        row=next(x for x in result["rows"] if x["code"]=="005930")
        self.assertIsNone(row["flow_reference"])
        self.assertEqual(result["flow_reference_unavailable_count"],1)
        self.assertEqual(result["listed_total"],7)

    def test_missing_stock_is_not_silently_deleted_and_delisted_not_added(self):
        snap,listing=fixture()
        listing.pop("300005")
        with self.assertRaises(ActiveCatalogError):
            build_active_catalog(snap,listing)
        snap,listing=fixture()
        listing["111111"]={"code":"111111","name":"지금 없는 상폐 종목",
                           "market":"KOSPI"}
        with self.assertRaises(ActiveCatalogError):
            build_active_catalog(snap,listing)

    def test_reference_flow_absent_does_not_become_zero(self):
        snap,listing=fixture()
        snap["flow_confirmed_count"]=0
        snap["stocks"][0]["flows_confirmed"]=False
        snap["stocks"][0]["foreign_shares"]=None
        snap["stocks"][0]["inst_shares"]=None
        c=build_active_catalog(snap,listing)
        a=next(x for x in c["rows"] if x["code"]=="005930")
        self.assertIsNone(a["flow_reference"])
        self.assertEqual(c["flow_reference_count"],0)

    def test_2500_active_symbols_do_not_clip_to_129_failures(self):
        start=400000
        total=2498
        snap,listing=fixture()
        snap["stocks"]=list(snap["stocks"])
        # Build a large all-current universe with each outcome represented.
        listing={}
        stocks=[]
        excludes=[]
        for i in range(total):
            code=f"{start+i:06d}"
            name=f"회사{i}"
            market="KOSPI" if i%2 else "KOSDAQ"
            listing[code]={"code":code,"name":name,"market":market,
                           "market_cap_eok":50+i,"trading_eok":1+i/100,
                           "pbr":None,"sector":None}
            if i<2369:
                stocks.append({"code":code,"name":name,"market":market,
                               "price":10000+i,"price_date":"2026-10-16",
                               "flows_confirmed":False,"pbr":None})
            else:
                excludes.append([code,"insufficient_valid_price_history"])
        snap.update({"stocks":stocks,"history_exclusions":excludes,
                     "eligible_count":total,"examined_count":total,
                     "history_deferred_count":0,"history_scan_complete":True,
                     "candidate_count":2369,"history_usable_count":2369,
                     "error_count":0,"collection_errors":[],
                     "flow_confirmed_count":0,"fundamental_count":0})
        result=build_active_catalog(snap,listing)
        self.assertEqual(result["listed_total"],2498)
        self.assertEqual(result["priced_count"],2369)
        self.assertEqual(result["status_counts"]["insufficient_history"],129)
        self.assertEqual(len(result["rows"]),2498)

    def test_reject_contradictory_price_data_and_unsafe_stale(self):
        snap,listing=fixture()
        snap["stocks"][0]["price_date"]="2026-10-15"
        with self.assertRaises(Exception):
            build_active_catalog(snap,listing)
        snap,listing=fixture()
        listing["005930"]["name"]="다른회사"
        with self.assertRaisesRegex(ActiveCatalogError,"catalog_derived_identity_mismatch"):
            build_active_catalog(snap,listing)

    def test_public_catalog_file_only_has_research_references_no_raw_ohlcv(self):
        snap,listing=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            dst=Path(tmp)/"docs"/"v22-market-catalog.json"
            summary=write_active_catalog(snap,listing,dst)
            data=json.loads(dst.read_text(encoding="utf-8"))
            self.assertEqual(summary["listed_total"],7)
            self.assertNotIn("rows",summary)
            self.assertEqual(data["listed_total"],7)
            raw=dst.read_text(encoding="utf-8")
            for banned in ["OutBlock_1","raw_sha256","API_KEY","TDD_CLSPRC"]:
                self.assertNotIn(banned,raw)


if __name__=="__main__":
    unittest.main()
