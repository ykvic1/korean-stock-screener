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


if __name__ == "__main__":
    unittest.main()
