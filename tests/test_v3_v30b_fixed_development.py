import unittest
from v3_v30b_fixed_development import component, SHORT_PRICE_HISTORY


class FixedDevelopmentTests(unittest.TestCase):
    def test_missing_data_zero_contribution_not_observed(self):
        part = component(None, 6, "price_source_validation")
        self.assertEqual(part["earned"], 0)
        self.assertEqual(part["observed_weight"], 0)
        self.assertEqual(part["zero_fallback_weight"], 6)
        self.assertTrue(part["source_review_pending"])

    def test_short_history_is_terminal_zero(self):
        part = component(None, 6, SHORT_PRICE_HISTORY)
        self.assertEqual(part["zero_fallback_points"], 0)
        self.assertFalse(part["source_review_pending"])

    def test_partial_observation_not_normalized(self):
        part = component([1, 2, "2026-11-20"], 6, "source_gap")
        self.assertEqual(part["earned"], 1)
        self.assertEqual(part["observed_weight"] + part["zero_fallback_weight"], 6)
        self.assertTrue(part["source_review_pending"])

    def test_observed_zero_distinguished_from_missing(self):
        part = component([0, 6, "2026-11-20"], 6, "source_gap")
        self.assertEqual(part["observed_weight"], 6)
        self.assertEqual(part["zero_fallback_weight"], 0)
        self.assertIsNone(part["reason"])

    def test_impossible_scores_rejected(self):
        for point in ([7, 6, "2026-11-20"], [0, 7, "2026-11-20"], [-1, 2, "2026-11-20"]):
            with self.assertRaises(ValueError):
                component(point, 6, "source_gap")

    def test_cashflow_missing_account_disposition_is_not_unfetched(self):
        from v3_v30b_fixed_development import cashflow_terminal_reviews
        from v3_dart_cashflow_gap_audit import CODE_BY_CLASS
        rows = {"000001":["CFS","V","I","V"],"000002":["OFS","N","N","P"],
                "000003":["CFS","V","V","V"],"000004":["CFS","V","U","B"]}
        from collections import Counter
        counts = Counter(symbol for row in rows.values() for symbol in row[1:])
        gap = {"asof":"2026-11-20","source_eligible_unfetched_company_years":0,
               "listed_issuer_count":4,"source_cashflow_sha256":"digest",
               "categories":{name:counts[symbol] for name,symbol in CODE_BY_CLASS.items()},
               "attempted_company_years":10,"issuers_with_saved_cashflow_years":4}
        evidence = {"asof":"2026-11-20","listed_total":4,"source_cashflow_sha256":"digest",
                    "source_receipt_scope_checked":True,"fiscal_years":[2023,2024,2025],"records":rows}
        report = {"listed_total":4,"rows":{code:{"parts":{"financial":{},"valuation":{}}} for code in rows}}
        result = cashflow_terminal_reviews(report,gap,evidence)
        self.assertEqual(set(result),{"000001:financial:cashflow_fcf_persistence",
                                     "000002:valuation:fcf_yield","000004:valuation:fcf_yield"})
        self.assertEqual(result["000001:financial:cashflow_fcf_persistence"]["missing_weight"],3)
        gap["source_eligible_unfetched_company_years"] = 1
        with self.assertRaises(ValueError):
            cashflow_terminal_reviews(report,gap,evidence)

    def test_price_error_review_requires_attempt_and_preserves_observed_parts(self):
        from v3_v30b_fixed_development import price_error_terminal_reviews
        day = "2026-11-20"
        codes = ["000001", "000002", "000003"]
        members = [{"code": c, "name": c, "market": "KOSPI",
                    "price_history_status": "price_source_or_validation_error",
                    "listing_zero_volume_reference": True} for c in codes]
        trading = {"asof": day, "listed_total": 3, "rows": {
            c: {"name": c, "market": "KOSPI", "state": "zero_volume"} for c in codes}}
        market = {"date": day, "examined_count": 3, "collection_errors": [
            ["000001", "price", "price_zero_latest_volume"],
            ["000002", "price", "transport_failure"]]}
        history = {"asof": day, "listed_total": 3, "pending_sessions": 0,
                   "requested_sessions": 721, "archived_sessions": 721,
                   "requested_completed_weeks": 154, "archived_completed_weeks": 154}
        parts = {"volume": {"long_horizon_growth": [1, 6, day]}, "technical": {},
                 "momentum": {}, "financial": {}, "valuation": {}}
        report = {"listed_total": 3, "rows": {c: {"parts": parts} for c in codes}}
        catalog = {"date": day, "rows": members}
        result = price_error_terminal_reviews(report, catalog, trading, market, history)
        self.assertTrue(result)
        self.assertTrue(all(k.startswith("000001:") for k in result))
        self.assertNotIn("000001:volume:long_horizon_growth", result)
        self.assertFalse(any(":financial:" in k or ":valuation:" in k for k in result))
        history["pending_sessions"] = 1
        with self.assertRaises(ValueError):
            price_error_terminal_reviews(report, catalog, trading, market, history)
