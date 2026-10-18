"""Offline regression for full-market, risk-first dynamic stock dossiers."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import recommendation_engine as rec


W={"거래량·OBV":20,"일봉 구조":20,"주봉·추세":15,"수급":20,
   "시장 대비 강도":10,"PBR":5,"실적·현금흐름":5,"위험·손익비":5}
C={"거래량·OBV":18,"일봉 구조":17,"주봉·추세":13,"수급":None,
   "시장 대비 강도":8,"PBR":None,"실적·현금흐름":None,
   "위험·손익비":4}  # 60 of 70 => 85.7 research score, 70% coverage


def stock(i,**overrides):
    row={"code":f"{i:06d}","name":f"기업{i}","market":"KOSPI",
         "price":10000,"stop_line":9400,"stop_pct":-6,
         "components":C.copy(),"trading_eok":5.5,
         "phase":"초기 돌파 확인 중","signals":["거래량 동반 초기 돌파"],
         "patterns":[],"risk_flags":["외국인·기관 수급 미확인",
                                    "실적·현금흐름 검증 필요"],
         "obv_rising":True,"one_day_volume_ratio":2.3,
         "ma120w_gap":5,"price20":3,"price60":7,
         "reference_target_pct":500,"reward_risk":25,
         "financial_confirmed":False,"flows_confirmed":False,"pbr":None}
    row.update(overrides)
    return row


def snapshot(rows,day="2026-10-16",complete=True):
    return {"status":"success","version":"2.0","date":day,
        "weights":W,"stocks":rows,
        "eligible_count":len(rows),"examined_count":len(rows),
        "history_scan_complete":complete}


class DynamicDossiers(unittest.TestCase):
    def test_user_70_of_85_normalization(self):
        row=stock(1,components={**C,"수급":None,
                                "PBR":None,"실적·현금흐름":None})
        grade=rec.observed_score(row,W)
        self.assertEqual(grade["available"],70)
        self.assertEqual(grade["normalized"],85.7)
        points={"거래량·OBV":15,"일봉 구조":18,"주봉·추세":13,
                "수급":None,"시장 대비 강도":9,"PBR":5,
                "실적·현금흐름":5,"위험·손익비":5} # 70/80
        self.assertEqual(rec.observed_score(stock(2,components=points),W)
                         ["normalized"],87.5)

    def test_full_universe_all_scanned_then_max20_display(self):
        rows=[stock(i) for i in range(1,2500)]
        result=rec.calculate(snapshot(rows))
        self.assertEqual(result["screened_count"],2499)
        self.assertEqual(result["score_ready_count"],2499)
        self.assertEqual(result["research_eligible_count"],2499)
        self.assertEqual(result["research_dossier_count"],20)
        self.assertEqual(result["confirmed_recommendation_count"],0)
        self.assertTrue(result["recommendations_are_not_forced"])
        self.assertEqual(len(set(d["code"] for d in result["dossiers"])),20)
        self.assertEqual(result["selection_basis"],
                         "all_actual_valid_symbols_before_display_limit")
        self.assertTrue(all(not d["confirmed_buy"] for d in result["dossiers"]))
        self.assertTrue(all(d["return_assessment"]["expected_gain_pct"] is None
                            for d in result["dossiers"]))
        self.assertEqual(result["dossiers"][0]["rank"],1)

    def test_all_tech_rallies_high_risk_then_zero_report_not_forced(self):
        rows=[stock(i,stop_pct=-25,stop_line=7500,
                    phase="추세 상승 중",
                    risk_flags=["단기 급등 — 추격 주의"])
              for i in range(1,56)]
        result=rec.calculate(snapshot(rows))
        self.assertEqual(result["screened_count"],55)
        self.assertEqual(result["research_eligible_count"],0)
        self.assertEqual(result["research_dossier_count"],0)
        self.assertEqual(result["confirmed_recommendation_count"],0)
        self.assertEqual(result["dossiers"],[])

    def test_sort_is_score_and_loss_without_fictitious_target(self):
        grade=rec.observed_score(stock(1),W)
        self.assertEqual(grade["available"],70)
        self.assertIsNone(rec.forward_return(stock(1), "2026-10-16"))
        self.assertIsNone(rec.forward_return(stock(1,reference_target_pct=50000),
                                             "2026-10-16"))
        x=rec.calculate(snapshot([
            stock(1,stop_line=7000,stop_pct=-6),
            stock(2,stop_line=9400,stop_pct=-6),
            stock(3,stop_line=8500,stop_pct=-15)
        ]))
        self.assertEqual(x["research_dossier_count"],2)
        self.assertNotIn("000001",[a["code"] for a in x["dossiers"]])
        self.assertEqual(x["dossiers"][0]["downside"]["stop"]["risk"],"low")

    def test_flow_and_financial_missing_do_not_masquerade_as_proven(self):
        s=rec.calculate(snapshot([stock(2)]))["dossiers"][0]
        self.assertFalse(s["flows"]["validated"])
        self.assertFalse(s["financials"]["validated"])
        self.assertFalse(s["valuation"]["validated"])
        self.assertFalse(s["industry"]["validated"])
        self.assertEqual(s["qualification"],"needs_deeper_evidence")
        self.assertEqual(s["trade_action"],"추가 검증 전 관망")
        self.assertFalse(s["confirmed_buy"])
        self.assertIsNone(s["scenarios"]["up"]["future_return_pct"])
        self.assertIsNone(s["scenarios"]["down"]["future_return_pct"])
        self.assertEqual(s["downside"]["structural_invalidation_price"],9400)
        self.assertFalse(s["return_assessment"]["past_52week_high_used_as_target"])
        self.assertEqual(s["elliott_primary"],None)
        self.assertEqual(s["elliott_alternative"],None)

    def test_quant_research_is_historical_not_forward_forecast(self):
        q={"asof":"2026-10-16","stocks":[{"code":"000002","periods":{
            "스윙 1개월":{"status":"exploratory","sample_n":80,
                "symbol_n":14,"quarter_n":18,
                "mean_net_return_pct":5,"stop_first_pct":24,
                "max_adverse_p75_pct":9},
            "중기 3개월":{"status":"exploratory","sample_n":70,
                "symbol_n":13,"quarter_n":15,
                "mean_net_return_pct":-3,"stop_first_pct":53,
                "max_adverse_p75_pct":18}
        }}]}
        x=rec.calculate(snapshot([stock(2,phase="상승 후 수렴")]),quant=q)
        d=x["dossiers"][0]
        self.assertEqual(d["approach_horizon"],"1m")
        self.assertTrue(d["long_holding_caution"])
        self.assertEqual(d["historical_quant"]["1m"]["mean_historical_net_pct"],5)
        self.assertIsNone(d["return_assessment"]["expected_gain_pct"])
        self.assertEqual(d["return_assessment"]["expected_reward_risk"],None)
        self.assertFalse(d["historical_quant"]["1m"]["forward_validated"])
        old=rec.calculate(snapshot([stock(2,phase="상승 후 수렴")],"2026-10-17"),quant=q)
        self.assertEqual(old["dossiers"][0]["approach_horizon"],"3m")
        self.assertFalse(old["research_source_matched"])

    def test_full_analysis_refresh_daily_reentry_exit_change(self):
        a=rec.calculate(snapshot([stock(1),stock(2)]))
        self.assertEqual(a["daily_changes"]["entered"],["000001","000002"])
        b=rec.calculate(snapshot([stock(2),stock(3),
                stock(4,stop_line=7000,stop_pct=-6)],"2026-10-19"),
                previous=a)
        self.assertEqual(set(b["daily_changes"]["entered"]),{"000003"})
        self.assertEqual(set(b["daily_changes"]["exited"]),{"000001"})
        self.assertEqual(set(b["daily_changes"]["updated"]),{"000002"})
        c=rec.calculate(snapshot([stock(2),stock(3),
                stock(4,stop_line=7000,stop_pct=-6)],"2026-10-19"),previous=b)
        self.assertEqual(set(c["daily_changes"]["unchanged"]),{"000002","000003"})
        self.assertEqual(c["daily_changes"]["entered"],[])

    def test_no_duplicate_missing_components_or_invalid_asof(self):
        with self.assertRaises(rec.RecommendationError):
            rec.calculate(snapshot([stock(1),stock(1)]))
        with self.assertRaises(rec.RecommendationError):
            rec.calculate({"status":"failure","stocks":[]})
        self.assertIsNone(rec.observed_score(stock(1,components={
            **C,"일봉 구조":float("nan")}),W))
        self.assertIsNone(rec.observed_score(stock(1,components={
            **C,"일봉 구조":True}),W))
        self.assertIsNone(rec.observed_score(stock(1,components={
            **C,"거래량·OBV":None,"일봉 구조":None,"주봉·추세":None,
            "시장 대비 강도":None,"위험·손익비":None}),W))

    def test_save_read_deterministic_and_90days_retention(self):
        with tempfile.TemporaryDirectory() as work:
            folder=Path(work)
            item=rec.publish(snapshot([stock(1),stock(2)]),folder)
            self.assertEqual(item["research_dossier_count"],2)
            self.assertTrue((folder/"recommendations.json").exists())
            again=rec.publish(snapshot([stock(1),stock(2)]),folder)
            self.assertEqual(set(again["daily_changes"]["unchanged"]),
                             {"000001","000002"})
            hist=json.loads((folder/"recommendation_history.json").read_text())
            self.assertEqual(len(hist),1)
            self.assertEqual(hist[0]["date"],"2026-10-16")
            self.assertEqual(rec.history_update(
                [{"date":f"2026-08-{i:02d}"} for i in range(1,31)],item)[0]["date"],
                "2026-10-16")


if __name__=="__main__":
    unittest.main()
