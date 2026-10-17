"""Offline success/failure policy tests: action Success != financial validation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO

from official_probe.financial import ACCOUNTS
from official_probe.pinned_2025_pilot import PINNED_ASOF, PINNED_CASES
from official_probe.pilot_completion import check_five_company_research, main


def sample_complete():
    companies = []
    for stock, (corp, receipt, basis) in PINNED_CASES.items():
        companies.append({
            'code': stock, 'corp_code': corp,
            'xbrl_comparison': {
                'rcept_no':receipt, 'fs_div':basis,
                'receipt_latest_asof':True,
                'financial_api_receipt_matches':True,
                'xbrl_original_archive_observed':True,
                'investment_data_ready':False,
                'confirmed_scoring_allowed':False,
                'xbrl_accounting_scope_verified':False,
                'status':'partial_or_different_values_unverified',
                'metrics':{k:{'status':'xbrl_fact_or_scope_ambiguous',
                              'scoring_allowed':False} for k in ACCOUNTS},
            },
        })
    return {
        'asof':PINNED_ASOF, 'mode':'live',
        'requested_symbols':list(PINNED_CASES),
        'investment_data_ready':False, 'confirmed_scoring_allowed':False,
        'dart':{'status':'observed_unverified',
                'xbrl_batch_pilot':{
                    'requested_cases':5, 'completed_case_reports':5,
                    'company_mapping_provenance':
                        'two_prior_official_probe_mapping_observations_not_live'},
                'companies_sampled':companies},
    }


class PilotCompletionTests(unittest.TestCase):
    def test_five_complete_research_observations_are_not_financial_certification(self):
        r = sample_complete()
        self.assertEqual(check_five_company_research(r),(True,[]))
        self.assertFalse(r['investment_data_ready'])
        self.assertFalse(r['confirmed_scoring_allowed'])

    def test_one_completed_and_four_skipped_must_fail(self):
        r=sample_complete()
        r['dart']['xbrl_batch_pilot']['completed_case_reports']=1
        for row in r['dart']['companies_sampled'][1:]:
            row['xbrl_comparison']={
                'status':'not_requested_due_to_research_time_budget',
                'metrics':None,'confirmed_scoring_allowed':False,
                'investment_data_ready':False,
            }
        self.assertFalse(check_five_company_research(r)[0])

    def test_wrong_receipt_wrong_corp_and_missing_metric_never_pass(self):
        for case in ('wrong_receipt','wrong_corp','missing_metric','scoring_true',
                     'scope_true','missing_company','wrong_mapping','bad_date',
                     'missing_original','stale_receipt'):
            with self.subTest(case=case):
                r=sample_complete()
                row=r['dart']['companies_sampled'][0]
                x=row['xbrl_comparison']
                if case=='wrong_receipt':x['rcept_no']='20260316001121'
                if case=='wrong_corp':row['corp_code']='99999999'
                if case=='missing_metric':del x['metrics']['revenue']
                if case=='scoring_true':x['metrics']['revenue']['scoring_allowed']=True
                if case=='scope_true':x['xbrl_accounting_scope_verified']=True
                if case=='missing_company':r['dart']['companies_sampled'].pop()
                if case=='wrong_mapping':r['dart']['xbrl_batch_pilot']['company_mapping_provenance']='live_official_corpCode_zip'
                if case=='bad_date':r['asof']='2026-10-17'
                if case=='missing_original':x['xbrl_original_archive_observed']=False
                if case=='stale_receipt':x['receipt_latest_asof']=False
                self.assertFalse(check_five_company_research(r)[0])

    def test_cli_validation_exit_code_and_sanitized_output(self):
        ok=sample_complete()
        partial=copy.deepcopy(ok)
        partial['dart']['companies_sampled'][-1]['xbrl_comparison']['metrics']=None
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'pilot.json'
            for doc,expected in ((ok,0),(partial,1)):
                p.write_text(json.dumps(doc),encoding='utf-8')
                output=StringIO()
                with redirect_stdout(output):
                    code=main([str(p)])
                self.assertEqual(code,expected)
                self.assertNotIn('api_amount_krw',output.getvalue())
                self.assertNotIn('corp_code',output.getvalue())
            with redirect_stdout(StringIO()):
                self.assertEqual(main([str(Path(tmp)/'missing.json')]),1)


if __name__=='__main__':
    unittest.main()
