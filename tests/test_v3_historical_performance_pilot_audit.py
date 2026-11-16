"""Saved delisting pilot audit, including known pricing and path gaps."""
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from v3_historical_performance_pilot_audit import (
    ROOT, TAG, PilotAuditError, audit, cash_dividend_replay, dividend_units,
)


class HistoricalPerformancePilotAuditTests(unittest.TestCase):
    def test_reconciles_saved_pilot_and_preserves_evidence_limits(self):
        report = audit()
        self.assertEqual(report["pilot_horizon_rows"], report["pilot_issuers"] * 5)
        self.assertEqual(report["methods"]["awaiting_settlement_unpriced"], 2)
        self.assertEqual(report["methods"]["scheduled_cash_entitlement"], 1)
        self.assertEqual(report["methods"]["cash_delivery"], 1)
        self.assertEqual(report["horizons"]["12"]["paths_with_unpriced_sessions"], report["pilot_issuers"])
        self.assertEqual(report["horizons"]["12"]["no_touch_observed"], 0)
        self.assertEqual(report["next_session_traded_non_spac_outside_pilot"], 1)
        self.assertFalse(report["source_prices_independently_verified"])
        self.assertFalse(report["point_in_time_scores_verified"])
        self.assertFalse(report["performance_validated"])

    def test_dividends_follow_entry_holdings_and_payments(self):
        report = audit()
        replays = report["dividend_replays"]
        self.assertEqual(report["dividend_event_rows"], 16)
        self.assertEqual(report["dividend_eligible_events"], 12)
        self.assertEqual(report["dividend_events_outside_holding"], 4)
        self.assertEqual(report["horizons"]["12"]["price_scenarios_with_reported_dividend_cash"], 8)
        self.assertFalse(report["dividend_coverage_complete"])
        self.assertNotIn("230360", replays)
        self.assertEqual(replays["082640"]["12"], {"entitled_cash_unverified_at_evaluation": 1})
        self.assertEqual(replays["138490"]["2"], {"entitled_cash_unverified_at_evaluation": 1})
        self.assertEqual(replays["057050"]["6"]["reported_cash_krw_per_entry_share"], "2800.0000")
        self.assertEqual(replays["057050"]["12"]["reported_cash_krw_per_entry_share"], "3213.2118")
        self.assertEqual(replays["010620"]["12"]["price_scenario_plus_reported_cash_pct"], "-47.78")

    def test_fractional_cash_uses_integer_delivery_and_the_disclosed_close(self):
        report = audit()
        replays = report["fractional_cash_replays"]
        self.assertEqual(report["fractional_cash_terms_issuers"], 8)
        self.assertEqual(report["fractional_cash_unmodeled_issuers"], ["028080", "299900"])
        self.assertEqual(report["horizons"]["12"]["price_scenarios_with_fractional_cash_claim"], 8)
        self.assertFalse(report["fractional_cash_payments_verified"])
        self.assertNotIn("2", replays["042670"])
        # 이마트 교환일 종가 83,400원, 100주 × 0.5031313 = 50주 + 단주 0.31313주.
        self.assertEqual(replays["031440"]["12"]["100"], {
            "successor_whole_shares": 50,
            "cash_claim_krw": "26115.0420",
            "price_plus_cash_claim_pct": "-19.95",
        })
        self.assertEqual(replays["031440"]["12"]["1"]["successor_whole_shares"], 0)
        self.assertEqual(replays["010620"]["1"]["100"]["price_plus_cash_claim_pct"], "2.36")

    def test_uncertain_share_delivery_is_not_treated_as_a_dividend_entitlement(self):
        settlement = {
            "successor_code": "000002", "event_on": "2025-12-01",
            "settlement_or_share_availability_on": "2025-12-15",
            "shares_per_old_share": "0.4", "treatment": "stock_exchange",
        }
        self.assertEqual(dividend_units("000001", "000001", date(2025, 11, 17), settlement, {}),
                         Decimal(0))
        self.assertIsNone(dividend_units("000001", "000002", date(2025, 12, 10), settlement, {}))
        self.assertEqual(dividend_units("000001", "000002", date(2025, 12, 16), settlement, {}),
                         Decimal("0.4"))

    def test_reported_cash_does_not_create_a_missing_terminal_valuation(self):
        events = [(date(2025, 12, 1), date(2025, 12, 10), "reported_in_payment_history", Decimal(2))]
        result = cash_dividend_replay({"value_per_entry_share_krw": ""}, events, date(2025, 12, 15),
                                      Decimal(100))
        self.assertEqual(result["reported_cash_krw_per_entry_share"], "2.0000")
        self.assertNotIn("price_scenario_plus_reported_cash_pct", result)

    def test_detects_workbook_errors_that_arithmetic_alone_would_miss(self):
        cases = (
            ("cash-dividends", {"payer_code": "316140"},
             {"payment_evidence": "reported_in_payment_history"}, "invalid_dividend_evidence"),
            ("cash-dividends", {"payer_code": "316140"},
             {"payer_code": "000000"}, "dividend_payer_mismatch"),
            ("fractional-cash", {"code": "031440"},
             {"close_on": "2026-08-11"}, "invalid_fractional_cash_evidence"),
            ("fractional-cash", {"code": "010620"},
             {"close_krw": "550000"}, "fractional_cash_quote_mismatch"),
            ("fractional-cash", {"code": "082640"},
             {"code": "028080"}, "fractional_cash_requires_sequential_settlement"),
            ("fractional-cash", {"code": "082640"},
             {"code": "299900"}, "fractional_cash_requires_sequential_settlement"),
            ("price-replay", {"code": "042670", "horizon_months": "2"},
             {"quote_or_cash_krw": "0"}, "invalid_unpriced_settlement"),
            ("price-replay", {"code": "010620", "horizon_months": "1"},
             {"price_change_pct": "2.37"}, "value_or_return_mismatch"),
            ("price-replay", {"code": "299900", "horizon_months": "12"},
             {"units_per_entry_share": "0.5774514", "value_per_entry_share_krw": "1270.3931",
              "price_change_pct": "75.23"}, "stock_delivery_ratio_mismatch"),
            ("downside-horizons", {"code": "012510", "horizon_months": "12"},
             {"touch_status": "no_touch_observed"}, "reference_touch_mismatch"),
            ("delisting-settlement", {"code": "230360"},
             {"settlement_date_evidence": "reported_completed"}, "cash_payment_evidence_mismatch"),
            ("downside-horizons", {"code": "008290", "horizon_months": "6"},
             {"horizon_months": "7"}, "pilot_horizon_join_mismatch"),
        )
        for ledger, match, changes, error in cases:
            with self.subTest(error=error), TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "ops").mkdir()
                for original in (ROOT / "ops").glob(f"v3-performance-*-{TAG}.csv"):
                    shutil.copyfile(original, root / "ops" / original.name)
                path = root / "ops" / f"v3-performance-{ledger}-{TAG}.csv"
                with path.open(encoding="utf-8", newline="") as source:
                    reader = csv.DictReader(source)
                    fieldnames, rows = reader.fieldnames, list(reader)
                row, = (r for r in rows if all(r[k] == v for k, v in match.items()))
                row.update(changes)
                with path.open("w", encoding="utf-8", newline="") as target:
                    writer = csv.DictWriter(target, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                with self.assertRaisesRegex(PilotAuditError, error):
                    audit(root)
