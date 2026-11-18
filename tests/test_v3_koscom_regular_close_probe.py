"""Regression checks for documented Koscom field scope and exact-date matching."""
import json
import unittest

from v3_koscom_regular_close_probe import compare


class KoscomRegularCloseProbeTests(unittest.TestCase):
    def test_total_fields_and_undated_regular_fields_do_not_match_krx(self):
        day = '2026-11-17'
        regular = dict(mainOpnPrc=100, mainHgPrc=110, mainLwPrc=90, mainTrdPrc=105,
                       mainAccTrdVol=10, mainAccTrdVal=1005, trdDd=20261117)
        cases = (
            ('KOSPI', '005930', {'trdPrc': 105, 'accTrdvol': 10, 'accTrdval': 1005}, 'KOSPI', 105,
             'regular_fields_missing'),
            ('KOSDAQ', '247540', {**regular, 'trdDd': None}, 'KOSDAQ', 105, 'provider_trade_date_missing'),
            ('KOSDAQ', '196170', {**regular, 'trdDd': 20261116}, 'KOSDAQ', 105, 'provider_trade_date_mismatch'),
            ('KOSDAQ', '214150', {**regular, 'mainAccTrdVal': 1101}, 'KOSDAQ', 105, 'invalid_regular_fields'),
            ('KOSPI', '034020', regular, 'KOSPI', 106, 'krx_close_mismatch'),
            ('KOSPI', '035720', regular, 'KOSDAQ', 105, 'krx_close_reference_unavailable'),
            ('KOSPI', '000660', regular, 'KOSPI', 105.0, 'regular_fields_present_close_matched'),
        )
        for market, code, result, reference_market, close, status in cases:
            with self.subTest(status=status):
                sample = {'asof': day, 'samples': [{'market': market, 'code': code,
                          'response': {'result': {'isuSrtCd': code, **result}}}]}
                reference = {'schema': 'v3-current-corroborated-regular-closes-3', 'asof': day,
                             'source': 'PUBLIC_CLOSES_CORROBORATED_WITH_KRX',
                             'rows': {code: {'market': reference_market, 'close': close}}}
                sample_raw, reference_raw = (json.dumps(item).encode() for item in (sample, reference))
                output = compare(sample_raw, reference_raw)
                self.assertEqual(output['states_by_market'][market], {status: 1})
                self.assertNotIn(code, output)
                self.assertFalse(output['volume_scope_certified'])
                self.assertFalse(output['turnover_scope_certified'])
                self.assertFalse(output['historical_obv_verified'])
                self.assertEqual(output['score_points_added'], 0)

    def test_report_rejects_duplicate_issuer_and_wrong_reference_date(self):
        entries = [{'market': market, 'code': '005930'} for market in ('KOSPI', 'KOSDAQ')]
        sample = json.dumps({'asof': '2026-11-17', 'samples': entries}).encode()
        reference = {'schema': 'v3-current-corroborated-regular-closes-3', 'asof': '2026-11-17',
                     'source': 'PUBLIC_CLOSES_CORROBORATED_WITH_KRX', 'rows': {}}
        with self.assertRaisesRegex(ValueError, 'duplicate_issuer'):
            compare(sample, json.dumps(reference).encode())
        reference['asof'] = '2026-11-16'
        with self.assertRaisesRegex(ValueError, 'invalid_or_wrong_day_krx_close_reference'):
            compare(sample, json.dumps(reference).encode())
