"""Regression coverage for dated candidate comparison and no raw export."""
from datetime import datetime
import json
import unittest

import pandas as pd

from v3_historical_close_source_probe import KST, compare, yahoo_closes


class HistoricalCloseSourceProbeTests(unittest.TestCase):
    def test_close_parser_validates_identity_date_and_duplicate_sessions(self):
        ticker = '001500.KS'
        days = ['2026-11-11', '2026-11-12']
        timestamps = [int(datetime.fromisoformat(d).replace(tzinfo=KST).timestamp())
                      for d in days]
        quote = {'meta': {'symbol': ticker, 'currency': 'KRW',
                          'exchangeTimezoneName': 'Asia/Seoul', 'instrumentType': 'EQUITY'},
                 'timestamp': timestamps,
                 'indicators': {'quote': [{'close': [150, 175]}]}}
        payload = {'chart': {'error': None, 'result': [quote]}}
        self.assertEqual(yahoo_closes(payload, ticker, days), dict(zip(days, [150, 175])))
        self.assertEqual(yahoo_closes(payload, '001500.KQ', days), {})
        self.assertEqual(yahoo_closes(payload, ticker, days[:1]), {})
        quote['timestamp'] = [timestamps[1], timestamps[1]]
        self.assertEqual(yahoo_closes(payload, ticker, days), {})

    def test_comparison_uses_common_reference_and_retains_source_gaps(self):
        days = ['2026-11-11', '2026-11-12']
        a, b = ('KOSPI', '000001'), ('KOSDAQ', '000002')
        reference = {days[0]: {a: 1000}, days[1]: {a: 2000, b: 3000}}

        def listing(day):
            return pd.DataFrame([
                {'Market': a[0], 'Code': a[1], 'Close': 1020 if day == days[0] else 2000},
                {'Market': b[0], 'Code': b[1], 'Close': 3000},
            ]), None

        result = compare(days, [a, b], client=None, loader=listing,
                         official=lambda _, day, keys: reference[day],
                         fetch=lambda key, dates: {days[0]: 1000, days[1]: 2000}
                         if key == a else {})
        self.assertEqual(result['sessions'][days[0]]['public_krx_cache'],
                         {'exact_close': 0, 'different_close': 1,
                          'source_unavailable': 0, 'official_unavailable': 1})
        self.assertEqual(result['sessions'][days[1]]['yahoo_daily']['source_unavailable'], 1)
        self.assertEqual(result['exact_match_all_sampled_sessions'],
                         {'public_krx_cache': 0, 'yahoo_daily': 1})
        for code in (a[1], b[1]):
            self.assertNotIn(code, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
