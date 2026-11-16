"""Saved delisting pilot audit, including known pricing and path gaps."""
import csv
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from v3_historical_performance_pilot_audit import ROOT, TAG, PilotAuditError, audit


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

    def test_detects_workbook_errors_that_arithmetic_alone_would_miss(self):
        cases = (
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
