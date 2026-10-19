"""No dummy quant success: all displayed dossiers must be prioritized in research."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from quant_priority_universe import prioritized_stocks
from v3_ranked_quant_coverage import (CoverageError,audit_recommended_quant,
                                       main,period_ready)

DAY="2026-10-16"
def make():
    stocks=[{"code":f"{100000+i:06d}","name":f"가상기업{i}",
             "price":1000+i,"price_date":DAY,"market":"KOSPI"} for i in range(140)]
    screen={"version":"2.0","status":"success","date":DAY,"stocks":stocks}
    r={"asof":DAY,"research_source_matched":True,"dossiers":[
        {"code":"100138","name":"개선 후보","qualification":"needs_deeper_evidence",
         "v2_score":{"normalized":84}},
        {"code":"100137","name":"위험 별도","qualification":"separate_caution_research_only",
         "v2_score":{"normalized":76}},
        {"code":"100136","name":"검증 매수 후보","qualification":"verified_recommendation",
         "v2_score":{"normalized":90}}
    ]}
    return screen,r

def valid_period():
    return {"status":"exploratory","sample_n":120,"symbol_n":20,
            "quarter_n":16,"mean_net_return_pct":3.5,
            "stop_first_pct":25.5,"max_adverse_p75_pct":13.2}

class RankedQuantCoverage(unittest.TestCase):
    def test_current_dossiers_all_fetched_before_regular_peers(self):
        m,r=make()
        chosen=prioritized_stocks(m,r,120)
        self.assertEqual([x["code"] for x in chosen[:3]],
                         ["100138","100137","100136"])
        self.assertEqual(len(chosen),120)
        self.assertEqual(len({x["code"] for x in chosen}),120)

    def test_today_actual_missing_quant_is_not_promoted(self):
        market,rec=make()
        study={"status":"exploratory","asof":DAY,"stocks":[
             {"code":"100001","status":"exploratory",
              "reference_price_date":DAY,"periods":{"스윙 1개월":valid_period()}}]}
        x=audit_recommended_quant(market,rec,study)
        self.assertEqual(x["candidate_count"],3)
        self.assertEqual(x["candidates_with_usable_historical_periods"],0)
        self.assertEqual(x["candidate_status_counts"]["awaiting_fresh_study"],3)
        self.assertFalse(x["predicted_profit_or_buy_certified"])
        self.assertTrue(x["survivorship_bias_unresolved"])
        self.assertTrue(all(z["priority_in_next_study"] for z in x["stocks"]))

    def test_distinguish_no_history_research_without_valid_period_and_usable(self):
        market,rec=make()
        study={"status":"exploratory","asof":DAY,"stocks":[
            {"code":"100138","status":"exploratory",
             "reference_price_date":DAY,"periods":{"스윙 1개월":valid_period()}},
            {"code":"100137","status":"insufficient","reason":"650_bars_not_available"},
            {"code":"100136","status":"exploratory","reference_price_date":DAY,
             "periods":{"스윙 1개월":{"status":"insufficient","sample_n":0}}}
        ]}
        x=audit_recommended_quant(market,rec,study)
        self.assertEqual(x["candidates_with_usable_historical_periods"],1)
        self.assertEqual(x["candidate_status_counts"]["history_insufficient"],1)
        self.assertEqual(x["candidate_status_counts"]["research_but_periods_insufficient"],1)
        self.assertEqual(x["candidate_status_counts"]["awaiting_fresh_study"],0)
        self.assertEqual(x["stocks"][0]["historical_period_count"],1)
        self.assertFalse(x["stocks"][0]["out_of_sample_prediction_validated"])

    def test_quant_source_not_same_day_always_unavailable(self):
        market,rec=make()
        q={"status":"exploratory","asof":"2026-10-15","stocks":[
            {"code":"100138","status":"exploratory",
             "reference_price_date":"2026-10-15",
             "periods":{"스윙 1개월":valid_period()}}
        ]}
        x=audit_recommended_quant(market,rec,q)
        self.assertFalse(x["quant_study_same_date"])
        self.assertEqual(x["candidates_with_usable_historical_periods"],0)
        self.assertTrue(all(z["state"]=="awaiting_fresh_study" for z in x["stocks"]))

    def test_old_or_missing_recommendations_block_publication(self):
        market,rec=make()
        rec["asof"]="2026-10-15"
        with self.assertRaisesRegex(CoverageError,"stale_or_unmatched"):
            audit_recommended_quant(market,rec,{})
        market,rec=make()
        rec["dossiers"].append(rec["dossiers"][0])
        with self.assertRaisesRegex(CoverageError,"duplicate"):
            audit_recommended_quant(market,rec,None)
        market,rec=make()
        rec["dossiers"].append({"code":"100999","qualification":"needs_deeper_evidence"})
        with self.assertRaisesRegex(CoverageError,"unknown"):
            audit_recommended_quant(market,rec,None)

    def test_threshold_requires_genuine_separate_symbols_and_quarters(self):
        p=valid_period()
        self.assertTrue(period_ready(p))
        p["symbol_n"]=1
        self.assertFalse(period_ready(p))
        p=valid_period();p["quarter_n"]=2
        self.assertFalse(period_ready(p))
        p=valid_period();p["sample_n"]=39
        self.assertFalse(period_ready(p))
        p=valid_period();p["mean_net_return_pct"]=None
        self.assertFalse(period_ready(p))

    def test_audited_cli_can_label_waiting_and_fails_strict(self):
        m,r=make()
        q={"status":"exploratory","asof":DAY,"stocks":[]}
        with tempfile.TemporaryDirectory() as tmp:
            args=[]
            for k,value in (("screen",m),("recommendations",r),("quant",q)):
                path=Path(tmp)/(k+".json")
                path.write_text(json.dumps(value),encoding="utf8")
                args.extend(["--"+k,str(path)])
            output=Path(tmp)/"coverage.json"
            self.assertEqual(main(args+["--output",str(output)]),0)
            self.assertEqual(json.loads(output.read_text())["candidate_count"],3)
            self.assertEqual(main(args+["--assert-study-candidates",
                                        "--output",str(output)]),1)
            self.assertNotIn("100999",output.read_text())

if __name__=="__main__":
    unittest.main()
