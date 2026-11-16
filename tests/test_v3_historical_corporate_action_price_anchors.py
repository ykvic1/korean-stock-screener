import unittest

from v3_historical_corporate_action_price_anchors import stage_outcome


class CorporateActionAnchorTest(unittest.TestCase):
    def test_requires_expected_share_count_and_all_four_prices(self):
        public = {'000001': ('KOSPI', 'Name', 20, 10, 12, 8, 11, 100)}
        exact = ('KOSPI', 'Na me', 'traded', 10, 12, 8, 11, 20)
        self.assertEqual(stage_outcome(public, {'000001': exact}, '000001', 20), 'matched')
        self.assertEqual(stage_outcome(public, {'000001': exact}, '000001', 40), 'ledger_share_count_mismatch')
        changed = (*exact[:5], 7, *exact[6:])
        self.assertEqual(stage_outcome(public, {'000001': changed}, '000001', 20), 'ohlc_mismatch')
        self.assertEqual(stage_outcome(public, {}, '000001', 20), 'absent_from_official')
