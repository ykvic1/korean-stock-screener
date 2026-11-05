import unittest
from v3_score_revision_inventory import inventory


class InventoryTests(unittest.TestCase):
    def test_does_not_mix_scopes_or_treat_missing_as_zero(self):
        values = {'assets': 100, 'equity': -10, 'liabilities': 110,
                  'revenue': 100, 'operating_income': -5}
        history = {'asof': '2026-11-04', 'fiscal_years': [2023, 2024, 2025], 'records': {
            'complete': {'years': {str(y): {'CFS': {'values': values}} for y in (2023, 2024, 2025)}},
            'mixed': {'years': {'2023': {'CFS': {'values': values}},
                                '2024': {'OFS': {'values': values}},
                                '2025': {'CFS': {'values': values}}}},
            'missing': {'years': {str(y): {'CFS': {'values': {}}} for y in (2023, 2024, 2025)}}}}
        result = inventory(history)
        for measure in result['distributions'].values():
            self.assertEqual(measure['observed'], 1)
            self.assertEqual(measure['unobserved'], 2)
        self.assertEqual(result['distributions']['equity_assets_pct']['nonpositive'], 1)
        self.assertEqual(result['distributions']['annual_loss_fraction']['quantiles_nearest_index']['0.5'], 1)
        self.assertEqual(result['issuer_scores_written'], 0)

    def test_empty_inventory_has_unknown_quantiles(self):
        result = inventory({'asof': '2026-11-04', 'fiscal_years': [2023, 2024, 2025], 'records': {}})
        self.assertIsNone(result['distributions']['equity_assets_pct']['quantiles_nearest_index']['0.5'])
