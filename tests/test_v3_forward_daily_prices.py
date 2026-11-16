"""Regress forward price session cutoff, gap retention and private reuse."""
from datetime import date
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from official_probe.clients import KRXProbeError
from tests.test_v3_forward_entry import MemoryStore, quote
from v3_forward_daily_prices import (
    DailyEvidenceError, collect_daily, completed_sessions, daily_row, official_publish_time, save_daily,
)
from v3_forward_entry import timestamp


def pair(day):
    return ({
        '005930': ('KOSPI', {**quote(), 'LIST_SHRS': '500'}),
        '123456': ('KOSDAQ', quote(code='123456', volume='0')),
    }, {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64})


class ForwardDailyPriceTest(unittest.TestCase):
    def test_signal_session_is_collected_before_the_entry_session_closes(self):
        storage, calls = MemoryStore(), []
        cohort = [{'score_asof': '2026-11-13', 'entry_session_candidate': '2026-11-16'}]
        now = timestamp('2026-11-16T01:30:00+00:00')
        with TemporaryDirectory() as root, patch('v3_forward_daily_prices.OUT', Path(root)):
            with patch('builtins.print'):
                collect_daily(cohort, now, lambda: storage,
                              lambda day: (calls.append(day), pair(day))[1], clock=lambda: now)
        self.assertEqual(calls, ['2026-11-13'])

    def test_official_daily_availability_starts_next_business_day_at_eight(self):
        self.assertEqual(official_publish_time(date(2026, 11, 13)),
                         timestamp('2026-11-15T23:00:00+00:00'))
        self.assertEqual(official_publish_time(date(2026, 7, 16)),
                         timestamp('2026-07-19T23:00:00+00:00'))
        cohort = [{'entry_session_candidate': '2026-11-16'}]
        storage = MemoryStore()
        before = timestamp('2026-11-16T22:59:59+00:00')
        at_cutoff = timestamp('2026-11-16T23:00:00+00:00')
        with TemporaryDirectory() as root, patch('v3_forward_daily_prices.OUT', Path(root)):
            with patch('builtins.print'), patch(__name__ + '.pair', wraps=pair) as query:
                collect_daily(cohort, before, lambda: storage, query, clock=lambda: before)
                query.assert_not_called()
                self.assertEqual(storage.blobs, {})
                collect_daily(cohort, at_cutoff, lambda: storage, query, clock=lambda: at_cutoff)
                query.assert_called_once_with('2026-11-16')

    def test_close_cutoff_exam_day_and_observed_holiday_override(self):
        exam = date(2026, 11, 19)
        self.assertEqual(list(completed_sessions(
            exam, exam, timestamp('2026-11-19T07:29:59+00:00'))), [])
        self.assertEqual(list(completed_sessions(
            exam, exam, timestamp('2026-11-19T07:30:00+00:00'))), ['2026-11-19'])
        holiday = date(2026, 7, 17)
        self.assertEqual(list(completed_sessions(
            holiday, holiday, timestamp('2026-07-17T09:00:00+00:00'))), [])

    def test_does_not_treat_zero_volume_or_invalid_close_as_trade(self):
        self.assertEqual(daily_row('KOSPI', quote(volume='0'))[2:7],
                         ['no_reported_volume', None, None, None, None])
        row = {**quote(), 'TDD_CLSPRC': '999999', 'LIST_SHRS': '50,000'}
        self.assertEqual(daily_row('KOSPI', row)[2:],
                         ['price_unavailable', None, None, None, None, 50000])

    def test_private_prices_and_share_counts_survive_round_trip(self):
        day = '2026-11-16'
        storage = MemoryStore()
        recorded = timestamp('2026-11-16T11:45:00+00:00')
        with self.assertRaisesRegex(DailyEvidenceError, 'invalid_private_daily_price_archive'):
            save_daily(day, storage, pair, clock=lambda: timestamp('2026-11-16T06:29:59+00:00'))
        result = save_daily(day, storage, pair, clock=lambda: recorded)
        self.assertEqual(result['daily_row_count'], 2)
        self.assertEqual(result['market_price_counts']['KOSDAQ']['no_reported_volume'], 1)
        self.assertEqual(result['unavailable_listed_shares_count'], 1)
        self.assertNotIn('rows', result)
        self.assertNotIn('005930', json.dumps(result))
        self.assertFalse(result['corporate_actions_certified'])

        with patch(__name__ + '.pair', side_effect=AssertionError('source repeated')):
            self.assertEqual(save_daily(day, storage, pair), result)
        path, blob = next(iter(storage.blobs.items()))
        stored = json.loads(gzip.decompress(blob))
        self.assertEqual(stored['rows']['005930'][3:], [50000, 52000, 48000, 51000, 500])
        stored['rows']['123456'][3] = 0
        storage.blobs[path] = gzip.compress(json.dumps(stored).encode())
        with self.assertRaisesRegex(DailyEvidenceError, 'unavailable_private_daily_price_has_value'):
            save_daily(day, storage, pair)

    def test_missing_earlier_pair_does_not_drop_subsequent_sessions(self):
        storage = MemoryStore()
        cohort = [{'entry_session_candidate': '2026-11-16'}]
        now = timestamp('2026-11-18T00:05:00+00:00')
        calls = []

        def partially_available(day):
            calls.append(day)
            if day == '2026-11-16':
                raise KRXProbeError('KOSDAQ', 'empty_market_rows')
            return pair(day)

        with TemporaryDirectory() as root, patch('v3_forward_daily_prices.OUT', Path(root)):
            with patch('builtins.print'):
                collect_daily(cohort, now, lambda: storage, partially_available, clock=lambda: now)
            self.assertEqual(calls, ['2026-11-16', '2026-11-17'])
            pending = json.loads((Path(root) / '2026-11-16.json').read_text())
            self.assertEqual(pending['status'], 'waiting_for_official_paired_response')
            with patch('builtins.print'):
                collect_daily(cohort, now, lambda: storage, pair, clock=lambda: now)
            completed = json.loads((Path(root) / '2026-11-16.json').read_text())
            self.assertEqual(completed['status'], 'archived_privately')

    def test_transient_source_failure_retries_without_archiving_partial_market(self):
        cohort = [{'entry_session_candidate': '2026-11-16'}]
        now = timestamp('2026-11-18T00:05:00+00:00')
        for reason in ('transport_failure', 'http_server_error', 'http_rate_limited'):
            with self.subTest(reason=reason), TemporaryDirectory() as root, patch(
                    'v3_forward_daily_prices.OUT', Path(root)), patch('builtins.print'):
                storage = MemoryStore()

                def partial(day):
                    if day == '2026-11-16':
                        raise KRXProbeError('KOSPI', reason)
                    return pair(day)

                collect_daily(cohort, now, lambda: storage, partial, clock=lambda: now)
                pending = Path(root) / '2026-11-16.json'
                self.assertEqual(json.loads(pending.read_text())['source_reason'], reason)
                self.assertEqual(len(storage.blobs), 1)
                collect_daily(cohort, now, lambda: storage, pair, clock=lambda: now)
                self.assertEqual(json.loads(pending.read_text())['status'], 'archived_privately')
                self.assertEqual(len(storage.blobs), 2)

        with TemporaryDirectory() as root, patch('v3_forward_daily_prices.OUT', Path(root)):
            def unauthorized(_day):
                raise KRXProbeError('KOSPI', 'http_unauthorized')

            with self.assertRaisesRegex(DailyEvidenceError, 'krx_http_unauthorized'):
                collect_daily(cohort, now, lambda: MemoryStore(), unauthorized, clock=lambda: now)
            self.assertEqual(list(Path(root).glob('*.json')), [])
