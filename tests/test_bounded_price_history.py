"""Bound the existing NAVER source without changing selected OHLCV rows."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
import requests
import screener


class BoundedHistoryTests(unittest.TestCase):
    def test_requested_count_covers_old_replays_and_matches_full_source_slice(self):
        today = datetime.now(screener.ZoneInfo('Asia/Seoul')).date()
        days = pd.bdate_range(end=today, periods=6000)
        full = pd.DataFrame({'Open':100, 'High':102, 'Low':99,
                             'Close':101, 'Volume':1000}, index=days)
        full.index.name = 'Date'
        for offset in (0, 3, 1000, 7000):
            end = today-timedelta(days=offset)
            start = end-timedelta(days=1100)

            def request(url, *, params, timeout):
                self.assertEqual(url, 'https://fchart.stock.naver.com/sise.nhn')
                self.assertEqual(timeout, (10, 30))
                self.assertEqual(params['symbol'], '005930')
                self.assertEqual(params['count'], min(6000, 1102+offset))
                tail = full.tail(params['count'])
                text = '<protocol>'+''.join(
                    '<item data="'+d.strftime('%Y%m%d')+'|100|102|99|101|1000" />'
                    for d in tail.index)+'</protocol>'
                return SimpleNamespace(text=text, raise_for_status=lambda: None)

            with self.subTest(offset=offset), patch.object(screener.requests, 'get', side_effect=request):
                actual = screener.price_history('005930', start.isoformat(), end.isoformat())
                pd.testing.assert_frame_equal(actual, full.loc[start.isoformat():end.isoformat()],
                                              check_freq=False)

    def test_empty_http_error_and_timeout_are_not_retried_or_zero_filled(self):
        with patch.object(screener.requests, 'get', return_value=SimpleNamespace(
                text='<protocol/>', raise_for_status=lambda: None)) as request:
            with self.assertRaisesRegex(ValueError, '가격 이력 없음'):
                screener.price_history('005930', '2023-01-01', '2026-11-06')
            self.assertEqual(request.call_count, 1)
        for error in (requests.Timeout, requests.HTTPError):
            with self.subTest(error=error), patch.object(screener.requests, 'get', side_effect=error) as request:
                with self.assertRaises(error):
                    screener.price_history('005930', '2023-01-01', '2026-11-06')
                self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
