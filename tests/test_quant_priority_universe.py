"""Current-day priority recommendations must appear in both quant collection and analysis."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from quant_priority_universe import prioritized_stocks,optional_recommendations


def fixture(n=140):
    rows=[]
    for i in range(n):
        code=f"{100000+i:06d}"
        rows.append({"code":code,"name":"Mock"+code,"market":"KOSPI",
                     "price_date":"2026-10-16","price":1000+i})
    return {"status":"success","date":"2026-10-16","stocks":rows}


class PriorityQuantHistoryTests(unittest.TestCase):
    def test_recommendations_not_lost_beyond_original_first_120(self):
        d=fixture()
        r={"asof":"2026-10-16","research_source_matched":True,
           "dossiers":[{"code":"100135","qualification":"needs_deeper_evidence"},
                       {"code":"100132","qualification":"needs_deeper_evidence"},
                       {"code":"100134","qualification":"needs_deeper_evidence"}]}
        selected=prioritized_stocks(d,r,120)
        self.assertEqual([x["code"] for x in selected[:3]],
                         ["100135","100132","100134"])
        self.assertEqual(len(selected),120)
        self.assertEqual(len(set(x["code"] for x in selected)),120)
        self.assertNotIn("100120",[x["code"] for x in selected])

    def test_separate_caution_is_studied_not_automatically_buyable(self):
        d=fixture()
        r={"asof":"2026-10-16","research_source_matched":True,
           "dossiers":[{"code":"100134","qualification":"separate_caution_research_only"},
                       {"code":"100135","qualification":"verified_recommendation"},
                       {"code":"100136","qualification":"risk_or_invalid_stop"}]}
        first=[x["code"] for x in prioritized_stocks(d,r,120)[:2]]
        self.assertEqual(first,["100134","100135"])
        self.assertNotEqual(first,["100136"])

    def test_stale_and_unproven_screen_does_not_override_stock_order(self):
        d=fixture()
        stale={"asof":"2026-10-15","research_source_matched":True,
               "dossiers":[{"code":"100138","qualification":"needs_deeper_evidence"}]}
        s=prioritized_stocks(d,stale,120)
        self.assertEqual(s[0]["code"],"100000")
        stale["asof"]="2026-10-16"
        stale["research_source_matched"]=False
        self.assertEqual(prioritized_stocks(d,stale,120)[0]["code"],"100000")

    def test_only_current_signal_codes_and_no_duplicates(self):
        d=fixture()
        r={"asof":"2026-10-16","research_source_matched":True,
           "dossiers":[{"code":"100140","qualification":"needs_deeper_evidence"},
                       {"code":"100138","qualification":"needs_deeper_evidence"},
                       {"code":"100138","qualification":"needs_deeper_evidence"},
                       {"code":"100137","qualification":"not_validated"}]}
        s=prioritized_stocks(d,r,120)
        self.assertEqual(s[0]["code"],"100138")
        self.assertEqual(len(s),120)
        d["stocks"][-2]["price_date"]="2026-10-15"
        self.assertNotIn("100138",[z["code"] for z in prioritized_stocks(d,r,120)])

    def test_invalid_or_absent_recommendation_file_degrades_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"recommendations.json"
            self.assertIsNone(optional_recommendations(path))
            path.write_text("{bad",encoding="utf-8")
            self.assertIsNone(optional_recommendations(path))
            path.write_text(json.dumps({"asof":"2026-10-16"}),encoding="utf-8")
            self.assertEqual(optional_recommendations(path)["asof"],"2026-10-16")

    def test_below_20_stock_fixture_remains_insufficient_not_fabricated(self):
        self.assertEqual(len(prioritized_stocks(fixture(1),None,120)),1)
        self.assertEqual(len(prioritized_stocks(fixture(140),None,20)),20)
        with self.assertRaises(ValueError):
            prioritized_stocks(fixture(140),None,5)


if __name__=="__main__":
    unittest.main()
