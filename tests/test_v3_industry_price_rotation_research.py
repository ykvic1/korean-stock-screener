"""No-network, no-sector-score regression for current company price breadth."""
import unittest
from copy import deepcopy
from unittest.mock import patch

from v3_industry_price_rotation_research import (
    IndustryPriceError, build, source_ready, to_csv, valid_relative,
)

DAY = "2026-10-22"


def fixture(n=20):
    members = {}
    classified = {}
    rows = []
    stocks = []
    for i in range(n):
        code = f"{i+1:06d}"
        members[code] = {"code": code, "name": f"fixture{i}", "market": "KOSPI"}
        classified[code] = {
            "status": "observed", "industry_code": "20123", "market": "KOSPI"
        }
        rows.append({"code": code, "market": "KOSPI", "name": f"fixture{i}",
                     "price_history_status": "priced"})
        stocks.append({"code": code, "market": "KOSPI", "name": f"fixture{i}",
                       "price_date": DAY, "relative20": 2.0, "relative60": -3.0,
                       "relative120": 4.5})
    market = {"date": DAY, "index_available": ["KOSPI"], "stocks": stocks}
    catalog = {"date": DAY, "listed_total": n, "rows": rows}
    industry = {
        "asof": DAY, "listed_total": n, "observed_industry_codes": n,
        "missing_industry_codes": 0, "not_yet_attempted": 0,
        "records": classified,
        "official_krx_sector_membership_verified": False,
        "past_constituent_membership_pit_verified": False,
        "industry_5433_points_authorized": False,
        "investable_v3_score_certified": False,
    }
    return market, catalog, industry, members, classified


def calculate(data):
    market, catalog, industry, members, classified = data
    with (patch("v3_industry_price_rotation_research._inputs",
                return_value=(DAY, members)),
          patch("v3_industry_price_rotation_research.prior_records",
                return_value=classified)):
        return build(market, catalog, industry)


