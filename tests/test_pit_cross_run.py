"""Cross-run reproducibility fixtures; never equate SHA agreement with PIT."""
from __future__ import annotations
import copy
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from official_probe.pinned_2025_pilot import PINNED_CASES, PINNED_ASOF
from official_probe.pit_cross_run import audit_probe_observations, main


def record(hour: int, *, latest_numbers=False):
    rows=[]
    for code,(corp,receipt,fs) in PINNED_CASES.items():
        comp={
            'corp_code':corp,'rcept_no':receipt,'fs_div':fs,
            'business_year':2025,'report_code':'11011',
            'xbrl_original_archive_observed':True,
            'receipt_latest_asof':True,
            'amendment_observed':False,
            'filing_list_status':'listing_observed_documents_not_requested',
            'historical_numeric_snapshot_verified':False,
            'xbrl_accounting_scope_verified':False,
            'confirmed_scoring_allowed':False,'investment_data_ready':False,
            'xbrl_zip_sha256':'a'*64,
            'financial_api_sha256':'b'*64}
        if latest_numbers:
            comp.update({
                'original_receipt_precedes_asof_date_only':True,
                'original_numeric_values_source':'receipt_specific_xbrl_zip',
                'original_numeric_values_separate_from_current_financial_api':True,
                'historical_receipt_zip_content_immutability_verified':False,
                'original_numeric_values_eligible_for_pit_backtest':False,
                'metrics':{},
            })
            for account in ('revenue','operating_income','operating_cashflow',
                            'assets','liabilities','equity'):
                # Only private input fixture contains these numbers.
                comp['metrics'][account]={
                    'scoring_allowed':False,
                    'original_receipt_numeric_candidates_private':[{
                        'source':'original_xbrl_zip_by_receipt',
                        'original_xbrl_amount_krw':12345,
                        'current_api_amount_equal':True,
                        'unit_verified_krw':True,'fiscal_period_verified':True,
                        'dimension_axes_exactly_separate_pattern_local_only':True,
                        'issuer_identity_verified_against_corp_code':False,
                        'original_zip_immutability_at_historical_date_verified':False,
                        'complete_dts_and_fs_scope_verified':False,
                        'historical_pit_numeric_value_certified':False,
                        'confirmed_scoring_allowed':False}],
                    'original_receipt_numeric_candidates_count':1,
                    'original_receipt_numeric_candidates_scope_certified':False,
                    'original_receipt_numeric_candidates_pit_certified':False,
                    'xbrl_requested_scope_pattern_only_candidate_count':1,
                    'xbrl_requested_scope_pattern_only_api_equal_count':1,
                }
        rows.append({'code':code,'corp_code':corp,'xbrl_comparison':comp})
    return {
        'asof':PINNED_ASOF,'mode':'live','confirmed_scoring_allowed':False,
        'investment_data_ready':False,
        'received_at_utc':(datetime(2026,10,17,8,tzinfo=timezone.utc)
                           +timedelta(hours=hour)).isoformat(),
        'dart':{'xbrl_batch_pilot':{'completed_case_reports':5,
                                   'requested_cases':5},
                'companies_sampled':rows},
    }


class CrossRunEvidenceTests(unittest.TestCase):
    def test_stable_four_runs_evidence_is_not_historically_authenticated(self):
        records=[record(i,latest_numbers=(i==3)) for i in range(4)]
        audit=audit_probe_observations(records)
        self.assertEqual(audit['observation_count'],4)
        self.assertEqual(audit['distinct_observation_calendar_dates'],1)
        self.assertTrue(audit['all_five_repeated_sha256_pairs_stable'])
        self.assertEqual(audit['newest_original_numeric_metric_groups'],30)
        self.assertEqual(audit['newest_original_numeric_candidates'],30)
        self.assertEqual(audit['newest_original_candidates_matching_current_api'],30)
        self.assertFalse(audit['source_bytes_authenticated_at_historical_cutoff'])
        self.assertFalse(audit['complete_post_cutoff_revision_chain_verified'])
        self.assertFalse(audit['historical_numeric_pit_certified'])
        self.assertFalse(audit['confirmed_scoring_allowed'])
        self.assertNotIn('12345',json.dumps(audit))
        self.assertNotIn('a'*64,json.dumps(audit))
        # Reverse input order: newest report must still be selected by timestamp.
        reversed_audit=audit_probe_observations(list(reversed(records)))
        self.assertEqual(reversed_audit['newest_original_numeric_metric_groups'],30)

    def test_changed_digest_is_reported_not_approved(self):
        records=[record(0),record(1)]
        records[1]['dart']['companies_sampled'][1][
            'xbrl_comparison']['xbrl_zip_sha256']='c'*64
        r=audit_probe_observations(records)
        self.assertFalse(r['all_five_repeated_sha256_pairs_stable'])
        self.assertEqual(sum(not v['xbrl_zip_sha256_stable_in_observation_window']
                             for v in r['companies']),1)
        self.assertFalse(r['historical_numeric_pit_certified'])
        self.assertFalse(r['investment_data_ready'])

    def test_invalid_input_not_misclassified_as_independent_pit_evidence(self):
        inputs=[record(0),record(1)]
        for label in ('duplicate','naive','before_cutoff','wrong_receipt',
                      'wrong_corp','overcertified','missing_hash',
                      'wrong_scope','wrong_cutoff','incomplete'):
            docs=copy.deepcopy(inputs)
            item=docs[1]['dart']['companies_sampled'][0]
            comp=item['xbrl_comparison']
            if label=='duplicate':
                docs[1]['received_at_utc']=docs[0]['received_at_utc']
            elif label=='naive':docs[1]['received_at_utc']='2026-10-17T09:00:00'
            elif label=='before_cutoff':docs[1]['received_at_utc']='2026-10-16T09:00:00+00:00'
            elif label=='wrong_receipt':comp['rcept_no']='0'*14
            elif label=='wrong_corp':item['corp_code']='0'*8
            elif label=='overcertified':comp['historical_numeric_snapshot_verified']=True
            elif label=='missing_hash':comp['xbrl_zip_sha256']=None
            elif label=='wrong_scope':comp['fs_div']='CFS'
            elif label=='wrong_cutoff':docs[1]['asof']='2026-10-15'
            elif label=='incomplete':docs[1]['dart']['companies_sampled'].pop()
            with self.subTest(label=label),self.assertRaises(ValueError):
                audit_probe_observations(docs)
        with self.assertRaises(ValueError):
            audit_probe_observations([record(0)])

    def test_cli_writes_only_nonnumeric_summary_outside_docs(self):
        root=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                for i in range(2):
                    Path(f'private{i}.json').write_text(
                        json.dumps(record(i)),encoding='utf-8')
                with redirect_stdout(StringIO()):
                    r=main(['private1.json','private0.json',
                            '--output','evidence/pit_crossrun_summary.json'])
                self.assertEqual(r['observation_count'],2)
                txt=Path('evidence/pit_crossrun_summary.json').read_text()
                self.assertNotIn('12345',txt)
                self.assertNotIn('a'*64,txt)
                self.assertIn('historical_numeric_pit_certified',txt)
                with self.assertRaises(SystemExit),redirect_stdout(StringIO()):
                    main(['private1.json','private0.json','--output','docs/leak.json'])
            finally:
                os.chdir(root)


if __name__=='__main__':
    unittest.main()
