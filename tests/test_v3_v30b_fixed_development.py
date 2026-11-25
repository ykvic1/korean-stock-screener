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
