import unittest

from v3_historical_pilot_official_prices import compare_market


class HistoricalOfficialPricesTest(unittest.TestCase):
    def test_ohlc_differences_and_membership_reported_without_raw_data(self):
        public = dict.fromkeys(('a', 'b', 'c', 'd', 'e', 'f'), ('KOSDAQ', 'Name', 200, 10, 13, 8, 12, 20))
        official = {
            'a': ('KOSDAQ', 'Na me', 'traded', 10, 12, 9, 12, 200),
            'b': ('KOSPI', 'Name', 'traded', 10, 13, 8, 12, 200),
            'c': ('KOSDAQ', 'Name', 'no_reported_volume', None, None, None, None, 200),
            'd': ('KOSDAQ', 'Name', 'traded', 10, 13, 8, 12, 250),
            'e': ('KOSDAQ', 'Name', 'traded', 10, 13, 8, 12, None),
            'g': ('KOSDAQ', 'Name', 'traded', 10, 13, 8, 12, 200),
        }
        result = compare_market(public, official, public.keys() | official.keys())
        self.assertEqual(result['scope'], 7)
        self.assertEqual(result['absent_from_official'], 1)
        self.assertEqual(result['absent_from_public'], 1)
        self.assertEqual(result['identity_mismatch'], 1)
        self.assertEqual(result['listed_share_mismatch'], 1)
        self.assertEqual(result['listed_shares_unavailable'], 1)
        self.assertEqual(result['positive_trade_on_public_only'], 1)
        self.assertEqual(result['positive_trade_on_both'], 3)
        self.assertEqual(result['exact_ohlc'], 2)
        self.assertEqual(result['different_price_by_field'], dict(open=0, high=1, low=1, close=0))
        self.assertNotIn('Name', str(result))
