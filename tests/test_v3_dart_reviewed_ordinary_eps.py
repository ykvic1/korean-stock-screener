import copy
import unittest

from v3_dart_reported_eps import metrics
from v3_dart_reviewed_ordinary_eps import SCHEMA, ordinary_basic_label, reviewed_eps_parts


class ReviewedOrdinaryEPSTests(unittest.TestCase):
    def setUp(self):
        self.code = '003550'
        self.receipt = '20260318001025'
        self.day = '2026-11-13'
        prefix = f'https://dart.fss.or.kr/report/viewer.do?rcpNo={self.receipt}&'
        self.entry = {'scope': 'CFS', 'receipt_id': self.receipt, 'operating_cashflow_krw': 12,
                      'basic_eps': [100, -200, 300],
                      'statement_label': '보통주 기본/희석주당순이익 (단위 : 원)',
                      'income_statement': {'url': prefix+'eleId=1', 'sha256': 'a'*64},
                      'cashflow_statement': {'url': prefix+'eleId=2', 'sha256': 'b'*64}}
        series = [None, -200, 300]
        self.saved = {'status': 'reported_basic_eps', 'fs_div': 'CFS',
                      'annual_receipt_id': self.receipt, 'observed_on': self.day,
                      'reported_basic_eps_2023_2025': series, 'metrics': metrics(series)}
        self.annual = {'receipt_id': self.receipt}
        self.cash = {'annual_receipt_id': self.receipt, 'scope': 'CFS',
                     'major_annual_receipt_matched': True, 'operating_cashflow_krw': 12}

    def evaluate(self, cutoff=None):
        return reviewed_eps_parts(
            {self.code: self.saved},
            {'records': {self.code: {'years': {'2025': {'CFS': self.annual}}}}},
            {'records': {self.code: {'years': {'2025': self.cash}}}},
            {'schema': SCHEMA, 'records': {self.code: self.entry}},
            cutoff or self.day)

    def test_admits_full_series_without_overwriting_original(self):
        old = copy.deepcopy(self.saved)
        self.assertEqual(self.evaluate()[self.code]['reported_basic_eps_2023_2025'], [100, -200, 300])
        self.assertEqual(self.saved, old)
        self.saved['reported_basic_eps_2023_2025'] = [10, -200, 300]
        self.saved['metrics'] = metrics(self.saved['reported_basic_eps_2023_2025'])
        self.assertEqual(self.evaluate(), {})

    def test_excludes_partial_classes_and_units(self):
        for label in ('계속영업 기본주당이익 (단위 : 원)', '우선주 기본주당손익 (단위 : 원)',
                      '희석주당이익 (단위 : 원)', '기본주당이익 (단위 : 천원)', '기본주당이익'):
            with self.subTest(label=label):
                self.assertFalse(ordinary_basic_label(label))

    def test_explicit_owner_and_combined_basic_ordinary_rows(self):
        self.assertTrue(ordinary_basic_label('지배기업 소유주 기본주당이익 (단위:원)'))
        self.assertTrue(ordinary_basic_label('기본 및 희석 보통주당손익 (단위:원)'))
        self.assertFalse(ordinary_basic_label('기본 및 희석 우선주당손익 (단위:원)'))
        self.assertFalse(ordinary_basic_label('지배기업 소유주 기본주당계속영업이익 (단위:원)'))

    def test_rejects_different_filing_scope_cfo_or_known_eps(self):
        for object_, key, invalid in (
                (self.entry, 'scope', 'OFS'), (self.entry, 'basic_eps', [100, -201, 300]),
                (self.cash, 'operating_cashflow_krw', 13), (self.annual, 'receipt_id', '20260318001026'),
                (self.entry, 'cashflow_statement', self.entry['income_statement'])):
            old = object_[key]
            with self.subTest(key=key):
                object_[key] = invalid
                self.assertEqual(self.evaluate(), {})
                object_[key] = old
        self.assertEqual(self.evaluate('2026-03-17'), {})
