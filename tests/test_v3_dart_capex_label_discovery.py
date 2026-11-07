"""Offline, bounded, source-identity tests. No provider calls or score writes."""
import unittest
from unittest.mock import patch

import v3_dart_capex_label_discovery as d
import v3_dart_cashflow_enrichment as cf
from v3_v30b_release_gate import R1D_PART_LIMITS
from test_v3_dart_bulk_history import CODES, CORPS
from test_v3_dart_cashflow_enrichment import rows

DAY = '2026-11-06'
CODE = CODES[0]
CORP = CORPS[CODE]


def missing_ppe(year=2023):
    return {
        'year': year, 'scope': 'CFS',
        'annual_receipt_id': f'{year+1}0318000001',
        'major_annual_receipt_matched': True,
        'operating_cashflow_krw': 1200000000,
        'ppe_capex_krw': None,
        'intangible_capex_krw': 100000000,
        'free_cashflow_krw': None,
        'account_name_recheck': {'result': 'checked_unavailable'},
    }


def source(record=None, label='유형자산 취득', **changes):
    record = record or missing_ppe()
    cfo = rows(record['year'], CODE)[0]
    row = {**cfo, 'account_id': cf.NONSTANDARD_ACCOUNT_ID,
           'account_nm': label, 'thstrm_amount': '123456789', **changes}
    return {'status': '000', 'list': [cfo, row]}


class LabelDiscoveryTests(unittest.TestCase):
    def test_only_one_gap_and_actual_fiscal_missing_years_are_selected(self):
        parts = {area: {key: [0, maximum] for key, maximum in entries.items()}
                 for area, entries in R1D_PART_LIMITS.items()}
        parts['financial']['cashflow_fcf_persistence'] = [0, 0]
        report = {'asof': DAY, 'rows': {CODE: {'parts': parts}}}
        catalog = {'date': DAY}
        bulk = {'asof': DAY}
        cash = {'asof': DAY, 'records': {CODE: {'years': {
            '2023': missing_ppe(), '2024': missing_ppe(2024),
        }}}}
        with patch.object(d.xp, 'operational_gap_priority', return_value={CODE}):
            picked, count = d.targets(report, {}, catalog, bulk, cash, 1)
            self.assertEqual((len(picked), count), (1, 2))
            self.assertEqual(picked[0][1], '2023')
            with self.assertRaises(d.LabelDiscoveryError):
                d.targets(report, {}, catalog, bulk, cash, 11)
            parts['valuation'][next(iter(parts['valuation']))] = [0, 0]
            self.assertEqual(d.targets(report, {}, catalog, bulk, cash)[0], [])
            report['asof'] = '2026-11-05'
            with self.assertRaises(d.LabelDiscoveryError):
                d.targets(report, {}, catalog, bulk, cash)

    def test_nonstandard_research_label_is_not_an_amount_or_score(self):
        result, reason = d.examine(source(), code=CODE, corp=CORP,
                                   record=missing_ppe(), day=DAY)
        self.assertEqual(result, [('유형자산취득', 'nonstandard')])
        self.assertEqual(reason, 'reviewed')
        self.assertNotIn('123456789', str(result))

    def test_mismatched_candidate_row_is_not_misclassified_as_same_filing(self):
        record = missing_ppe()
        for change in ({'corp_code': '99999999'}, {'stock_code': '999999'},
                       {'bsns_year': '2024'}, {'fs_div': 'OFS'},
                       {'reprt_code': '11012'}):
            with self.assertRaises(cf.CashflowError):
                d.examine(source(**change), code=CODE,
                          corp=CORP, record=record, day=DAY)
        for change in ({'rcept_no': '20240321000001'},
                       {'currency': 'USD'}, {'sj_div': 'BS'},
                       {'account_detail': '사업부별'}):
            result, reason = d.examine(source(**change), code=CODE,
                                       corp=CORP, record=record, day=DAY)
            self.assertEqual((result, reason), ([], 'reviewed'), change)

    def test_wrong_cfo_anchor_and_unusable_payload_are_quarantined(self):
        body = source()
        body['list'][0]['thstrm_amount'] = '1200000001'
        self.assertEqual(d.examine(body, code=CODE, corp=CORP,
                                   record=missing_ppe(), day=DAY),
                         ([], 'cfo_anchor_not_matched'))
        with self.assertRaises(d.LabelDiscoveryError):
            d.examine({'status': '020'}, code=CODE, corp=CORP,
                      record=missing_ppe(), day=DAY)
        self.assertEqual(d.examine({'status': '013'}, code=CODE, corp=CORP,
                                   record=missing_ppe(), day=DAY),
                         ([], 'no_statement'))

    def test_mixed_assets_net_and_untrusted_strings_are_excluded(self):
        for label in ('유무형자산의취득', '유형자산의 취득 및 처분',
                      '유형자산의 순증가', '유형자산 취득/매각',
                      '유형자산의 취득\u200b'):
            self.assertIsNone(d._safe_label(label), label)
        self.assertEqual(d._safe_label(' 유형자산의  취득 '), '유형자산의취득')


if __name__ == '__main__':
    unittest.main()
