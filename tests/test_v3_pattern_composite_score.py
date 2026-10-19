"""Regression: day/week patterns belong to TECHNICAL 15 of original six pillars."""
import copy
import unittest

from v3_pattern_composite_score import (
    SCHEMA, TECHNICAL_WEIGHTS, evaluate, compute_candidates,
)
from v3_score_contract import PILLAR_POINTS
from v3_research_factor_engine import PILLAR_METRICS

DAY = "2026-10-20"
FRIDAY = "2026-10-16"


def research(tf, *, source_date=DAY, kind="bull_flag",
             stage="forming", crossing=False, bullish=False, wedge=False):
    data = {
        "schema": "v3-pattern-first-daily-research-0.1",
        "asof": source_date, "timeframe": tf,
        "status": "heuristic_unverified", "buy_signal": False,
        "scoring_allowed": False,
        "official_regular_session_verified": False,
        "patterns": [] if kind is None else [{"type": kind, "stage": stage}],
        "rsi14": 38.5, "rsi30_cross_recent": crossing,
        "rsi_bullish_divergence_recent": bullish,
        "pattern_geometry": {
            "status": "heuristic_unvalidated",
            "falling_wedge_candidate": wedge,
            "inverse_head_shoulders_breakout_candidate": False,
            "triangle_candidate": False,
            "post_rise_consolidation_candidate": False,
        },
    }
    if tf == "weekly":
        data["completed_week_only"] = True
        data["weekly_ma120_gap_pct"] = 4.0
    return data


def stock(code="005930"):
    return {
        "code": code, "price_date": DAY,
        "price_latest": 100.0, "stop_line": 92.0, "stop_pct": -8.0,
        "price20": 5.0, "price60": 12.0,
        "risk_flags": [],
        "pattern_research": research(
            "daily", stage="breakout_observed", crossing=True, bullish=True),
        "pattern_weekly_research": research(
            "weekly", source_date=FRIDAY, wedge=True, bullish=True),
        "one_day_volume_ratio": 1.7,
        "obv_rising": True, "obv_divergence": True,
        "score": 77.7, "decision": "추가 검증 필요",
    }


