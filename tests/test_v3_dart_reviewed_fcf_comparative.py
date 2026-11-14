import copy
from datetime import date
import unittest
from unittest.mock import patch

from v3_dart_reviewed_fcf_comparative import SCHEMA, comparative_fcf_parts, verified_ratio


class ReviewedComparativeTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = date(2026, 11, 13)
        receipts = {'2024': '20250321001271', '2025': '20260323001100'}
        url = 'https://dart.fss.or.kr/report/viewer.do?rcpNo=' + receipts['2025']
        self.entry = {
            'scope': 'CFS',
            'anchor_receipts': receipts,
            'cashflow_statement': {'url': url + '&eleId=1', 'sha256': '1' * 64},
            'balance_sheet': {'url': url + '&eleId=2', 'sha256': '2' * 64},
            'years': {year: {
                'operating_cashflow_krw': cfo, 'ppe_cash_payments_krw': 10,
                'intangible_cash_payments_krw': 5, 'assets_krw': 500,
                'investing_cashflow_krw': -10, 'other_investing_cashflow_krw': 5,
            } for year, cfo in (('2023', 80), ('2024', 55), ('2025', 30))},
        }
        self.history = {'years': {year: {'CFS': {
            'receipt_id': receipt, 'values': {'assets': 500},
        }} for year, receipt in receipts.items()}}
        self.cash = {'scope': 'CFS', 'years': {year: {
            'scope': 'CFS', 'annual_receipt_id': receipt, 'major_annual_receipt_matched': True,
            'operating_cashflow_krw': self.entry['years'][year]['operating_cashflow_krw'],
            'ppe_capex_krw': 10, 'intangible_capex_krw': 5,
            'free_cashflow_krw': self.entry['years'][year]['operating_cashflow_krw'] - 15,
        } for year, receipt in receipts.items()}}

    def test_original_comparison_and_two_same_scope_anchors(self):
        self.assertEqual(verified_ratio(self.entry, self.history, self.cash, self.cutoff), 8)
        for field, invalid in (('scope', 'OFS'), ('ppe_capex_krw', 20),
                               ('annual_receipt_id', '20260324001100'),
                               ('major_annual_receipt_matched', False)):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.cash)
                changed['years']['2025'][field] = invalid
                self.assertIsNone(verified_ratio(self.entry, self.history, changed, self.cutoff))
        self.assertIsNone(verified_ratio(self.entry, self.history, self.cash, date(2026, 3, 22)))
        self.entry['years']['2023']['investing_cashflow_krw'] = -20
        self.assertIsNone(verified_ratio(self.entry, self.history, self.cash, self.cutoff))

    def test_direct_evidence_has_priority_and_stale_inputs_are_rejected(self):
        day = self.cutoff.isoformat()
        code = '463480'
        other_codes = [f'{i:06d}' for i in range(30)]
        catalog = {'date': day, 'rows': [
            {'code': c, 'market': 'KOSDAQ'} for c in [code, *other_codes]
        ]}
        history = {'asof': day, 'records': {code: self.history}}
        cash = {'asof': day, 'records': {code: self.cash}}
        reviewed = {'schema': SCHEMA, 'records': {code: self.entry}}
        existing = {c: {'financial_fcf_assets_3y_pct': 6} for c in other_codes}
        with patch('v3_dart_reviewed_fcf_comparative.real_features', return_value=existing):
            parts = comparative_fcf_parts(catalog, history, cash, reviewed)
            self.assertEqual(parts[code], {'points': 2.951613, 'available': 3, 'asof': day})
            existing[code] = {'financial_fcf_assets_3y_pct': 8}
            self.assertEqual(comparative_fcf_parts(catalog, history, cash, reviewed), {})
            existing.pop(code)
            cash['asof'] = '2026-11-12'
            self.assertEqual(comparative_fcf_parts(catalog, history, cash, reviewed), {})
