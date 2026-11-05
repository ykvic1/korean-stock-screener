"""Quota rejection must not become a tight write loop or a provider replay."""
import unittest
from unittest.mock import patch

import v3_private_dart_structured_action_receipts as action
from v3_dart_continuous_catchup import QuotaWaitingPrivateGitStorage
from v3_krx_private_github_backfill import PrivateHistoryError


def blocked(status):
    error = PrivateHistoryError('private_storage_api_failed')
    error.safe_http_status = status
    error.safe_retry_after_seconds = 65
    return error


class StorageCooldownTests(unittest.TestCase):
    def store(self, deadline=1000):
        ticks = [0]
        sleeps = []
        def sleep(seconds):
            ticks[0] += seconds
            sleeps.append(seconds)
        store = QuotaWaitingPrivateGitStorage(
            'ykvic1/private-research', 'synthetic-token-long-enough',
            deadline=deadline, clock=lambda: ticks[0], sleeper=sleep)
        return store, ticks, sleeps

    def test_quota_rejected_new_report_waits_then_reconciles_before_second_put(self):
        store, ticks, sleeps = self.store()
        events = []
        def get(path):
            events.append('read')
            return None, None
        def put(*args):
            events.append('write')
            if events.count('write') == 1:
                raise blocked('http_403_rate_limited')
        with patch.object(store, 'get_content', side_effect=get), patch.object(store, 'put_new', side_effect=put):
            action._put_immutable_report_with_retry(store, 'report', b'validated')
        self.assertEqual(events, ['read', 'write', 'read', 'write'])
        self.assertEqual(ticks[0], 65)
        self.assertTrue(all(s <= 30 for s in sleeps))

    def test_checkpoint_readback_adopts_saved_write_without_overwriting(self):
        store, ticks, _ = self.store()
        with patch.object(store, 'get_content', side_effect=[(b'old', 'sha1'), (b'new', 'sha2')]) as get, \
             patch.object(store, 'transport', side_effect=blocked('http_429')) as put:
            action._write_checkpoint_with_retry(store, b'old', b'new')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(put.call_count, 1)
        self.assertEqual(ticks[0], 65)

    def test_auth_conflict_unknown_and_deadline_errors_stop_without_retry(self):
        for status in ('http_401', 'http_403', 'http_422', 'http_other', 'http_429'):
            for checkpoint in (False, True):
                with self.subTest(status=status, checkpoint=checkpoint):
                    store, _, sleeps = self.store(deadline=90)
                    with patch.object(store, 'get_content', side_effect=blocked(status)) as get:
                        with self.assertRaises(PrivateHistoryError):
                            if checkpoint:
                                action._write_checkpoint_with_retry(store, b'old', b'new')
                            else:
                                action._put_immutable_report_with_retry(store, 'report', b'new')
                    self.assertEqual(get.call_count, 1)
                    self.assertEqual(sleeps, [])

    def test_repository_head_409_retries_only_after_unchanged_cursor_readback(self):
        store, _, _ = self.store()
        with patch.object(store, 'get_content', side_effect=[(b'old', 'sha1'), (b'old', 'sha1')]) as get, \
             patch.object(store, 'transport', side_effect=[blocked('http_409'), {}]) as put, \
             patch.object(action.time, 'sleep'):
            action._write_checkpoint_with_retry(store, b'old', b'new')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(put.call_count, 2)

    def test_repository_head_409_never_overwrites_a_concurrent_cursor(self):
        store, _, _ = self.store()
        with patch.object(store, 'get_content', side_effect=[(b'old', 'sha1'), (b'other', 'sha2')]) as get, \
             patch.object(store, 'transport', side_effect=blocked('http_409')) as put, \
             patch.object(action.time, 'sleep'):
            with self.assertRaisesRegex(action.StructuredActionError, 'checkpoint_changed'):
                action._write_checkpoint_with_retry(store, b'old', b'new')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(put.call_count, 1)

    def test_quota_wait_still_rejects_a_conflicting_checkpoint(self):
        store, _, _ = self.store()
        with patch.object(store, 'get_content', side_effect=[(b'old', 'sha1'), (b'other-writer', 'sha2')]), \
             patch.object(store, 'transport', side_effect=blocked('http_429')) as put:
            with self.assertRaisesRegex(action.StructuredActionError, 'checkpoint_changed'):
                action._write_checkpoint_with_retry(store, b'old', b'new')
        self.assertEqual(put.call_count, 1)


if __name__ == '__main__':
    unittest.main()
