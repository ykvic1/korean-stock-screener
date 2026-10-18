"""No-network market-universe tests: NO arbitrary company-size/stock-count filter.

Every KOSPI/KOSDAQ ordinary share is eligible for discovery. Individual OHLCV
requests are bounded by RUN TIME, not an arbitrary number of securities.
Financial quality is an assessment criterion, NOT a company-size proxy.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import inspect
import unittest

import pandas as pd

import screener


def dataset(n=1800):
    return {
        f"{100000+i:06d}": {
            "code": f"{100000+i:06d}",
            "name": f"synthetic-{i}",
            "market": "KOSPI" if i % 2 else "KOSDAQ",
            # Strong financials have NOT been verified for synthetic rows.
            # Size and current-day liquidity deliberately vary widely.
            "market_cap_eok": [20, 300, 4999, 10000, 50000, 150000][i % 6],
            "trading_eok": [0, 0.01, 1.8, 2, 50, 500][i % 6],
            "sector": None,
            "pbr": None,
            "valuation_source": None,
        }
        for i in range(n)
    }


def select(rows, day):
    return screener.select_universe(rows, screener.cfg_read(), datetime.fromisoformat(day))


class NoMarketSizeOrCountLimitTests(unittest.TestCase):
    def test_config_has_no_arbitrary_company_size_volume_count_or_view_limits(self):
        c = screener.cfg_read()
        for forbidden in ("min_market_cap_eok", "max_market_cap_eok",
                          "min_daily_trading_eok", "preselect_limit",
                          "max_watch_rows", "top_candidates_for_flows"):
            self.assertNotIn(forbidden, c)
        self.assertGreater(c["history_time_budget_seconds"], 120)
        self.assertGreater(c["flow_time_budget_seconds"], 0)

    def test_all_1800_stocks_kept_including_small_and_zero_turnover(self):
        all_rows = dataset()
        result, total = select(all_rows, "2026-10-16")
        self.assertEqual(total, 1800)
        self.assertEqual(len(result), 1800)
        self.assertEqual(set(x["code"] for x in result), set(all_rows))
        self.assertIn("100000", {x["code"] for x in result})  # 20억, 0원 거래

    def test_small_and_large_and_illiquid_are_not_false_financial_rejections(self):
        all_rows = dataset(63)
        selected, count = select(all_rows, "2026-10-19")
        self.assertEqual(count, 63)
        self.assertTrue(all(x["pbr"] is None for x in selected))
        self.assertTrue(any(x["market_cap_eok"] < 5000 for x in selected))
        self.assertTrue(any(x["market_cap_eok"] > 50000 for x in selected))
        self.assertTrue(any(x["trading_eok"] == 0 for x in selected))

    def test_day_rotation_changes_priority_not_eligibility(self):
        d = dataset(1800)
        days = ("2026-10-16", "2026-10-19", "2026-10-20")
        result = [select(d,day)[0] for day in days]
        for rows in result:
            self.assertEqual(len(rows),len(d))
            self.assertEqual(set(x["code"] for x in rows),set(d))
        self.assertNotEqual(result[0][0]["code"],result[1][0]["code"])
        self.assertNotEqual(result[1][0]["code"],result[2][0]["code"])

    def test_weekend_rotation_does_not_spuriously_advance(self):
        d = dataset(1800)
        fri, _ = select(d,"2026-10-16")
        for x in ("2026-10-17","2026-10-18"):
            today, _ = select(d,x)
            self.assertEqual([r["code"] for r in today],
                             [r["code"] for r in fri])

    def test_deterministic_across_dict_order(self):
        d = dataset()
        swapped = dict(reversed(list(d.items())))
        a,_=select(d,"2026-10-19")
        b,_=select(swapped,"2026-10-19")
        self.assertEqual([r["code"] for r in a],[r["code"] for r in b])

    def test_does_not_drop_trading_stocks_based_on_missing_financials(self):
        d=dataset(120)
        for r in d.values():
            r["financial_confirmed"]=False
            r["pbr"]=None
        result,total=select(d,"2026-10-19")
        self.assertEqual(total,120)
        self.assertEqual(len(result),120)

    def test_market_snapshot_includes_genuine_zero_volume_and_small_cap(self):
        data = pd.DataFrame([{
            "Code": str(100000+i),
            "Name": f"보통주{i}",
            "Market": ("KOSPI" if i%2 else "KOSDAQ"),
            "Close": 100 + i,
            "Volume": 0 if i==0 else 3000,
            "Amount": 0 if i==0 else 350000,
            "Marcap": 100000000 if i==0 else 10000000000
        } for i in range(1001)])
        with patch.object(screener.fdr,"StockListing",return_value=data):
            snapshot=screener.market_snapshot()
        self.assertEqual(len(snapshot),1001)
        self.assertEqual(snapshot["100000"]["market_cap_eok"],1)
        self.assertEqual(snapshot["100000"]["trading_eok"],0)

    def test_market_snapshot_excludes_invalid_quotes_not_small_firms(self):
        data = pd.DataFrame([{
            "Code": str(100000+i), "Name": f"보통주{i}", "Market":"KOSDAQ",
            "Close": 100, "Volume": 1, "Amount": 1, "Marcap": 1
        } for i in range(1001)])
        data.loc[0,"Close"] = 0
        data.loc[1,"Volume"] = -1
        data.loc[2,"Marcap"] = 0
        with patch.object(screener.fdr,"StockListing",return_value=data):
            snapshot=screener.market_snapshot()
        self.assertEqual(len(snapshot),998)
        for code in ("100000","100001","100002"):
            self.assertNotIn(code,snapshot)

    def test_full_market_selector_never_uses_network(self):
        with patch.object(screener.fdr,"StockListing",side_effect=AssertionError("network")):
            selected,count=select(dataset(),"2026-10-19")
        self.assertEqual(len(selected),1800)
        self.assertEqual(count,1800)

    def test_price_and_flow_publication_no_fixed_output_slices(self):
        self.assertNotIn("max_watch_rows",inspect.getsource(screener.web_publish))
        self.assertNotIn("max_watch_rows",inspect.getsource(screener.make_report))
        self.assertNotIn("top_candidates_for_flows",inspect.getsource(screener.build_rows))
        self.assertIn("history_time_budget_seconds",inspect.getsource(screener.build_rows))
        self.assertIn("flow_time_budget_seconds",inspect.getsource(screener.build_rows))

    def test_history_time_budget_restricts_cost_not_company_eligibility(self):
        d=dataset(200)
        for r in d.values():
            r["price"]=100
        cfg=screener.cfg_read()
        cfg["history_time_budget_seconds"]=120
        cfg["flow_time_budget_seconds"]=120
        counter=[-1]
        def simulated_clock():
            counter[0]+=1
            return counter[0]*5
        def fake_history(code,cfg,day):
            return {"price_date":"2026-10-16","price_latest":100}
        def score_stub(row,cfg):
            row.update({
                "components":{"일봉 구조":5,"주봉·추세":5,"거래량·OBV":5},
                "score":30,"coverage_pct":25,
                "decision":"추가 검증 필요",
            })
            return row
        class FakeCollector:
            def __init__(self,*args,**kwargs):
                self.attempted=0
            def collect(self,code):
                return None,"deferred: source unavailable"

        with (patch.object(screener,"market_snapshot",return_value=d),
              patch.object(screener,"optional_listings",return_value=[]),
              patch.object(screener,"load_indexes",return_value={}),
              patch.object(screener,"analyze_history",side_effect=fake_history),
              patch.object(screener,"score_one",side_effect=score_stub),
              patch.object(screener,"FlowCollector",FakeCollector),
              patch.object(screener.time,"monotonic",side_effect=simulated_clock),
              patch.object(screener.time,"sleep")):
            rows,errors,day,meta=screener.build_rows(datetime(2026,10,16),cfg)
        self.assertEqual(meta["eligible"],200)
        self.assertTrue(20<=meta["examined"]<200)
        self.assertEqual(meta["history_deferred"],200-meta["examined"])
        self.assertEqual(meta["flow_attempted"],0)
        self.assertEqual(meta["flow_deferred"],len(rows))
        self.assertFalse(meta["history_scan_complete"])
        self.assertEqual(len(rows),meta["examined"])

    def test_impossible_timeout_configuration_fails_closed(self):
        d=dataset(40)
        c=screener.cfg_read()
        c["history_time_budget_seconds"]=20000
        with (patch.object(screener,"market_snapshot",return_value=d),
              patch.object(screener,"optional_listings",return_value=[]),
              patch.object(screener,"load_indexes",return_value={})):
            with self.assertRaisesRegex(ValueError,"시간 예산"):
                screener.build_rows(datetime(2026,10,16),c)


if __name__=="__main__":
    unittest.main()