class PatternWithinOriginalSixPillars(unittest.TestCase):
    def test_fixed_six_pillar_contract_and_15_technical_points(self):
        self.assertEqual(dict(PILLAR_POINTS), {
            "ias": 25, "financial": 20, "valuation": 15,
            "technical": 15, "industry": 15, "momentum": 10})
        self.assertEqual(TECHNICAL_WEIGHTS, {
            "daily_pattern": 3, "weekly_pattern": 4,
            "daily_rsi": 1, "weekly_rsi": 2,
            "weekly_ma120": 3, "volume_confirmation": 2})
        self.assertEqual(sum(TECHNICAL_WEIGHTS.values()), 15)
        self.assertEqual([x[1] for x in PILLAR_METRICS["technical"]], [3, 3, 7, 2])

    def test_pattern_weight_both_timeframes_without_double_count(self):
        r = stock()
        x = evaluate(r)
        self.assertEqual(x["schema"], SCHEMA)
        self.assertEqual(x["technical_parts"], {
            "daily_pattern": 3.0,  # Bull Flag observed breakout
            "weekly_pattern": 3.52,  # wedge .88 beats forming flag .72
            "daily_rsi": 1, "weekly_rsi": 2,
            "weekly_ma120": 3, "volume_confirmation": 2})
        self.assertAlmostEqual(x["technical_research_candidate"], 14.52)
        self.assertEqual(x["technical_observed_maximum"], 15)
        self.assertEqual(x["six_pillar_coverage_pct"], 15.0)
        self.assertIsNone(x["six_pillar_full_candidate_only"])
        self.assertEqual(set(x["missing_pillars"]), {
            "ias", "financial", "valuation", "industry", "momentum"})
        self.assertFalse(x["provisional_full_six_pillar_sort_eligible"])
        self.assertFalse(x["buy_certified"])
        self.assertFalse(x["v3_release_ready"])
        self.assertIsNone(x["investable_score"])
        self.assertIsNone(x["confirmed_rank"])
        self.assertEqual(r["score"], 77.7)
        self.assertEqual(r["decision"], "추가 검증 필요")

    def test_obv_not_required_or_double_scored_inside_technical(self):
        r = stock()
        a = evaluate(r)
        r["obv_rising"] = False
        r["obv_divergence"] = False
        b = evaluate(r)
        self.assertEqual(a["technical_research_candidate"],
                         b["technical_research_candidate"])

    def test_missed_weekly_candles_never_mint_100(self):
        r = stock()
        r.pop("pattern_weekly_research")
        x = evaluate(r)
        self.assertIsNone(x["technical_research_candidate"])
        self.assertEqual(x["technical_observed_maximum"], 6)
        self.assertIsNone(x["six_pillar_full_candidate_only"])
        self.assertFalse(x["provisional_full_six_pillar_sort_eligible"])
        self.assertIn("technical", x["missing_pillars"])

    def test_stale_future_or_unclosed_candle_fails_closed(self):
        operations = (
            lambda x: x["pattern_research"].update(asof="2026-10-19"),
            lambda x: x["pattern_weekly_research"].update(asof="2026-10-23"),
            lambda x: x["pattern_weekly_research"].update(asof="2026-10-02"),
            lambda x: x["pattern_weekly_research"].update(asof="2026-10-19"),
            lambda x: x["pattern_weekly_research"].update(completed_week_only=False),
            lambda x: x["pattern_weekly_research"].update(buy_signal=True),
            lambda x: x["pattern_research"].update(scoring_allowed=True),
            lambda x: x["pattern_weekly_research"].pop("pattern_geometry"),
            lambda x: x["pattern_research"]["pattern_geometry"].update(
                triangle_candidate="yes"),
            lambda x: x["pattern_weekly_research"].update(rsi14=110),
            lambda x: x["pattern_weekly_research"].update(weekly_ma120_gap_pct=None),
        )
        for mutate in operations:
            with self.subTest(op=str(mutate)):
                r = stock()
                mutate(r)
                self.assertIsNone(evaluate(r)["technical_research_candidate"])

    def test_rsi_recovery_and_divergence_are_independent_evidence(self):
        r = stock()
        r["pattern_research"]["rsi30_cross_recent"] = False
        r["pattern_research"]["rsi_bullish_divergence_recent"] = False
        r["pattern_weekly_research"]["rsi_bullish_divergence_recent"] = False
        r["pattern_weekly_research"]["rsi30_cross_recent"] = False
        x = evaluate(r)
        self.assertEqual(x["technical_parts"]["daily_rsi"], 0)
        self.assertEqual(x["technical_parts"]["weekly_rsi"], 0)
        self.assertAlmostEqual(x["technical_research_candidate"], 11.52)

    def test_loss_risks_not_waived_by_good_pattern(self):
        r = stock()
        r["risk_flags"] = ["거래대금 부족 — 호가 스프레드"]
        x = evaluate(r)
        self.assertFalse(x["provisional_full_six_pillar_sort_eligible"])
        self.assertTrue(x["risk_warnings"])
        r = stock()
        r["stop_line"] = 82
        r["stop_pct"] = -18
        self.assertTrue(evaluate(r)["risk_warnings"])

    def test_other_pillars_complete_candidate_not_real_buy_score(self):
        r = stock()
        full = evaluate(r, other_pillars={
            "ias": 21, "financial": 17, "valuation": 11,
            "industry": 13, "momentum": 9})
        self.assertAlmostEqual(full["six_pillar_full_candidate_only"], 85.52)
        self.assertEqual(full["six_pillar_coverage_pct"], 100)
        self.assertTrue(full["provisional_full_six_pillar_sort_eligible"])
        self.assertIsNone(full["investable_score"])
        self.assertIsNone(full["confirmed_rank"])
        self.assertFalse(full["buy_certified"])
        with self.assertRaisesRegex(ValueError, "technical_pillar_double_count"):
            evaluate(r, other_pillars={"technical": 14.0})

    def test_main_market_publication_does_not_fabricate_other_five(self):
        rows = [stock("111111"), stock("222222")]
        rows[0]["score"] = 90  # legacy score unrelated
        original = [(r["code"], r["score"], r["decision"]) for r in rows]
        self.assertEqual(compute_candidates(rows), [])
        self.assertEqual(
            [(r["code"], r["score"], r["decision"]) for r in rows], original)
        self.assertTrue(all(
            r["v3_pattern_integrated"]["six_pillar_full_candidate_only"] is None
            for r in rows))
        self.assertTrue(all(
            r["v3_pattern_integrated"]["six_pillar_coverage_pct"] == 15.0
            for r in rows))


if __name__ == "__main__":
    unittest.main()
