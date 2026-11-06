import unittest
from unittest.mock import patch

import v3_dart_bulk_history as bulk
import v3_dart_revenue_2023_comparative as source
import v3_dart_revenue_2022 as direct_source


def fixtures():
    catalog={'date':'2026-11-05','listed_total':1,
             'rows':[{'code':'005490','name':'기업','market':'KOSPI'}]}
    years={}
    for i,y in enumerate((2022,2023,2024,2025)):
        years[str(y)]={'CFS':{'receipt_id':f'{y+1}0318000001',
            'values':{'revenue':None if y==2022 else 200+100*(i-1),
                      'operating_income':None}}}
    history={'schema':bulk.SCHEMA,'asof':catalog['date'],
             'listed_total':1,'fiscal_years':[2022,2023,2024,2025],
             'historical_PIT_source_certified':False,
             'records':{'005490':{'years':years}}}
    base=source.expected_sources(catalog,history)['005490']
    row={'corp_code':'00123456','bsns_year':'2023','reprt_code':'11011',
         'stock_code':'005490','fs_div':'CFS','sj_div':'IS',
         'account_detail':'-','account_id':'ifrs-full_Revenue',
         'account_nm':'매출액','currency':'KRW',
         'rcept_no':base['source_2023_receipt_id'],
         'thstrm_amount':'200','frmtrm_amount':'100'}
    return catalog,history,base,row


class ComparativeRevenueTests(unittest.TestCase):
    def test_original_receipt_and_2023_current_amount_are_both_anchored(self):
        catalog,history,base,row=fixtures()
        actual=source.parse({'status':'000','list':[row]},base,'00123456',catalog['date'])
        self.assertEqual(actual['status'],'observed')
        self.assertEqual(actual['revenue_2022_krw'],100)
        self.assertEqual(actual['source_receipt_id'],base['source_2023_receipt_id'])
        saved=source.snapshot(catalog['date'],{'005490':actual},catalog)
        self.assertEqual(len(source.verified_prior(saved,source.expected_sources(catalog,history),
                                                    catalog['date'])),1)

    def test_no_unearned_2022_if_prior_or_current_revenue_disagrees(self):
        catalog,history,base,row=fixtures()
        for bad in ({'thstrm_amount':'201'}, {'frmtrm_amount':'0'},
                    {'frmtrm_amount':'-'}, {'thstrm_amount':'-'}):
            with self.subTest(bad=bad):
                actual=source.parse({'status':'000','list':[{**row,**bad}]},
                                    base,'00123456',catalog['date'])
                self.assertEqual(actual['status'],'not_observed')
                self.assertIsNone(actual['revenue_2022_krw'])
        actual=source.parse({'status':'013'},base,'00123456',catalog['date'])
        self.assertIsNone(actual['revenue_2022_krw'])

    def test_receipt_currency_identity_and_conflicts_never_promote(self):
        catalog,history,base,row=fixtures()
        for bad in ({'currency':'USD'}, {'rcept_no':'20240319000001'},
                    {'corp_code':'00000001'}, {'stock_code':'005491'}):
            with self.subTest(bad=bad), self.assertRaises(source.ComparativeRevenueError):
                source.parse({'status':'000','list':[{**row,**bad}]},
                             base,'00123456',catalog['date'])
        conflict=source.parse({'status':'000','list':[
            row,{**row,'frmtrm_amount':'101'}]},base,'00123456',catalog['date'])
        self.assertEqual(conflict['source_error'],'comparative_revenue_conflict')
        self.assertIsNone(conflict['revenue_2022_krw'])

    def test_revised_2023_source_revokes_stale_comparative(self):
        catalog,history,base,row=fixtures()
        actual=source.parse({'status':'000','list':[row]},base,'00123456',catalog['date'])
        saved=source.snapshot(catalog['date'],{'005490':actual},catalog)
        history['records']['005490']['years']['2023']['CFS']['receipt_id']='20240415000001'
        _,retained,selected=source.plan(catalog,history,None,saved,limit=1)
        self.assertEqual(retained,{})
        self.assertEqual(selected,['005490'])

    def test_direct_2022_evidence_outranks_later_comparative(self):
        catalog,history,base,row=fixtures()
        original=direct_source.expected_sources(catalog,history)['005490']
        direct=direct_source.snapshot(catalog['date'],{'005490':{
            **original,'checked_on':catalog['date'],'status':'observed',
            'revenue_krw':100,'source_account_id':'ifrs-full_Revenue',
            'source_account_name':None,'source_receipt_id':original['receipt_id']
        }},catalog)
        self.assertEqual(source.expected_sources(catalog,history,direct),{})

    def test_issuer_gap_does_not_block_following_valid_issuer(self):
        catalog,history,base,row=fixtures()
        code='005491'
        catalog['listed_total']=history['listed_total']=2
        catalog['rows'].append({'code':code,'name':'다음기업','market':'KOSPI'})
        history['records'][code]=history['records']['005490']

        class Response:
            def __init__(self,body):self.body=body
            def raise_for_status(self):pass
            def json(self):return self.body
        class Session:
            def get(self,url,params,timeout):
                if params['corp_code']=='00123456':
                    return Response({'status':'000','list':[{**row,'currency':'USD'}]})
                return Response({'status':'000','list':[{**row,'corp_code':'00123457',
                    'stock_code':code}]})
        checkpoints=[]
        with patch('v3_dart_revenue_2023_comparative.verified_company_mapping_cache',
                   return_value={'005490':'00123456',code:'00123457'}):
            saved,calls=source.collect(catalog,history,None,None,key='A'*40,
                session=Session(),cache={},limit=2,on_progress=checkpoints.append)
        self.assertEqual(calls,2)
        self.assertEqual(len(checkpoints),2)
        self.assertEqual(saved['observed_issuer_count'],1)
        self.assertIsNone(saved['records']['005490']['revenue_2022_krw'])
        self.assertEqual(saved['records'][code]['revenue_2022_krw'],100)


if __name__=='__main__':unittest.main()
