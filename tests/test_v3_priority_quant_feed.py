"""Direct-to-user three-name pilot feed: no raw price, no fake holdout."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from v3_priority_quant_feed import build_priority_feed, main
from v3_ranked_quant_coverage import CoverageError

ASOF="2026-10-16"
def make():
    stocks=[{"code":f"{100000+i:06d}","name":f"Mock{i}","market":"KOSPI",
             "price_date":ASOF,"price":10000+i} for i in range(40)]
    m={"status":"success","version":"2.0","date":ASOF,"stocks":stocks}
    rec={"asof":ASOF,"research_source_matched":True,"dossiers":[
        {"code":"100039","name":"top A","qualification":"needs_deeper_evidence"},
        {"code":"100038","name":"top B","qualification":"needs_deeper_evidence"},
        {"code":"100037","name":"top C","qualification":"needs_deeper_evidence"}]}
    period={"status":"exploratory","sample_n":100,"symbol_n":13,
            "quarter_n":20,"mean_net_return_pct":2.4,"stop_first_pct":25.1,
            "max_adverse_p75_pct":10.3,"up_pct":55.1,
            "target_first_pct":36.2,
            "chronological_holdout":{"status":"research_only",
                                     "test_mean_net_return_pct":12.4}}
    q={"status":"exploratory","asof":ASOF,"requested_symbols":40,
       "available_symbols":29,
       "bias_audit":{"prediction_validated":False},
       "stocks":[{"code":code,"status":"exploratory",
                  "reference_price_date":ASOF,"periods":{"스윙 1개월":period}}
                 for code in ("100039","100038","100037")]}
    return m,rec,q

class PriorityLiveCandidateQuant(unittest.TestCase):
    def test_three_candidate_real_study_derived_data_and_no_fake_holdout(self):
        m,rec,q=make()
        result=build_priority_feed(m,rec,q)
        self.assertEqual(result["candidate_count"],3)
        self.assertEqual(result["peer_cohort_valid"],29)
        self.assertEqual(result["peer_cohort_requested"],40)
        self.assertFalse(result["verified_predictive_accuracy"])
        self.assertFalse(result["raw_ohlcv_copied"])
        self.assertTrue(result["survivorship_bias_unresolved"])
        self.assertEqual([x["code"] for x in result["stocks"]],
                         ["100039","100038","100037"])
        p=result["stocks"][0]["periods"]["스윙 1개월"]
        self.assertEqual(p["mean_net_return_pct"],2.4)
        self.assertEqual(p["stop_first_pct"],25.1)
        self.assertEqual(p["chronological_holdout"]["test_mean_net_return_pct"],None)
        data=json.dumps(result,ensure_ascii=False)
        self.assertNotIn('"Open"',data)
        self.assertNotIn('"High"',data)
        self.assertNotIn('"Close"',data)
        self.assertNotIn('"Volume"',data)

    def test_missing_candidate_or_old_day_aborts(self):
        m,r,q=make()
        q["stocks"]=q["stocks"][1:]
        with self.assertRaisesRegex(CoverageError,"missing_featured"):
            build_priority_feed(m,r,q)
        m,r,q=make()
        q["asof"]="2026-10-15"
        with self.assertRaisesRegex(CoverageError,"stale_priority"):
            build_priority_feed(m,r,q)
        m,r,q=make()
        q["stocks"][0]["reference_price_date"]="2026-10-15"
        with self.assertRaisesRegex(CoverageError,"missing_featured_symbol_in_live_study"):
            build_priority_feed(m,r,q)

    def test_peer_sample_must_be_at_least_twenty_real_names(self):
        m,r,q=make()
        q["available_symbols"]=19
        with self.assertRaisesRegex(CoverageError,"insufficient_live_peer"):
            build_priority_feed(m,r,q)

    def test_cli_never_publishes_invalid_result(self):
        m,r,q=make()
        with tempfile.TemporaryDirectory() as tmp:
            params=[]
            for key,value in (("market",m),("recommendations",r),("quant",q)):
                p=Path(tmp)/(key+".json");p.write_text(json.dumps(value),encoding="utf8")
                params.extend(["--"+key,str(p)])
            output=Path(tmp)/"feed.json"
            self.assertEqual(main(params+["--output",str(output)]),0)
            result=json.loads(output.read_text())
            self.assertEqual(result["peer_cohort_valid"],29)
            m["date"]="2026-10-15"
            (Path(tmp)/"market.json").write_text(json.dumps(m))
            self.assertEqual(main(params+["--output",str(output)]),1)
            self.assertEqual(json.loads(output.read_text())["asof"],ASOF)

if __name__=="__main__":
    unittest.main()
