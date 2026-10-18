"""Causal observed daily/weekly technical level tests; synthetic only."""
from __future__ import annotations
import copy
import unittest
from v3_structural_levels import structural_levels, StructureError
from v3_offline_workbench import demo_bundle, run_workbench

class LevelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = demo_bundle()
        cls.stock = cls.demo["stocks"][0]

    def make(self):
        bars=copy.deepcopy(self.stock["price_bars"])
        bars[-22]["low"] = 50.0
        bars[-18]["high"] = 200.0
        bars[-12]["low"] = 70.0
        bars[-8]["high"] = 170.0
        return bars

    def test_latest_daily_support_resistance_not_older_convenient(self):
        b=self.make();r=structural_levels(b,asof=b[-1]["date"])
        self.assertEqual(r["daily_confirmed_support"]["price"],70)
        self.assertEqual(r["daily_confirmed_resistance"]["price"],170)
        self.assertEqual(r["reference_invalidation"]["price"],70)
        self.assertFalse(r["structural_price_is_trade_stop"])
        self.assertFalse(r["price_provenance_authenticated"])
        self.assertFalse(r["confirmed_buy"])

    def test_breached_latest_level_does_not_choose_older_level(self):
        b=self.make()
        b[-1].update(open=67,high=68,low=65,close=67)
        r=structural_levels(b,asof=b[-1]["date"])
        self.assertEqual(r["daily_confirmed_support"]["price"],70)
        self.assertTrue(r["daily_confirmed_support"]["level_invalidated_or_breached"])
        self.assertTrue(r["reference_invalidation"]["already_invalidated"])

    def test_last_unconfirmed_pivot_is_not_assumed_support(self):
        b=self.make()
        b[-2]["low"]=40
        r=structural_levels(b,asof=b[-1]["date"])
        self.assertEqual(r["daily_confirmed_support"]["price"],70)
        self.assertNotEqual(r["daily_confirmed_support"]["price"],40)

    def test_asof_prefix_never_uses_future_bars_for_pivot(self):
        b=self.make();old=b[:-10]
        first=structural_levels(old,asof=old[-1]["date"])
        later=copy.deepcopy(b)
        later[-1].update(open=500,high=510,low=490,close=500)
        updated=structural_levels(later,asof=later[-1]["date"])
        self.assertEqual(first,structural_levels(old,asof=old[-1]["date"]))
        self.assertNotEqual(first["asof"],updated["asof"])

    def test_missing_levels_report_null_not_arbitrary_five_percent(self):
        b=copy.deepcopy(self.stock["price_bars"][:5])
        r=structural_levels(b,asof=b[-1]["date"])
        self.assertIsNone(r["daily_confirmed_support"])
        self.assertIsNone(r["reference_invalidation"])
        self.assertIsNone(r["weekly_confirmed_support"])
        self.assertFalse(r["confirmed_buy"])
        empty=structural_levels(None,asof="2026-10-16")
        self.assertEqual(empty["missing_reason"],"no_price_history")

    def test_cross_history_oohlcv_bad_and_future_date_rejected(self):
        b=self.make();day=b[-1]["date"]
        b[-1]["session"]="AFTER_HOURS"
        with self.assertRaises(StructureError):
            structural_levels(b,asof=day)
        b=self.make()
        with self.assertRaises(StructureError):
            structural_levels(b,asof="2026-10-15")

    def test_workbench_all_stocks_attached_and_no_production_rank(self):
        report=run_workbench(self.demo)
        self.assertEqual(report["research_stock_count"],64)
        for row in report["stocks_in_code_order_not_ranked"]:
            levels=row["observed_daily_weekly_levels"]
            self.assertIn("weekly_confirmed_support", levels)
            self.assertIn("daily_confirmed_resistance",levels)
            self.assertFalse(levels["confirmed_buy"])
            self.assertFalse(row["trade_approval"])
        self.assertFalse(report["v3_release_ready"])

    def test_weekly_pivot_already_observed_only(self):
        b=self.make()
        b[-65]["low"]=25
        b[-45]["high"]=230
        r=structural_levels(b,asof=b[-1]["date"])
        self.assertGreater(r["observed_week_count"],120)
        self.assertIsNotNone(r["weekly_confirmed_support"])
        self.assertIsNotNone(r["weekly_confirmed_resistance"])
        self.assertFalse(r["weekly_pattern_confirmed"])

if __name__=="__main__":
    unittest.main()
