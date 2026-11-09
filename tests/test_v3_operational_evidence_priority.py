import copy
import json
from pathlib import Path
import unittest

import v3_operational_evidence_priority as p

ROOT=Path(__file__).resolve().parents[1]/'docs'


class EvidencePriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def read(n):return json.loads((ROOT/n).read_text())
        cls.report=read('v3-operational-research-scores.json')
        cls.comparison=read('v3-operational-research-comparison.json')
        cls.catalog=read('v22-market-catalog.json')
        cls.legacy=read('v3-near-100-evidence-priority.json')

    def test_all_current_issuers_are_accounted_for_without_added_points(self):
        result=p.build(self.report,self.comparison,self.catalog,self.legacy)
        self.assertEqual(result['available_100_count']+result['incomplete_count'],result['listed_total'])
        self.assertEqual(len(result['single_component_queue'])+len(result['multiple_missing_index']),result['incomplete_count'])
        self.assertEqual(result['points_added'],0)
        self.assertEqual(result['provider_calls'],0)
        for row in result['single_component_queue']:
            self.assertEqual(len(row['gaps']),1)
            self.assertAlmostEqual(row['missing_weight'],100-row['available'],places=5)

    def test_forged_score_or_legacy_coverage_cannot_drive_new_queue(self):
        bad=copy.deepcopy(self.report)
        next(row for row in bad['rows'].values() if row['available']<100)['available']=100
        with self.assertRaises(ValueError):p.build(bad,self.comparison,self.catalog)
        old=copy.deepcopy(self.comparison)
        old['operational_coverage']['available_100_count']+=1
        with self.assertRaises(ValueError):p.build(self.report,old,self.catalog)

    def test_only_confirmed_catalog_short_history_gets_that_reason(self):
        self.assertEqual(p.category('technical','weekly_pattern','insufficient_history',False),
                         'catalog_confirmed_insufficient_history')
        self.assertEqual(p.category('technical','weekly_pattern','priced',False),
                         'chart_window_or_adjustment_review')
        self.assertEqual(p.category('financial','cashflow_fcf_persistence','insufficient_history',True),
                         'original_filing_review_no_blind_refetch')

    def test_conditional_source_family_upper_bounds_never_count_mixed_gaps(self):
        def row(*pairs):
            return {'gaps':[{'area':a,'component':k} for a,k in pairs]}
        fcf=('financial','cashflow_fcf_persistence')
        yield_gap=('valuation','fcf_yield')
        eps=('valuation','per_eps_quality_growth')
        money=('volume','correction_contraction')
        summary=p.source_family_completion_upper_bounds([
            row(fcf),row(fcf,yield_gap),row(eps),row(money),row(fcf,eps)])
        self.assertEqual(summary['full_100_if_all_missing_family_facts_became_observed'],{
            'dart_cashflow_capex':2,'dart_annual_fundamentals':0,
            'reported_eps_earnings_yield':1,'exchange_actual_money_windows':1,
            'price_chart_windows':0})
        self.assertTrue(summary['no_missing_factor_assumed_zero_or_observed'])
        with self.assertRaises(ValueError):p.source_family_completion_upper_bounds([row()])

    def test_full_market_bounds_are_subset_of_incomplete_issuers(self):
        out=p.build(self.report,self.comparison,self.catalog,self.legacy)
        bounds=out['source_family_completion_upper_bounds']
        values=bounds['full_100_if_all_missing_family_facts_became_observed']
        self.assertLessEqual(max(values.values()),out['incomplete_count'])
        self.assertGreaterEqual(values['dart_cashflow_capex'],
                                sum(r['gaps'][0]['component']=='cashflow_fcf_persistence'
                                    for r in out['single_component_queue']))
        self.assertEqual(out['available_100_count']+out['incomplete_count'],
                         out['listed_total'])

    def test_90_first_bands_and_conditional_queue_reconcile_real_inputs(self):
        out=p.build(self.report,self.comparison,self.catalog,self.legacy)
        first=out['coverage_90_first']
        bands=first['available_weight_bands']
        self.assertEqual(sum(bands.values()),out['listed_total'])
        self.assertEqual(bands['100'],out['available_100_count'])
        self.assertEqual(first['already_90_or_more'],sum(
            bands[k] for k in ('100','95_to_100','90_to_95')))
        self.assertEqual(first['below_90_issuers'],sum(
            bands[k] for k in ('80_to_90','70_to_80','60_to_70','below_60')))
        self.assertEqual(first['one_source_family_upper_bound_count']+
                         first['multiple_source_families_needed_at_minimum'],
                         first['below_90_issuers'])
        self.assertTrue(first['independent_source_recoverability_not_proven'])
        self.assertTrue(first['recovery_not_a_score_or_observation'])
        incomplete={code:r for code,r in self.report['rows'].items()
                    if r['available']<90}
        for family,codes in first['family_conditional_priority_codes'].items():
            self.assertEqual(len(codes),len(set(codes)))
            self.assertEqual(len(codes),first['family_upper_bound_counts'][family])
            for code in codes:
                self.assertIn(code,incomplete)
                row=incomplete[code]
                potential=sum(maximum-(row['parts'][area].get(key) or [0,0])[1]
                              for area,key in p.SOURCE_FAMILIES[family]
                              for maximum in (p.R1D_PART_LIMITS[area][key],))
                self.assertGreaterEqual(row['available']+potential,90-1e-5)

    def test_90_first_never_counts_a_partial_family_as_an_observation(self):
        queue=[
            {'code':'000001','available':89,'gaps':[
                {'area':'valuation','component':'fcf_yield','missing_weight':2.5}]},
            {'code':'000002','available':75,'gaps':[
                {'area':'valuation','component':'fcf_yield','missing_weight':2.5}]},
            {'code':'000003','available':95,'gaps':[
                {'area':'valuation','component':'fcf_yield','missing_weight':2.5}]},
        ]
        # This isolated test checks the queue classifier, not a full 100-weight
        # source report (whose part weights are audited in build()).
        result=p.coverage_90_first(queue,listed_total=3,full_100_count=0)
        self.assertEqual(result['available_weight_bands']['95_to_100'],1)
        self.assertEqual(result['below_90_issuers'],2)
        self.assertEqual(result['one_source_family_upper_bound_count'],1)
        self.assertEqual(result['multiple_source_families_needed_at_minimum'],1)
        self.assertEqual(result['family_conditional_priority_codes']['dart_cashflow_capex'],
                         ['000001'])
        self.assertTrue(result['missing_data_never_zero_filled'])
