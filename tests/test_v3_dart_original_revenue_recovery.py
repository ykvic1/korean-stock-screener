import unittest

from v3_dart_bulk_history import _stable_digest
from v3_dart_original_revenue_recovery import SCHEMA, review_statement, targets, verified
from v3_dart_revenue_2022 import Revenue2022Error


class OriginalRevenueRecoveryTests(unittest.TestCase):
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
