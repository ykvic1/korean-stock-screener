import unittest
from unittest.mock import patch

from v3_dart_bulk_history import SCHEMA as BULK_SCHEMA
from v3_dart_revenue_2022 import (
    NONSTANDARD, Revenue2022Error, collect, expected_sources, parse, plan,
    snapshot, verified_prior,
)


def fixtures():
    catalog = {'date': '2026-11-05', 'listed_total': 1,
               'rows': [{'code': '005490', 'name': '시험기업', 'market': 'KOSPI'}]}
    years = {}
    for y in range(2022, 2026):
        receipt = f'{y + 1}0311000001'
        years[str(y)] = {'CFS': {'receipt_id': receipt,
            'values': {'revenue': None if y == 2022 else 1000 + (y - 2022) * 100,
                       'operating_income': 100}}}
    history = {'schema': BULK_SCHEMA, 'asof': catalog['date'],
               'listed_total': 1, 'fiscal_years': [2022, 2023, 2024, 2025],
               'historical_PIT_source_certified': False,
               'records': {'005490': {'years': years}}}
    base = expected_sources(catalog, history)['005490']
    row = {'corp_code': '00123456', 'bsns_year': '2022', 'reprt_code': '11011',
           'stock_code': '005490', 'fs_div': 'CFS', 'sj_div': 'IS',
           'account_detail': '-', 'account_id': 'ifrs-full_Revenue',
           'account_nm': '매출액', 'currency': 'KRW',
           'rcept_no': base['receipt_id'], 'thstrm_amount': '1,000'}
    return catalog, history, base, row


