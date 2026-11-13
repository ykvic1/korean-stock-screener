import copy
import json
import unittest

import v3_dart_bulk_history as bulk
import v3_dart_revenue_2024_comparative as comp
import v3_revenue_growth_evidence as growth


DAY='2026-11-12'
YEARS=[2022,2023,2024,2025]
CODE='000000'
CORP='00123456'


def inputs(count=31):
    catalog={'date':DAY,'listed_total':count,'rows':[
        {'code':f'{i:06d}','market':'KOSPI','name':'가상기업'}
        for i in range(count)]}
    records={}
    for i in range(count):
        code=f'{i:06d}'
        years={str(year):{'CFS':{
            'receipt_id':f'{year+1}0318000001',
            'values':{'revenue':100*(year-2020),'operating_income':None}}}
            for year in YEARS}
        records[code]={'years':years}
    history={'schema':bulk.SCHEMA,'asof':DAY,
        'historical_PIT_source_certified':False,'fiscal_years':YEARS,
        'listed_total':count,'records':records}
    return catalog,history


def payload(base,**changes):
    row={'corp_code':CORP,'stock_code':base['code'],'bsns_year':'2024',
         'reprt_code':'11011','fs_div':base['scope'],'sj_div':'IS',
         'account_detail':'-','account_id':'ifrs-full_Revenue',
         'account_nm':'매출액','currency':'KRW',
         'rcept_no':base['source_2024_receipt_id'],
         'thstrm_amount':'400','frmtrm_amount':'300',
         'bfefrmtrm_amount':'200'}
    return {'status':'000','list':[{**row,**changes}]}


class FY2024RevenueTests(unittest.TestCase):
    def test_two_real_comparative_years_recover_without_rewriting_old_receipts(self):
        catalog,history=inputs()
        source=history['records'][CODE]['years']
        source['2022']['CFS']['values']['revenue']=None
        source['2023']['CFS']['values']['revenue']=None
        original=copy.deepcopy(history)
        base=comp.expected_sources(catalog,history)[CODE]
        self.assertEqual(base['missing_years'],['2022','2023'])
        observed=comp.parse(payload(base),base,corp=CORP,day=DAY)
        self.assertEqual(observed['recovered_revenue_krw'],{'2022':200,'2023':300})
        saved=comp.snapshot(DAY,catalog,{CODE:observed})
        self.assertEqual(saved['recovered_company_years'],2)
        self.assertEqual(comp.verified_prior(saved,comp.expected_sources(catalog,history),DAY),
                         {CODE:observed})
        parts,summary=growth.derive(history,catalog,None,None,saved)
        self.assertEqual(parts[CODE]['available'],3.5)
        self.assertEqual(summary['verified_2024_comparative_issuers'],1)
        self.assertEqual(summary['provider_calls'],0)
        self.assertEqual(history,original)

    def test_source2024_receipt_revision_reopens_review_and_never_promotes_old(self):
        catalog,history=inputs()
        history['records'][CODE]['years']['2022']['CFS']['values']['revenue']=None
        base=comp.expected_sources(catalog,history)[CODE]
        saved=comp.snapshot(DAY,catalog,{CODE:comp.parse(
            payload(base),base,corp=CORP,day=DAY)})
        self.assertEqual(comp.plan(catalog,history,saved)[2],[])
        revised=copy.deepcopy(history)
        revised['records'][CODE]['years']['2024']['CFS']['receipt_id']='20250319000001'
        self.assertEqual(comp.plan(catalog,revised,saved)[2],[CODE])
        incomplete,_=growth.derive(revised,catalog,None,None,saved)
        self.assertNotIn(CODE,incomplete)

    def test_unknown_currency_wrong_identity_and_2024_anchor_are_rejected(self):
        catalog,history=inputs(1)
        history['records'][CODE]['years']['2022']['CFS']['values']['revenue']=None
        base=comp.expected_sources(catalog,history)[CODE]
        for changes in ({'rcept_no':'20250319000001'}, {'currency':'USD'},
                        {'stock_code':'000001'}, {'fs_div':'OFS'}):
            with self.subTest(changes=changes), self.assertRaises(comp.Revenue2024Error):
                comp.parse(payload(base,**changes),base,corp=CORP,day=DAY)
        outcome=comp.parse(payload(base,thstrm_amount='401'),base,corp=CORP,day=DAY)
        self.assertEqual(outcome['status'],'not_observed')
        self.assertEqual(outcome['reason'],'current_year_revenue_conflict')
        self.assertEqual(outcome['recovered_revenue_krw'],{})

    def test_missing_or_conflicting_older_year_is_never_zero(self):
        catalog,history=inputs(1)
        history['records'][CODE]['years']['2022']['CFS']['values']['revenue']=None
        base=comp.expected_sources(catalog,history)[CODE]
        blank=comp.parse(payload(base,bfefrmtrm_amount='-'),base,corp=CORP,day=DAY)
        self.assertEqual(blank['status'],'not_observed')
        self.assertEqual(blank['recovered_revenue_krw'],{})
        status=comp.parse({'status':'013'},base,corp=CORP,day=DAY)
        self.assertEqual(status['status'],'not_observed')
        self.assertEqual(status['reason'],'no_reported_comparative')
        other=payload(base)
        other['list'].append({**other['list'][0],'bfefrmtrm_amount':'999'})
        conflict=comp.parse(other,base,corp=CORP,day=DAY)
        self.assertEqual(conflict['status'],'not_observed')
        self.assertEqual(conflict['reason'],'conflicting_revenue_accounts')

    def test_checkpoint_digest_and_prior_source_amount_cannot_be_forged(self):
        catalog,history=inputs(1)
        history['records'][CODE]['years']['2022']['CFS']['values']['revenue']=None
        base=comp.expected_sources(catalog,history)[CODE]
        recorded=comp.parse(payload(base),base,corp=CORP,day=DAY)
        snapshot=comp.snapshot(DAY,catalog,{CODE:recorded})
        tampered=copy.deepcopy(snapshot)
        tampered['records'][CODE]['recovered_revenue_krw']['2022']=1
        with self.assertRaises(comp.Revenue2024Error):
            comp.verified_prior(tampered,comp.expected_sources(catalog,history),DAY)
        self.assertEqual(comp.plan(catalog,history,snapshot)[2],[])

    def test_only_missing_years_are_eligible_and_sub90_comes_first(self):
        catalog,history=inputs(3)
        for code in ('000000','000001','000002'):
            history['records'][code]['years']['2022']['CFS']['values']['revenue']=None
        expected,prior,selected=comp.plan(catalog,history,None,
            priority_codes={'000002'},limit=2)
        self.assertEqual(selected,['000002','000000'])
        self.assertEqual(len(expected),3)
        self.assertEqual(prior,{})
        # A source-eligible issuer with already available 3.5 revenue points
        # must not consume an official-provider slot.
        full,retained,only=comp.plan(catalog,history,None,
            priority_codes={'000002'},source_gap_codes={'000000','000001'},
            limit=16)
        self.assertEqual(len(full),3)
        self.assertEqual(only,['000000','000001'])
        self.assertEqual(retained,{})
        self.assertEqual(comp.plan(catalog,history,None,
            source_gap_codes=set())[2],[])
        history['records']['000002']['years']['2022']['CFS']['values']['revenue']=200
        self.assertNotIn('000002',comp.expected_sources(catalog,history))


if __name__=='__main__':
    unittest.main()
