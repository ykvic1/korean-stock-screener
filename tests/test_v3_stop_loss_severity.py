"""V3.0b structural-stop reference: no bonus, penalty, or candidate gating."""
import copy
import csv
from io import StringIO
import math
import unittest

from v3_stop_loss_severity import preview, SCHEMA
from v3_current_observed_scorecard import generate, to_csv
from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build
from test_v3_current_observed_scorecard import small


DAY = "2026-11-04"


def record(**patch):
    value = {
        "price_date": DAY, "price": 100., "stop_line": 95.,
        "stop_pct": -5., "reference_resistance": 120.,
        "price20": 4., "price60": 12., "risk_flags": [],
    }
    value.update(patch)
    return value


class StopLossSeverityResearchTests(unittest.TestCase):
    def test_reference_distance_and_reward_room_have_no_adjustment(self):
        original = record()
        before = copy.deepcopy(original)
        result = preview(original, asof=DAY)
        self.assertEqual(original, before)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertAlmostEqual(result["stop_distance_pct"], 5.)
        self.assertAlmostEqual(result["reference_reward_risk_only"], 4.)
        self.assertIsNone(result["research_adjustment_points_not_applied"])
        self.assertEqual(result["status"], "reference_only")
        self.assertFalse(result["research_adjustment_applied_to_total"])
        self.assertFalse(result["scoring_authorized"])
        self.assertIsNone(result["historical_stop_hit_probability"])

    def test_stop_distance_never_changes_bonus_or_penalty(self):
        for distance in (1, 2.9, 5, 10, 13.5, 20, 40):
            with self.subTest(distance=distance):
                result = preview(record(stop_line=100-distance,stop_pct=-distance),
                                 asof=DAY)
                self.assertIsNone(result["research_adjustment_points_not_applied"])
                self.assertEqual(result["status"], "reference_only")
                self.assertAlmostEqual(result["stop_distance_pct"], distance)

    def test_stop_too_close_is_a_reference_not_a_grade(self):
        got = preview(record(stop_line=99., stop_pct=-1.), asof=DAY)
        self.assertIsNone(got["research_adjustment_points_not_applied"])
        self.assertEqual(got["status"], "reference_only")

    def test_adverse_flags_do_not_change_structural_stop_reference(self):
        metadata_only = (
            "외국인·기관 수급 미확인",
            "실적·현금흐름 검증 필요",
            "PBR 미확인",
        )
        candidate = preview(record(risk_flags=list(metadata_only)),asof=DAY)
        self.assertIsNone(candidate["research_adjustment_points_not_applied"])
        self.assertEqual(candidate["status"], "reference_only")
        for adverse in ("거래대금 부족 — 환금성·호가 스프레드 점검 필요",
                        "52주 고점권 — 추격 주의",
                        "장기 추세 취약", "추가 미확인 위험"):
            checked=preview(record(risk_flags=[*metadata_only,adverse]),
                            asof=DAY)
            self.assertEqual(checked["status"], "reference_only")
            self.assertEqual(checked["stop_distance_pct"], candidate["stop_distance_pct"])

    def test_unverified_setup_does_not_affect_reference(self):
        for change in (
            {"risk_flags":["거래대금 부족"]},
            {"price20":20.},
            {"price60":41.},
            {"price20":None},
            {"reference_resistance":105.},
            {"reference_resistance":None},
            {"risk_flags":None},
        ):
            with self.subTest(change=change):
                result=preview(record(**change),asof=DAY)
                self.assertIsNone(result["research_adjustment_points_not_applied"])
                self.assertEqual(result["status"],"reference_only")

    def test_stale_missing_mismatched_and_hostile_inputs_are_unknown_not_zero(self):
        changes=(
            {"price_date":"2026-11-03"}, {"price_date":"2026-11-05"},
            {"price_date":"2026-02-31"}, {"price_date":None},
            {"price":True}, {"price":math.nan}, {"price":0},
            {"stop_line":None}, {"stop_line":100}, {"stop_line":101},
            {"stop_pct":-20}, {"stop_pct":None}, {"stop_pct":True},
        )
        for change in changes:
            with self.subTest(change=change):
                res=preview(record(**change),asof=DAY)
                self.assertIsNone(res["research_adjustment_points_not_applied"])
                self.assertNotEqual(res["reason_codes"],[])
                self.assertFalse(res["scoring_authorized"])
        for bad in ("not_date","2026-02-31",None):
            with self.assertRaisesRegex(ValueError,"invalid_stop_research_input"):
                preview(record(),asof=bad)
        with self.assertRaisesRegex(ValueError,"invalid_stop_research_input"):
            preview([],asof=DAY)

    def test_all_listed_report_exposes_research_but_never_alters_100_score(self):
        market,catalog=small()
        stocks=market["stocks"]
        first=stocks[0]
        self.assertGreater(first["price"],0)
        # Hold all price-derived factors constant across both runs.
        first.update({"reference_resistance":first["price"]*1.2,
                      "risk_flags":[],"price20":4.,"price60":12.})
        baseline,report0=generate(market,catalog,volume_build(market,catalog),
                                  price_build(market,catalog))
        first.update({"stop_line":first["price"]*.95,"stop_pct":-5.})
        updated,report1=generate(market,catalog,volume_build(market,catalog),
                                 price_build(market,catalog))
        by0={r["code"]:r for r in baseline}
        by1={r["code"]:r for r in updated}
        value=by1[first["code"]]["stop_loss_severity_research"]
        self.assertIsNone(value["research_adjustment_points_not_applied"])
        self.assertEqual(value["status"], "reference_only")
        self.assertEqual(by1[first["code"]]["earned"],by0[first["code"]]["earned"])
        self.assertEqual(by1[first["code"]]["available"],by0[first["code"]]["available"])
        self.assertTrue(report1["stop_risk_adjustment_is_not_part_of_100point_total"])
        self.assertTrue(report1["stop_risk_historical_probability_unverified"])
        self.assertEqual(sum(report1["stop_loss_severity_research_counts"].values()),
                         report1["listed_total"])
        self.assertEqual(report0["area_budgets"],report1["area_budgets"])
        data=list(csv.DictReader(StringIO(to_csv(updated,report1).lstrip("\ufeff"))))
        self.assertEqual(data[0]["손절심각도_연구가감점_총점미반영"], "")
        self.assertEqual(data[0]["정식투자점수"],"")


if __name__ == "__main__":
    unittest.main()
