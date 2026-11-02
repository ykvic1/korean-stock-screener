"""Read-only issuer history integrity; loss of a score is never a source lag."""
import copy
import unittest

from v3_issuer_history_watch import inspect
from v3_issuer_factor_ledger import build


def catalog(day,names):
    return {"date":day,"listed_total":len(names),"rows":[
        {"code":code,"name":name,"market":"KOSPI"} for code,name in names]}


def price_row(day,code,name,point=2.0):
    return {"code":code,"name":name,"market":"KOSPI","asof":day,
            "factors":{"volume":5,"technical":7,"financial":point,
                       "valuation":4,"momentum":2},
            "factor_available":{"volume":16,"technical":25,"financial":8,
                               "valuation":10,"momentum":8}}


def fiscal(day,code,points):
    return {"code":code,"market":"KOSPI","asof":day,
            "unverified_components":{"pbr":{
                "max_points":5,"candidate_points":points}}}


class IssuerHistoryWatchTests(unittest.TestCase):
    def test_read_only_audit_accepts_real_rollover_and_new_ticker(self):
        old="2026-10-30";now="2026-11-02"
        first=build(catalog(old,[("005930","A")]),
                    [price_row(old,"005930","A")],
                    fiscal_rows={"005930":fiscal(old,"005930",2.5)})
        second=build(catalog(now,[("005930","A"),("000020","B")]),
                     [price_row(now,"005930","A"),
                      price_row(now,"000020","B")],first,
                     fiscal_rows={"005930":fiscal(now,"005930",3.5)})
        report=inspect(second,first)
        self.assertEqual(report["status"],"ok")
        self.assertTrue(report["missing_source_is_not_zero"])
        self.assertFalse(report["score_mutations_authorized"])
        self.assertEqual(report["counts"]["active"],2)
        self.assertEqual(report["counts"]["component_history_slots"],1)
        self.assertEqual(report["counts"]["prior_component_events"],1)
        self.assertEqual(report["per_component_observed_issuer_counts"]["valuation.pbr"],1)

    def test_a_lag_does_not_become_an_error_or_a_zero(self):
        old="2026-10-30";now="2026-11-02"
        first=build(catalog(old,[("005930","A")]),
                    [price_row(old,"005930","A")],
                    fiscal_rows={"005930":fiscal(old,"005930",0)})
        later=build(catalog(now,[("005930","A")]),
                    [price_row(now,"005930","A")],first)
        result=inspect(later,first)
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["counts"]["retained_component_observations"],1)
        self.assertEqual(result["counts"]["valid_observed_zero_points"],1)
        self.assertEqual(result["counts"]["component_observations"],1)

    def test_disappearing_score_or_component_is_detected(self):
        day="2026-11-02"
        first=build(catalog(day,[("005930","A")]),
                    [price_row(day,"005930","A")],
                    fiscal_rows={"005930":fiscal(day,"005930",3)})
        bad=copy.deepcopy(first)
        del bad["rows"]["005930"]["factors"]["financial"]
        del bad["rows"]["005930"]["components"]["valuation"]["pbr"]
        report=inspect(bad,first)
        self.assertIn("lost_existing_area_observation",report["issues"])
        self.assertIn("lost_existing_component_observation",report["issues"])
        self.assertEqual(report["status"],"error")

    def test_invalid_history_does_not_certify_score(self):
        day="2026-11-02"
        first=build(catalog(day,[("005930","A")]),
                    [price_row(day,"005930","A")],
                    fiscal_rows={"005930":fiscal(day,"005930",3)})
        forged=copy.deepcopy(first)
        forged["rows"]["005930"]["component_history"]={
            "valuation":{"pbr":[{"asof":"2027-01-01","points":4.1}]}}
        report=inspect(forged)
        self.assertEqual(report["status"],"error")
        self.assertIn("invalid_prior_observation",report["issues"])
        self.assertFalse(report["score_mutations_authorized"])

    def test_old_schema_without_history_is_compatible_until_republished(self):
        day="2026-11-02"
        first=build(catalog(day,[("005930","A")]),
                    [price_row(day,"005930","A")])
        del first["rows"]["005930"]["component_history"]
        self.assertEqual(inspect(first)["status"],"ok")


if __name__=="__main__":
    unittest.main()
