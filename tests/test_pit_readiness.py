"""PIT numeric evidence must never imply verified history or scoring."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from official_probe.financial import ACCOUNTS
from official_probe.pinned_2025_pilot import PINNED_CASES, PINNED_ASOF
from official_probe.pit_readiness import _examined_metric, inspect_private_original_numbers, main


def fixture():
    rows=[]
    for code,(corp,receipt,scope) in PINNED_CASES.items():
        metrics={}
        for account in ACCOUNTS:
            concepts=['Revenue','RevenueFromContractsWithCustomers'] if account=='revenue' and code in ('252990','280360') else [account]
            candidates=[]
            for idx,concept in enumerate(concepts):
                candidates.append({
                    'source':'original_xbrl_zip_by_receipt',
                    'concept_local':concept,
                    'original_xbrl_amount_krw': 2025000+idx,
                    'current_api_amount_equal':idx==0,
                    'unit_verified_krw':True,
                    'fiscal_period_verified':True,
                    'dimension_axes_exactly_separate_pattern_local_only':True,
                    'issuer_identity_verified_against_corp_code':False,
                    'original_zip_immutability_at_historical_date_verified':False,
                    'complete_dts_and_fs_scope_verified':False,
                    'historical_pit_numeric_value_certified':False,
                    'confirmed_scoring_allowed':False,
                })
            metrics[account]={
                'scoring_allowed':False,
                'original_receipt_numeric_candidates_private':candidates,
                'original_receipt_numeric_candidates_count':len(candidates),
                'original_receipt_numeric_candidates_scope_certified':False,
                'original_receipt_numeric_candidates_pit_certified':False,
                'xbrl_requested_scope_pattern_only_candidate_count':len(candidates),
                'xbrl_requested_scope_pattern_only_api_equal_count':1,
            }
        comp={
            'corp_code':corp,'rcept_no':receipt,'fs_div':scope,
            'business_year':2025,'metrics':metrics,
            'xbrl_original_archive_observed':True,
            'receipt_latest_asof':True,
            'original_receipt_precedes_asof_date_only':True,
            'amendment_observed':False,
            'original_numeric_values_source':'receipt_specific_xbrl_zip',
            'original_numeric_values_separate_from_current_financial_api':True,
            'historical_receipt_zip_content_immutability_verified':False,
            'original_numeric_values_eligible_for_pit_backtest':False,
            'historical_numeric_snapshot_verified':False,
            'confirmed_scoring_allowed':False,
            'investment_data_ready':False}
        rows.append({'code':code,'corp_code':corp,'xbrl_comparison':comp})
    return {'asof':PINNED_ASOF,'mode':'live',
            'investment_data_ready':False,'confirmed_scoring_allowed':False,
            'dart':{'companies_sampled':rows}}


class PitReadinessTests(unittest.TestCase):
    def test_exact_five_companies_30_accounts_32_candidates_but_never_pit(self):
        report=inspect_private_original_numbers(fixture())
        self.assertEqual(report['company_count'],5)
        self.assertEqual(report['metric_count'],30)
        self.assertEqual(report['metrics_with_receipt_bound_numeric_candidates'],30)
        self.assertEqual(report['private_original_numeric_candidates_observed'],32)
        self.assertEqual(report['candidates_matching_current_api'],30)
        self.assertFalse(report['numeric_values_written_to_summary'])
        self.assertFalse(report['original_zip_bytes_authenticated_at_historical_cutoff'])
        self.assertFalse(report['historical_numeric_pit_certified'])
        self.assertFalse(report['confirmed_scoring_allowed'])
        self.assertFalse(report['investment_data_ready'])
        self.assertNotIn('2025000',json.dumps(report))
        self.assertEqual(report['filing_cutoff_granularity'],'calendar_date_only')

    def test_one_missing_or_false_certified_value_must_not_be_counted(self):
        for kind in ('missing','overcertified','notkrw','badperiod','extra',
                     'no_current_api_match','wrong_axis'):
            with self.subTest(kind=kind):
                obj=fixture()
                comp=obj['dart']['companies_sampled'][0]['xbrl_comparison']
                metric=comp['metrics']['revenue']
                group=metric['original_receipt_numeric_candidates_private']
                if kind=='missing':del metric['original_receipt_numeric_candidates_private']
                if kind=='overcertified':group[0]['historical_pit_numeric_value_certified']=True
                if kind=='notkrw':group[0]['unit_verified_krw']=False
                if kind=='badperiod':group[0]['fiscal_period_verified']=False
                if kind=='extra':group[0]['original_xbrl_amount_krw']=True
                if kind=='no_current_api_match':group[0]['current_api_amount_equal']=False
                if kind=='wrong_axis':group[0]['dimension_axes_exactly_separate_pattern_local_only']=False
                r=inspect_private_original_numbers(obj)
                self.assertLess(r['metrics_with_receipt_bound_numeric_candidates'],30)
                self.assertFalse(r['confirmed_scoring_allowed'])
                self.assertFalse(r['historical_numeric_pit_certified'])

    def test_receipt_invalid_cohort_or_overcertified_document_fails_closed(self):
        for kind in ('wrongreceipt','wrongcompany','date','scoring'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                obj=fixture()
                if kind=='wrongreceipt':
                    obj['dart']['companies_sampled'][0]['xbrl_comparison']['rcept_no']='0'*14
                if kind=='wrongcompany':
                    obj['dart']['companies_sampled'][0]['corp_code']='0'*8
                if kind=='date':obj['asof']='2026-10-17'
                if kind=='scoring':obj['confirmed_scoring_allowed']=True
                inspect_private_original_numbers(obj)
        obj=fixture()
        obj['dart']['companies_sampled'][0]['xbrl_comparison'][
            'historical_receipt_zip_content_immutability_verified']=True
        r=inspect_private_original_numbers(obj)
        self.assertFalse(r['per_company'][0]['receipt_and_cutoff_date_only_observed'])
        self.assertEqual(r['metrics_with_receipt_bound_numeric_candidates'],24)

    def test_cli_private_only_and_works_without_api_access(self):
        original=os.getcwd()
        with tempfile.TemporaryDirectory() as root:
            try:
                os.chdir(root)
                Path('private.json').write_text(json.dumps(fixture()),encoding='utf-8')
                r=main(['private.json','--output','evidence/pit_evidence_summary.json'])
                self.assertEqual(r['metrics_with_receipt_bound_numeric_candidates'],30)
                result=json.loads(Path('evidence/pit_evidence_summary.json').read_text())
                self.assertFalse(result['historical_numeric_pit_certified'])
                self.assertNotIn('2025000',json.dumps(result))
                with self.assertRaises(SystemExit):
                    main(['private.json','--output','docs/public.json'])
                with self.assertRaises(SystemExit):
                    main(['private.json','--output','../unsafe.json'])
            finally:
                os.chdir(original)


if __name__ == '__main__':
    unittest.main()
