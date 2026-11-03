"""Offline regression: vendor OHLCV cannot be reused or published by date alone."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import quant_fetch_history as fetch
from quant_engine import read_ohlcv, source_cohort_digest


class QuantHistoryQualityTests(unittest.TestCase):
    ASOF = "2026-11-02"

    def bars(self):
        dates = pd.bdate_range(end=self.ASOF, periods=660)
        return pd.DataFrame({
            "Open": 100., "High": 101., "Low": 99.,
            "Close": 100., "Volume": 1000.,
        }, index=dates)

    def collect_with(self, folder, bars):
        signal = folder / "signals.json"
        signal.write_text(json.dumps({"status": "success", "date": self.ASOF}),
                          encoding="utf-8")
        cache = folder / "history"
        with (patch.object(fetch, "prioritized_stocks",
                           return_value=[{"code": "005930"}]),
              patch.object(fetch, "optional_recommendations", return_value=None),
              patch.object(fetch.fdr, "DataReader", return_value=bars) as vendor,
              patch.object(fetch.time, "sleep") as pause):
            result = fetch.collect(signal, cache, self.ASOF, sleep=0)
            if result["reused"]:
                vendor.assert_not_called()
            if result["failed_n"]:
                # Three real provider attempts, but only two useful pauses.
                self.assertEqual(vendor.call_count, 3)
                self.assertEqual(
                    [call.args[0] for call in pause.call_args_list
                     if call.args and call.args[0] > 0],
                    [1.5, 3.0])
            return result, cache

    def test_invalid_provider_bars_do_not_become_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bad = self.bars()
            bad.iloc[-1, bad.columns.get_loc("Close")] = 150.
            result, cache = self.collect_with(folder, bad)
            self.assertEqual(result["failed_n"], 1)
            self.assertEqual(result["cohort_sha256"],
                             source_cohort_digest(self.ASOF, ["005930"]))
            self.assertEqual(result["fetched"], 0)
            self.assertFalse((cache / "005930.csv").exists())
            self.assertFalse((cache / "005930.tmp").exists())
            self.assertIn("종가 범위 오류", result["failed"][0][1])

    def test_rejected_early_event_only_marks_unverified_tail_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self.bars()
            bad.iloc[1, bad.columns.get_loc("Close")] = 150.
            result, cache = self.collect_with(Path(tmp), bad)
            self.assertEqual(result["failed_n"], 1)
            self.assertEqual(result["fetched"], 0)
            self.assertFalse((cache / "005930.csv").exists())
            self.assertEqual(len(result["failed_bar_diagnostics"]), 1)
            code, diagnostic = result["failed_bar_diagnostics"][0]
            self.assertEqual(code, "005930")
            self.assertGreaterEqual(diagnostic["suspect_bar_count"], 1)
            self.assertTrue(diagnostic["trailing_650_session_candidate"])
            self.assertGreaterEqual(diagnostic["trailing_valid_sessions"], 650)
            self.assertTrue(diagnostic["research_only"])
            self.assertFalse(diagnostic["official_adjustment_certified"])
            self.assertEqual(json.loads((cache / "cache_status.json").read_text())
                             ["failed_bar_diagnostics"], result["failed_bar_diagnostics"])

    def test_recent_invalid_bar_is_not_tail_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self.bars()
            bad.iloc[-1, bad.columns.get_loc("Close")] = 150.
            result, _ = self.collect_with(Path(tmp), bad)
            self.assertEqual(result["failed_n"], 1)
            diagnostic = result["failed_bar_diagnostics"][0][1]
            self.assertEqual(diagnostic["trailing_valid_sessions"], 0)
            self.assertFalse(diagnostic["trailing_650_session_candidate"])

    def test_stale_vendor_data_has_no_fake_current_tail_diagnostic(self):
        bars = self.bars().iloc[:-1]
        self.assertIsNone(fetch.invalid_tail_diagnostic(bars, self.ASOF))
        self.assertIsNone(fetch.invalid_tail_diagnostic(None, self.ASOF))

    def test_same_day_corrupt_cache_must_refetch_and_revalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cache = folder / "history"
            cache.mkdir()
            bad = self.bars()
            bad.iloc[-1, bad.columns.get_loc("Close")] = 150.
            bad.to_csv(cache / "005930.csv", index_label="Date")
            result, _ = self.collect_with(folder, self.bars())
            self.assertEqual(result["reused"], 0)
            self.assertEqual(result["fetched"], 1)
            self.assertEqual(len(read_ohlcv(cache / "005930.csv", self.ASOF)), 660)
            again, _ = self.collect_with(folder, self.bars())
            self.assertEqual(again["reused"], 1)
            self.assertEqual(again["fetched"], 0)


if __name__ == "__main__":
    unittest.main()
