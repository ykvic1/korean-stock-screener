"""V3.0b stop severity proxy: synthetic observations, never live risk odds."""
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
    def test_small_bounded_loss_with_good_reference_room_only_research_bonus(self):
        original = record()
        before = copy.deepcopy(original)
        result = preview(original, asof=DAY)
        self.assertEqual(original, before)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertAlmostEqual(result["stop_distance_pct"], 5.)
        self.assertAlmostEqual(result["reference_reward_risk_only"], 4.)
        self.assertAlmostEqual(result["research_adjustment_points_not_applied"], 1.8)
        self.assertEqual(result["status"], "bounded_downside_and_reference_room")
        self.assertFalse(result["research_adjustment_applied_to_total"])
        self.assertFalse(result["scoring_authorized"])
        self.assertIsNone(result["historical_stop_hit_probability"])

    def test_wider_loss_distance_penalized_more_up_to_minus_five(self):
        pairs = [(10, -0.5), (13.5, -2.), (20, -4.), (40, -5.)]
        for distance, expected in pairs:
            with self.subTest(distance=distance):
                result = preview(record(stop_line=100-distance,stop_pct=-distance),
                                 asof=DAY)
                self.assertAlmostEqual(result["research_adjustment_points_not_applied"],
                                       expected)

    def test_stop_too_close_is_not_confused_with_low_stopout_probability(self):
        got = preview(record(stop_line=99., stop_pct=-1.), asof=DAY)
        self.assertEqual(got["research_adjustment_points_not_applied"], -3.)
        self.assertEqual(got["status"], "too_close_to_market_noise")

    def test_informational_missing_data_badges_do_not_erase_risk_bonus(self):
        metadata_only = (
            "외국인·기관 수급 미확인",
            "실적·현금흐름 검증 필요",
            "PBR 미확인",
        )
        candidate = preview(record(risk_flags=list(metadata_only)),asof=DAY)
        self.assertAlmostEqual(
            candidate["research_adjustment_points_not_applied"],1.8)
        self.assertEqual(candidate["status"],
                         "bounded_downside_and_reference_room")
        for adverse in ("거래대금 부족 — 환금성·호가 스프레드 점검 필요",
                        "52주 고점권 — 추격 주의",
                        "장기 추세 취약", "추가 미확인 위험"):
            checked=preview(record(risk_flags=[*metadata_only,adverse]),
                            asof=DAY)
            self.assertEqual(checked["research_adjustment_points_not_applied"],0.0)

    def test_unverified_setup_never_earns_bonus_despite_near_stop(self):
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
                self.assertEqual(result["research_adjustment_points_not_applied"],0.)
                self.assertEqual(result["status"],"low_distance_but_unconfirmed_setup")

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
        self.assertGreater(value["research_adjustment_points_not_applied"],0)
        self.assertEqual(by1[first["code"]]["earned"],by0[first["code"]]["earned"])
        self.assertEqual(by1[first["code"]]["available"],by0[first["code"]]["available"])
        self.assertTrue(report1["stop_risk_adjustment_is_not_part_of_100point_total"])
        self.assertTrue(report1["stop_risk_historical_probability_unverified"])
        self.assertEqual(sum(report1["stop_loss_severity_research_counts"].values()),
                         report1["listed_total"])
        self.assertEqual(report0["area_budgets"],report1["area_budgets"])
        data=list(csv.DictReader(StringIO(to_csv(updated,report1).lstrip("\ufeff"))))
        self.assertIn("손절심각도_연구가감점_총점미반영",data[0])
        self.assertEqual(data[0]["정식투자점수"],"")


if __name__ == "__main__":
    unittest.main()
