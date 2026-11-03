import unittest
from datetime import date, timedelta

from v3_krx_money_5day import SCHEMA as COHORT_SCHEMA
from v3_krx_money_horizon_research import (
    MoneyHorizonError, SCHEMA, observe,
)


def input_cohorts(n=130):
    newest = date(2026, 10, 12)
    entries = []
    for i in range(n):
        start = newest - timedelta(weeks=i)
        end = start + timedelta(days=4)
        obj = {
            "schema": COHORT_SCHEMA,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "amount_bucket_krw": 100_000,
            "daily_amount_or_price_bars_retained": False,
            "investment_scoring_allowed": False,
            "derived_issuer_money_factors": {
                "KOSPI:000001": {
                    "market": "KOSPI", "issuer_code": "000001",
                    "observed_sessions": 5,
                    "total_actual_amount_100k_krw": 5000,
                    "money_pressure_intrabucket_bp": 1000,
                    "late2_vs_early2_money_pressure_bp": 200,
                    "money_amount_is_explicit_acc_trdval": True,
                    "score_authorized": False,
                },
            },
        }
        entries.append({"index": i, "cohort": obj})
    return entries


class MoneyHorizonResearchTests(unittest.TestCase):
    def test_real_reported_amount_aggregates_to_20_60_120_240_days(self):
        result = observe(input_cohorts(), market="KOSPI", code="000001",
                         asof="2026-10-16")
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(set(result["daily_research"]), {"20", "60", "120", "240"})
        self.assertEqual(result["daily_research"]["240"]["observed_sessions"], 240)
        self.assertEqual(
            result["daily_research"]["20"]["average_reported_amount_eok_per_session"], 1)
        self.assertEqual(
            result["daily_research"]["120"]["money_intrabucket_pressure_proxy_bp"], 1000)
        self.assertEqual(
            result["daily_research"]["20"]["positive_money_persistence_cohort_ratio"], 1)
        self.assertEqual(set(result["five_session_week_proxies_not_calendar_weeks"]),
                         {"5", "10", "20", "60", "120"})
        self.assertIsNotNone(result["five_session_week_proxies_not_calendar_weeks"]["120"])
        self.assertTrue(all(x is None for x in result["calendar_weekly_verified"].values()))
        self.assertFalse(result["exact_money_obv_certified"])
        self.assertFalse(result["scoring_allowed"])
        self.assertIsNone(result["investment_points"])

    def test_partial_missing_issuer_windows_not_zero_filled(self):
        cohorts = input_cohorts()
        cohorts[2]["cohort"]["derived_issuer_money_factors"] = {}
        result = observe(cohorts, market="KOSPI", code="000001",
                         asof="2026-10-16")
        self.assertIsNone(result["daily_research"]["20"])
        self.assertIsNone(result["daily_research"]["240"])
        self.assertIsNone(result["five_session_week_proxies_not_calendar_weeks"]["5"])
        self.assertIsNone(result["five_session_week_proxies_not_calendar_weeks"]["120"])

    def test_short_history_avoids_long_horizon_promotion(self):
        result = observe(input_cohorts(4), market="KOSPI", code="000001",
                         asof="2026-10-16")
        self.assertIsNotNone(result["daily_research"]["20"])
        self.assertIsNone(result["daily_research"]["60"])
        self.assertIsNone(result["five_session_week_proxies_not_calendar_weeks"]["5"])

    def test_unverified_source_and_nonconsecutive_indices_fail_closed(self):
        cohorts = input_cohorts(4)
        cohorts[2]["index"] = 3
        with self.assertRaisesRegex(MoneyHorizonError, "missing_or_disordered"):
            observe(cohorts, market="KOSPI", code="000001",
                    asof="2026-10-16")
        cohorts = input_cohorts(4)
        cohorts[0]["cohort"]["investment_scoring_allowed"] = True
        with self.assertRaisesRegex(MoneyHorizonError, "unverified"):
            observe(cohorts, market="KOSPI", code="000001",
                    asof="2026-10-16")

    def test_future_date_or_forged_source_amount_rejected(self):
        cohorts = input_cohorts(4)
        with self.assertRaisesRegex(MoneyHorizonError, "future_or_reversed"):
            observe(cohorts, market="KOSPI", code="000001",
                    asof="2026-10-09")
        cohorts = input_cohorts(4)
        factor = cohorts[0]["cohort"]["derived_issuer_money_factors"]["KOSPI:000001"]
        factor["money_amount_is_explicit_acc_trdval"] = False
        with self.assertRaisesRegex(MoneyHorizonError, "invalid_issuer"):
            observe(cohorts, market="KOSPI", code="000001",
                    asof="2026-10-16")


if __name__ == "__main__":
    unittest.main()
