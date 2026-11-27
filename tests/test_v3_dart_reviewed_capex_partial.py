"""Regression: retain independently observed component across source revision."""
import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch

import v3_dart_reviewed_capex as capex


class ReviewedPartialCapexTest(unittest.TestCase):
    def setUp(self):
        self.code = '123456'
        self.cutoff = date(2026, 11, 25)
        self.prior = '20250318000001'
        self.later = '20260318000001'
        self.item = {'annual_receipt': self.prior, 'operating_cashflow_krw': 100,
                     'ppe_capex_krw': 20, 'intangible_capex_krw': 0,
                     'investing_cashflow_krw': -25, 'other_investing_cashflow_krw': -5}
        self.registry = {self.code: {'scope':'CFS', 'source_receipt':self.later,
            'source_url':f'https://dart.fss.or.kr/report/viewer.do?rcpNo={self.later}&dcmNo=1',
            'source_sha256':'a'*64, 'years':{'2024':self.item,
                '2025':{'annual_receipt':self.later, 'operating_cashflow_krw':110,
                 'ppe_capex_krw':30,'intangible_capex_krw':5,
                 'investing_cashflow_krw':-40,'other_investing_cashflow_krw':-5}}}}
        self.cash = {'asof':self.cutoff.isoformat(), 'records':{self.code:{'scope':'CFS',
            'years':{'2024':{'scope':'CFS','annual_receipt_id':self.prior,
                'major_annual_receipt_matched':True,'operating_cashflow_krw':100,
                'ppe_capex_krw':20,'intangible_capex_krw':None,
                'total_capex_krw':None,'free_cashflow_krw':None},
                '2025':{'scope':'CFS','annual_receipt_id':self.later,
                'major_annual_receipt_matched':True,'operating_cashflow_krw':110,
                'ppe_capex_krw':30,'intangible_capex_krw':5,
                'total_capex_krw':35,'free_cashflow_krw':75}}}}}
        self.patcher = patch.object(capex,'statements',return_value=self.registry)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_exact_match_and_restore(self):
        saved = deepcopy(self.cash)
        output, added = capex.apply(self.cash)
        self.assertEqual(added,[(self.code,'2024')])
        years = output['records'][self.code]['years']
        row = years['2024']
        self.assertEqual(row['free_cashflow_krw'],80)
        self.assertEqual(row['intangible_capex_krw'],0)
        capex.verify(self.code,'2024',years,self.cutoff)
        self.assertEqual(capex.restore(row),saved['records'][self.code]['years']['2024'])
        self.assertEqual(self.cash,saved)
        self.assertEqual(capex.apply(output)[1],[])
        row[capex.PROOF]['existing_parts']['ppe_capex_krw']=21
        with self.assertRaises(ValueError):
            capex.verify(self.code,'2024',years,self.cutoff)

    def test_conflict_or_other_proof_not_promoted(self):
        row = self.cash['records'][self.code]['years']['2024']
        row['ppe_capex_krw']=21
        self.assertEqual(capex.apply(self.cash)[1],[])
        row['ppe_capex_krw']=20
        row['original_xbrl_review']={}
        self.assertEqual(capex.apply(self.cash)[1],[])

    def test_negative_reviews_do_not_block_receipt_matched_comparative(self):
        from v3_dart_xbrl_cashflow_recovery import CONTRACT as XBRL
        from v3_dart_reported_capex_total import CONTRACT as TOTAL
        row = self.cash['records'][self.code]['years']['2024']
        row['original_xbrl_review'] = {'contract': XBRL, 'receipt_id': self.prior,
                                      'reason': 'no_missing_supported_fact'}
        row['reported_total_review'] = {'contract': TOTAL, 'receipt_id': self.prior,
                                        'reason': 'no_reported_total'}
        saved = deepcopy(row)
        output, added = capex.apply(self.cash)
        self.assertEqual(added, [(self.code, '2024')])
        years = output['records'][self.code]['years']
        capex.verify(self.code, '2024', years, self.cutoff)
        self.assertEqual(capex.restore(years['2024']), saved)
        row['reported_total_review']['receipt_id'] = self.later
        with self.assertRaises(ValueError):
            capex.apply(self.cash)

    def test_old_both_missing_contract_retained(self):
        row = self.cash['records'][self.code]['years']['2024']
        row['ppe_capex_krw']=None
        output, added=capex.apply(self.cash)
        self.assertEqual(added,[(self.code,'2024')])
        years=output['records'][self.code]['years']
        self.assertNotIn('existing_parts',years['2024'][capex.PROOF])
        capex.verify(self.code,'2024',years,self.cutoff)
        self.assertEqual(capex.restore(years['2024']),row)


if __name__=='__main__':
    unittest.main()
