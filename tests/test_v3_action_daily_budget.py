"""Private budget reservations, ambiguous writes and real runner integration."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import v3_action_daily_budget as b
import v3_dart_structured_action_continuation as principal
import v3_dart_extension_continuation as supplement
from v3_krx_private_github_backfill import PrivateHistoryError
from test_v3_private_dart_structured_action_extension import Private, record

DAY = '2026-11-05'


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.store = Private()

    def reserve(self, kind='principal', amount=1536, day=DAY):
        return b.reserve(self.store, kind, amount, day=day)

    def test_crashed_runs_remain_fully_charged_and_seventh_run_is_denied(self):
        tickets = [self.reserve() for _ in range(6)]
        self.assertTrue(all(t['reserved'] == 1536 for t in tickets))
        self.assertIsNone(self.reserve())
        raw = self.store.blobs[b._path('principal', DAY)]
        book = b._read(raw, 'principal', DAY)
        self.assertEqual(b._charged(book), 9216)
        self.assertTrue(all(e['used'] is None for e in book['reservations'].values()))

    def test_verified_usage_releases_only_unused_capacity_and_settlement_is_idempotent(self):
        ticket = self.reserve()
        self.assertEqual(b.finish(self.store, ticket, 378), 9216 - 378)
        self.assertEqual(b.finish(self.store, ticket, 378), 9216 - 378)
        with self.assertRaises(b.DailyBudgetError):
            b.finish(self.store, ticket, 0)
        with self.assertRaises(b.DailyBudgetError):
            b.finish(self.store, ticket, 1537)
        with self.assertRaises(b.DailyBudgetError):
            b.finish(self.store, ticket, True)

    def test_partial_grant_reserves_whole_existing_transaction(self):
        self.reserve(amount=9204)
        last = self.reserve()
        self.assertEqual(last['reserved'], 12)
        self.assertIsNone(self.reserve())

    def test_principal_supplement_and_next_day_have_separate_verified_limits(self):
        self.reserve(amount=9216)
        supplemental = self.reserve('supplemental', 3840)
        self.assertEqual(supplemental['reserved'], 3840)
        self.assertIsNone(self.reserve('supplemental', 640))
        self.assertEqual(self.reserve(day='2026-11-06')['reserved'], 1536)
        self.assertTrue(all(p.startswith('derived/action-continuation-budgets/')
                            for p in self.store.blobs))

    def test_lost_successful_reservation_write_is_adopted_without_double_charge(self):
        original = self.store.put_new
        once = [True]
        def lost(*args):
            original(*args)
            if once[0]:
                once[0] = False
                raise PrivateHistoryError('private_storage_api_failed')
        self.store.put_new = lost
        with patch('v3_private_dart_structured_action_receipts.time.sleep'):
            ticket = self.reserve()
        book = b._read(self.store.blobs[b._path('principal', DAY)], 'principal', DAY)
        self.assertEqual(len(book['reservations']), 1)
        self.assertEqual(b._charged(book), ticket['reserved'])

    def test_concurrent_budget_change_is_never_overwritten(self):
        path = b._path('principal', DAY)
        external = b._read(None, 'principal', DAY)
        external['reservations']['a'*32] = {'reserved': 1536, 'used': None}
        encoded = json.dumps(external).encode()
        original = self.store.get_content
        reads = [0]
        def change(location):
            if location == path:
                reads[0] += 1
                if reads[0] == 2:
                    self.store.blobs[path] = encoded
            return original(location)
        self.store.get_content = change
        with self.assertRaises(principal.source.StructuredActionError):
            self.reserve()
        self.assertEqual(self.store.blobs[path], encoded)

    def test_corrupt_and_overcharged_budget_cannot_be_reset(self):
        ticket = self.reserve()
        path = b._path('principal', DAY)
        original = json.loads(self.store.blobs[path])
        for mutate in (lambda x: x.update(limit=100000),
                       lambda x: x['reservations'][ticket['token']].update(used=-1),
                       lambda x: x['reservations'][ticket['token']].update(reserved=10000)):
            book = json.loads(json.dumps(original))
            mutate(book)
            self.store.blobs[path] = json.dumps(book).encode()
            with self.assertRaises(b.DailyBudgetError):
                self.reserve()

    def test_bad_destination_day_and_bool_budgets_fail_before_source_use(self):
        for values in (('principal', True, DAY), ('principal', 5, DAY),
                       ('supplemental', 19, DAY), ('untrusted', 20, DAY),
                       ('principal', 20, '2026-02-30')):
            with self.assertRaises(b.DailyBudgetError):
                self.reserve(*values)
        self.store.repository = 'untrusted/destination'
        with self.assertRaises(b.DailyBudgetError):
            self.reserve()
        self.assertEqual(self.store.blobs, {})

    def test_runtime_stops_before_utc_day_boundary_and_never_reuses_old_day(self):
        def at(hour, minute, second=0):
            return datetime(2026, 11, 5, hour, minute, second, tzinfo=timezone.utc)
        self.assertEqual(b.runtime_seconds(DAY, now=at(12, 0)), 3300)
        self.assertEqual(b.runtime_seconds(DAY, now=at(23, 58, 30)), 30)
        self.assertEqual(b.runtime_seconds(DAY, now=at(23, 59, 30)), 0)
        self.assertEqual(b.runtime_seconds('2026-11-04', now=at(12, 0)), 0)
        with self.assertRaises(b.DailyBudgetError):
            b.runtime_seconds(DAY, now=datetime(2026, 11, 5))

    def test_only_successful_bounded_progress_with_remaining_budget_can_resume(self):
        good = dict(stop_reason='batch_limit', completed_batches=1,
                    completed_month_pairs=1, total_month_pairs=119)
        self.assertTrue(b.resumable(good, 6, 6))
        for change, remaining in (({'stop_reason': 'complete'}, 100),
                                  ({'stop_reason': 'waiting_for_upstream'}, 100),
                                  ({'completed_batches': 0}, 100),
                                  ({'completed_month_pairs': 119}, 100), ({}, 5)):
            self.assertFalse(b.resumable({**good, **change}, remaining, 6))


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = Private()
        self.records = [record(f'20161101{i:06d}', ['rights_issue']) for i in range(1, 10)]
        self.output_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.output_dir.cleanup)
        self.output = Path(self.output_dir.name) / 'outputs'
        self.environment = patch.dict(os.environ, {
            'DART_API_KEY': 'x'*40, 'V3_PRIVATE_ARCHIVE_REPO': principal.source.PRIVATE_REPO,
            'V3_PRIVATE_ARCHIVE_TOKEN': 'x'*20, 'GITHUB_OUTPUT': str(self.output),
            'V3_ACTION_CONTINUATION_BATCHES': '1', 'V3_EXTENSION_CONTINUATION_BATCHES': '1',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def prepared(self, module):
        s = module.source
        self.store.blobs[s.TRIAGE_CHECKPOINT] = b'opaque cursor'
        self.store.blobs[s.triage_path(0)] = b'opaque queue'
        patches = [patch.object(module, 'QuotaWaitingPrivateGitStorage', return_value=self.store),
                   patch.object(s, 'triage_cursor', return_value={'next_pair_index': 1}),
                   patch.object(s, '_candidate_queue', return_value=(self.records, 'a'*64, 9)),
                   patch.object(b, 'utc_day', return_value=DAY),
                   patch.object(b, 'runtime_seconds', return_value=3300)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_primary_success_records_actual_calls_and_requests_immediate_resume(self):
        self.prepared(principal)
        with patch.object(principal.source, 'official_fetch', return_value={'status': '013'}) as fetch:
            with redirect_stdout(io.StringIO()):
                principal.main()
        self.assertEqual(fetch.call_count, 6)
        self.assertEqual(self.output.read_text().splitlines()[-1], 'resume=true')
        book = b._read(self.store.blobs[b._path('principal', DAY)], 'principal', DAY)
        self.assertEqual(b._charged(book), 6)

    def test_primary_exhausted_daily_budget_never_calls_provider_or_resumes(self):
        self.prepared(principal)
        b.reserve(self.store, 'principal', 9216, day=DAY)
        with patch.object(principal.source, 'official_fetch') as fetch:
            with redirect_stdout(io.StringIO()):
                principal.main()
        fetch.assert_not_called()
        self.assertEqual(self.output.read_text().strip(), 'resume=false')

    def test_supplemental_success_settles_lower_actual_endpoint_usage(self):
        self.prepared(supplement)
        with patch.object(supplement.source, 'official_fetch', return_value={'status': '013'}) as fetch:
            with redirect_stdout(io.StringIO()):
                result = supplement.main()
        self.assertEqual(result, 0)
        self.assertEqual(fetch.call_count, 8)
        self.assertEqual(self.output.read_text().splitlines()[-1], 'resume=true')
        book = b._read(self.store.blobs[b._path('supplemental', DAY)], 'supplemental', DAY)
        self.assertEqual(b._charged(book), 8)

    def test_quota_failure_keeps_full_reservation_and_cannot_spawn_retry_loop(self):
        self.prepared(supplement)
        with patch.object(supplement.source, 'official_fetch', return_value={'status': '020'}) as fetch:
            with redirect_stdout(io.StringIO()):
                result = supplement.main()
        self.assertEqual(result, 1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.output.read_text().strip(), 'resume=false')
        book = b._read(self.store.blobs[b._path('supplemental', DAY)], 'supplemental', DAY)
        self.assertEqual(b._charged(book), 20)
        self.assertIsNone(next(iter(book['reservations'].values()))['used'])

    def test_live_workflows_dispatch_only_on_verified_resume_output(self):
        for filename in ('v3-dart-extension-continuation.yml',
                         'v3-dart-structured-action-continuation.yml'):
            text = (Path('.github/workflows') / filename).read_text()
            offline, live = text.split('  private-continuation:', 1)
            self.assertNotIn('actions: write', offline)
            self.assertNotIn('secrets.', offline)
            self.assertIn('actions: write', live)
            self.assertIn("if: steps.collect.outputs.resume == 'true'", live)
            self.assertIn('gh workflow run ' + filename, live)
            self.assertIn('--ref main -f mode=live', live)
            self.assertNotIn('continue-on-error:', live)


if __name__ == '__main__':
    unittest.main()
