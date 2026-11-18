"""Exercise the boundary and identity checks without making provider requests."""
import csv
from datetime import date
import io
import json
import unittest

from v3_krx_trading_snapshot_comparison import FIELDS, compare

DAY = date(2026, 11, 18)


def capture(rows, stamp='2026-11-18T15:45:00+09:00', day=DAY):
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow((day.isoformat(), stamp, *row))
    return buf.getvalue().encode('utf-8-sig')


class TradingSnapshotTests(unittest.TestCase):
    def test_aggregate_only_same_issuer_comparison(self):
        early = capture([('KOSPI', '005930', '1,000', 1000, 2100, 2100),
                         ('KOSPI', '000660', 0, 0, 0, 0),
                         ('KOSDAQ', '100003', 25, 25, 2500, 2500)])
        final = capture([('KOSPI', '005930', 1000, 1000, 2200, 2200),
                         ('KOSDAQ', '100003', 22, 22, 2500, 2500),
                         ('KOSDAQ', '100004', 12, 12, 24, 24)], '2026-11-18T11:00:00Z')
        result = compare(early, final, DAY)
        self.assertEqual(result['status'], 'stages_compared')
        stock, secondary = (result['by_market'][market] for market in ('KOSPI', 'KOSDAQ'))
        self.assertEqual((stock['matched_count'], stock['early_only_count']), (1, 1))
        self.assertEqual((secondary['matched_count'], secondary['final_only_count']), (1, 1))
        self.assertEqual(stock['changes']['volume']['unchanged'], 1)
        self.assertEqual(stock['changes']['turnover']['increased'], 1)
        self.assertEqual(secondary['changes']['volume']['decreased'], 1)
        self.assertFalse(result['regular_only_scope_verified'])
        self.assertFalse(result['openapi_scope_verified'])
        for code in ('005930', '000660', '100003', '100004'):
            self.assertNotIn(json.dumps(code), json.dumps(result))

    def test_market_mismatch_is_not_a_match(self):
        early = capture([('KOSPI', '005930', 1, 1, 10, 10)])
        final = capture([('KOSDAQ', '005930', 1, 1, 10, 10)], '2026-11-18T20:00:00+09:00')
        self.assertEqual(compare(early, final, DAY)['status'], 'no_matched_issuers')

    def test_missing_totals_and_invalid_capture_metadata_are_rejected(self):
        normal = ('KOSPI', '005930', 1, 1, 10, 10)
        final = capture([normal], '2026-11-18T20:00:00+09:00')
        invalid = (
            (capture([normal], '2026-11-18T15:44:59+09:00'), 'outside_capture_window'),
            (capture([normal], '2026-11-18T20:00:00+09:00'), 'outside_capture_window'),
            (capture([normal], '2026-11-18T15:45:00'), 'capture_time_requires_offset'),
            (capture([normal], day=date(2026, 11, 17)), 'trade_day_mismatch'),
            (capture([normal, normal]), 'duplicate_issuer'),
            (capture([(*normal[:2], '-', '-', 10, 10)]), 'missing_or_invalid_total'),
            (capture([(*normal[:2], 1, 2, 10, 10)]), 'buy_sell_totals_do_not_balance'),
            (capture([(*normal[:2], 0, 0, 10, 10)]), 'volume_value_zero_mismatch'),
        )
        for early, reason in invalid:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                compare(early, final, DAY)
        with self.assertRaisesRegex(ValueError, 'outside_capture_window'):
            compare(capture([normal]), capture([normal], '2026-11-18T19:59:59+09:00'), DAY)
        with self.assertRaisesRegex(ValueError, 'standard_trading_hours_not_applicable'):
            compare(capture([normal]), final, date(2026, 11, 19))


if __name__ == '__main__':
    unittest.main()
