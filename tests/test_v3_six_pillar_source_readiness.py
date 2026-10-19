"""Synthetic audited-source inventory; no fake IAS, valuation or rank."""
import copy
import tempfile
import json
import unittest
from pathlib import Path
from v3_six_pillar_source_readiness import audit, main, ReadinessError


def fixture():
    market = {
        "status": "success", "date": "2026-10-19",
        "candidate_count": 2, "flow_confirmed_count": 1,
        "six_pillar_research_sort": [],
        "stocks": [
            {"code": "000001", "flows_confirmed": True,
             "relative20": 2.1, "relative60": -3.4,
             "v3_pattern_integrated": {
                 "schema": "v3-pattern-within-six-pillars-research-0.4",
                 "asof": "2026-10-19",
                 "technical_research_candidate": 15.0}},
            {"code": "000002", "flows_confirmed": False,
             "relative20": None, "relative60": 3.4,
             "v3_pattern_integrated": None},
        ],
    }
    catalog = {"asof": "2026-10-19", "listed_total": 3,
               "rows": [{"code": c} for c in ("000001","000002","000003")]}
    book = {"asof": "2026-10-19", "market_listed_total": 3,
            "attempted_reference_count": 2,
            "observed_annual_count": 1,
            "pbr_simple_reference_count": 1,
            "historical_point_in_time_verified": False,
            "official_exchange_pbr_verified": False}
    return market, catalog, book


class PublishedReadinessRegressions(unittest.TestCase):
    def test_approved_weights_and_source_counts_without_scoring(self):
        m, c, b = fixture()
        r = audit(m,c,b)
        self.assertEqual(r["contract_weights"], {
            "ias":20,"financial":15,"valuation":15,
            "technical":25,"industry":15,"momentum":10})
        self.assertEqual(sum(r["contract_weights"].values()),100)
        v = r["observed_counts"]
        self.assertEqual(v["current_market_listed"],3)
        self.assertEqual(v["priced_screened_issuer_count"],2)
        self.assertEqual(v["v2_legacy_flow_confirmed_count_not_v3_ias_certified"],1)
        self.assertEqual(v["research_technical25_observed_issuer_count_not_trade_certified"],1)
        self.assertEqual(v["v2_relative_strength_pair_count_not_official_v3_momentum"],1)
        self.assertEqual(v["dart_issuer_attempts"],2)
        self.assertEqual(v["dart_annual_issuer_observed_not_3y"],1)
        self.assertEqual(v["dart_simple_pb_references_not_official_pbr"],1)
        self.assertEqual(v["unattempted_listed_dart_issuers"],1)
        self.assertEqual(set(r["pillars"]),set(r["contract_weights"]))
        self.assertTrue(all(not item["confirmed_for_investment"]
                            for item in r["pillars"].values()))
        self.assertFalse(r["full_six_pillar_investment_rank_ready"])
        self.assertIsNone(r["confirmed_buy_candidates"])
        self.assertFalse(r["investment_decision_changed"])
        self.assertFalse(r["user_interface_changed"])
        self.assertEqual(r["source_day_lag_calendar_days"],0)
        # The audit is pure/read-only.
        self.assertEqual(m["candidate_count"],2)

    def test_rollover_older_dart_reference_is_not_secretly_today(self):
        m,c,b=fixture()
        b["asof"]="2026-10-16"
        x=audit(m,c,b)
        self.assertEqual(x["source_day_lag_calendar_days"],3)
        self.assertEqual(x["dart_reference_asof"],"2026-10-16")

    def test_future_or_cross_day_census_never_reported_as_complete(self):
        for which, key, value in (
            ("book","asof","2026-10-20"),
            ("catalog","asof","2026-10-18"),
            ("book","market_listed_total",5),
            ("market","status","failed"),
            ("market","candidate_count",3),
            ("market","flow_confirmed_count",2),
            ("book","attempted_reference_count",4),
            ("book","pbr_simple_reference_count",2),
            ("book","historical_point_in_time_verified",True),
            ("book","official_exchange_pbr_verified",True),
        ):
            with self.subTest(which=which,key=key,value=value):
                m,c,b=fixture()
                data={"market":m,"catalog":c,"book":b}[which]
                data[key]=value
                with self.assertRaises(ReadinessError):
                    audit(m,c,b)

    def test_duplicate_codes_and_unlisted_screened_issuers_block(self):
        m,c,b=fixture()
        m["stocks"][1]["code"]="000001"
        with self.assertRaises(ReadinessError):
            audit(m,c,b)
        m,c,b=fixture()
        m["stocks"][1]["code"]="999999"
        with self.assertRaises(ReadinessError):
            audit(m,c,b)

    def test_manual_offline_command_outputs_only_analysis_artifact(self):
        m,c,b=fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            paths=[]
            for label,data in (("market",m),("catalog",c),("book",b)):
                p=root/(label+".json")
                p.write_text(json.dumps(data),encoding="utf-8")
                paths.append(str(p))
            out=root/"artifacts"/"readiness.json"
            main(["--market",paths[0],"--catalog",paths[1],
                  "--book",paths[2],"--output",str(out)])
            self.assertTrue(out.exists())
            result=json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(result["full_six_pillar_investment_rank_ready"])
            self.assertFalse((root/"docs"/"data.json").exists())


if __name__=="__main__":
    unittest.main()