class Revenue2022Tests(unittest.TestCase):
    def test_missing_2022_only_is_eligible_without_mutating_source(self):
        catalog, history, base, row = fixtures()
        expected, retained, selected = plan(catalog, history, limit=1)
        self.assertEqual(selected, ['005490'])
        self.assertTrue(base['recent_revenue_complete'])
        self.assertIsNone(history['records']['005490']['years']['2022']['CFS']['values']['revenue'])
        self.assertEqual(expected['005490'], base)

    def test_genuine_mixed_code_enterprise_is_valid_2022_revenue_source_target(self):
        catalog,history,base,row=fixtures()
        original='005490'
        mixed='0008Z0'
        catalog['rows'][0]['code']=mixed
        history['records'][mixed]=history['records'].pop(original)
        targets=expected_sources(catalog,history)
        self.assertEqual(list(targets),[mixed])
        self.assertEqual(targets[mixed]['fiscal_year'],2022)
        malformed={'0008z0','00/8Z0','0008Z0X'}
        for code in malformed:
            catalog['rows'][0]['code']=code
            with self.assertRaisesRegex(Revenue2022Error,
                                        'invalid_revenue_2022_market_identity'):
                expected_sources(catalog,history)

    def test_exact_standard_krw_receipt(self):
        _, _, base, row = fixtures()
        result = parse({'status': '000', 'list': [row]}, base, '00123456', '2026-11-05')
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['revenue_krw'], 1000)
        self.assertEqual(result['source_account_id'], 'ifrs-full_Revenue')

    def test_unqualified_or_ambiguous_does_not_fill(self):
        _, _, base, row = fixtures()
        for key, bad in [('currency', 'USD'), ('rcept_no', '20230311000002'),
                         ('corp_code', '00000001')]:
            with self.subTest(key=key), self.assertRaises(Revenue2022Error):
                parse({'status': '000', 'list': [{**row, key: bad}]}, base,
                      '00123456', '2026-11-05')
        with self.assertRaisesRegex(Revenue2022Error, 'conflicting'):
            parse({'status': '000', 'list': [row, {**row, 'thstrm_amount': '1,001'}]},
                  base, '00123456', '2026-11-05')

    def test_exact_nonstandard_is_allowed_but_fuzzy_is_not(self):
        _, _, base, row = fixtures()
        nonstandard = {**row, 'account_id': NONSTANDARD, 'account_nm': '영업수익'}
        result = parse({'status': '000', 'list': [nonstandard]}, base,
                       '00123456', '2026-11-05')
        self.assertEqual(result['source_account_name'], '영업수익')
        fuzzy = {**nonstandard, 'account_nm': '영업수익(추정)'}
        result = parse({'status': '000', 'list': [fuzzy]}, base,
                       '00123456', '2026-11-05')
        self.assertEqual(result['status'], 'not_observed')
        self.assertIsNone(result['revenue_krw'])

    def test_no_source_row_stays_missing_and_is_idempotent(self):
        catalog, history, base, row = fixtures()
        result = parse({'status': '013'}, base, '00123456', '2026-11-05')
        self.assertIsNone(result['revenue_krw'])
        state = snapshot(catalog['date'], {'005490': result}, catalog)
        expected, retained, selected = plan(catalog, history, state, limit=1)
        self.assertEqual(selected, [])
        self.assertEqual(retained['005490'], result)

    def test_previous_conflict_label_retains_live_source_checkpoint(self):
        catalog, history, base, _ = fixtures()
        source = snapshot(catalog['date'], {'005490': {
            **base, 'checked_on': catalog['date'], 'status': 'not_observed',
            'revenue_krw': None, 'source_account_id': None,
            'source_account_name': None, 'source_receipt_id': None,
            'source_error': 'conflicting_annual_accounts',
        }}, catalog)
        _, retained, selected = plan(catalog, history, source, limit=1)
        self.assertEqual(selected, [])
        self.assertEqual(retained['005490']['source_error'], 'conflicting_annual_accounts')

    def test_revised_original_receipt_invalidates_old_observation(self):
        catalog, history, base, row = fixtures()
        result = parse({'status': '000', 'list': [row]}, base, '00123456', '2026-11-05')
        state = snapshot(catalog['date'], {'005490': result}, catalog)
        history['records']['005490']['years']['2022']['CFS']['receipt_id'] = '20230401000001'
        _, retained, selected = plan(catalog, history, state, limit=1)
        self.assertEqual(retained, {})
        self.assertEqual(selected, ['005490'])

    def test_tampered_source_is_rejected(self):
        catalog, history, base, row = fixtures()
        result = parse({'status': '000', 'list': [row]}, base, '00123456', '2026-11-05')
        result['source_account_id'] = 'ifrs-full_Assets'
        state = snapshot(catalog['date'], {'005490': result}, catalog)
        with self.assertRaisesRegex(Revenue2022Error, 'tampered'):
            verified_prior(state, expected_sources(catalog, history), catalog['date'])

    def test_direct_annual_receipt_for_wholly_missing_2022_major_scope(self):
        catalog, history, base, row = fixtures()
        history['records']['005490']['years']['2022'] = {}
        expected = expected_sources(catalog, history)['005490']
        self.assertIsNone(expected['receipt_id'])
        parsed = parse({'status': '000', 'list': [row]}, expected,
                       '00123456', '2026-11-05')
        self.assertEqual(parsed['source_receipt_id'], row['rcept_no'])
        source = snapshot(catalog['date'], {'005490': parsed}, catalog)
        self.assertEqual(len(verified_prior(source, expected_sources(catalog, history),
                                            catalog['date'])), 1)
        mixed = [{**row}, {**row, 'rcept_no': '20230312000001'}]
        with self.assertRaisesRegex(Revenue2022Error, 'conflicting'):
            parse({'status': '000', 'list': mixed}, expected, '00123456', '2026-11-05')

    def test_one_conflicted_issuer_remains_unobserved_and_next_is_collected(self):
        catalog, history, base, row = fixtures()
        second = '005491'
        catalog['rows'].append({'code': second, 'name': '두번째', 'market': 'KOSPI'})
        catalog['listed_total'] = history['listed_total'] = 2
        history['records'][second] = {
            'years': {year: {'CFS': dict(part['CFS'])}
                      for year, part in history['records']['005490']['years'].items()}}

        class Response:
            def __init__(self, payload):
                self.payload = payload
            def raise_for_status(self):
                pass
            def json(self):
                return self.payload

        class Session:
            def __init__(self, first_rows):
                self.first_rows = first_rows
            def get(self, url, params, timeout):
                if params['corp_code'] == '00123456':
                    return Response({'status': '000', 'list': self.first_rows})
                return Response({'status': '000', 'list': [
                    {**row, 'corp_code': '00123457', 'stock_code': second}]})
        cases = [([row, {**row, 'thstrm_amount': '1,001'}],
                  'revenue_2022_conflicting_annual_accounts'),
                 ([{**row, 'currency': 'USD'}],
                  'revenue_2022_receipt_or_unit_mismatch'),
                 ([{**row, 'corp_code': '00123455'}],
                  'revenue_2022_statement_identity_mismatch'),
                 ([{**row, 'thstrm_amount': '0'}],
                  'revenue_2022_invalid_reported_amount')]
        for first, reason in cases:
            with self.subTest(source_gap=reason):
                checkpoints = []
                with patch('v3_dart_revenue_2022.verified_company_mapping_cache',
                           return_value={'005490': '00123456', second: '00123457'}):
                    saved, calls = collect(catalog, history, None, key='A' * 40,
                        session=Session(first), cache={}, limit=2,
                        on_progress=checkpoints.append)
                self.assertEqual(calls, 2)
                self.assertEqual(len(checkpoints), 2)
                self.assertEqual(saved['observed_issuer_count'], 1)
                self.assertEqual(saved['records']['005490']['source_error'], reason)
                self.assertIsNone(saved['records']['005490']['revenue_krw'])
                self.assertEqual(saved['records'][second]['revenue_krw'], 1000)
                self.assertEqual(len(verified_prior(saved, expected_sources(catalog, history),
                                                    catalog['date'])), 2)


if __name__ == '__main__':
    unittest.main()
