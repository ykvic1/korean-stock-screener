"""Offline, source-date-safe daily+weekly technical priority ranking regressions."""
import copy
import unittest
from datetime import date, timedelta

from v3_pattern_composite_score import (
    SCHEMA, WEIGHTS, evaluate, rank_eligible_rows,
)

DAY = "2026-10-20"
FRIDAY = "2026-10-16"


def observed(tf, *, date_=DAY, typ="bull_flag", stage="forming",
             rsi=39.0, crossing=False, bullish=False, geometry=None):
    data = {
        "schema": "v3-pattern-first-daily-research-0.1",
        "asof": date_, "timeframe": tf,
        "status": "heuristic_unverified",
        "patterns": [] if typ is None else [{"type": typ, "stage": stage}],
        "rsi14": rsi, "rsi30_cross_recent": crossing,
        "rsi_bullish_divergence_recent": bullish,
        "scoring_allowed": False, "buy_signal": False,
        "official_regular_session_verified": False,
    }
    if tf == "weekly":
        data["completed_week_only"] = True
        data["pattern_geometry"] = {
            "status": "heuristic_unvalidated",
            "falling_wedge_candidate": False,
            "inverse_head_shoulders_breakout_candidate": False,
            "triangle_candidate": False,
            "post_rise_consolidation_candidate": False,
        }
        data["pattern_geometry"].update(geometry or {})
    return data


def stock(code="005930"):
    return {
        "code": code, "price_date": DAY, "price_latest": 100.0,
        "stop_line": 92.0, "stop_pct": -8.0,
        "price20": 5.0, "price60": 12.0,
        "patterns": [], "is_wedge": False, "is_ihs": False,
        "is_triangle": False,
        "pattern_research": observed("daily", typ="bull_flag",
            stage="breakout_observed", crossing=True, bullish=True),
        "pattern_weekly_research": observed("weekly", date_=FRIDAY,
            typ="bull_flag", stage="forming", bullish=True,
            geometry={"falling_wedge_candidate": True}),
        "one_day_volume_ratio": 1.7,
        "obv_rising": True, "obv_divergence": True,
        "ma120w_gap": 6.0, "weekly_turn": False,
        "weekly_aligned": True, "risk_flags": [],
        "score": 77.7, "coverage_pct": 70.0,
        "decision": "추가 검증 필요",
    }


