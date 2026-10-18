"""Offline 4y annual statement calculation; synthetic only, no DART API."""
from __future__ import annotations

import copy
import json
import unittest

from v3_financial_feature_adapter import (
    FinancialFeatureError, FINANCIAL_OUT, VALUATION_OUT,
    calculate_financial_raw_features,
)
from v3_offline_workbench import (
    WorkbenchError, demo_bundle, run_workbench
)


def fixture():
    rows = []
    for year, sales in zip(range(2022, 2026),
                           [100, 110, 121, 133.1]):
        revenue = round(sales * 1_000_000_000)
        k = year - 2022
        assets = 180_000_000_000 + k*10_000_000_000
        liabilities = 70_000_000_000 + k*5_000_000_000
        rows.append({
            "issuer_id": "00000001", "fiscal_year": year,
            "scope": "CFS", "currency": "KRW",
            "rcept_no": f"{year+1}0301000001",
            "original_sha256": "b" * 64,
            "published_on": f"{year+1}-03-01",
            "accounts": {
                "revenue_krw": revenue,
                "operating_income_krw": round(revenue * 0.10),
                "operating_cashflow_krw": 15_000_000_000,
                "capex_cash_outflow_krw": 5_000_000_000,
                "assets_krw": assets,
                "liabilities_krw": liabilities,
                "equity_krw": assets - liabilities,
                "owners_equity_krw": assets - liabilities - 10_000_000_000,
                "owners_profit_krw": 12_000_000_000,
                "cash_krw": 20_000_000_000,
                "interest_bearing_debt_krw": 50_000_000_000,
            },
        })
    return rows


def compute(rows=None, cap=180_000_000_000):
    return calculate_financial_raw_features(
        fixture() if rows is None else rows,
        asof="2026-10-16", issuer_id="00000001",
        market_cap=({"asof": "2026-10-16", "amount_krw": cap}
                    if cap is not None else None))


