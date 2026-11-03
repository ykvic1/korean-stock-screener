import unittest
from datetime import date, timedelta

from v3_turnover_multihorizon import (
    SCHEMA, TurnoverHistoryError, observe,
)


def sessions(count):
    current = date(2024, 1, 1)
    rows = []
    while len(rows) < count:
        if current.weekday() < 5:
            i = len(rows)
            rows.append({
                "date": current.isoformat(),
                "close": float(100 + i),
                "amount": float(100_000_000 + i * 1_000_000),
            })
        current += timedelta(days=1)
    return rows


class ActualMoneyMultihorizonTests(unittest.TestCase):
    def test_full_daily_and_weekly_windows_are_explicit_and_unscored(self):
        rows = sessions(700)
        outcome = observe(rows, asof=rows[-1]["date"])
        self.assertEqual(outcome["schema"], SCHEMA)
        self.assertEqual(outcome["available_daily_horizons"], [20, 60, 120, 240])
        self.assertEqual(outcome["available_weekly_horizons"], [5, 10, 20, 60, 120])
        self.assertGreater(outcome["daily"]["20"]["average_eok"], 0)
        self.assertEqual(outcome["daily"]["120"]["signed_money_pressure"], 1.0)
        self.assertEqual(outcome["weekly"]["60"]["signed_money_pressure"], 1.0)
        self.assertFalse(outcome["official_regular_session_verified"])
        self.assertFalse(outcome["corporate_actions_verified"])
        self.assertFalse(outcome["scoring_allowed"])
        self.assertIsNone(outcome["investment_points"])
        self.assertFalse(outcome["original_price_or_value_bars_exported"])

    def test_short_history_does_not_invent_long_windows(self):
        rows = sessions(140)
        outcome = observe(rows, asof=rows[-1]["date"])
        self.assertIsNone(outcome["daily"]["240"])
        self.assertIsNone(outcome["weekly"]["60"])
        self.assertIsNone(outcome["weekly"]["120"])
        self.assertNotIn(240, outcome["available_daily_horizons"])

    def test_proxy_price_times_shares_is_explicitly_rejected(self):
        rows = sessions(700)
        with self.assertRaisesRegex(
                TurnoverHistoryError, "proxy_amount_is_not_actual"):
            observe(rows, asof=rows[-1]["date"], exact_reported_amount=False)

    def test_missing_value_gap_and_asof_mismatch_are_rejected(self):
        rows = sessions(250)
        copied = [dict(x) for x in rows]
        del copied[80]["amount"]
        with self.assertRaisesRegex(TurnoverHistoryError, "invalid_session_columns"):
            observe(copied, asof=rows[-1]["date"])
        copied = [dict(x) for x in rows]
        copied[80]["amount"] = float("nan")
        with self.assertRaisesRegex(TurnoverHistoryError, "missing_actual"):
            observe(copied, asof=rows[-1]["date"])
        with self.assertRaisesRegex(TurnoverHistoryError, "stale_actual"):
            observe(rows, asof="2026-11-03")

    def test_long_price_jump_marks_corporate_action_review(self):
        rows = sessions(300)
        rows[250]["close"] *= 5
        outcome = observe(rows, asof=rows[-1]["date"])
        self.assertTrue(outcome["price_jump_corporate_action_review_required"])
        self.assertFalse(outcome["scoring_allowed"])

    def test_one_day_spike_is_distinguished_from_sustained_increase(self):
        sustained, spike = sessions(40), sessions(40)
        for row in sustained + spike:
            row["amount"] = 100_000_000
        for row in sustained[-20:]:
            row["amount"] = 200_000_000
        spike[-1]["amount"] = 2_100_000_000
        steady = observe(sustained, asof=sustained[-1]["date"])["daily"]["20"]
        burst = observe(spike, asof=spike[-1]["date"])["daily"]["20"]
        self.assertEqual(steady["versus_previous_disjoint_window_ratio"], 2)
        self.assertEqual(burst["versus_previous_disjoint_window_ratio"], 2)
        self.assertEqual(steady["subwindows_above_previous_average_ratio"], 1)
        self.assertEqual(burst["subwindows_above_previous_average_ratio"], .25)
        self.assertEqual(steady["largest_bar_amount_share"], .05)
        self.assertEqual(burst["largest_bar_amount_share"], .525)

    def test_comparison_requires_full_nonoverlapping_nonzero_baseline(self):
        rows = sessions(39)
        result = observe(rows, asof=rows[-1]["date"])["daily"]["20"]
        self.assertIsNone(result["versus_previous_disjoint_window_ratio"])
        self.assertIsNone(result["subwindows_above_previous_average_ratio"])
        rows = sessions(40)
        for row in rows[:20]:
            row["amount"] = 0
        result = observe(rows, asof=rows[-1]["date"])["daily"]["20"]
        self.assertIsNone(result["versus_previous_disjoint_window_ratio"])
        self.assertIsNone(result["subwindows_above_previous_average_ratio"])

    def test_open_week_cannot_distort_completed_week_average(self):
        rows = sessions(26)  # five full weeks followed by Monday
        for row in rows:
            row["amount"] = 100_000_000
        rows[-1]["amount"] = 100_000_000_000
        result = observe(rows, asof=rows[-1]["date"])
        self.assertTrue(result["latest_calendar_week_excluded"])
        self.assertEqual(result["weekly_window_source_weeks"], 5)
        self.assertEqual(result["weekly_windows_asof"], rows[-2]["date"])
        self.assertEqual(result["weekly"]["5"]["average_eok"], 5)
        self.assertIsNone(result["weekly"]["5"]["signed_money_pressure"])
        self.assertFalse(result["scoring_allowed"])

    def test_midweek_start_cannot_fill_a_five_week_window(self):
        rows = sessions(25)[1:]  # first Monday missing; last Friday observed
        result = observe(rows, asof=rows[-1]["date"])
        self.assertTrue(result["first_calendar_week_excluded"])
        self.assertFalse(result["latest_calendar_week_excluded"])
        self.assertEqual(result["weekly_window_source_weeks"], 4)
        self.assertIsNone(result["weekly"]["5"])
        self.assertNotIn(5, result["available_weekly_horizons"])

    def test_completed_week_comparison_ignores_following_partial_week(self):
        rows = sessions(51)
        for row in rows[:25]:
            row["amount"] = 100_000_000
        for row in rows[25:50]:
            row["amount"] = 200_000_000
        rows[-1]["amount"] = 100_000_000_000
        result = observe(rows, asof=rows[-1]["date"])["weekly"]["5"]
        self.assertEqual(result["versus_previous_disjoint_window_ratio"], 2)
        self.assertEqual(result["subwindows_above_previous_average_ratio"], 1)
        self.assertEqual(result["persistence_subwindow_bars"], 1)


if __name__ == "__main__":
    unittest.main()
