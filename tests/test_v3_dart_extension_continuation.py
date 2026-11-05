"""Real supplemental transactions in memory; no source, credentials or network."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_dart_extension_continuation as c
import v3_private_dart_structured_action_extension as s
from test_v3_private_dart_structured_action_extension import Private, record


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.store = Private()
        self.store.blobs[s.TRIAGE_CHECKPOINT] = b'opaque triage'
        self.store.blobs[s.triage_path(0)] = b'opaque queue'
        self.records = [record(f'20161101{i:06d}', ['rights_issue']) for i in range(1, 10)]
        self.patches = [patch.object(s, 'triage_cursor', return_value={'next_pair_index': 1}),
                        patch.object(s, '_candidate_queue', return_value=(self.records, 'a'*64, 9))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

    def fetch(self, *args):
        self.calls.append(args)
        return {'status': '013'}

    def test_real_transactions_continue_then_wait_without_promoting_scores(self):
        events = []
        result = c.continuous(self.store, self.fetch, emit=events.append)
        self.assertEqual(result['processed_candidates'], 9)
        self.assertEqual(result['completed_batches'], 3)
        self.assertEqual(result['provider_requests'], 18)
        self.assertEqual(result['completed_month_pairs'], 1)
        self.assertEqual(result['stop_reason'], 'waiting_for_upstream')
        self.assertEqual(len(events), 3)
        self.assertEqual(result['scores_promoted'], 0)
        self.assertFalse(result['effective_dates_certified'])
        for forbidden in ('corp_code', 'rcept_no', '00121932', '20161101000001', 'raw_provider'):
            self.assertNotIn(forbidden, json.dumps([result, events]))
        self.assertFalse(any(p.startswith('derived/dart-structured-action-receipts/')
                             for p in self.store.blobs))

    def test_budget_reserves_whole_transaction_and_restart_skips_committed_reports(self):
        first = c.continuous(self.store, self.fetch, max_requests=20)
        self.assertEqual(first['processed_candidates'], 4)
        self.assertEqual(first['provider_requests'], 8)
        self.assertEqual(first['stop_reason'], 'provider_budget')
        second = c.continuous(self.store, self.fetch)
        self.assertEqual(second['processed_candidates'], 5)
        self.assertEqual(second['provider_requests'], 10)
        self.assertEqual(len(self.calls), 18)

    def test_deadline_preserves_orphan_report_for_zero_duplicate_replay(self):
        now = [0]
        def fetch(*args):
            response = self.fetch(*args)
            if len(self.calls) == 2:
                now[0] = 5
            return response
        first = c.continuous(self.store, fetch, clock=lambda: now[0], max_seconds=5)
        self.assertEqual(first['stop_reason'], 'time_limit')
        self.assertEqual(first['provider_requests'], 2)
        self.assertNotIn(s.CHECKPOINT, self.store.blobs)
        self.assertIn(s._report_path(0, self.records[0]['rcept_no']), self.store.blobs)
        second = c.continuous(self.store, self.fetch)
        self.assertEqual(second['previous_reports_reused'], 1)
        self.assertEqual(second['provider_requests'], 16)
        self.assertEqual(len(self.calls), 18)

    def test_failed_provider_attempt_is_not_retried_or_saved_as_no_data(self):
        for status in ('020', '010'):
            with self.assertRaises(s.StructuredActionError):
                c.continuous(self.store, lambda *args: {'status': status})
            self.assertNotIn(s.CHECKPOINT, self.store.blobs)
        def fail(*args):
            self.calls.append(args)
            raise s.StructuredActionError('official_structured_action_transport_or_json_failed')
        with self.assertRaises(s.StructuredActionError):
            c.continuous(self.store, fail)
        self.assertEqual(len(self.calls), 1)

    def test_no_endpoint_and_empty_pair_progress_with_zero_provider_calls(self):
        for records in ([], [record('20161101000001', ['stock_dividend'])]):
            self.store = Private()
            self.store.blobs[s.TRIAGE_CHECKPOINT] = b'opaque triage'
            self.store.blobs[s.triage_path(0)] = b'opaque queue'
            with patch.object(s, '_candidate_queue', return_value=(records, 'a'*64, len(records))):
                result = c.continuous(self.store, self.fetch)
            self.assertEqual(result['provider_requests'], 0)
            self.assertEqual(result['completed_month_pairs'], 1)
            self.assertEqual(result['candidates_without_supplemental_endpoint'], len(records))

    def test_many_transactions_survive_restart_without_refetch(self):
        records = [record(f'20161101{i:06d}', ['bonus_issue']) for i in range(1, 518)]
        with patch.object(s, '_candidate_queue', return_value=(records, 'a'*64, len(records))):
            first = c.continuous(self.store, self.fetch)
            self.assertEqual(first['completed_batches'], 128)
            self.assertEqual(first['processed_candidates'], 512)
            self.assertEqual(first['provider_requests'], 512)
            self.assertEqual(first['stop_reason'], 'batch_limit')
            second = c.continuous(self.store, self.fetch)
        self.assertEqual(second['processed_candidates'], 5)
        self.assertEqual(second['provider_requests'], 5)
        self.assertEqual(len(self.calls), 517)

    def test_five_endpoint_worst_case_obeys_global_attempt_limit(self):
        hints = list(s.HINT_ENDPOINTS)
        records = [record(f'20161101{i:06d}', hints) for i in range(1, 200)]
        with patch.object(s, '_candidate_queue', return_value=(records, 'a'*64, len(records))):
            result = c.continuous(self.store, self.fetch)
        self.assertEqual(result['provider_requests'], 640)
        self.assertEqual(result['processed_candidates'], 128)
        self.assertEqual(result['stop_reason'], 'provider_budget')

    def test_complete_cursor_has_zero_calls_and_rejects_corrupted_tail(self):
        with patch.object(c, 'TARGET_PAIRS', 1):
            result = c.continuous(self.store, self.fetch)
            self.assertEqual(result['stop_reason'], 'complete')
            again = c.continuous(self.store, self.fetch)
            self.assertEqual(again['stop_reason'], 'complete')
            self.assertEqual(again['provider_requests'], 0)
            state = s._cursor(self.store.blobs[s.CHECKPOINT])
            self.store.blobs[state['last_report_file']] = b'corrupt'
            with self.assertRaises(s.ExtensionError):
                c.continuous(self.store, self.fetch)

    def test_external_cursor_changes_never_trigger_another_transaction(self):
        def mutate(event):
            self.store.blobs.pop(s.CHECKPOINT)
        with self.assertRaisesRegex(c.ExtensionContinuationError, 'cursor_changed'):
            c.continuous(self.store, self.fetch, emit=mutate)
        self.assertEqual(len(self.calls), 8)

    def test_false_certification_and_jump_or_inconsistent_counts_rejected(self):
        for key, value in (('corporate_action_effective_dates_certified', True),
                           ('scores_promoted', 1), ('provider_calls', 1),
                           ('next_pair_index', 2), ('source_pair_remaining_candidates', 0)):
            def corrupt(storage, fetch):
                result = s.run(storage, fetch)
                result[key] = value
                return result
            with self.subTest(key=key), self.assertRaises(c.ExtensionContinuationError):
                c.continuous(self.store, self.fetch, max_batches=1, collector=corrupt)
            self.store.blobs.pop(s.CHECKPOINT, None)

    def test_cursor_read_lag_reconciles_without_extra_source_calls(self):
        original = self.store.get_content
        lag = []
        def get(path):
            if path == s.CHECKPOINT and lag:
                return lag.pop(0), 'sha'
            return original(path)
        self.store.get_content = get
        def collect(storage, fetch):
            before = original(s.CHECKPOINT)[0]
            result = s.run(storage, fetch)
            if result['status'] == 'private_structured_action_supplement_completed':
                lag.extend([before, before])
            return result
        with patch.object(c.time, 'sleep'):
            result = c.continuous(self.store, self.fetch, collector=collect)
        self.assertEqual(result['provider_requests'], 18)

    def test_invalid_budgets_callbacks_or_destination_do_not_read_sources(self):
        for values in ({'max_batches': 129}, {'max_batches': True}, {'max_requests': 641},
                       {'max_requests': 19}, {'max_seconds': 3301}, {'clock': False},
                       {'collector': False}, {'emit': False}):
            with self.assertRaises(c.ExtensionContinuationError):
                c.continuous(self.store, self.fetch, **values)
        self.store.repository = 'untrusted/destination'
        with self.assertRaises(c.ExtensionContinuationError):
            c.continuous(self.store, self.fetch)
        self.assertEqual(self.calls, [])

    def test_workflow_has_offline_pr_and_bounded_shared_writer(self):
        text = Path('.github/workflows/v3-dart-extension-continuation.yml').read_text()
        offline, live = text.split('  private-continuation:', 1)
        self.assertNotIn('secrets.', offline)
        self.assertNotIn('concurrency:', offline)
        self.assertIn('needs: offline', live)
        self.assertIn('group: v3-dart-private-historical-event-receipts', live)
        self.assertIn('queue: max', live)
        self.assertIn('timeout-minutes: 65', live)
        self.assertIn("options: [dry_run, live]", text)
        self.assertIn("cron: '25 0,4,8,12,16,20 * * *'", text)
        self.assertNotIn('upload-artifact', text)
        self.assertNotIn('git push', text)
        self.assertNotIn('KRX_API_KEY', text)


if __name__ == '__main__':
    unittest.main()