class AnnualRawFinancialFeatureTests(unittest.TestCase):
    def test_three_year_cagr_margin_fcf_debt_and_owner_valuation(self):
        report = compute()
        f = report["features"]
        self.assertEqual(set(f), set(FINANCIAL_OUT) | set(VALUATION_OUT))
        self.assertAlmostEqual(f["financial_revenue_cagr_3y_pct"], 10, places=3)
        self.assertAlmostEqual(f["financial_operating_margin_3y_pct"], 10, places=3)
        # 2023, 2024, 2025 FCF/assets: 10 / 190, 200, 210.
        avg = (10/190 + 10/200 + 10/210)/3*100
        self.assertAlmostEqual(f["financial_fcf_assets_3y_pct"], avg, places=5)
        self.assertAlmostEqual(f["financial_net_debt_equity_pct"],
                               30/125*100, places=5)
        self.assertAlmostEqual(f["valuation_pbr"], 180/115, places=5)
        self.assertAlmostEqual(f["valuation_earnings_yield_pct"], 12/180*100, places=5)
        self.assertAlmostEqual(f["valuation_fcf_yield_pct"], 10/180*100, places=5)
        self.assertEqual(report["accounting_scope"], "CFS")
        self.assertEqual(report["annual_years_observed"], [2022, 2023, 2024, 2025])
        self.assertFalse(report["receipt_and_sha_strings_authenticated"])
        self.assertFalse(report["revision_history_pit_certified"])
        self.assertFalse(report["full_v3_scoring_authorized"])
        self.assertFalse(report["confirmed_buy"])

    def test_missing_capitalization_does_not_fabricate_per_pbr_fcf_yield(self):
        report = compute(cap=None)
        self.assertTrue(all(report["features"][k] is None for k in VALUATION_OUT))
        self.assertIsNotNone(report["features"]["financial_revenue_cagr_3y_pct"])

    def test_genuine_missing_cashflow_stays_null_not_zero(self):
        rows = fixture()
        rows[-1]["accounts"]["operating_cashflow_krw"] = None
        report = compute(rows)
        self.assertIsNone(report["features"]["financial_fcf_assets_3y_pct"])
        self.assertIsNone(report["features"]["valuation_fcf_yield_pct"])
        self.assertIsNotNone(report["features"]["financial_operating_margin_3y_pct"])

    def test_loss_or_negative_equity_not_wrapped_to_positive_valuation(self):
        rows = fixture()
        accounts = rows[-1]["accounts"]
        accounts["owners_profit_krw"] = -12_000_000_000
        report = compute(rows)
        self.assertLess(report["features"]["valuation_earnings_yield_pct"], 0)
        self.assertIn("negative_owners_profit", report["financial_red_flag_candidates"])
        accounts["equity_krw"] = -10_000_000_000
        accounts["liabilities_krw"] = accounts["assets_krw"] + 10_000_000_000
        accounts["owners_equity_krw"] = -12_000_000_000
        report = compute(rows)
        self.assertIsNone(report["features"]["valuation_pbr"])
        self.assertIsNone(report["features"]["financial_net_debt_equity_pct"])
        self.assertIn("negative_or_zero_equity", report["financial_red_flag_candidates"])

    def test_asof_publication_and_cfs_ofs_scope_mixing_fail_closed(self):
        changes = {
            "same_day": lambda r: r[-1].update(published_on="2026-10-16"),
            "future_publication": lambda r: r[-1].update(published_on="2026-10-17"),
            "before_year_end": lambda r: r[-1].update(published_on="2025-12-31"),
            "mixed_cfs_ofs": lambda r: r[-1].update(scope="OFS"),
            "non_krw": lambda r: r[-1].update(currency="USD"),
            "other_issuer": lambda r: r[-1].update(issuer_id="00000002"),
            "repeated_year": lambda r: r[-1].update(fiscal_year=2024),
            "skipped_year": lambda r: r[1].update(fiscal_year=2019),
            "bad_receipt": lambda r: r[-1].update(rcept_no="fake"),
            "bad_digest": lambda r: r[-1].update(original_sha256="unverified"),
            "unreasonable_number": lambda r: r[-1]["accounts"].update(
                revenue_krw=10**30),
            "bool_number": lambda r: r[-1]["accounts"].update(assets_krw=True),
            "neg_capex": lambda r: r[-1]["accounts"].update(
                capex_cash_outflow_krw=-20),
            "accounting_identity": lambda r: r[-1]["accounts"].update(
                assets_krw=10),
            "debt_exceeds_liabilities": lambda r: r[-1]["accounts"].update(
                interest_bearing_debt_krw=999_000_000_000),
        }
        for label, mutator in changes.items():
            with self.subTest(label=label):
                rows = fixture()
                mutator(rows)
                with self.assertRaises(FinancialFeatureError):
                    compute(rows)

    def test_market_cap_wrong_date_and_nonpositive_blocked(self):
        for value in (0, -1, True, 1.2):
            with self.subTest(value=value), self.assertRaises(FinancialFeatureError):
                compute(cap=value)
        with self.assertRaisesRegex(FinancialFeatureError, "market_cap"):
            calculate_financial_raw_features(
                fixture(), asof="2026-10-16", issuer_id="00000001",
                market_cap={"asof": "2026-10-15", "amount_krw": 180_000_000_000})

    def test_demo_workbench_now_uses_actual_financial_arithmetic(self):
        input_data = demo_bundle()
        example = input_data["stocks"][0]
        # PR industry-cycle adapter now derives these from cohort raw inputs.
        self.assertEqual(set(example["manual_raw_features"]), set())
        self.assertEqual(len(input_data["industry_cohorts"]), 2)
        self.assertEqual(len(example["annual_statements"]), 4)
        financial = calculate_financial_raw_features(
            example["annual_statements"], asof=example["asof"],
            issuer_id=example["issuer_id"], market_cap=example["market_cap"])
        self.assertTrue(all(v is not None for v in financial["features"].values()))
        report = run_workbench(input_data)
        self.assertEqual(report["fully_covered_six_pillar_research_only"], 64)
        self.assertFalse(report["v3_release_ready"])
        self.assertFalse(report["confirmed_buy"])

    def test_conflicting_manual_financial_override_rejected(self):
        inputs = demo_bundle()
        stock = inputs["stocks"][0]
        stock["manual_raw_features"]["valuation_pbr"] = 0.01
        with self.assertRaisesRegex(WorkbenchError, "manual_financial_metric_conflicts"):
            run_workbench({"asof": inputs["asof"], "stocks": [stock]})

    def test_financial_report_omits_underlying_krw_accounts_and_source_hash(self):
        result = compute()
        serialized = json.dumps(result)
        self.assertNotIn("133100000000", serialized)
        self.assertNotIn("b"*64, serialized)
        self.assertNotIn("rcept_no", serialized)


if __name__ == "__main__":
    unittest.main()
