import unittest

from v3_dart_bulk_history import _stable_digest
from v3_dart_original_revenue_recovery import (
    REVIEWED_SCHEMA, SCHEMA, overlay, review_statement, targets, verified,
)
from v3_dart_revenue_2022 import Revenue2022Error
from v3_revenue_growth_evidence import window


class OriginalRevenueRecoveryTests(unittest.TestCase):
    def test_consecutive_filings_restore_missing_history_from_two_overlaps(self):
        day, code = '2026-11-13', '448900'
        earlier_receipt, receipt = '20250321008345', '20260320001061'
        fiscal = [2022, 2023, 2024, 2025]
        figures = {str(y): {'revenue': y, 'operating_income': -1} for y in fiscal}
        catalog = {'date': day, 'listed_total': 1, 'rows': [{'code': code}]}
        years = {str(y): {} for y in fiscal}
        years['2025']['CFS'] = {'receipt_id': receipt, 'values': figures['2025'].copy()}
        history = {'asof': day, 'listed_total': 1, 'fiscal_years': fiscal,
                   'records': {code: {'years': years}}}
        bridge = {
            'scope': 'CFS', 'receipt_id': receipt, 'source_sha256': 'a' * 64,
            'source_url': f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&eleId=1',
            'earlier_receipt_id': earlier_receipt, 'earlier_source_sha256': 'b' * 64,
            'earlier_source_url': (
                f'https://kind.krx.co.kr/external/2025/03/21/002182/{earlier_receipt}/11011.htm'),
            'latest_figures': {y: figures[y].copy() for y in ('2023', '2024', '2025')},
            'earlier_figures': {y: figures[y].copy() for y in ('2022', '2023', '2024')},
        }
        reviewed = {'schema': REVIEWED_SCHEMA, 'records': {},
                    'annual_history_bridges': {code: bridge}}
        proposed, codes = overlay(catalog, history, None, reviewed=reviewed)
        self.assertEqual(codes, {code})
        recovered = proposed['records'][code]
        self.assertEqual(window(recovered, fiscal, day)['scope'], 'CFS')
        self.assertEqual(recovered['years']['2022']['CFS']['comparative_filing_year'], '2024')
        self.assertEqual(recovered['years']['2024']['CFS']['receipt_id'], earlier_receipt)
        self.assertEqual(years['2024'], {})
        bridge['latest_figures']['2023']['operating_income'] -= 1
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())
        bridge['latest_figures']['2023']['operating_income'] += 1
        years['2024']['CFS'] = {'receipt_id': earlier_receipt, 'values': {'revenue': 1}}
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())

    def test_earlier_comparatives_use_the_anchor_filing_and_preserve_originals(self):
        day, code, receipt = '2026-11-13', '000001', '20250321000001'
        catalog = {'date': day, 'listed_total': 1, 'rows': [{'code': code}]}
        figures = {str(y): {'revenue': y, 'operating_income': 1} for y in (2022, 2023, 2024)}
        fiscal = [2022, 2023, 2024, 2025]
        years = {'2022': {}, '2023': {},
                 '2024': {'CFS': {'receipt_id': receipt, 'values': figures['2024'].copy()}},
                 '2025': {'CFS': {'receipt_id': '20260323001654', 'values': {'revenue': 2025}}}}
        history = {'asof': day, 'listed_total': 1, 'fiscal_years': fiscal,
                   'records': {code: {'years': years}}}
        proof = {'scope': 'CFS', 'filing_year': '2024', 'receipt_id': receipt,
                 'source_url': f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&eleId=1',
                 'source_sha256': 'a' * 64, 'figures': figures}
        reviewed = {'schema': REVIEWED_SCHEMA, 'records': {}, 'annual_comparatives': {code: proof}}
        proposed, codes = overlay(catalog, history, None, reviewed=reviewed)
        self.assertEqual(codes, {code})
        record = proposed['records'][code]
        self.assertEqual(record['years']['2022']['CFS']['comparative_filing_year'], '2024')
        self.assertEqual(record['years']['2022']['CFS']['receipt_id'], receipt)
        self.assertIsNotNone(window(record, fiscal, day))
        self.assertEqual(years['2022'], {})
        years['2022']['CFS'] = {'receipt_id': '20230324000001', 'values': {'revenue': 11}}
        proposed, _ = overlay(catalog, history, None, reviewed=reviewed)
        self.assertEqual(proposed['records'][code]['years']['2022'], years['2022'])
        years['2024']['CFS']['values']['operating_income'] = 2
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())

    def test_reviewed_statement_requires_original_receipt_and_income_anchor(self):
        day = '2026-11-13'
        code = '000001'
        receipt = '20260323001654'
        catalog = {'date': day, 'listed_total': 1, 'rows': [{'code': code}]}
        annual = {'receipt_id': receipt, 'values': {'revenue': None, 'operating_income': -10}}
        history = {'asof': day, 'listed_total': 1, 'fiscal_years': [2025],
                   'records': {code: {'years': {'2025': {'CFS': annual}}}}}
        proof = {'receipt_id': receipt, 'operating_income_krw': -10,
                 'revenue_krw': 100, 'account_name': '영업수익', 'source_sha256': 'a' * 64,
                 'source_url': f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&dcmNo=1'}
        reviewed = {'schema': REVIEWED_SCHEMA, 'records': {f'{code}:2025:CFS': proof}}
        proposed, codes = overlay(catalog, history, None, reviewed=reviewed)
        self.assertEqual(codes, {code})
        self.assertEqual(proposed['records'][code]['years']['2025']['CFS']['values']['revenue'], 100)
        self.assertIsNone(annual['values']['revenue'])
        annual['values']['operating_income'] = -11
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())
        annual['values']['operating_income'] = -10
        annual['receipt_id'] = '20260324001654'
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())

    def test_annual_scope_requires_same_latest_receipt_and_both_comparatives(self):
        day = '2026-11-13'
        code = '000001'
        receipt = '20260323001654'
        catalog = {'date': day, 'listed_total': 1, 'rows': [{'code': code}]}
        figures = {str(y): {'revenue': y, 'operating_income': 1} for y in (2023, 2024, 2025)}
        anchors = {'2023': '20240321000001', '2024': '20250321000001'}
        years = {y: {'OFS': {'receipt_id': r, 'values': figures[y].copy()}}
                 for y, r in anchors.items()}
        years['2025'] = {'CFS': {'receipt_id': receipt, 'values': figures['2025'].copy()}}
        history = {'asof': day, 'listed_total': 1, 'fiscal_years': [2023, 2024, 2025],
                   'records': {code: {'years': years}}}
        proof = {'scope': 'OFS', 'receipt_id': receipt,
                 'source_url': f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&eleId=1',
                 'source_sha256': 'a' * 64, 'figures': figures, 'anchor_receipts': anchors}
        reviewed = {'schema': REVIEWED_SCHEMA, 'records': {}, 'annual_scopes': {code: proof}}
        proposed, codes = overlay(catalog, history, None, reviewed=reviewed)
        self.assertEqual(codes, {code})
        self.assertEqual(proposed['records'][code]['years']['2025']['OFS']['values'], figures['2025'])
        self.assertNotIn('OFS', years['2025'])
        years['2023']['OFS']['values']['operating_income'] = -1
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())
        years['2023']['OFS']['values']['operating_income'] = 1
        years['2025']['CFS']['receipt_id'] = '20260324001654'
        self.assertEqual(overlay(catalog, history, None, reviewed=reviewed)[1], set())

    def test_consecutive_reports_can_anchor_a_missing_latest_scope(self):
        day, code = '2026-11-13', '000001'
        r24, r25 = '20250321000001', '20260323001654'
        catalog = {'date': day, 'listed_total': 1, 'rows': [{'code': code}]}
        fiscal = [2022, 2023, 2024, 2025]
        figures = {str(y): {'revenue': y, 'operating_income': -1} for y in fiscal}
        years = {'2022': {}, '2023': {},
                 '2024': {'OFS': {'receipt_id': r24, 'values': figures['2024']}},
                 '2025': {'CFS': {'receipt_id': r25, 'values': figures['2025']}}}
        history = {'asof': day, 'listed_total': 1, 'fiscal_years': fiscal,
                   'records': {code: {'years': years}}}
        prefix = 'https://dart.fss.or.kr/report/viewer.do?rcpNo='
        comparative = {'scope': 'OFS', 'filing_year': '2024', 'receipt_id': r24,
                       'source_url': prefix + r24 + '&eleId=1', 'source_sha256': 'a' * 64,
                       'figures': {y: figures[y] for y in ('2022', '2023', '2024')}}
        latest = {'scope': 'OFS', 'receipt_id': r25, 'source_url': prefix + r25 + '&eleId=2',
                  'source_sha256': 'b' * 64, 'anchor_receipts': dict.fromkeys(('2023', '2024'), r24),
                  'figures': {y: figures[y].copy() for y in ('2023', '2024', '2025')}}
        reviewed = {'schema': REVIEWED_SCHEMA, 'records': {},
                    'annual_comparatives': {code: comparative}, 'annual_scopes': {code: latest}}
        proposed, _ = overlay(catalog, history, None, reviewed=reviewed)
        record = proposed['records'][code]
        self.assertEqual(record['years']['2025']['OFS']['receipt_id'], r25)
        self.assertEqual(window(record, fiscal, day)['scope'], 'OFS')
        self.assertNotIn('OFS', years['2025'])
        latest['figures']['2023']['revenue'] += 1
        rejected, _ = overlay(catalog, history, None, reviewed=reviewed)
        self.assertNotIn('OFS', rejected['records'][code]['years']['2025'])

    def test_receipt_mismatch_is_reused_only_for_the_same_filing(self):
        day = '2026-11-13'
        corp = '00000001'
        identity = '000001:2025:CFS'
        base = {'code': '000001', 'scope': 'CFS', 'fiscal_year': 2025,
                'receipt_id': '20260323001654'}
        different_receipt = '20260323001655'
        payload = {'status': '000', 'list': [{
            'corp_code': corp, 'bsns_year': '2025', 'reprt_code': '11011', 'fs_div': 'CFS',
            'sj_div': 'IS', 'account_id': 'ifrs-full_Revenue', 'currency': 'KRW',
            'rcept_no': different_receipt, 'thstrm_amount': '1000000',
        }]}
        report = {'rows': {'000001': {'available': 88.5,
                                     'parts': {'financial': {}, 'valuation': {}}}}}
        row = review_statement(payload, base, corp, day)
        self.assertEqual(row['source_error'], 'revenue_2022_receipt_or_unit_mismatch')
        self.assertEqual(row['status'], 'not_observed')
        self.assertIsNone(row['revenue_krw'])
        self.assertIsNone(row['source_receipt_id'])
        prior = {'schema': SCHEMA, 'asof': day, 'raw_provider_rows_published': False,
                 'records': {identity: row}}
        expected = {identity: base}
        retained = verified(prior, expected, '2026-11-16')
        self.assertEqual(retained, prior['records'])
        self.assertEqual(targets(report, expected, retained), [])

        corrected = {**base, 'receipt_id': different_receipt}
        expected = {identity: corrected}
        retained = verified(prior, expected, day)
        self.assertEqual(retained, {})
        self.assertEqual(targets(report, expected, retained), [identity])
        observed = review_statement(payload, corrected, corp, day)
        self.assertEqual(observed['revenue_krw'], 1000000)
        self.assertNotIn('source_error', observed)

        invalid = {**observed, 'source_error': row['source_error']}
        invalid.pop('digest')
        invalid['digest'] = _stable_digest(invalid)
        with self.assertRaisesRegex(ValueError, 'invalid_original_revenue_record'):
            verified({**prior, 'records': {identity: invalid}}, expected, day)

    def test_provider_outage_does_not_create_a_review_result(self):
        with self.assertRaisesRegex(Revenue2022Error, 'provider_response_unusable'):
            review_statement({'status': '020'}, {'fiscal_year': 2025},
                             '00000001', '2026-11-13')