class IndustryPriceResearchTest(unittest.TestCase):
    def test_20day_early_recovery_is_watch_only_with_full_census(self):
        report = calculate(fixture())
        row = report["groups"][0]
        self.assertEqual(report["listed_total"], 20)
        self.assertEqual(report["paired_20_60_observations"], 20)
        self.assertEqual(report["relative120_observations"], 20)
        self.assertEqual(row["relative120_issuer_count"], 20)
        self.assertEqual(row["median_market_relative_120pct_sample"], 4.5)
        self.assertEqual(report["groups_early_recovery_watch"], 1)
        self.assertEqual(row["early_rotation_research_state"],
                         "early_relative_recovery_watch")
        self.assertEqual(row["positive_relative20_breadth_all_lower_bound"], 1)
        self.assertIsNone(row["official_industry_15_points"])
        self.assertFalse(report["official_industry_15_points_authorized"])
        self.assertFalse(report["historical_krx_sector_membership_pit_verified"])
        self.assertEqual(report["new_provider_calls"], 0)
        sheet = to_csv(report)
        self.assertIn("상대강도 회복 관찰 후보", sheet)
        self.assertIn("미확인,미검증,", sheet)

    def test_unpriced_and_missing_pairs_remain_in_denominator(self):
        data = fixture()
        market, catalog, _, _, _ = data
        market["stocks"] = market["stocks"][:14]
        for row in catalog["rows"][14:]:
            row["price_history_status"] = "unpriced"
        report = calculate(data)
        row = report["groups"][0]
        self.assertEqual(row["current_listed_issuers"], 20)
        self.assertEqual(row["same_day_priced_issuers"], 14)
        self.assertEqual(row["complete_relative20_60_issuer_count"], 14)
        self.assertEqual(row["missing_relative20_60_issuer_count"], 6)
        self.assertEqual(row["positive_relative20_breadth_all_lower_bound"], .7)
        self.assertEqual(row["positive_relative20_breadth_all_upper_bound"], 1)
        self.assertEqual(row["early_rotation_research_state"],
                         "insufficient_sample")

    def test_missing_one_horizon_never_fills_from_other_one(self):
        data = fixture()
        data[0]["stocks"][0]["relative60"] = None
        report = calculate(data)
        row = report["groups"][0]
        self.assertEqual(row["complete_relative20_60_issuer_count"], 19)
        self.assertEqual(row["positive_relative20_issuers_sample"], 19)

    def test_optional_120_never_filled_from_20_or_60(self):
        data = fixture()
        for row in data[0]["stocks"][:5]:
            row.pop("relative120")
        report = calculate(data)
        row = report["groups"][0]
        self.assertEqual(report["paired_20_60_observations"], 20)
        self.assertEqual(report["relative120_observations"], 15)
        self.assertEqual(row["relative120_issuer_count"], 15)
        self.assertEqual(row["missing_relative120_issuer_count"], 5)
        self.assertEqual(row["observed_120_coverage"], .75)
        self.assertEqual(row["median_market_relative_120pct_sample"], 4.5)

    def test_120_small_sample_hidden_and_export_empty(self):
        data = fixture()
        for row in data[0]["stocks"][2:]:
            row["relative120"] = None
        report = calculate(data)
        group = report["groups"][0]
        self.assertEqual(group["relative120_issuer_count"], 2)
        self.assertIsNone(group["median_market_relative_120pct_sample"])
        self.assertIn("120일_시장대비상대강도_표본중앙값_%", to_csv(report))

    def test_low_sample_aggregate_blank_even_when_all_available(self):
        report = calculate(fixture(2))
        row = report["groups"][0]
        self.assertIsNone(row["median_market_relative_20pct_sample"])
        self.assertIsNone(row["positive_relative20_breadth_observed_only"])
        self.assertEqual(row["early_rotation_research_state"],
                         "insufficient_sample")
        csv = to_csv(report)
        self.assertIn("표본·확보율 부족", csv)
        self.assertNotIn("2.00,-3.00", csv)

    def test_high_relative20_is_not_early_rotation_recommendation(self):
        data = fixture()
        for row in data[0]["stocks"]:
            row["relative20"] = 20.0
        report = calculate(data)
        self.assertEqual(report["groups"][0]["early_rotation_research_state"],
                         "relative20_extension_caution")

    def test_bad_price_date_or_duplicate_rejected(self):
        data = fixture()
        data[0]["stocks"][0]["price_date"] = "2026-10-21"
        with self.assertRaises(IndustryPriceError):
            calculate(data)
        data = fixture()
        data[0]["stocks"].append(deepcopy(data[0]["stocks"][0]))
        with self.assertRaises(IndustryPriceError):
            calculate(data)

    def test_source_day_or_completion_mismatch_defers(self):
        data = fixture()
        self.assertTrue(source_ready(*data[:3]))
        data[2]["asof"] = "2026-10-21"
        self.assertFalse(source_ready(*data[:3]))
        with self.assertRaises(IndustryPriceError):
            calculate(data)
        data = fixture()
        data[2]["observed_industry_codes"] -= 1
        self.assertFalse(source_ready(*data[:3]))

    def test_provenance_and_csv_guard(self):
        report = calculate(fixture())
        report["official_industry_15_points_authorized"] = True
        with self.assertRaises(IndustryPriceError):
            to_csv(report)
        data = fixture()
        data[2]["past_constituent_membership_pit_verified"] = True
        with self.assertRaises(IndustryPriceError):
            calculate(data)

    def test_relative_bounds_and_bool_never_numeric(self):
        for bad in (True, False, -100.01, 1000.01, float("nan"),
                    float("inf"), None, "2.0"):
            self.assertFalse(valid_relative(bad))
        self.assertTrue(valid_relative(0.0))
        self.assertTrue(valid_relative(-100))

    def test_market_without_index_does_not_infer_relative_signal(self):
        data = fixture()
        data[0]["index_available"] = []
        report = calculate(data)
        row = report["groups"][0]
        self.assertEqual(row["complete_relative20_60_issuer_count"], 0)
        self.assertIsNone(row["median_market_relative_20pct_sample"])
        self.assertEqual(row["early_rotation_research_state"],
                         "insufficient_sample")


if __name__ == "__main__":
    unittest.main()
