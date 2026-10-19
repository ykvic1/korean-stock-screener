"""100% synthetic step-8 regression: short history works; 120wk optional.

No live market data, code/price publication, API tokens, or network calls.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v3_optional_technical_research import (
    analyze_observed_panel, run_optional_technical_research,
    PartialTechnicalError, main)
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_local_private_store import store_pair

D0 = "2026-10-15"
D1 = "2026-10-16"


def weekdays(start, count):
    out = []
    day = date.fromisoformat(start)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def series(days, *, gaps=(), closes=None):
    close = closes or [100 + i for i in range(len(days))]
    bars = []
    for i, d in enumerate(days):
        if d in gaps:
            continue
        p = close[i]
        bars.append({
            "date": d, "open": p, "high": p + 2, "low": p - 2,
            "close": p, "aggregate_acc_trdvol_observed": 1000,
            "source_scope": "KRX_EOD_AGGREGATE_UNVERIFIED",
            "corporate_action_adjustment_verified": False,
            "issuer_identity_verified": False,
            "regular_only_volume_verified": False})
    return {"observed_bars": bars}


def raw(day, code, close="103"):
    rows = [{
        "BAS_DD": day.replace("-", ""), "ISU_CD": code,
        "TDD_OPNPRC": "100", "TDD_HGPRC": "110",
        "TDD_LWPRC": "90", "TDD_CLSPRC": close,
        "ACC_TRDVOL": "1000"}]
    return json.dumps({"OutBlock_1": rows}).encode()


class OptionalTechnicalTests(unittest.TestCase):
    def test_first_ten_days_do_not_require_120_week_history(self):
        days = weekdays("2026-09-14", 10)
        result = analyze_observed_panel(series(days), days)
        self.assertEqual(result["contiguous_ending_bars"], 10)
        self.assertEqual(result["indicators"]["daily_ma5"], 107.0)
        self.assertIsNone(result["indicators"]["daily_ma20"])
        self.assertIsNone(result["indicators"]["weekly_ma120"])
        self.assertFalse(result["v3_score_allowed"])

    def test_65_trading_days_yield_60_day_signals_but_no_weekly_120(self):
        days = weekdays("2026-06-01", 65)
        report = analyze_observed_panel(series(days), days)
        v = report["indicators"]
        self.assertEqual(report["asof"], days[-1])
        self.assertEqual(v["daily_ma20"], 154.5)
        self.assertEqual(v["daily_ma60"], 134.5)
        self.assertEqual(v["daily_rsi14"], 100.0)
        self.assertAlmostEqual(v["close_return20_pct"], 100 * (164 / 144 - 1), 4)
        self.assertAlmostEqual(v["close_return60_pct"], 100 * (164 / 104 - 1), 4)
        self.assertEqual(v["prior20_support_low"], 142)
        self.assertEqual(v["prior20_resistance_high"], 165)
        self.assertEqual(v["reported_acc_volume_ratio20"], 1.0)
        self.assertEqual(v["reported_acc_obv_proxy20_pct"], 100.0)
        self.assertEqual(v["reported_acc_obv_proxy60_pct"], 100.0)
        self.assertIsNone(v["weekly_ma20"])
        self.assertIsNone(v["weekly_ma120"])
        self.assertFalse(report["reported_acc_volume_is_regular_only"])

    def test_105_days_compute_weekly_20_optional_not_weekly120(self):
        days = weekdays("2026-04-20", 105)
        result = analyze_observed_panel(series(days), days)
        self.assertIn("weekly_ma20", result["available"])
        self.assertIn("weekly_ma10", result["available"])
        self.assertIn("daily_ma60", result["available"])
        self.assertIn("weekly_ma120", result["unavailable"])

    def test_120week_if_present_but_not_required(self):
        days = weekdays("2024-04-01", 610)
        result = analyze_observed_panel(series(days), days)
        self.assertGreaterEqual(result["contiguous_ending_weeks"], 120)
        self.assertIn("weekly_ma120", result["available"])
        self.assertFalse(result["v3_score_allowed"])

    def test_missing_recent_day_blocks_long_windows_not_backfills(self):
        days = weekdays("2026-06-01", 65)
        data = series(days, gaps={days[-10]})
        report = analyze_observed_panel(data, days)
        self.assertEqual(report["contiguous_ending_bars"], 9)
        self.assertIsNotNone(report["indicators"]["daily_ma5"])
        self.assertIsNone(report["indicators"]["daily_ma20"])
        self.assertIsNone(report["indicators"]["close_return20_pct"])
        self.assertIsNone(report["indicators"]["reported_acc_obv_proxy20_pct"])

    def test_last_day_missing_or_quarantined_yields_no_stale_indicators(self):
        days = weekdays("2026-06-01", 65)
        report = analyze_observed_panel(series(days, gaps={days[-1]}), days)
        self.assertEqual(report["status"], "latest_saved_pair_missing_or_quarantined")
        self.assertEqual(len(report["available"]), 0)
        self.assertFalse(report["v3_score_allowed"])

    def test_unadjusted_jump_breaks_causal_contiguous_history(self):
        days = weekdays("2026-06-01", 65)
        closes = [100.0] * 40 + [50.0] * 25
        report = analyze_observed_panel(series(days, closes=closes), days)
        self.assertEqual(report["contiguous_ending_bars"], 25)
        self.assertEqual(report["warning"],
                         "large_unadjusted_price_jump_in_older_history")
        self.assertIsNotNone(report["indicators"]["daily_ma20"])
        self.assertIsNone(report["indicators"]["daily_ma60"])
        self.assertIsNone(report["indicators"]["weekly_ma120"])

    def test_source_spoofing_is_rejected(self):
        days = weekdays("2026-09-14", 6)
        fake = series(days)
        fake["observed_bars"][-1]["regular_only_volume_verified"] = True
        with self.assertRaisesRegex(PartialTechnicalError,
                                    "false_regular_session_assertion"):
            analyze_observed_panel(fake, days)
        fake = series(days)
        fake["observed_bars"][-1]["source_scope"] = "KRX_REGULAR_VERIFIED"
        with self.assertRaisesRegex(PartialTechnicalError,
                                    "unexpected_or_spoofed_source_scope"):
            analyze_observed_panel(fake, days)

    def test_previously_archived_prefix_computes_short_periods_without_future(self):
        days = calendar_weekday_candidates("2025-10-13", "2026-10-16")
        current = days[:65]
        mock_bundle = {
            "summary": {"archived_two_market_pairs": 65},
            "panels": {("KOSPI", "005930"): series(current)}}
        with patch("v3_optional_technical_research.build_private_price_panels",
                   return_value=mock_bundle):
            report = run_optional_technical_research(
                start="2025-10-13", end="2026-10-16", private_db="/nothing.sqlite3",
                targets=[{"market":"KOSPI","code":"005930"}],
                calendar_source="weekdays")
        summary = report["summary"]
        self.assertEqual(summary["archived_paired_market_days"], 65)
        self.assertEqual(summary["targets_with_daily_ma20"], 1)
        self.assertEqual(summary["targets_with_daily_ma60"], 1)
        self.assertEqual(summary["targets_with_optional_weekly_ma120"], 0)
        self.assertFalse(summary["last_archived_day_is_requested_end"])
        self.assertFalse(summary["full_v3_six_pillar_score_ready"])
        self.assertNotIn("005930", json.dumps(summary))

    def test_real_sqlite_integrated_pipeline_is_offline_and_summary_only(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "private" / "prices.sqlite3"
            for day in (D0, D1):
                store_pair(
                    database_path=db, asof=day,
                    kospi_raw=raw(day,"005930"),
                    kosdaq_raw=raw(day,"247540"),
                    kospi_received_at_utc=day+"T07:31:00Z",
                    kosdaq_received_at_utc=day+"T07:31:01Z")
            buffer = StringIO()
            with redirect_stdout(buffer):
                rc = main([
                    "--start", D0, "--end", D1, "--private-db", str(db),
                    "--calendar-source", "weekdays", "--market", "KOSPI",
                    "--code", "005930"])
            self.assertEqual(rc, 0)
            shown = buffer.getvalue()
            self.assertIn('"archived_paired_market_days": 2', shown)
            self.assertNotIn("005930", shown)
            self.assertNotIn("247540", shown)
            self.assertNotIn("TDD_CLSPRC", shown)
            self.assertNotIn(str(db), shown)
            self.assertNotIn('"private_results"', shown)
            self.assertIn('"weekly_120_ma_is_mandatory": false', shown)
            self.assertIn('"v3_release_ready": false', shown)

    def test_empty_store_no_simulated_prices_or_scores(self):
        with tempfile.TemporaryDirectory() as t:
            db = Path(t) / "private" / "prices.sqlite3"
            result = run_optional_technical_research(
                start=D0, end=D1, private_db=db,
                targets=[{"market": "KOSDAQ", "code": "247540"}],
                calendar_source="weekdays")
            s = result["summary"]
            self.assertFalse(db.exists())
            self.assertEqual(s["targets_with_any_daily_indicator"], 0)
            self.assertIsNone(s["last_archived_day"])
            self.assertFalse(s["v3_release_ready"])


if __name__ == "__main__":
    unittest.main()
