import copy
import json
from pathlib import Path
import unittest

import v3_dart_bulk_history as bulk
import v3_revenue_growth_evidence as revenue
import v3_operational_research_score as ops
import v3_dart_revenue_2022 as revenue_2022

ROOT=Path(__file__).resolve().parents[1]/'docs'
YEARS=[2022,2023,2024,2025]
DAY='2026-11-05'


def record(scope='CFS'):
    return {'years':{str(y):{scope:{'receipt_id':f'{y+1}0318000001',
        'values':{'revenue':100*(i+1),'operating_income':None}}}
        for i,y in enumerate(YEARS)}}


class RevenueEvidenceTests(unittest.TestCase):
    def test_exact_2022_official_supplement_only_recovers_missing_first_year(self):
        catalog={'date':DAY,'listed_total':31,'rows':[
            {'code':f'{i:06d}','name':f'기업{i}','market':'KOSPI'} for i in range(31)]}
        history={'schema':bulk.SCHEMA,'asof':DAY,
            'historical_PIT_source_certified':False,'fiscal_years':YEARS,
            'listed_total':31,'records':{f'{i:06d}':record() for i in range(31)}}
        target=history['records']['000000']
        target['years']['2022']['CFS']['values']['revenue']=None
        expected=revenue_2022.expected_sources(catalog,history)['000000']
        source=revenue_2022.snapshot(DAY,{'000000':{**expected,'checked_on':DAY,
            'status':'observed','revenue_krw':100,
            'source_account_id':'ifrs-full_Revenue','source_account_name':None,
            'source_receipt_id':expected['receipt_id']}},catalog)
        before=json.dumps(history,sort_keys=True)
        parts,summary=revenue.derive(history,catalog,source)
        self.assertIn('000000',parts)
        self.assertEqual(parts['000000']['available'],3.5)
        self.assertEqual(summary['verified_2022_supplement_issuers'],1)
        self.assertEqual(json.dumps(history,sort_keys=True),before)
        source['records']['000000']['receipt_id']='20230319000001'
        stale,summary=revenue.derive(history,catalog,source)
        self.assertNotIn('000000',stale)
        self.assertEqual(summary['verified_2022_supplement_issuers'],0)

    def test_wholly_missing_2022_major_row_needs_real_direct_annual_receipt(self):
        catalog={'date':DAY,'listed_total':31,'rows':[
            {'code':f'{i:06d}','name':f'기업{i}','market':'KOSPI'} for i in range(31)]}
        history={'schema':bulk.SCHEMA,'asof':DAY,
            'historical_PIT_source_certified':False,'fiscal_years':YEARS,
            'listed_total':31,'records':{f'{i:06d}':record() for i in range(31)}}
        history['records']['000000']['years']['2022']={}
        expected=revenue_2022.expected_sources(catalog,history)['000000']
        self.assertIsNone(expected['receipt_id'])
        source=revenue_2022.snapshot(DAY,{'000000':{**expected,'checked_on':DAY,
            'status':'observed','revenue_krw':100,
            'source_account_id':'ifrs-full_Revenue','source_account_name':None,
            'source_receipt_id':'20230318000001'}},catalog)
        actual,summary=revenue.derive(history,catalog,source)
        self.assertIn('000000',actual)
        self.assertEqual(summary['verified_2022_supplement_issuers'],1)
        source['records']['000000']['source_receipt_id']='20220318000001'
        with self.assertRaises(revenue_2022.Revenue2022Error):
            revenue.derive(history,catalog,source)

    def test_four_actual_revenues_do_not_require_profit_values(self):
        source=record();before=copy.deepcopy(source)
        self.assertIsNone(bulk._best_income_window(source,YEARS))
        observed=revenue.window(source,YEARS,DAY)
        self.assertAlmostEqual(observed['growth'],100*(4**(1/3)-1),places=5)
        self.assertTrue(observed['independent_of_missing_profit'])
        self.assertIsNone(bulk._three_year_margin(source,YEARS))
        self.assertEqual(source,before)

    def test_missing_year_invalid_receipt_and_nonpositive_revenue_remain_unobserved(self):
        for kind in ('absent','future','wrong_fiscal','zero','negative','bool','shorter'):
            with self.subTest(kind=kind):
                source=record();years=YEARS[:]
                if kind=='absent':del source['years']['2022']
                elif kind=='future':source['years']['2022']['CFS']['receipt_id']='20990318000001'
                elif kind=='wrong_fiscal':source['years']['2022']['CFS']['receipt_id']='20220318000001'
                elif kind=='shorter':years=YEARS[1:]
                else:source['years']['2022']['CFS']['values']['revenue']={'zero':0,'negative':-1,'bool':True}[kind]
                self.assertIsNone(revenue.window(source,years,DAY))

    def test_accounting_scopes_never_mix_and_existing_scope_is_preserved(self):
        source=record()
        source['years']['2022']['OFS']=source['years']['2022'].pop('CFS')
        self.assertIsNone(revenue.window(source,YEARS,DAY))
        source=record()
        separate=record('OFS')
        for y in YEARS:
            source['years'][str(y)].update(separate['years'][str(y)])
            source['years'][str(y)]['OFS']['values']['operating_income']=10
        self.assertEqual(bulk._best_income_window(source,YEARS)['accounting_scope'],'OFS')
        self.assertEqual(revenue.window(source,YEARS,DAY)['scope'],'OFS')

    def test_peer_minimum_and_lag_do_not_fabricate_points(self):
        history={'schema':bulk.SCHEMA,'asof':DAY,'historical_PIT_source_certified':False,
            'fiscal_years':YEARS,'records':{'000001':record()}}
        catalog={'date':DAY,'rows':[{'code':'000001','market':'KOSPI'}]}
        parts,summary=revenue.derive(history,catalog)
        self.assertEqual(parts,{})
        self.assertEqual(summary['eligible_revenue_issuers'],1)
        history['asof']='2026-11-04'
        self.assertEqual(revenue.derive(history,catalog)[0],{})

    def test_actual_saved_source_recovers_missing_revenue_parts_without_other_credit(self):
        def read(n):return json.loads((ROOT/n).read_text())
        names=['data.json','v22-market-catalog.json','v3-issuer-last-observed-factors.json',
            'v3-dart-bulk-history.json','v3-actual-money-participation.json',
            'v3-personal-adjusted-score-summary.json','v3-dart-industry-classification.json',
            'v3-dart-book-reference.json','v3-dart-reported-eps.json']
        inputs=[read(n) for n in names]
        report=ops.build(*inputs)
        fresh,summary=revenue.derive(inputs[3],inputs[1])
        for code,source in inputs[3]['records'].items():
            if code not in fresh or bulk._best_income_window(source,YEARS) is not None:continue
            row=report['rows'][code]
            self.assertEqual(row['parts']['financial']['revenue_growth'],fresh[code])
            self.assertEqual(fresh[code]['available'],3.5)
            baseline,_=ops.score_issuer(None,inputs[2]['rows'][code],day=inputs[1]['date'])
            self.assertEqual(row['parts']['financial']['operating_growth_margin'],baseline['financial']['operating_growth_margin'])
        self.assertEqual(summary['provider_calls'],0)
        self.assertFalse(report['investment_score_certified'])
