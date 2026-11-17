from unittest import TestCase

from v3_historical_financial_factor_replay import (
    FISCAL, HORIZONS, SIGNAL, add_statement, calculate_parts, dated_reference, summarize_outcomes,
)
from v3_historical_performance_pilot_audit import PilotAuditError


class HistoricalFinancialFactorReplayTests(TestCase):
    def test_reference_drops_same_day_and_later_receipts_and_changed_identity(self):
        year = {"2022": {"CFS": {"receipt_id": "20230317000001", "values": {"revenue": 90}}},
                "2023": {"CFS": {"receipt_id": "20251113000002", "values": {"revenue": 99}}},
                "2024": {"CFS": {"receipt_id": "20251202000003", "values": {"revenue": 100}}}}
        original = {"records": {"000001": {"years": year}, "000002": {"years": year}}}
        anchor = {"000001": ("KOSPI", "가"), "000002": ("KOSDAQ", "나")}
        current = {**anchor, "000002": ("KOSDAQ", "다")}
        history = dated_reference(original, anchor, current)
        self.assertEqual(list(history), ["000001"])
        self.assertEqual(list(history["000001"]["years"]), ["2022"])

    def test_known_missing_sales_remains_missing_and_live_part_limits_are_reused(self):
        roster = [{"code": f"{i:06}", "name": f"회사{i}", "market": "KOSPI",
                   "reference_market_cap_eok": 1} for i in range(31)]
        identity = {row["code"]: ("KOSPI", row["name"]) for row in roster}
        financials = {}
        for i, row in enumerate(roster):
            values = {"assets": 100, "liabilities": 50, "equity": 50, "operating_income": 1}
            if i:
                values["revenue"] = 100
            for year in FISCAL[1:]:
                add_statement(financials, row["code"], row["name"], row["market"], "CFS",
                              "20250317000001", year, values, identity)
        catalog = {"date": SIGNAL.isoformat(), "rows": roster, "listed_total": len(roster)}
        result = calculate_parts(catalog, financials)
        self.assertEqual(set(result["000000"]), {"balance_sheet_strength"})
        self.assertEqual(result["000000"]["balance_sheet_strength"]["points"], 1.5)
        self.assertEqual(result["000001"]["operating_growth_margin"]["available"], 3)
        self.assertEqual(result["000001"]["sales_to_cap"]["available"], 2.5)
        self.assertEqual(result["000001"]["sales_to_cap"]["same_market_peers"], 30)

    def test_invalid_balance_and_missing_delisting_marks_cannot_look_priced(self):
        with self.assertRaisesRegex(PilotAuditError, "unreconciled"):
            add_statement({}, "000001", "회사", "KOSPI", "CFS", "20250317000001", 2024,
                          {"assets": 100, "liabilities": 90, "equity": 50},
                          {"000001": ("KOSPI", "회사")})
        rows = {str(h): {"000001": {"basis": "delisting_case_replay", "price_change_pct": None,
                                    "observed_adverse_excursion_pct": 44.0, "unpriced_sessions": 2}}
                for h in HORIZONS}
        result = summarize_outcomes({"000001"}, rows, {})
        self.assertEqual(result["12"]["priced"], 0)
        self.assertEqual(result["12"]["adverse_path_observed"], 1)
        self.assertIsNone(result["12"]["median_price_change_pct"])

        rows["12"]["000002"] = {"basis": "reviewed_share_action", "price_change_pct": 20.0,
                                "observed_adverse_excursion_pct": None, "unpriced_sessions": 1}
        result = summarize_outcomes({"000001", "000002"}, rows, {})["12"]
        self.assertEqual((result["priced"], result["adverse_path_observed"]), (1, 1))
        self.assertEqual(result["median_observed_adverse_excursion_pct"], 44.0)
