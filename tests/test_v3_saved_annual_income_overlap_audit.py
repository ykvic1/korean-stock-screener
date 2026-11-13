import copy
import unittest

import v3_saved_annual_income_overlap_audit as audit


DAY='2026-11-12'
CODE='005930'


def fixture():
    member={'code':CODE,'name':'issuer','market':'KOSPI'}
    catalog={'date':DAY,'listed_total':1,'rows':[member]}
    receipts={y:f'{y+1}0318000001' for y in (2022,2023,2024,2025)}
    years={str(y):{'CFS':{'receipt_id':receipts[y],
            'ambiguous':[],
            'values':{'revenue':(y-2020)*100,
                      'operating_income':(y-2020)*10 if y<2025 else None}}}
           for y in (2022,2023,2024,2025)}
    history={'asof':DAY,'listed_total':1,'fiscal_years':[2022,2023,2024,2025],
             'historical_PIT_source_certified':False,
             'records':{CODE:{'years':years}}}
    book={'asof':DAY,'market_listed_total':1,
          'records':[{'code':CODE,'status':'observed',
                      'annual':{'year':2025,'fs_div':'CFS','currency':'KRW',
                                'receipt_id':receipts[2025],
                                'values':{'revenue':500,'operating_income':50}}}]}
    report={'asof':DAY,'investment_score_certified':False,
            'rows':{CODE:{'available':88,
                           'parts':{'financial':{'operating_growth_margin':[0,0],
                                                 'revenue_growth':[0,3.5]}}}}}
    return catalog,book,history,report


class SavedAnnualIncomeOverlapTests(unittest.TestCase):
    def test_exact_saved_2025_receipt_could_complete_observed_margin_without_score(self):
        inputs=fixture()
        original=copy.deepcopy(inputs)
        result=audit.analyze(*inputs)
        self.assertEqual(result['counts']['same_receipt_same_scope_current_year'],1)
        self.assertEqual(result['counts']['new_2025_operating_income_issuers'],1)
        self.assertEqual(result['counts']['newly_completable_three_year_operating_margin'],1)
        self.assertEqual(result['below90_counts'][
            'possible_single_factor_threshold_cross_three_year_operating_margin'],1)
        self.assertEqual(result['provider_calls'],0)
        self.assertEqual(result['points_added'],0)
        self.assertEqual(inputs,original)

    def test_prior_year_gap_is_not_called_a_complete_three_year_margin(self):
        catalog,book,history,report=fixture()
        history['records'][CODE]['years']['2024']['CFS']['values']['operating_income']=None
        result=audit.analyze(catalog,book,history,report)
        self.assertEqual(result['counts']['new_2025_income_source_issuers'],1)
        self.assertNotIn('newly_completable_three_year_operating_margin',result['counts'])

    def test_wrong_scope_receipt_currency_or_conflict_never_recovers(self):
        for change in ('receipt','scope','currency','conflict','ambiguous'):
            with self.subTest(change=change):
                catalog,book,history,report=fixture()
                source=book['records'][0]['annual']
                if change=='receipt':source['receipt_id']='20260319000001'
                elif change=='scope':source['fs_div']='OFS'
                elif change=='currency':source['currency']='USD'
                elif change=='conflict':
                    history['records'][CODE]['years']['2025']['CFS']['values']['operating_income']=40
                else:history['records'][CODE]['years']['2025']['CFS']['ambiguous']=['operating_income']
                result=audit.analyze(catalog,book,history,report)
                self.assertNotIn('new_2025_income_source_issuers',result['counts'])
                self.assertEqual(result['points_added'],0)

    def test_fy2024_original_op_comparative_has_independent_2024_anchor(self):
        catalog,book,history,report=fixture()
        source=history['records'][CODE]['years']
        source['2023']['CFS']['values']['operating_income']=None
        source['2025']['CFS']['values']['operating_income']=50
        summary=audit.analyze(catalog,book,history,report)
        self.assertEqual(summary['historical_missing_year_fields']['missing_2023_operating_income'],1)
        self.assertEqual(summary['historical_missing_year_fields'][
            'fy2024_original_2023_operating_comparative_candidate'],1)
        self.assertEqual(summary['below90_historical_missing_year_fields'][
            'fy2024_original_2023_operating_comparative_candidate'],1)
        # A broken 2024 source anchor eliminates this candidate.
        source['2024']['CFS']['values']['operating_income']=None
        blocked=audit.analyze(catalog,book,history,report)
        self.assertNotIn('fy2024_original_2023_operating_comparative_candidate',
                         blocked['historical_missing_year_fields'])

    def test_fy2025_revenue_comparative_requires_reported_2022_and_2025_anchors(self):
        catalog,book,history,report=fixture()
        report['rows'][CODE]['parts']['financial']['revenue_growth']=[0,0]
        source=history['records'][CODE]['years']
        source['2023']['CFS']['values']['revenue']=None
        summary=audit.analyze(catalog,book,history,report)
        self.assertEqual(summary['historical_missing_year_fields'][
            'fy2025_original_2023_2024_revenue_comparative_candidate'],1)
        source['2022']['CFS']['values']['revenue']=None
        blocked=audit.analyze(catalog,book,history,report)
        self.assertNotIn('fy2025_original_2023_2024_revenue_comparative_candidate',
                         blocked['historical_missing_year_fields'])

    def test_cross_date_or_missing_certification_stops(self):
        catalog,book,history,report=fixture()
        book['asof']='2026-11-11'
        with self.assertRaises(audit.IncomeOverlapError):
            audit.analyze(catalog,book,history,report)
        book['asof']=DAY
        report['investment_score_certified']=True
        with self.assertRaises(audit.IncomeOverlapError):
            audit.analyze(catalog,book,history,report)


if __name__=='__main__':
    unittest.main()
