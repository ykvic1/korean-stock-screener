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

    def test_delisted_issuer_history_row_cannot_vanish_while_census_matches(self):
        old = "2026-10-30"
        now = "2026-11-02"
        first = build(
            catalog(old, [("005930", "A"), ("000020", "B")]),
            [price_row(old, "005930", "A"),
             price_row(old, "000020", "B")])
        later = build(
            catalog(now, [("005930", "A")]),
            [price_row(now, "005930", "A")], first)
        self.assertEqual(inspect(later, first)["status"], "ok")
        self.assertFalse(later["rows"]["000020"]["active"])
        self.assertEqual(later["rows"]["000020"]["factors"]["financial"]["points"], 2.0)

        damaged = copy.deepcopy(later)
        del damaged["rows"]["000020"]
        # Simulate a broken publisher which also rewrites its count: the
        # ordinary active/inactive market census check would still pass.
        damaged["inactive_issuer_count"] = 0
        report = inspect(damaged, first)
        self.assertEqual(report["status"], "error")
        self.assertIn("lost_existing_issuer_record", report["issues"])
        self.assertNotIn("market_census_mismatch", report["issues"])
        self.assertFalse(report["score_mutations_authorized"])

    def test_prior_inactive_issuer_history_must_survive_next_publication(self):
        old, mid, now = "2026-10-29", "2026-10-30", "2026-11-02"
        first = build(
            catalog(old, [("005930", "A"), ("000020", "B")]),
            [price_row(old, "005930", "A"),
             price_row(old, "000020", "B")])
        middle = build(
            catalog(mid, [("005930", "A")]),
            [price_row(mid, "005930", "A")], first)
        last = build(
            catalog(now, [("005930", "A")]),
            [price_row(now, "005930", "A")], middle)
        self.assertEqual(inspect(last, middle)["status"], "ok")
        tampered = copy.deepcopy(last)
        tampered["rows"].pop("000020")
        tampered["inactive_issuer_count"] = 0
        self.assertIn("lost_existing_issuer_record",
                      inspect(tampered, middle)["issues"])

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

    def test_existing_key_cannot_hide_regressed_date_or_narrowed_coverage(self):
        day="2026-11-02"
        first=build(catalog(day,[("005930","A")]),
                    [price_row(day,"005930","A")],
                    fiscal_rows={"005930":fiscal(day,"005930",3)})
        for field in ("factor","component"):
            bad=copy.deepcopy(first)
            entry=(bad["rows"]["005930"]["factors"]["financial"] if field=="factor"
                   else bad["rows"]["005930"]["components"]["valuation"]["pbr"])
            entry["asof"]="2026-10-30"
            report=inspect(bad,first)
            self.assertIn("regressed_existing_observation_date",report["issues"])
        bad=copy.deepcopy(first)
        entry=bad["rows"]["005930"]["factors"]["financial"]
        entry.update(available=4,points=2)
        self.assertIn("narrowed_existing_observation_coverage",inspect(bad,first)["issues"])

    def test_broader_historical_recovery_and_legitimate_point_decline_are_allowed(self):
        day="2026-11-02"
        first=build(catalog(day,[("005930","A")]),[price_row(day,"005930","A")])
        broader=copy.deepcopy(first)
        broader["rows"]["005930"]["factors"]["financial"].update(
            available=12,points=2,asof="2026-10-30")
        self.assertEqual(inspect(broader,first)["status"],"ok")
        lower=copy.deepcopy(first)
        lower["rows"]["005930"]["factors"]["financial"]["points"]=0
        self.assertEqual(inspect(lower,first)["status"],"ok")

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

    def test_identity_change_keeps_retired_history_but_not_current_fiscal_score(self):
        original = "2026-10-30"
        today = "2026-11-02"
        first = build(catalog(original,[("005930","Historical Company")]),
                      [price_row(original,"005930","Historical Company")],
                      fiscal_rows={"005930":fiscal(original,"005930",3.5)})
        second = build(catalog(today,[("005930","New Company")]),
                       [price_row(today,"005930","New Company",point=2)],
                       first,
                       fiscal_rows={"005930":fiscal(today,"005930",4.5)})
        report = inspect(second,first)
        self.assertEqual(report["status"],"ok")
        self.assertEqual(report["counts"]["identity_transitions_review_only"],1)
        self.assertEqual(report["counts"]["retired_identity_snapshots"],1)
        self.assertEqual(report["counts"]["retired_area_observations"],5)
        self.assertEqual(report["counts"]["retired_component_observations"],1)
        self.assertFalse(second["rows"]["005930"]["components"])
        self.assertEqual(second["rows"]["005930"]["factors"]["financial"]["source"],
                         "current_close_replay")
        self.assertFalse(second["investment_score_certified"])

    def test_lost_or_changed_retired_history_fails_continuity_audit(self):
        a="2026-10-30";b="2026-11-02";c="2026-11-03"
        first=build(catalog(a,[("005930","Old")]),
                    [price_row(a,"005930","Old")])
        second=build(catalog(b,[("005930","New")]),
                     [price_row(b,"005930","New")],first)
        third=build(catalog(c,[("005930","New")]),
                    [price_row(c,"005930","New")],second)
        self.assertEqual(inspect(third,second)["status"],"ok")
        lost=copy.deepcopy(third)
        lost["identity_review_archive"]={}
        missing=inspect(lost,second)
        self.assertEqual(missing["status"],"error")
        self.assertIn("lost_retired_issuer_identity_history",missing["issues"])
        forged=copy.deepcopy(third)
        key=next(iter(forged["identity_review_archive"]))
        forged["identity_review_archive"][key]["factors"]["financial"]["points"]=12
        bad=inspect(forged,second)
        self.assertEqual(bad["status"],"error")
        self.assertIn("invalid_retired_issuer_identity_archive",bad["issues"])

    def test_unarchived_identity_reuse_is_detected_without_promoting_pit(self):
        old="2026-10-30";new="2026-11-02"
        first=build(catalog(old,[("005930","Before")]),
                    [price_row(old,"005930","Before")])
        last=build(catalog(new,[("005930","After")]),
                   [price_row(new,"005930","After")],first)
        last["identity_review_archive"]={}
        audit=inspect(last,first)
        self.assertEqual(audit["status"],"error")
        self.assertIn("unarchived_issuer_identity_transition",audit["issues"])

    def test_old_schema_without_history_is_compatible_until_republished(self):
        day="2026-11-02"
        first=build(catalog(day,[("005930","A")]),
                    [price_row(day,"005930","A")])
        del first["rows"]["005930"]["component_history"]
        self.assertEqual(inspect(first)["status"],"ok")


if __name__=="__main__":
    unittest.main()
