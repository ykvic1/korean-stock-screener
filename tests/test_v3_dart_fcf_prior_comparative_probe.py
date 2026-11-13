import copy
import unittest

import v3_dart_fcf_prior_comparative_probe as probe
from test_v3_dart_xbrl_cashflow_recovery import xml
from test_v3_dart_xbrl_capex_pilot import archive
from v3_dart_xbrl_cashflow_recovery import archive_values


PRIOR = {'year': 2024, 'scope': 'CFS', 'annual_receipt_id': '20250318000001',
         'major_annual_receipt_matched': True, 'operating_cashflow_krw': 100,
         'ppe_capex_krw': 20, 'intangible_capex_krw': None,
         'free_cashflow_krw': None}
LATEST = {'year': 2025, 'scope': 'CFS', 'annual_receipt_id': '20260318000001',
          'major_annual_receipt_matched': True, 'operating_cashflow_krw': 150}


class OriginalFcfComparativeTests(unittest.TestCase):
    def test_2024_cfo_anchored_actual_intangible_zero_remains_research(self):
        values, _ = archive_values(archive(xml()), year=2024, scope='CFS',
                                   identifiers={'00123456'}, expected_cfo=100)
        proposal = probe.reconcile_prior(PRIOR, values, 'recovered')
        self.assertEqual(proposal, {'reason': 'complete_comparative_candidate',
                                    'candidate_parts': {'intangible_capex_krw': 0}})
        record = probe.make_record('053620', LATEST, [('2024', PRIOR)],
            research={'2024': proposal}, archive_hash='a' * 64,
            profile_hash='b' * 64, day='2026-11-06')
        probe.verify_record(record)
        self.assertTrue(record['scores_unchanged'])
        self.assertIsNone(PRIOR['intangible_capex_krw'])

    def test_wrong_saved_ppe_or_cfo_do_not_promote(self):
        for observed in ({'operating_cashflow_krw': 101, 'ppe_capex_krw': 20},
                         {'operating_cashflow_krw': 100, 'ppe_capex_krw': 21,
                          'intangible_capex_krw': 0}):
            proposal = probe.reconcile_prior(PRIOR, observed, 'recovered')
            self.assertEqual(proposal['reason'], 'reported_comparative_conflict')
            self.assertEqual(proposal['candidate_parts'], {})

    def test_absent_2023_2024_facts_never_turn_into_zero(self):
        for observed in ({}, {'operating_cashflow_krw': 100,
                              'ppe_capex_krw': 20}):
            proposal = probe.reconcile_prior(PRIOR, observed, 'no_supported_fact')
            self.assertFalse(proposal['candidate_parts'])

    def test_target_selection_only_one_gap_with_matching_receipt_and_scope(self):
        priority = {'single_component_queue': [
            {'code': '053620', 'gaps': [{'component': 'cashflow_fcf_persistence'}]},
            {'code': '005930', 'gaps': [{'component': 'per_eps_quality_growth'}]},
        ]}
        cash = {'records': {'053620': {'years': {'2024': PRIOR, '2025': LATEST}}}}
        self.assertEqual(len(probe.targets(priority, cash)), 1)
        other = copy.deepcopy(cash)
        other['records']['053620']['years']['2024']['scope'] = 'OFS'
        self.assertEqual(probe.targets(priority, other), [])
        proposal = {'reason': 'complete_comparative_candidate',
                    'candidate_parts': {'intangible_capex_krw': 0}}
        record = probe.make_record('053620', LATEST, [('2024', PRIOR)],
            research={'2024': proposal}, archive_hash='a' * 64,
            profile_hash='b' * 64, day='2026-11-06')
        self.assertEqual(probe.targets(priority, cash, {'053620': record}), [])

    def test_tampered_provenance_is_rejected(self):
        record = probe.make_record('053620', LATEST, [('2024', PRIOR)],
            research={'2024': {'reason': 'no_verified_prior_cfo_scope',
                               'candidate_parts': {}}},
            archive_hash='a' * 64, profile_hash='b' * 64, day='2026-11-06')
        altered = copy.deepcopy(record)
        altered['years']['2024']['candidate_parts'] = {'ppe_capex_krw': 9}
        with self.assertRaises(probe.ComparativeError):
            probe.verify_record(altered)

    def test_multiple_gaps_admitted_after_single_gaps_without_duplicate_calls(self):
        priority = {'single_component_queue': [
            {'code': '053620', 'gaps': [{'component': 'cashflow_fcf_persistence'}]}],
            'shape_names': [
                'financial:cashflow_fcf_persistence+valuation:per_eps_quality_growth',
                'financial:revenue_growth+valuation:fcf_yield'],
            'multiple_missing_index': [
                ['005930', 90, 10, 0, 'available'],
                ['053620', 90, 10, 0, 'available'],
                ['000660', 90, 10, 1, 'available']]}
        cash = {'records': {code: {'years': {'2024': copy.deepcopy(PRIOR),
                                           '2025': copy.deepcopy(LATEST)}}
                            for code in ('053620', '005930', '000660')}}
        before = copy.deepcopy(cash)
        self.assertEqual([x[0] for x in probe.targets(priority, cash)],
                         ['053620', '005930'])
        record = probe.make_record('005930', LATEST, [('2024', PRIOR)],
            research={'2024': {'reason': 'no_verified_prior_cfo_scope',
                               'candidate_parts': {}}},
            archive_hash='a' * 64, profile_hash='b' * 64, day='2026-11-06')
        self.assertEqual([x[0] for x in probe.targets(priority, cash, {'005930': record})],
                         ['053620'])
        # Changed source identity is independently reviewed again.
        cash['records']['005930']['years']['2025']['annual_receipt_id'] = '20260318000002'
        self.assertEqual(len(probe.targets(priority, cash, {'005930': record})), 2)
        cash['records']['005930']['years']['2025']['annual_receipt_id'] = LATEST['annual_receipt_id']
        self.assertEqual(cash, before)
        cash['records']['005930']['years']['2024']['scope'] = 'OFS'
        self.assertEqual([x[0] for x in probe.targets(priority, cash)], ['053620'])

    def test_below90_two_fcf_gaps_precede_100_completion_with_no_repeat_calls(self):
        priority={
            'single_component_queue': [
                {'code':'053620','gaps':[{'component':'cashflow_fcf_persistence'}]}],
            'shape_names':['financial:cashflow_fcf_persistence+valuation:fcf_yield'],
            'multiple_missing_index':[['005930',87,13,0,'available']],
            'coverage_90_first':{
                'recovery_not_a_score_or_observation': True,
                'missing_data_never_zero_filled': True,
                'family_conditional_priority_codes':{'dart_cashflow_capex':['005930']},
            },
        }
        cash={'records':{
            code:{'years':{'2024':copy.deepcopy(PRIOR),'2025':copy.deepcopy(LATEST)}}
            for code in ('053620','005930')}}
        original=copy.deepcopy(cash)
        self.assertEqual(probe.below_90_cashflow_priority(priority),{'005930'})
        self.assertEqual([code for code,_,_ in probe.targets(priority,cash)],
                         ['005930','053620'])
        seen=probe.make_record('005930',LATEST,[('2024',PRIOR)],
            research={'2024':{'reason':'no_additional_comparative_capex',
                              'candidate_parts':{}}},
            archive_hash='a'*64,profile_hash='b'*64,day='2026-11-06')
        self.assertEqual([code for code,_,_ in probe.targets(priority,cash,
                                                             {'005930':seen})],['053620'])
        self.assertEqual(cash,original)

    def test_malformed_90_first_queue_fails_closed(self):
        priority={'coverage_90_first':{
            'recovery_not_a_score_or_observation': True,
            'missing_data_never_zero_filled': True,
            'family_conditional_priority_codes':{'dart_cashflow_capex':['005930']},
        }}
        for invalid in ({'coverage_90_first':'not_a_dict'},
                        {'coverage_90_first':{'recovery_not_a_score_or_observation':False}},
                        {'coverage_90_first':{
                            **priority['coverage_90_first'],
                            'family_conditional_priority_codes':{
                                'dart_cashflow_capex':['005930','005930']}}},
                        {'coverage_90_first':{
                            **priority['coverage_90_first'],
                            'family_conditional_priority_codes':{
                                'dart_cashflow_capex':[['005930']]}}):
            with self.subTest(invalid=invalid), self.assertRaises(probe.ComparativeError):
                probe.below_90_cashflow_priority(invalid)

    def test_malformed_multi_gap_indexes_fail_closed(self):
        base = {'single_component_queue': [], 'shape_names': ['financial:cashflow_fcf_persistence']}
        for index in (-1, True, 1, '0'):
            with self.subTest(index=index), self.assertRaises(probe.ComparativeError):
                probe.targets({**base, 'multiple_missing_index': [
                    ['005930', 90, 10, index, 'available']]}, {'records': {}})


if __name__ == '__main__':
    unittest.main()
