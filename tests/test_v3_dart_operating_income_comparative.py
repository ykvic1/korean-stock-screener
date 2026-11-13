import copy
import unittest

import v3_dart_operating_income_comparative as op


DAY='2026-11-12'
CODE='0001A0'


def fixtures(count=1):
    rows=[]
    records={}
    for n in range(count):
        code=CODE if count==1 else f'{n:06d}'
        rows.append({'code':code,'name':'anonymous','market':'KOSPI'})
        years={}
        for year in (2022,2023,2024,2025):
            receipt=f'{year+1}0318000001'
            years[str(year)]={'CFS':{
                'receipt_id':receipt,'values':{
                    'revenue':100*(year-2020),
                    'operating_income':None if year==2023 and n==0 else year-2020}}}
        records[code]={'years':years}
    return ({'date':DAY,'listed_total':count,'rows':rows},
            {'asof':DAY,'listed_total':count,'fiscal_years':[2022,2023,2024,2025],
             'historical_PIT_source_certified':False,'records':records})


def provider(base,op23='3',op24='4',op25='5',**changes):
    row={'corp_code':'00001234','stock_code':base['code'],
         'bsns_year':'2025','reprt_code':'11011','fs_div':base['scope'],
         'sj_div':'IS','account_detail':'-','rcept_no':base['source_receipt_id'],
         'currency':'KRW','account_id':'dart_OperatingIncomeLoss',
         'account_nm':'영업이익(손실)','bfefrmtrm_amount':op23,
         'frmtrm_amount':op24,'thstrm_amount':op25}
    return {'status':'000','list':[{**row,**changes}]}


class AnnualComparativeMarginTests(unittest.TestCase):
    def test_2025_receipt_and_2023_missing_income_are_real_observations(self):
        catalog,history=fixtures()
        expected=op.expected_sources(catalog,history)
        self.assertEqual(list(expected),[CODE])
        base=expected[CODE]
        self.assertEqual(base['missing_years'],['2023'])
        row=op.parse(provider(base),base,corp='00001234',day=DAY)
        self.assertEqual(row['status'],'observed')
        self.assertEqual(row['recovered_operating_income_krw'],{'2023':3})
        self.assertEqual(history['records'][CODE]['years']['2023']['CFS']['values']['operating_income'],None)
        saved=op.snapshot(DAY,catalog,{CODE:row})
        self.assertEqual(op.verified_prior(saved,expected,DAY),{CODE:row})
        self.assertEqual(op.plan(catalog,history,saved)[2],[])

    def test_conflicting_comparatives_and_identity_fail_closed(self):
        catalog,history=fixtures()
        base=op.expected_sources(catalog,history)[CODE]
        variants=({'thstrm_amount':'999'},
                  {'frmtrm_amount':'400'},
                  {'rcept_no':'20260319000001'},
                  {'currency':'USD'},
                  {'fs_div':'OFS'},
                  {'stock_code':'0001B0'})
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(op.OperatingComparativeError):
                op.parse(provider(base,**changes),base,corp='00001234',day=DAY)
        with self.assertRaises(op.OperatingComparativeError):
            op.parse({'status':'020'},base,corp='00001234',day=DAY)
        bad=provider(base)
        bad['list'].append({**bad['list'][0],'bfefrmtrm_amount':'30'})
        with self.assertRaises(op.OperatingComparativeError):
            op.parse(bad,base,corp='00001234',day=DAY)

    def test_negative_operating_income_is_observed_but_missing_not_zero(self):
        catalog,history=fixtures()
        base=op.expected_sources(catalog,history)[CODE]
        row=op.parse(provider(base,op23='-3'),base,corp='00001234',day=DAY)
        self.assertEqual(row['recovered_operating_income_krw']['2023'],-3)
        absent=op.parse(provider(base,op23='-'),base,corp='00001234',day=DAY)
        self.assertEqual(absent['status'],'not_observed')
        self.assertEqual(absent['recovered_operating_income_krw'],{})

    def test_receipt_revision_invalidates_prior_and_empty_year_is_not_assumed(self):
        catalog,history=fixtures()
        base=op.expected_sources(catalog,history)[CODE]
        saved=op.snapshot(DAY,catalog,{CODE:op.parse(
            provider(base),base,corp='00001234',day=DAY)})
        edited=copy.deepcopy(history)
        edited['records'][CODE]['years']['2025']['CFS']['receipt_id']='20260319000001'
        self.assertEqual(op.plan(catalog,edited,saved)[2],[CODE])
        extra=copy.deepcopy(saved)
        extra['records'][CODE]['recovered_operating_income_krw']={'2023':0,'2024':0}
        with self.assertRaises(op.OperatingComparativeError):
            op.verified_prior(extra,op.expected_sources(catalog,history),DAY)

    def test_derived_margin_adds_only_actual_three_year_comparative(self):
        catalog,history=fixtures(31)
        code='000000'
        source=op.expected_sources(catalog,history)
        self.assertEqual(set(source),{code})
        row=op.parse(provider(source[code],op23='-3'),
                     source[code],corp='00001234',day=DAY)
        saved=op.snapshot(DAY,catalog,{code:row})
        before,_=op.margin_parts(catalog,history)
        after,summary=op.margin_parts(catalog,history,saved)
        self.assertNotIn(code,before)
        self.assertIn(code,after)
        self.assertEqual(after[code]['available'],3)
        self.assertEqual(summary['verified_2025_comparative_margin_issuers'],1)
        self.assertEqual(len(after),31)
        self.assertFalse(summary['historical_PIT_source_certified'])
        self.assertIsNone(history['records'][code]['years']['2023']['CFS']['values']['operating_income'])

    def test_plan_prefers_90_first_only_when_independent_source_eligible(self):
        catalog,history=fixtures(31)
        expected=op.expected_sources(catalog,history)
        retained,selected=op.plan(catalog,history,priority=['000000'])[1:]
        self.assertEqual(len(retained),0)
        self.assertEqual(selected,['000000'])


if __name__=='__main__':
    unittest.main()
