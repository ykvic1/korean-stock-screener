"""No-network proofs for parent equity and current-cap PBR replay."""
import copy
import json
from pathlib import Path
import unittest

import v3_dart_parent_equity_recovery as source
from v3_dart_bulk_history import research_parts
from v3_current_observed_scorecard import finance_source


ROOT=Path(__file__).resolve().parent.parent/'docs'


def data():
    read=lambda n:json.loads((ROOT/n).read_text())
    catalog,book,cash,major=[read(x) for x in (
        'v22-market-catalog.json','v3-dart-book-reference.json',
        'v3-dart-cashflow-reference.json','v3-dart-bulk-history.json')]
    code='069510'
    annual=next(x['annual'] for x in book['records'] if x['code']==code)
    assert annual['values']['owners_equity'] is None
    return catalog,book,cash,major,code,annual


def payload(code,annual,nci,corp='00100001'):
    common={'corp_code':corp,'stock_code':code,'bsns_year':'2025','reprt_code':'11011',
            'fs_div':'CFS','rcept_no':annual['receipt_id'],'sj_div':'BS',
            'account_detail':'-','currency':'KRW'}
    return {'status':'000','list':[
        {**common,'account_id':'ifrs-full_Equity',
         'thstrm_amount':str(annual['values']['equity'])},
        {**common,'account_id':'ifrs-full_NoncontrollingInterests',
         'thstrm_amount':str(nci)}]}


class ParentEquityRecoveryTests(unittest.TestCase):
    def checked(self):
        catalog,book,cash,major,code,annual=data()
        nci=100000000
        original=source.blank(catalog['date'])
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        out,why=source.merge(original,code,annual,cfo,payload(code,annual,nci),
                             corp='00100001',day=catalog['date'],
                             catalog=catalog,book=book,cash=cash)
        self.assertEqual(why,'verified_reported_parent_equity')
        return out,catalog,book,cash,major,code,annual,nci

    def test_only_explicit_reported_nci_is_provable_and_replays_current_cap(self):
        saved,catalog,book,cash,major,code,annual,nci=self.checked()
        self.assertEqual(source.verify(saved,catalog,book,cash),1)
        self.assertFalse(saved['investment_score_certified'])
        self.assertFalse(saved['raw_provider_rows_published'])
        self.assertEqual(saved['records'][code]['explicit_nci_krw'],nci)
        self.assertNotIn('corp_code',str(saved))
        raw_before=copy.deepcopy(book)
        overlaid=source.overlay(book,catalog,saved,cash)
        self.assertEqual(book,raw_before,'standalone book cannot be rewritten')
        obs=next(r for r in overlaid['observations'] if r['code']==code)
        self.assertGreater(obs['pbr_simple_annual_reference'],0)
        raw=next(r for r in book['observations'] if r['code']==code)
        self.assertIsNone(raw['pbr_simple_annual_reference'])
        new={r['code']:r for r in research_parts(major,catalog,overlaid)['rows']}
        self.assertIsNotNone(new[code]['unverified_components']['pbr']['candidate_points'])

    def test_absent_nci_never_mints_pbr_evidence(self):
        catalog,book,cash,_,code,annual=data()
        body=payload(code,annual,1)
        body['list'].pop()
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        result,why=source.merge(source.blank(catalog['date']),code,annual,cfo,body,
                                corp='00100001',day=catalog['date'],
                                catalog=catalog,book=book,cash=cash)
        self.assertEqual(why,'neither_owner_nor_explicit_nci')
        self.assertEqual(result['records'],{})

    def test_current_finance_replay_accepts_verified_supplement_without_book_mutation(self):
        saved,catalog,book,cash,major,code,*_=self.checked()
        parts=json.loads((ROOT/'v3-dart-bulk-provisional.json').read_text())
        audit=json.loads((ROOT/'v3-real-source-six-area-coverage.json').read_text())
        got=finance_source(catalog,major,parts,book,audit,
                           parent_equity_source=saved,parent_equity_cash=cash)
        self.assertIsNotNone(got)
        self.assertTrue(got[code]['parent_equity_2025_source_replayed'])
        self.assertIsNotNone(got[code]['unverified_components']['pbr']['candidate_points'])

    def test_tampering_receipt_nci_cfo_or_amount_rejected(self):
        saved,catalog,book,cash,*_=self.checked()
        code='069510'
        for field,value in [('receipt_id','20260321000099'),
                            ('explicit_nci_krw',-50000000),
                            ('cfo_anchor_krw',1),
                            ('owners_equity_krw',3),
                            ('provider_sha256','not_a_sha')]:
            forged=copy.deepcopy(saved);forged['records'][code][field]=value
            with self.subTest(field=field),self.assertRaises(source.ParentEquityRecoveryError):
                source.verify(forged,catalog,book,cash)
        future=copy.deepcopy(saved);future['observed_on']='2030-01-01'
        with self.assertRaises(source.ParentEquityRecoveryError):
            source.verify(future,catalog,book,cash)


if __name__=='__main__':unittest.main()
