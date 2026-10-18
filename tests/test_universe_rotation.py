"""No-network market coverage tests: full listing -> 210 per weekday -> 200 shown.

The scanner already gets the entire KRX-MARCAP listing once. These tests
prevent silently losing medium/quiet eligible stocks under random sampling.
They do not fetch shares, authorize investor flows, or generate v3 scores.
"""
from datetime import date, datetime, timedelta
import unittest

import screener


def dataset(n=316):
    # Every symbol is ordinary-looking, within the existing 5,000억~5조
    # preferred market-cap interval and at least 2억 daily turnover.
    return {
        f"{100000+i:06d}": {
            "code": f"{100000+i:06d}",
            "name": f"synthetic-{i}",
            "market": "KOSPI" if i % 2 else "KOSDAQ",
            "market_cap_eok": 10000,
            "trading_eok": float(n + 10 - i),
        }
        for i in range(n)
    }


def sample(rows, asof, cfg=None):
    config = cfg or screener.cfg_read()
    day = datetime.fromisoformat(asof)
    result, eligible = screener.select_universe(rows, config, day)
    return result, eligible


class PracticalMarketExpansionTests(unittest.TestCase):
    def test_config_extends_publication_without_extra_daily_ohlcv_calls(self):
        cfg = screener.cfg_read()
        self.assertEqual(cfg["preselect_limit"], 210)
        self.assertEqual(cfg["max_watch_rows"], 200)
        self.assertEqual(cfg["top_candidates_for_flows"], 35)
        self.assertLessEqual(cfg["max_watch_rows"], cfg["preselect_limit"])
        self.assertLessEqual(cfg["top_candidates_for_flows"], cfg["preselect_limit"])

    def test_316_eligible_210_per_day_union_316_in_two_weekdays(self):
        rows = dataset()
        friday, eligible1 = sample(rows, "2026-10-16")
        monday, eligible2 = sample(rows, "2026-10-19")
        self.assertEqual((eligible1, eligible2), (316, 316))
        self.assertEqual((len(friday), len(monday)), (210, 210))
        f, m = {r["code"] for r in friday}, {r["code"] for r in monday}
        self.assertEqual(len(f | m), 316)
        self.assertNotEqual(f, m)
        self.assertEqual(len(f), 210)
        self.assertEqual(len(m), 210)

    def test_high_liquidity_third_remains_covered(self):
        rows = dataset(316)
        top70 = {f"{100000+i:06d}" for i in range(70)}
        for day in ("2026-10-16", "2026-10-19", "2026-10-20"):
            selected, total = sample(rows, day)
            self.assertEqual(total, 316)
            self.assertTrue(top70 <= {r["code"] for r in selected})

    def test_low_liquidity_group_is_not_permanently_excluded(self):
        rows = dataset(316)
        neglected = {f"{100000+i:06d}" for i in range(250, 316)}
        selections = [sample(rows, d)[0] for d in ("2026-10-16", "2026-10-19")]
        union = {r["code"] for rows_day in selections for r in rows_day}
        self.assertTrue(neglected <= union)

    def test_400_eligible_covered_within_three_consecutive_weekdays(self):
        rows = dataset(400)
        dates = ("2026-10-16", "2026-10-19", "2026-10-20")
        union = {r["code"] for day in dates for r in sample(rows, day)[0]}
        self.assertEqual(len(union), 400)

    def test_fixed_date_deterministic_even_if_dictionary_order_changes(self):
        original = dataset(316)
        reversed_rows = dict(reversed(list(original.items())))
        a, _ = sample(original, "2026-10-19")
        b, _ = sample(reversed_rows, "2026-10-19")
        self.assertEqual([r["code"] for r in a], [r["code"] for r in b])

    def test_weekend_does_not_move_rotating_bucket(self):
        rows = dataset(316)
        friday, _ = sample(rows, "2026-10-16")
        for day in ("2026-10-17", "2026-10-18"):
            weekend, _ = sample(rows, day)
            self.assertEqual([r["code"] for r in weekend],
                             [r["code"] for r in friday])

    def test_existing_market_cap_and_turnover_filters_preserved(self):
        rows = dataset(316)
        rows["999990"] = {"code": "999990", "market_cap_eok": 4900, "trading_eok": 200}
        rows["999991"] = {"code": "999991", "market_cap_eok": 51000, "trading_eok": 200}
        rows["999992"] = {"code": "999992", "market_cap_eok": 10000, "trading_eok": 1.8}
        chosen, total = sample(rows, "2026-10-19")
        self.assertEqual(total, 316)
        self.assertFalse({"999990", "999991", "999992"} & {r["code"] for r in chosen})

    def test_under_cap_keeps_all_and_not_repeat(self):
        rows = dataset(80)
        selected, eligible = sample(rows, "2026-10-19")
        self.assertEqual(eligible, 80)
        self.assertEqual(len(selected), 80)
        self.assertEqual(len({r["code"] for r in selected}), 80)

    def test_invalid_expensive_query_limit_rejected(self):
        for cap in (0, 2, 211, 10000):
            cfg = screener.cfg_read()
            cfg["preselect_limit"] = cap
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                sample(dataset(316), "2026-10-19", cfg)

    def test_weekly_randomness_removed_not_trading_day_coverage(self):
        rows = dataset(316)
        monday, _ = sample(rows, "2026-10-19")
        tuesday, _ = sample(rows, "2026-10-20")
        self.assertNotEqual({r["code"] for r in monday},
                            {r["code"] for r in tuesday})
        self.assertEqual(len(set(r["code"] for r in monday+tuesday)), 316)

    def test_selector_does_not_write_files_or_fetch_network(self):
        from unittest.mock import patch
        rows = dataset(316)
        with patch.object(screener.fdr, "StockListing", side_effect=AssertionError("network")):
            selected, eligible = sample(rows, "2026-10-19")
        self.assertEqual(eligible, 316)
        self.assertEqual(len(selected), 210)


if __name__ == "__main__":
    unittest.main()