class CompositeContract(unittest.TestCase):
    def test_fixed_points_and_pattern_dominance(self):
        self.assertEqual(WEIGHTS, {
            "daily_pattern": 25, "weekly_pattern": 25,
            "daily_rsi": 10, "weekly_rsi": 10,
            "volume_obv": 10, "weekly_trend": 10, "downside": 10})
        x = stock()
        out = evaluate(x)
        self.assertEqual(out["schema"], SCHEMA)
        self.assertEqual(out["components"], {
            "daily_pattern": 25,
            "weekly_pattern": 20,  # Wedge > Bull Flag forming; no stacking!
            "daily_rsi": 10,
            "weekly_rsi": 7,
            "volume_obv": 10,
            "weekly_trend": 8,
            "downside": 10,
        })
        self.assertEqual(out["research_priority_score"], 90)
        self.assertEqual(out["daily_top_pattern"]["type"], "bull_flag")
        self.assertEqual(out["weekly_top_pattern"]["type"], "falling_wedge_candidate")
        self.assertEqual(out["observed_weight"], 100)
        self.assertEqual(out["status"], "rank_eligible")
        self.assertTrue(out["ready_for_ranking"])
        self.assertFalse(out["buy_certified"])
        self.assertFalse(out["v2_score_changed"])
        self.assertIsNone(out["v3_six_pillar_score"])
        self.assertIsNone(out["historical_success_probability"])
        self.assertEqual(x["score"], 77.7, "v2 score remains unchanged")
        self.assertEqual(x["decision"], "추가 검증 필요")

    def test_stop_price_uses_analysed_ohlcv_not_old_listing_snapshot(self):
        x = stock()
        x["price"] = 70  # stale raw-listing value, not the price_latest day
        out = evaluate(x)
        self.assertEqual(out["components"]["downside"], 10)
        self.assertTrue(out["ready_for_ranking"])

    def test_missing_weekly_evidence_must_not_fake_full_100(self):
        x = stock()
        del x["pattern_weekly_research"]
        out = evaluate(x)
        self.assertIsNone(out["components"]["weekly_pattern"])
        self.assertIsNone(out["components"]["weekly_rsi"])
        self.assertEqual(out["observed_weight"], 65)
        self.assertIsNone(out["research_priority_score"])
        self.assertEqual(out["status"], "incomplete_unscored")
        self.assertFalse(out["ready_for_ranking"])

    def test_pattern_signal_does_not_require_obv(self):
        x = stock()
        x["obv_rising"] = False
        x["obv_divergence"] = False
        result = evaluate(x)
        self.assertEqual(result["components"]["volume_obv"], 6)
        self.assertEqual(result["research_priority_score"], 86)
        self.assertTrue(result["ready_for_ranking"])
        self.assertEqual(result["components"]["daily_pattern"], 25)
        self.assertEqual(result["components"]["weekly_pattern"], 20)

    def test_rsi_is_only_counted_if_observed_and_bullish(self):
        x = stock()
        for key in ("pattern_research", "pattern_weekly_research"):
            x[key]["rsi30_cross_recent"] = False
            x[key]["rsi_bullish_divergence_recent"] = False
        result = evaluate(x)
        self.assertEqual(result["components"]["daily_rsi"], 0)
        self.assertEqual(result["components"]["weekly_rsi"], 0)
        self.assertEqual(result["research_priority_score"], 73)

    def test_very_high_pattern_cannot_override_risk_veto(self):
        x = stock()
        x["stop_line"] = 82
        x["stop_pct"] = -18
        x["risk_flags"] = ["예상 손절폭 과대"]
        out = evaluate(x)
        self.assertEqual(out["status"], "risk_excluded")
        self.assertIsNotNone(out["research_priority_score"])
        self.assertFalse(out["ready_for_ranking"])
        self.assertIsNone(out["rank"])
        x = stock()
        x["risk_flags"] = ["거래대금 부족 — 환금성·호가 스프레드 점검 필요"]
        self.assertEqual(evaluate(x)["status"], "risk_excluded")
        x = stock()
        x["price60"] = 46
        self.assertFalse(evaluate(x)["ready_for_ranking"])

    def test_source_and_week_completeness_must_match_market_date(self):
        x = stock()
        for mutation in (
            lambda z: z["pattern_research"].update(asof="2026-10-19"),
            lambda z: z["pattern_weekly_research"].update(asof="2026-10-23"),
            lambda z: z["pattern_weekly_research"].update(asof="2026-10-02"),
            lambda z: z["pattern_weekly_research"].update(asof="2026-10-19"),
            lambda z: z["pattern_weekly_research"].update(completed_week_only=False),
            lambda z: z["pattern_weekly_research"].update(buy_signal=True),
            lambda z: z["pattern_research"].update(scoring_allowed=True),
            lambda z: z["pattern_weekly_research"].pop("pattern_geometry"),
            lambda z: z["pattern_weekly_research"]["pattern_geometry"].update(
                falling_wedge_candidate="yes"),
            lambda z: z["pattern_weekly_research"].update(rsi14=105),
        ):
            r = stock()
            mutation(r)
            score = evaluate(r)
            self.assertIsNone(score["research_priority_score"],
                              f"source error should be unscored: {score}")
            self.assertFalse(score["ready_for_ranking"])

    def test_identical_shape_different_timeframes_each_gets_credit(self):
        x = stock()
        x["pattern_weekly_research"]["pattern_geometry"]["falling_wedge_candidate"] = False
        x["pattern_weekly_research"]["patterns"] = [{
            "type": "bull_flag", "stage": "breakout_observed"}]
        out = evaluate(x)
        self.assertEqual(out["components"]["daily_pattern"], 25)
        self.assertEqual(out["components"]["weekly_pattern"], 25)

    def test_full_market_rank_is_deterministic_and_preserves_original_order(self):
        winner = stock("333333")
        rival = stock("111111")
        weak = stock("999999")
        weak["pattern_research"]["patterns"] = []
        weak["pattern_weekly_research"]["patterns"] = []
        weak["pattern_weekly_research"]["pattern_geometry"]["falling_wedge_candidate"] = False
        missing = stock("000001")
        missing.pop("pattern_weekly_research")
        unsafe = stock("000002")
        unsafe["risk_flags"] = ["거래대금 부족"]
        rows = [missing, weak, rival, unsafe, winner]
        order_before = [r["code"] for r in rows]
        ordered = rank_eligible_rows(rows)
        self.assertEqual([r["code"] for r in ordered],
                         ["111111", "333333", "999999"])
        self.assertEqual([r["code"] for r in rows], order_before)
        self.assertEqual([r["pattern_composite"]["rank"] for r in ordered], [1,2,3])
        self.assertIsNone(missing["pattern_composite"]["rank"])
        self.assertIsNone(unsafe["pattern_composite"]["rank"])
        self.assertEqual(unsafe["pattern_composite"]["status"], "risk_excluded")

    def test_no_future_price_to_backfill(self):
        x = stock()
        x["price_date"] = "2026-10-15"
        self.assertIsNone(evaluate(x)["research_priority_score"])
        self.assertTrue(any("incomplete" in m for m in evaluate(x)["risk_warnings"]))


if __name__ == "__main__":
    unittest.main()
