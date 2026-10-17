"""Offline-only Revenue fact candidate review: never mistake clues for certification."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO

from official_probe.offline_revenue_evidence import (
    examine_revenue_report, audit_private_cohort, main)
from official_probe.pinned_2025_pilot import PINNED_CASES, PINNED_ASOF


def fixture_report():
    return {
        'business_year': 2025,
        'fs_div': 'OFS',
        'receipt_latest_asof': True,
        'financial_api_receipt_matches': True,
        'xbrl_original_archive_observed': True,
        'original_receipt_precedes_asof_date_only': True,
        'amendment_observed': False,
        'primary_statement_role_verified': False,
        'embedded_role_type_definitions_complete_dts_verified': False,
        'historical_numeric_snapshot_verified': False,
        'confirmed_scoring_allowed': False,
        'metrics': {
            'revenue': {
                'scoring_allowed': False,
                'xbrl_requested_scope_pattern_is_verified': False,
                'xbrl_requested_scope_pattern_only_candidate_count': 2,
                'xbrl_requested_scope_pattern_only_api_equal_count': 1,
                'xbrl_scope_pattern_concept_groups': [
                    {'concept_local': 'Revenue', 'namespace_sha256_prefix': 'a'*16,
                     'fact_count': 1, 'api_amount_equal_count': 1,
                     'distinct_context_count': 1,'distinct_value_count': 1},
                    {'concept_local': 'RevenueFromContractsWithCustomers',
                     'namespace_sha256_prefix': 'b'*16,
                     'fact_count': 1, 'api_amount_equal_count': 0,
                     'distinct_context_count': 1,'distinct_value_count': 1},
                ],
            },
        },
        'revenue_embedded_presentation_role_samples_truncated': False,
        'revenue_embedded_presentation_role_samples': [
            {'concept_local': 'Revenue', 'role_uri_sha256_prefix': 'c'*16,
             'embedded_role_type_found': True,
             'embedded_role_type_used_on_presentation': True,
             'parent_sample_truncated': False, 'as_child_arc_count': 1,
             'direct_parent_concept_local_samples':
                 ['StatementOfComprehensiveIncomeAbstract'],
             'embedded_role_definition_hints_unverified':
                 ['income_statement','separate']},
            {'concept_local': 'RevenueFromContractsWithCustomers',
             'role_uri_sha256_prefix': 'd'*16,
             'embedded_role_type_found': True,
             'embedded_role_type_used_on_presentation': True,
             'parent_sample_truncated': False, 'as_child_arc_count': 1,
             'direct_parent_concept_local_samples':
                 ['DisclosureOfRevenueFromContractsWithCustomersLineItems'],
             'embedded_role_definition_hints_unverified': ['notes']},
        ],
    }


def fixture_cohort():
    sample=[]
    for code,(corp,receipt,scope) in PINNED_CASES.items():
        report=fixture_report()
        report.update(corp_code=corp,rcept_no=receipt,fs_div=scope)
        sample.append({'code':code, 'corp_code':corp, 'xbrl_comparison':report})
    return {'asof':PINNED_ASOF,'mode':'live',
            'investment_data_ready':False,'confirmed_scoring_allowed':False,
            'dart':{'companies_sampled':sample}}


class OfflineRevenueEvidenceTests(unittest.TestCase):
    def test_unique_revenue_role_and_single_api_equal_scope_candidate(self):
        x=examine_revenue_report(fixture_report())
        self.assertEqual(x['status'],
                         'unique_revenue_primary_candidate_research_only')
        self.assertEqual(x['research_primary_statement_candidate_concept'],'Revenue')
        self.assertEqual(x['candidate_namespace_sha256_prefix'],'a'*16)
        self.assertEqual(x['candidate_role_sha256_prefix'],'c'*16)
        for flag in ('individual_fact_to_presentation_role_link_verified',
                     'full_dts_verified','ofs_scope_certified',
                     'numeric_pit_snapshot_verified','confirmed_scoring_allowed',
                     'investment_data_ready'):
            self.assertIs(x[flag], False)

    def test_conflicting_primary_roles_are_blocked(self):
        doc=fixture_report()
        doc['revenue_embedded_presentation_role_samples'][1]={
            **doc['revenue_embedded_presentation_role_samples'][0],
            'concept_local':'RevenueFromContractsWithCustomers',
            'role_uri_sha256_prefix':'f'*16}
        self.assertEqual(examine_revenue_report(doc)['status'],
                         'primary_role_evidence_ambiguous_or_absent')
        doc=fixture_report()
        doc['revenue_embedded_presentation_role_samples'].append(
            {**doc['revenue_embedded_presentation_role_samples'][0],
             'role_uri_sha256_prefix':'e'*16})
        self.assertEqual(examine_revenue_report(doc)['status'],
                         'primary_role_evidence_ambiguous_or_absent')

    def test_bad_revenue_values_truncation_and_missing_role_fail_closed(self):
        for change in ('api_match','two_matches','missing_role','truncated',
                       'consolidated_role','amendment','pit_trust','bad_counts',
                       'wrong_fs','context_duplicated','bad_hex'):
            doc=fixture_report()
            group=doc['metrics']['revenue']['xbrl_scope_pattern_concept_groups']
            role=doc['revenue_embedded_presentation_role_samples'][0]
            if change=='api_match':
                group[0]['api_amount_equal_count']=0
                doc['metrics']['revenue']['xbrl_requested_scope_pattern_only_api_equal_count']=0
            elif change=='two_matches':
                group[1]['api_amount_equal_count']=1
                doc['metrics']['revenue']['xbrl_requested_scope_pattern_only_api_equal_count']=2
            elif change=='missing_role':
                role['direct_parent_concept_local_samples']=['DisclosureOfRevenueLineItems']
            elif change=='truncated':
                doc['revenue_embedded_presentation_role_samples_truncated']=True
            elif change=='consolidated_role':
                role['embedded_role_definition_hints_unverified']=['income_statement','consolidated']
            elif change=='amendment':
                doc['amendment_observed']=True
            elif change=='pit_trust':
                doc['historical_numeric_snapshot_verified']=True
            elif change=='bad_counts':
                group[0]['fact_count']=3
            elif change=='wrong_fs':
                doc['fs_div']='CFS'
            elif change=='context_duplicated':
                group[0]['distinct_context_count']=2
            elif change=='bad_hex':
                group[0]['namespace_sha256_prefix']='not-hex'
            with self.subTest(change=change):
                result=examine_revenue_report(doc)
                self.assertNotEqual(result['status'],
                                    'unique_revenue_primary_candidate_research_only')
                self.assertIs(result['confirmed_scoring_allowed'],False)

    def test_full_cohort_identity_and_privacy(self):
        result=audit_private_cohort(fixture_cohort())
        self.assertEqual(result['companies_evaluated'],5)
        self.assertEqual(result['primary_revenue_candidates_only'],5)
        self.assertEqual([x['code'] for x in result['per_company']],
                         list(PINNED_CASES))
        self.assertFalse(result['all_six_metric_accounting_scope_verified'])
        self.assertFalse(result['original_historical_values_pit_certified'])
        self.assertNotIn('api_amount_krw',json.dumps(result))
        self.assertNotIn('crtfc_key',json.dumps(result))
        for case in ('missing_company','wrong_receipt','wrong_asof',
                     'scoring_enabled'):
            obj=fixture_cohort()
            if case=='missing_company':obj['dart']['companies_sampled'].pop()
            if case=='wrong_receipt':
                obj['dart']['companies_sampled'][0]['xbrl_comparison']['rcept_no']='0'*14
            if case=='wrong_asof':obj['asof']='2026-10-17'
            if case=='scoring_enabled':obj['confirmed_scoring_allowed']=True
            with self.subTest(case=case),self.assertRaises(ValueError):
                audit_private_cohort(obj)

    def test_cli_private_json_and_unpublished_local_output(self):
        with tempfile.TemporaryDirectory() as root:
            import os
            original=os.getcwd()
            try:
                os.chdir(root)
                Path('private.json').write_text(
                    json.dumps(fixture_cohort()),encoding='utf-8')
                with redirect_stdout(StringIO()) as stream:
                    ret=main(['private.json','--output','evidence/revenue_review.json'])
                self.assertEqual(ret['primary_revenue_candidates_only'],5)
                data=json.loads(Path('evidence/revenue_review.json').read_text())
                self.assertFalse(data['confirmed_scoring_allowed'])
                self.assertIn('scoring_allowed',stream.getvalue())
                with self.assertRaises(SystemExit),redirect_stdout(StringIO()):
                    main(['private.json','--output','docs/leak.json'])
            finally:
                os.chdir(original)


if __name__ == '__main__':
    unittest.main()
