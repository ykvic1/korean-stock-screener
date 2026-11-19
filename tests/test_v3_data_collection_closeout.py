"""Collection may close with missing data while certification remains blocked."""
import copy
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from v3_data_collection_closeout import build, valid, OUTPUT

ROOT = Path(__file__).resolve().parents[1]


class CollectionCloseoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads((ROOT/'docs'/OUTPUT).read_text())

    def test_checkpoint_can_close_with_missing_data_but_rejects_unfetched_sources(self):
        record = self.record
        score_name = 'v3-operational-research-scores.json'
        evidence = {key: record[key] for key in (
            'asof', 'listed_total', 'available_weight_distribution')}
        evidence['publication_coherent'] = True
        evidence['file_sha256'] = {score_name: record['source_sha256'][score_name]}
        priority = {
            'asof': record['asof'],
            'source_report_sha256': evidence['file_sha256'][score_name],
            'incomplete_count': record['incomplete_weight_issuers'],
            'component_missing_issuer_counts': record['missing_component_issuer_counts'],
            'reason_component_counts': record['missing_reason_component_counts'],
        }
        with TemporaryDirectory() as directory, patch(
                'v3_operational_publication_audit.audit', return_value=evidence):
            docs = Path(directory)
            for name in ('v3-dart-historical-receipt-progress.json',
                         'v3-krx-derived-backfill-progress.json'):
                (docs/name).write_bytes((ROOT/'docs'/name).read_bytes())
            (docs/'v3-operational-evidence-priority.json').write_text(json.dumps(priority))
            gap_path = docs/'v3-dart-cashflow-gap-summary.json'
            gap = {'asof': record['asof'], 'source_eligible_unfetched_company_years': 0}
            gap_path.write_text(json.dumps(gap))
            result = build(docs)
            gap['source_eligible_unfetched_company_years'] = 2
            gap_path.write_text(json.dumps(gap))
            with self.assertRaisesRegex(ValueError, 'collection_closeout_evidence_not_aligned'):
                build(docs)
        self.assertTrue(valid(result, result['asof']))
        self.assertGreater(result['incomplete_weight_issuers'], 0)
        self.assertTrue(result['initial_data_collection_complete'])
        self.assertFalse(result['investment_score_certified'])
        self.assertFalse(result['v30b_release_ready'])

    def test_milestone_survives_a_later_daily_refresh(self):
        self.assertTrue(valid(self.record, '2026-11-18'))
        self.assertFalse(valid(self.record, '2026-11-12'))

    def test_invalid_checkpoint_cannot_claim_completion(self):
        for key, value in [('source_eligible_unfetched_fcf_company_years', 1),
                           ('historical_receipt_completed_months', 119),
                           ('missing_values_filled_or_weights_normalized', True),
                           ('daily_refresh_continues', False),
                           ('investment_score_certified', True)]:
            with self.subTest(key=key):
                item = copy.deepcopy(self.record)
                item[key] = value
                self.assertFalse(valid(item, '2026-11-18'))
        item = copy.deepcopy(self.record)
        item['checks']['publication_coherent'] = False
        self.assertFalse(valid(item, '2026-11-18'))

    def test_corrupt_or_incomplete_distribution_is_rejected(self):
        item = copy.deepcopy(self.record)
        item['available_weight_distribution']['100'] += 1
        self.assertFalse(valid(item, '2026-11-18'))
        item = copy.deepcopy(self.record)
        del item['available_weight_distribution']['under_60']
        self.assertFalse(valid(item, '2026-11-18'))

    def test_ui_distinguishes_closeout_daily_refresh_and_certification(self):
        script = """
const assert=require('node:assert/strict');
const api=require('./docs/operational-score.js');
const r=require('./docs/v3-data-collection-closeout.json');
const text=api.collectionStatus(r,'2026-11-18');
assert.ok(text.includes('초기 데이터 수집 완료 (결측 제외)'));
assert.ok(text.includes('일일 자동 갱신 계속'));
assert.ok(text.includes('정식 검증 진행 중'));
assert.equal(api.collectionStatus(r,'2026-11-12'),null);
assert.equal(api.collectionStatus({...r,investment_score_certified:true},'2026-11-18'),null);
assert.equal(api.collectionStatus(null,'2026-11-18'),null);
"""
        subprocess.run(['node', '-e', script], cwd=ROOT, check=True)

    def test_release_audit_reports_collection_without_unlocking_release(self):
        from v3_v30b_release_gate import main
        # Suppress the CLI's aggregate output; exercise its actual default paths.
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            result = main(['--assert-blocked'])
        self.assertTrue(result['initial_data_collection_complete'])
        self.assertTrue(result['daily_data_refresh_continues'])
        self.assertFalse(result['v30b_release_ready'])
        self.assertFalse(result['confirmed_scoring_authorized'])


if __name__ == '__main__':
    unittest.main()
