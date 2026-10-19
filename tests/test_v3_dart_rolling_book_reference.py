"""Offline accounting, value and stale-date tests of live-listing rolling DART."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import v3_dart_rolling_book_reference as mod

DAY="2026-10-19"


def fixture():
    rows=[
      {"code":"005930","name":"실제조회대상A","market":"KOSPI","price":65000,
       "price_date":DAY,"flows_confirmed":False,"pbr":None,
       "score":75,"coverage_pct":55},
      {"code":"247540","name":"실제조회대상B","market":"KOSDAQ","price":12000,
       "price_date":DAY,"flows_confirmed":False,"pbr":None,
       "score":63,"coverage_pct":49},
    ]
    daily={"version":"2.0","status":"success","date":DAY,
           "candidate_count":2,"history_usable_count":2,"stocks":rows,
           "eligible_count":5,"examined_count":5,"history_deferred_count":0,
           "history_scan_complete":True,"error_count":1,
           "collection_errors":[["300003","가격이력","price_zero_latest_volume"]],
           "history_exclusions":[["300004","insufficient_valid_price_history"],
                                 ["300005","insufficient_valid_price_history"]],
           "flow_confirmed_count":0,"fundamental_count":0}
    names=[("005930","KOSPI","priced",7000),
           ("247540","KOSDAQ","priced",1200),
           ("300003","KOSPI","price_source_or_validation_error",50),
           ("300004","KOSDAQ","insufficient_history",800),
           ("300005","KOSDAQ","insufficient_history",650)]
    catalog={
      "schema":"v22-current-listed-catalog-0.1","date":DAY,
      "listed_total":5,"priced_count":2,
      "rows":[{"code":code,"name":"회사"+code,"market":market,
               "price_history_status":status,"reference_market_cap_eok":cap}
              for code,market,status,cap in names]}
    return daily,catalog


def report(code,*,scope="OFS",receipt="20260312001140",equity=400_000_000_000):
    return {"code":code,"status":"observed","reports":[{
        "year":2025,"fs_div":scope,"receipt_id":receipt,"currency":"KRW",
        "values":{"equity":equity,
                  "owners_equity":equity/2 if scope=="CFS" else None,
                  "revenue":700_000_000_000,"operating_income":30_000_000_000,
                  "operating_cashflow":70_000_000_000},
        "source":"OpenDART fnlttSinglAcntAll annual"
    }]}


class RollingCurrentListedDARTTests(unittest.TestCase):
    def test_choose_highest_actual_score_first_then_whole_live_universe(self):
        daily,catalog=fixture()
        day,members,history,targets=mod.plan(daily,catalog,limit=2)
        self.assertEqual(day,DAY)
        self.assertEqual([r["code"] for r in targets],["005930","247540"])
        self.assertEqual(len(members),5)
        self.assertFalse(history)
        first=mod.merge(daily,catalog,None,[report("005930"),report("247540")],limit=2)
        self.assertEqual(first["new_attempts_this_run"],2)
        self.assertEqual(first["attempted_reference_count"],2)
        self.assertEqual(first["observed_annual_count"],2)
        self.assertEqual(first["pbr_simple_reference_count"],2)
        self.assertFalse(first["official_exchange_pbr_verified"])
        self.assertFalse(first["score_weight_changed"])
        self.assertFalse(first["stock_buy_certified"])
        self.assertFalse(first["historical_point_in_time_verified"])
        self.assertAlmostEqual(first["observations"][0]["pbr_simple_annual_reference"],1.75)
        self.assertAlmostEqual(first["observations"][1]["pbr_simple_annual_reference"],.3)
        self.assertNotIn("stock_price",json.dumps(first))
        self.assertNotIn("daily_ohlcv",json.dumps(first))
        self.assertEqual([r["code"] for r in mod.plan(daily,catalog,prior=first,limit=2)[3]],
                         ["300004","300005"])
        second=mod.merge(daily,catalog,first,
                         [report("300004"),report("300005")],limit=2)
        self.assertEqual(second["attempted_reference_count"],4)
        self.assertEqual(second["new_attempts_this_run"],2)
        self.assertEqual([r["code"] for r in mod.plan(daily,catalog,prior=second,limit=2)[3]],
                         ["300003"])
        third=mod.merge(daily,catalog,second,[{"code":"300003","status":"unconfirmed",
                          "reports":[]}],limit=2)
        self.assertEqual(third["attempted_reference_count"],5)
        self.assertEqual(third["observed_annual_count"],4)
        self.assertEqual(third["other_listed_without_book_observation"],1)
        self.assertEqual([r["code"] for r in mod.plan(daily,catalog,prior=third,limit=2)[3]],
                         ["300003"],"retry unknown only after visiting all 5 current stocks")

    def test_scope_and_missing_equity_does_not_mint_fake_official_pbr(self):
        daily,catalog=fixture()
        data=mod.merge(daily,catalog,None,[
            report("005930",scope="CFS"),
            report("247540",scope="OFS",equity=None)],limit=2)
        vals={v["code"]:v for v in data["observations"]}
        self.assertAlmostEqual(vals["005930"]["pbr_simple_annual_reference"],3.5,
                               msg="consolidated equity uses OWNERS equity only")
        self.assertIsNone(vals["247540"]["pbr_simple_annual_reference"])
        self.assertEqual(data["pbr_simple_reference_count"],1)
        self.assertFalse(vals["005930"]["official_exchange_pbr_verified"])
        self.assertTrue(all(not x["financial_statement_pit_verified"] for x in data["observations"]))

    def test_receipt_from_future_and_unknown_values_never_become_annual_fact(self):
        daily,catalog=fixture()
        later=report("005930",receipt="20271019001140")
        good=report("247540")
        result=mod.merge(daily,catalog,None,[later,good],limit=2)
        self.assertEqual(result["observed_annual_count"],1)
        self.assertEqual(result["pbr_simple_reference_count"],1)
        self.assertEqual(next(r for r in result["records"] if r["code"]=="005930")["status"],
                         "unconfirmed")
        self.assertFalse(result["raw_dart_account_rows_published"])

    def test_bad_source_market_list_future_or_duplicate_rejected(self):
        daily,catalog=fixture()
        bad=copy.deepcopy(catalog)
        bad["rows"][0]["code"]="999999"
        bad["rows"][1]["code"]="999999"
        with self.assertRaises(ValueError):
            mod.plan(daily,bad)
        bad=copy.deepcopy(catalog)
        bad["date"]="2026-10-16"
        with self.assertRaisesRegex(ValueError,"catalog"):
            mod.plan(daily,bad)
        with self.assertRaisesRegex(ValueError,"overlarge"):
            mod.plan(daily,catalog,limit=100)
        with self.assertRaisesRegex(ValueError,"incomplete"):
            mod.merge(daily,catalog,None,[report("005930")],limit=2)
        with self.assertRaisesRegex(ValueError,"identity"):
            mod.merge(daily,catalog,None,[report("005930"),report("005930")],limit=2)
        with self.assertRaisesRegex(ValueError,"invalid_market_day"):
            mod._day("2026-02-31")

    def test_prior_live_cap_recalculates_not_stale_2026_pbr_and_delisted_removed(self):
        daily,catalog=fixture()
        earlier=mod.merge(daily,catalog,None,[report("005930"),report("247540")],limit=2)
        nxt=copy.deepcopy(daily)
        cat=copy.deepcopy(catalog)
        nxt["date"]="2026-10-20"
        for row in nxt["stocks"]: row["price_date"]="2026-10-20"
        cat["date"]="2026-10-20"
        cat["rows"][0]["reference_market_cap_eok"]=14000
        result=mod.merge(nxt,cat,earlier,[report("300004"),report("300005")],limit=2)
        val=next(v for v in result["observations"] if v["code"]=="005930")
        self.assertEqual(val["pbr_simple_annual_reference"],3.5,
                         "current cap must change while prior annual observed equity persists")
        self.assertEqual(val["market_cap_reference_day"],"2026-10-20")

    def test_seed_existing_two_real_candidate_annuals_only_if_source_day_matches(self):
        daily,catalog=fixture()
        official={"asof":DAY,"dart":{"stocks":[report("005930")]}}
        targets=mod.plan(daily,catalog,official=official,limit=2)[3]
        self.assertEqual([t["code"] for t in targets],["247540","300004"])
        invalid=copy.deepcopy(official);invalid["asof"]="2026-10-16"
        self.assertEqual([t["code"] for t in mod.plan(daily,catalog,official=invalid,limit=2)[3]],
                         ["005930","247540"])

    def test_publish_atomic_full_candidate_batch_success_failure_and_no_secret(self):
        daily,catalog=fixture()
        calls=[]
        def good(stocks,day,key,years,budget_seconds):
            calls.append((len(stocks),day,years,budget_seconds))
            return [report(x["code"]) for x in stocks]
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/"v3-dart-book.json"
            out=mod.write(daily,catalog,output=dest,key="not-exported-key",
                          collector=good,limit=2)
            self.assertEqual(calls,[(2,DAY,1,300)])
            self.assertEqual(out["observed_annual_count"],2)
            payload=dest.read_text(encoding="utf8")
            self.assertNotIn("not-exported-key",payload)
            self.assertNotIn("fnlttSinglAcntAll.json",payload)
            saved=dest.read_bytes()
            with self.assertRaisesRegex(ValueError,"partial_batch"):
                mod.write(daily,catalog,output=dest,key="not-exported-key",
                          collector=lambda *a,**k:[],limit=2)
            self.assertEqual(dest.read_bytes(),saved,
                             "failed provider result cannot overwrite prior good book values")
            with self.assertRaisesRegex(ValueError,"credentials"):
                mod.write(daily,catalog,output=dest,collector=good,limit=2)
            self.assertEqual(dest.read_bytes(),saved)


if __name__=="__main__":
    unittest.main()
