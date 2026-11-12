"""Offline checks for strict, receipt-bound parent-equity research."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

import v3_dart_parent_equity_pilot as mod

CORP = '00126380'
CODE = '005930'
RECEIPT = '20260312001140'
TOTAL = 1000000000


def row(account, amount, **kw):
    return {'corp_code': CORP, 'stock_code': CODE, 'bsns_year': '2025',
            'reprt_code': '11011', 'fs_div': 'CFS', 'rcept_no': RECEIPT,
            'sj_div': 'BS', 'account_detail': '-', 'currency': 'KRW',
            'account_id': account, 'thstrm_amount': str(amount), **kw}


def payload(*other):
    return {'status': '000', 'list': [row('ifrs-full_Equity', TOTAL), *other]}


def parse(source):
    return mod.parse(source, corp=CORP, code=CODE, receipt=RECEIPT,
                     year=2025, scope='CFS', expected_equity=TOTAL)


class ParentEquityPilotTests(unittest.TestCase):
    def test_genuine_mixed_code_equity_proof_identity_is_valid_but_malformed_is_not(self):
        self.assertIsNotNone(mod.CODE.fullmatch('0008Z0'))
        self.assertIsNotNone(mod.CODE.fullmatch('A12345'))
        for invalid in ('0008z0', '00/8Z0', '0008Z01', '00593'):
            self.assertIsNone(mod.CODE.fullmatch(invalid))
        body = copy.deepcopy(payload(
            row('ifrs-full_NoncontrollingInterests', 200000000)))
        for fact in body['list']:
            fact['stock_code'] = '0008Z0'
        value, reason = mod.parse(
            body, corp=CORP, code='0008Z0', receipt=RECEIPT,
            year=2025, scope='CFS', expected_equity=TOTAL)
        self.assertEqual((value, reason),
                         (800000000, 'same_receipt_total_minus_explicit_nci'))
        body['list'][1]['stock_code'] = '0008z0'
        with self.assertRaisesRegex(mod.ParentEquityError,
                                    'parent_equity_stock_mismatch'):
            mod.parse(body, corp=CORP, code='0008Z0', receipt=RECEIPT,
                      year=2025, scope='CFS', expected_equity=TOTAL)

    def test_never_substitute_undifferentiated_total_equity(self):
        self.assertEqual(parse(payload()), (None, 'neither_owner_nor_explicit_nci'))

    def test_direct_standard_owner_equity_is_reported_not_calculated(self):
        got = parse(payload(row('ifrs-full_EquityAttributableToOwnersOfParent', 800000000)))
        self.assertEqual(got, (800000000, 'same_receipt_direct_parent_equity'))

    def test_only_explicit_nci_permits_identity_and_explicit_zero(self):
        self.assertEqual(parse(payload(row('ifrs-full_NoncontrollingInterests', 200000000))),
                         (800000000, 'same_receipt_total_minus_explicit_nci'))
        self.assertEqual(parse(payload(row('ifrs-full_NoncontrollingInterests', 0))),
                         (TOTAL, 'same_receipt_total_minus_explicit_nci'))

    def test_owners_plus_nci_must_reconcile(self):
        source = payload(row('ifrs-full_NoncontrollingInterests', 10),
                         row('ifrs-full_EquityAttributableToOwnersOfParent', TOTAL-20))
        with self.assertRaisesRegex(mod.ParentEquityError, 'identity'):
            parse(source)

    def test_reject_unmatched_receipt_scope_currency_and_ambiguous_facts(self):
        for field, value, reason in (('rcept_no','20260312001141','receipt'),
                                     ('fs_div','OFS','scope'),
                                     ('currency','USD','currency')):
            source = payload(row('ifrs-full_NoncontrollingInterests', 3))
            source['list'][1][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(mod.ParentEquityError, reason):
                parse(source)
        source = payload(row('ifrs-full_NoncontrollingInterests', 3),
                         row('ifrs_NoncontrollingInterests', 5))
        with self.assertRaisesRegex(mod.ParentEquityError, 'conflicting'):
            parse(source)

    def test_unrelated_non_balance_sheet_receipt_does_not_poison_verified_fact(self):
        source = payload(row('ifrs-full_NoncontrollingInterests', 3))
        other = row('ifrs-full_Revenue', 8000)
        other['sj_div'] = 'IS'
        other['rcept_no'] = '20260318009999'
        source['list'].append(other)
        self.assertEqual(parse(source), (TOTAL-3, 'same_receipt_total_minus_explicit_nci'))

    def test_wrong_total_equity_is_not_used_as_formula_anchor(self):
        source = payload(row('ifrs-full_NoncontrollingInterests', 7))
        source['list'][0]['thstrm_amount'] = str(TOTAL-5)
        self.assertEqual(parse(source), (None, 'total_equity_anchor_missing_or_conflicting'))

    def test_no_nonstandard_label_dimensions_or_missing_zero_is_inferred(self):
        source = payload(row('-표준계정코드 미사용-', 1000),
                         row('ifrs-full_NoncontrollingInterests', 3))
        source['list'][-1]['account_detail'] = '연결재무제표 [member]'
        self.assertEqual(parse(source), (None, 'neither_owner_nor_explicit_nci'))
        self.assertEqual(parse({'status':'013'}), (None, 'no_annual_statement'))

    def test_non_won_numeric_bad_rows_and_bad_scope_are_rejected(self):
        source = payload(row('ifrs-full_NoncontrollingInterests', 'not_an_amount'))
        with self.assertRaisesRegex(mod.ParentEquityError, 'amount'):
            parse(source)
        with self.assertRaisesRegex(mod.ParentEquityError, 'anchor'):
            mod.parse(payload(), corp=CORP, code=CODE, receipt=RECEIPT,
                      year=2025, scope='OFS', expected_equity=TOTAL)

    def test_research_is_a_read_only_bounded_pilot(self):
        code = Path(mod.__file__).read_text()
        self.assertNotIn('write_json(', code)
        self.assertNotIn('git push', code)
        self.assertLessEqual(mod.MAX_CALLS, 12)

    def test_partial_coverage_issuers_are_included_only_when_requested(self):
        from unittest.mock import patch
        from v3_v30b_release_gate import R1D_PART_LIMITS
        import types
        day='2026-11-06';rows={};book={'asof':day,'records':[]};cash={'asof':day,'records':{}}
        for code,multi in (('000001',False),('000002',True)):
            parts={a:{k:[0,m] for k,m in limits.items()} for a,limits in R1D_PART_LIMITS.items()}
            del parts['valuation']['pbr_equity_quality']
            if multi:del parts['financial']['cashflow_fcf_persistence']
            rows[code]={'parts':parts,'available':94.5 if multi else 97.5}
            annual={'year':2025,'fs_div':'CFS','currency':'KRW','receipt_id':RECEIPT,'values':{'owners_equity':None,'equity':TOTAL}}
            book['records'].append({'code':code,'annual':annual})
            cash['records'][code]={'years':{'2025':{'scope':'CFS','annual_receipt_id':RECEIPT,
                'major_annual_receipt_matched':True,'operating_cashflow_krw':10}}}
        report={'asof':day,'rows':rows};catalog={'date':day,'rows':[{'code':c} for c in rows]}
        with patch.object(mod,'r1d_public_reconciles',return_value={}):
            single,_=mod.eligible_targets(report,{},catalog,book,cash)
            broad,_=mod.eligible_targets(report,{},catalog,book,cash,include_partial=True)
        self.assertEqual([c for c,a in single],['000001'])
        self.assertEqual([c for c,a in broad],['000001','000002'])

    def test_current_public_snapshot_pbr_only_candidates_are_bounded(self):
        folder = Path(__file__).resolve().parent.parent / 'docs'
        files = ('v3-operational-research-scores.json','v3-operational-research-comparison.json',
                 'v22-market-catalog.json','v3-dart-book-reference.json','v3-dart-cashflow-reference.json')
        report,comp,catalog,book,cash = [json.loads((folder/f).read_text()) for f in files]
        if any(source.get('asof') != catalog['date'] for source in (report,book,cash)):
            self.skipTest('published market and DART evidence await same-close reconciliation')
        selected,n = mod.targets(report,comp,catalog,book,cash,limit=7)
        self.assertGreater(n, 0)
        self.assertLessEqual(len(selected), 7)
        self.assertTrue(all(row['fs_div']=='CFS' and row['values']['owners_equity'] is None
                            for _,row in selected))


if __name__ == '__main__':
    unittest.main()
