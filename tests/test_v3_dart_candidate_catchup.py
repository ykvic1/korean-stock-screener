"""Fault tests for request reuse without hiding mutable source changes."""
from hashlib import sha1
import unittest
from unittest.mock import patch

import v3_dart_candidate_catchup as c


def version(raw):
    return sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def result(status):
    return {'status': status, 'provider_calls': 0, 'scores_promoted': 0,
            'confirmed_corporate_actions': 0}


class CandidateCatchupTests(unittest.TestCase):
    def store(self):
        return c.ReusingPrivateStorage(c.monthly.PRIVATE_REPO, 'synthetic-token-at-least-20', deadline=10000)

    def test_repeated_input_reads_reuse_bytes_but_cursors_and_outputs_stay_live(self):
        store = self.store()
        immutable = 'derived/dart-filing-receipts/2025-01/B/0001.json.gz'
        mutable = c.monthly.DART_CHECKPOINT
        report = c.monthly.OUT + '/2025-01.json'
        calls = []
        def remote(_, path):
            calls.append(path)
            raw = b'validated-source' if path == immutable else str(len(calls)).encode()
            return raw, version(raw)
        with patch.object(c.PrivateGitStorage, 'get_content', autospec=True, side_effect=remote):
            first = store.get_content(immutable)
            for _ in range(12):
                self.assertEqual(store.get_content(immutable), first)
            self.assertNotEqual(store.get_content(mutable), store.get_content(mutable))
            self.assertNotEqual(store.get_content(report), store.get_content(report))
        self.assertEqual(calls.count(immutable), 1)
        self.assertEqual(store.cache_hits, 12)

    def test_missing_input_and_oversized_cache_entry_are_not_cached(self):
        store = self.store()
        path = 'derived/quality/price-discontinuities-2016-12-01.json.gz'
        raw = b'newly-committed'
        with patch.object(c.PrivateGitStorage, 'get_content', side_effect=[(None, None), (raw, version(raw))]) as get:
            self.assertEqual(store.get_content(path), (None, None))
            self.assertEqual(store.get_content(path)[0], raw)
        self.assertEqual(get.call_count, 2)
        store = self.store()
        with patch.object(c, 'MAX_CACHE_BYTES', 1), patch.object(c.PrivateGitStorage, 'get_content', return_value=(raw, version(raw))) as get:
            store.get_content(path)
            store.get_content(path)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(store.cache_bytes, 0)

    def test_invalid_blob_identity_fails_without_caching(self):
        store = self.store()
        path = 'derived/dart-filing-receipts/2025-01/B/0001.json.gz'
        with patch.object(c.PrivateGitStorage, 'get_content', return_value=(b'tampered', '0'*40)):
            with self.assertRaisesRegex(c.PrivateHistoryError, 'blob_mismatch'):
                store.get_content(path)
        self.assertEqual(store.cache_bytes, 0)

    def test_waiting_stops_phase_polling_but_later_phases_can_progress(self):
        calls = []
        def waiting(_):
            calls.append('waiting')
            return result('waiting_for_complete_official_filing_month')
        def success(_):
            calls.append('success')
            return result('completed')
        summary = c.catchup(None, phases=(('a', waiting, 'completed'), ('b', success, 'completed')))
        self.assertEqual(calls, ['waiting'] + ['success']*4)
        self.assertEqual(summary['completed_segments'], 4)

    def test_validation_failure_or_deadline_never_replays_transaction(self):
        from unittest.mock import Mock
        collect = Mock(side_effect=c.monthly.CrosscheckError('source_cursors_changed_during_match'))
        with self.assertRaises(c.monthly.CrosscheckError):
            c.catchup(None, phases=(('a', collect, 'completed'),))
        self.assertEqual(collect.call_count, 1)
        collect.reset_mock()
        summary = c.catchup(None, phases=(('a', collect, 'completed'),), clock=lambda: 10, deadline=10)
        self.assertEqual(summary['stop_reason'], 'time_limit')
        collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
