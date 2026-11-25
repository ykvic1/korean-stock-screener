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
