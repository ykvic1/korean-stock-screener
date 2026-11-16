"""Market replay isolates unpriced and discontinuous holdings."""

import unittest

from v3_historical_market_coverage import END, ENTRY, SIGNAL, day, summarize


class HistoricalMarketCoverageTests(unittest.TestCase):
    def test_changes_and_gaps_preserve_the_original_cohort_denominator(self):
        prices = ("KOSPI", "company", 100, 100, 110, 90, 101, 50)
        entry = {code: prices for code in ("005930", "000660", "SPLIT", "PAUSE", "DELIST", "NOENT")}
        entry["NOENT"] = (*prices[:-1], 0)
        sessions = (ENTRY, day("2025-12-15"), day("2026-01-13"), day("2026-02-13"),
                    day("2026-05-13"), END)
        panels = {SIGNAL: dict.fromkeys(entry, prices), **{s: entry.copy() for s in sessions}}
        panels[sessions[1]]["SPLIT"] = (*prices[:2], 200, *prices[3:])
        panels[sessions[1]]["PAUSE"] = (*prices[:-1], 0)
        panels[sessions[1]].pop("DELIST")
        roster = [{"code": "DELIST", "anchor_membership": "present", "market": "KOSPI", "anchor_name": "company"}]
        trades = [{"code": "DELIST", "next_session_status": "traded"}]
        delisting_prices = [{"code": "DELIST", "horizon_months": "1", "value_per_entry_share_krw": ""}]
        report = summarize(panels, roster, trades, delisting_prices)
        self.assertEqual(report["listed_total"], 6)
        self.assertEqual(report["delisted_with_entry_trade"], 1)
        self.assertEqual(report["horizons"]["1"]["valuation_unpriced"], 1)
        last = report["horizons"]["12"]
        self.assertEqual(last["listed_shares_changed_or_missing"], 1)
        self.assertEqual(last["stable_quote_valuations"], 3)
        self.assertEqual(last["stable_quote_paths_with_gaps"], 1)
        self.assertEqual(last["non_delisted_entry_unpriced"], 1)
        self.assertFalse(report["performance_validated"])
