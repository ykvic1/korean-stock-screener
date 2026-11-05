"""Real private synthetic source ledgers, no provider APIs or credentials."""
from contextlib import redirect_stdout
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_dart_date_reconciliation_sweep as s
import v3_private_dart_krx_filing_title_triage as t
from test_v3_private_dart_declared_date_proximity import (
    RECEIPTS, private_shortlist, principal_blob, supplement_blob)
from test_v3_private_dart_structured_action_extension import Private


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.store = Private()
        blob = private_shortlist()
        self.store.blobs[t._path(0)] = blob
        self.triage = {**t._empty(), 'next_pair_index': 1,
                       'last_file': t._path(0), 'last_digest': sha256(blob).hexdigest()}
        self.store.blobs[t.CHECKPOINT] = s.principal.canonical(self.triage)
        self.store.blobs[s.principal._path(0, RECEIPTS[0]['rcept_no'])] = principal_blob()
        self.store.blobs[s.supplement._report_path(0, RECEIPTS[0]['rcept_no'])] = supplement_blob()
        s.principal.run(self.store, lambda *args: {'status': '013'})
        s.supplement.run(self.store, lambda *args: {'status': '013'})
        for module in (s.principal, s.supplement):
            p = patch.object(module, 'official_fetch', side_effect=AssertionError('no provider API'))
            p.start()
            self.addCleanup(p.stop)

    def test_completed_pair_saved_with_source_hashes_and_no_false_certification(self):
        events = []
        result = s.sweep(self.store, emit=events.append)
        self.assertEqual(result['completed_month_pairs'], 1)
        self.assertEqual(result['reviewed_pair_receipts_this_run'], 2)
        self.assertEqual(result['declared_date_field_observations'], 3)
        self.assertEqual(result['dates_near_risk_window'], 1)
        self.assertEqual(result['dates_outside_risk_window'], 2)
        self.assertEqual(result['stop_reason'], 'waiting_for_completed_source_pairs')
        self.assertFalse(result['automatic_resume_ready'])
        self.assertTrue(all(result[k] is False for k in s.FLAGS))
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['scores_promoted'], 0)
        raw = self.store.blobs[s._path(0, 0)]
        saved = json.loads(raw)
        self.assertEqual(len(saved['source_hashes_private']), 5)
        for path, digest in saved['source_hashes_private'].items():
            self.assertEqual(sha256(self.store.blobs[path]).hexdigest(), digest)
        public = json.dumps([result, events])
        for forbidden in ('00121932', '20161101000001', '2016-11-03', 'source_hashes_private'):
            self.assertNotIn(forbidden, public)

    def test_partial_chunks_resume_once_without_double_counting(self):
        with patch.object(s.audit, 'MAX_BATCH', 1):
            first = s.sweep(self.store, max_batches=1)
            self.assertTrue(first['automatic_resume_ready'])
            self.assertEqual(first['cumulative_pair_receipt_observations'], 1)
            second = s.sweep(self.store)
        self.assertEqual(second['reviewed_pair_receipts_this_run'], 1)
        self.assertEqual(second['cumulative_pair_receipt_observations'], 2)
        self.assertEqual(second['declared_date_field_observations'], 3)
        again = s.sweep(self.store)
        self.assertEqual(again['completed_batches'], 0)
        self.assertEqual(again['cumulative_pair_receipt_observations'], 2)

    def test_partial_source_pair_is_waiting_not_missing_or_negative_evidence(self):
        self.store.blobs[s.supplement.CHECKPOINT] = s.principal.canonical(s.supplement._empty())
        before = set(self.store.blobs)
        result = s.sweep(self.store)
        self.assertEqual(result['completed_batches'], 0)
        self.assertEqual(result['reviewed_pair_receipts_this_run'], 0)
        self.assertEqual(result['stop_reason'], 'waiting_for_completed_source_pairs')
        self.assertEqual(before, set(self.store.blobs))

    def test_legacy_matched_report_without_schedule_fields_is_unexamined_not_empty(self):
        for report_path, raw in list(self.store.blobs.items()):
            if '/reports/' in report_path:
                obj = json.loads(raw)
                for row in obj['endpoint_results']:
                    row['reported_schedule_date_parser_version'] = 2
                self.store.blobs[report_path] = s.principal.canonical(obj)
        path = s.principal._path(0, RECEIPTS[0]['rcept_no'])
        old = json.loads(self.store.blobs[path])
        old['endpoint_results'][2].pop('reported_schedule_date_candidates')
        old['endpoint_results'][2].pop('reported_schedule_date_parser_version')
        self.store.blobs[path] = s.principal.canonical(old)
        result = s.sweep(self.store)
        self.assertEqual(result['legacy_matched_schedule_endpoints'], 1)
        self.assertEqual(result['legacy_schedule_endpoints'], 1)
        self.assertEqual(result['schedule_examined_endpoints'], 7)
        self.assertEqual(result['declared_date_field_observations'], 1)
        self.assertFalse(result['actual_completion_verified'])

    def test_completed_source_missing_report_cannot_advance_cursor(self):
        self.store.blobs.pop(s.principal._path(0, RECEIPTS[0]['rcept_no']))
        with self.assertRaisesRegex(s.DateSweepError, 'missing_or_unsafe'):
            s.sweep(self.store)
        self.assertNotIn(s.CHECKPOINT, self.store.blobs)

    def test_source_tail_mutation_blocks_even_when_shortlist_exists(self):
        state = s.supplement._cursor(self.store.blobs[s.supplement.CHECKPOINT])
        self.store.blobs[state['last_report_file']] = b'corrupt'
        with self.assertRaisesRegex(s.DateSweepError, 'upstream_tail_changed'):
            s.sweep(self.store)

    def test_lost_checkpoint_write_reuses_orphan_slice_without_duplicate_totals(self):
        original = s.principal._write_checkpoint_with_retry
        def fail(*args, **kwargs):
            if kwargs.get('checkpoint_path') == s.CHECKPOINT:
                raise s.DateSweepError('fixture_checkpoint_interrupted')
            return original(*args, **kwargs)
        with patch.object(s.principal, '_write_checkpoint_with_retry', side_effect=fail):
            with self.assertRaises(s.DateSweepError):
                s.sweep(self.store)
        self.assertIn(s._path(0, 0), self.store.blobs)
        self.assertNotIn(s.CHECKPOINT, self.store.blobs)
        result = s.sweep(self.store)
        self.assertEqual(result['cumulative_pair_receipt_observations'], 2)
        self.assertEqual(len([p for p in self.store.blobs if p.startswith(s.BASE + '/reports/')]), 1)

    def test_partial_source_queue_digest_change_is_not_silently_adopted(self):
        with patch.object(s.audit, 'MAX_BATCH', 1):
            s.sweep(self.store, max_batches=1)
        blob = gzip.compress(gzip.decompress(self.store.blobs[t._path(0)]), mtime=1)
        self.store.blobs[t._path(0)] = blob
        self.triage['last_digest'] = sha256(blob).hexdigest()
        self.store.blobs[t.CHECKPOINT] = s.principal.canonical(self.triage)
        with self.assertRaisesRegex(s.DateSweepError, 'queue_changed'):
            s.sweep(self.store)

    def test_saved_tail_and_overcertified_tail_are_rejected(self):
        s.sweep(self.store)
        state = s._cursor(self.store.blobs[s.CHECKPOINT])
        raw = self.store.blobs[state['last_file']]
        self.store.blobs[state['last_file']] = b'corrupt'
        with self.assertRaisesRegex(s.DateSweepError, 'tail_changed'):
            s.sweep(self.store)
        report = json.loads(raw)
        report['actual_completion_verified'] = True
        forged = s.principal.canonical(report)
        self.store.blobs[state['last_file']] = forged
        state['last_digest'] = sha256(forged).hexdigest()
        self.store.blobs[s.CHECKPOINT] = s.principal.canonical(state)
        with self.assertRaisesRegex(s.DateSweepError, 'invalid_private_date_sweep_tail'):
            s.sweep(self.store)

    def test_source_regression_and_external_sweep_cursor_changes_stop(self):
        def change(event):
            self.store.blobs.pop(s.CHECKPOINT)
        with self.assertRaisesRegex(s.DateSweepError, 'cursor_changed'):
            s.sweep(self.store, emit=change)
        s.sweep(self.store)
        self.store.blobs[s.principal.CHECKPOINT] = s.principal.canonical(s.principal._empty())
        with self.assertRaisesRegex(s.DateSweepError, 'upstream_regressed'):
            s.sweep(self.store)

    def test_complete_cursor_stops_without_more_records(self):
        with patch.object(s, 'TARGET', 1):
            result = s.sweep(self.store)
            self.assertEqual(result['stop_reason'], 'complete')
            self.assertFalse(result['automatic_resume_ready'])
            again = s.sweep(self.store)
            self.assertEqual(again['completed_batches'], 0)

    def test_invalid_scope_and_wrong_destination_never_write(self):
        for values in ({'max_batches': 33}, {'max_batches': True},
                       {'max_seconds': 1201}, {'clock': False}, {'emit': False}):
            with self.assertRaises(s.DateSweepError):
                s.sweep(self.store, **values)
        self.store.repository = 'untrusted/destination'
        with self.assertRaises(s.DateSweepError):
            s.sweep(self.store)
        self.assertNotIn(s.CHECKPOINT, self.store.blobs)

    def test_main_logs_only_aggregates_and_workflow_has_no_provider_credentials(self):
        output = io.StringIO()
        with (patch.dict(os.environ, {'V3_PRIVATE_ARCHIVE_REPO': s.audit.PRIVATE_REPO,
                                     'V3_PRIVATE_ARCHIVE_TOKEN': 't'*40}, clear=True),
              patch.object(s, 'QuotaWaitingPrivateGitStorage', return_value=self.store),
              redirect_stdout(output)):
            self.assertEqual(s.main(), 0)
        for forbidden in ('00121932', RECEIPTS[0]['rcept_no'], 'source_hashes_private'):
            self.assertNotIn(forbidden, output.getvalue())
        text = Path('.github/workflows/v3-dart-date-reconciliation-sweep.yml').read_text()
        offline, live = text.split('  private-sweep:', 1)
        self.assertNotIn('secrets.', offline)
        self.assertNotIn('DART_API_KEY', text)
        self.assertNotIn('KRX_API_KEY', text)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", live)
        self.assertIn("if: steps.reconcile.outputs.resume == 'true'", live)
        self.assertIn('queue: max', live)

    def test_storage_failure_logs_only_allowlisted_transport_codes(self):
        error = s.PrivateHistoryError('private-token-and-receipt-must-not-print')
        error.safe_http_status = 'http_403_rate_limited'
        error.safe_http_operation = 'read_repository_metadata'
        self.assertEqual(s._storage_diagnostic(error), {
            'storage_status': 'http_403_rate_limited', 'storage_operation': 'read_repository_metadata'})
        error.safe_retry_after_seconds = 1800
        self.assertEqual(s._storage_diagnostic(error)['storage_retry_after_seconds'], 1800)
        error.safe_http_status = 'private-token'
        error.safe_http_operation = 'private-receipt'
        error.safe_retry_after_seconds = 'private-header'
        self.assertEqual(s._storage_diagnostic(error), {})


if __name__ == '__main__':
    unittest.main()
