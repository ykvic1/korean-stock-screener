"""Regress frozen-source integrity, independent closes and resumable private evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_v3_forward_entry import MemoryStore, timestamp
from v3_forward_daily_prices import SCHEMA as DAILY_SCHEMA
import v3_forward_signal_close as signal


class SignalCloseTest(unittest.TestCase):
    def setUp(self):
        self.day = '2026-11-13'
        self.market = {'date': self.day, 'stocks': [
            {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI',
             'price_date': self.day, 'price': 100},
            {'code': '123456', 'name': '코스닥', 'market': 'KOSDAQ',
             'price_date': self.day, 'price': 200},
        ]}
        self.observation = {'score_asof': self.day, 'source_commit': 'a' * 40, 'rows': {
            '005930': ['삼성전자', 'KOSPI', 60, 1],
            '123456': ['코스닥', 'KOSDAQ', 50, 2],
            '12345A': ['미관측', 'KOSDAQ', None, None],
        }}
        self.raw = json.dumps(self.market, ensure_ascii=False).encode()
        self.observation['source_market_sha256'] = sha256(self.raw).hexdigest()
        self.daily = {'date': self.day, 'source_sha256': {'KOSPI': 'b' * 64, 'KOSDAQ': 'c' * 64},
                      'rows': {'005930': ['KOSPI', '삼성전자', 'traded', 90, 110, 80, 100, 500],
                               '123456': ['KOSDAQ', '코스닥', 'traded', 190, 220, 180, 200, 500]}}

    def test_full_census_is_retained_and_disagreements_remain_explicit(self):
        for stock_change, daily_change, expected in (
            ({}, {}, 'matched'),
            ({'price_date': '2026-11-12'}, {}, 'published_price_stale'),
            ({'price': None}, {}, 'published_price_unavailable'),
            ({'name': '다른회사'}, {}, 'identity_unresolved'),
            ({}, {'name': '다른회사'}, 'identity_unresolved'),
            ({'price': 101}, {}, 'close_mismatch'),
            ({}, {'state': 'no_reported_volume'}, 'no_reported_volume'),
            ({}, {'state': 'price_unavailable'}, 'official_price_unavailable'),
        ):
            with self.subTest(expected=expected):
                market, daily = deepcopy(self.market), deepcopy(self.daily)
                market['stocks'][0].update(stock_change)
                if 'name' in daily_change:
                    daily['rows']['005930'][1] = daily_change['name']
                if 'state' in daily_change:
                    daily['rows']['005930'][2:7] = [daily_change['state'], None, None, None, None]
                raw = json.dumps(market, ensure_ascii=False).encode()
                observation = {**self.observation, 'source_market_sha256': sha256(raw).hexdigest()}
                summary, cases = signal.compare_closes(observation, raw, daily)
                self.assertEqual(cases['005930']['state'], expected)
                self.assertEqual(cases['12345A']['state'], 'published_row_absent')
                self.assertEqual(sum(summary['states'].values()), 3)
                self.assertNotIn('005930', json.dumps(summary))
                self.assertFalse(summary['all_ohlcv_regular_session_certified'])
                if expected == 'close_mismatch':
                    self.assertEqual(cases['005930']['published_close'], 101)
                    self.assertEqual(cases['005930']['official_close'], 100)

    def test_wrong_hash_or_duplicate_market_rows_are_rejected(self):
        with self.assertRaisesRegex(signal.SignalCloseError, 'frozen_market_hash_mismatch'):
            signal.compare_closes(self.observation, self.raw + b' ', self.daily)
        self.market['stocks'].append(self.market['stocks'][0])
        raw = json.dumps(self.market).encode()
        obs = {**self.observation, 'source_market_sha256': sha256(raw).hexdigest()}
        with self.assertRaisesRegex(signal.SignalCloseError, 'invalid_signal_close_source'):
            signal.compare_closes(obs, raw, self.daily)

    def test_frozen_market_recovers_the_original_commit_after_current_source_advances(self):
        with TemporaryDirectory() as tmp, patch.object(signal, 'MARKET_PATH', str(Path(tmp) / 'data.json')):
            Path(signal.MARKET_PATH).write_bytes(self.raw)
            with patch.object(signal.subprocess, 'check_output') as git:
                self.assertEqual(signal.frozen_market(self.observation), self.raw)
                git.assert_not_called()
            Path(signal.MARKET_PATH).write_bytes(b'new source')
            with (patch.object(signal.subprocess, 'check_output', side_effect=[
                      subprocess.CalledProcessError(128, 'git'), self.raw]),
                  patch.object(signal.subprocess, 'run') as fetch):
                self.assertEqual(signal.frozen_market(self.observation), self.raw)
                self.assertEqual(fetch.call_args.args[0][-1], 'a' * 40)

    def test_collection_waits_then_reuses_evidence_and_versions_corrected_sources(self):
        storage = MemoryStore()
        raw = json.dumps(self.observation).encode()
        entry = {'score_asof': self.day, 'score_observation_sha256': sha256(raw).hexdigest()}
        now = timestamp('2026-11-16T01:30:00+00:00')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, daily_status, out = root / 'scores', root / 'daily', root / 'out'
            archive.mkdir()
            daily_status.mkdir()
            (archive / f'{self.day}.json').write_bytes(raw)

            def publish_daily():
                (daily_status / f'{self.day}.json').write_text(json.dumps({
                    'schema': DAILY_SCHEMA, 'date': self.day, 'status': 'archived_privately',
                    'source_sha256': self.daily['source_sha256'],
                }))

            with (patch.object(signal, 'ARCHIVE', archive), patch.object(signal, 'DAILY_STATUS', daily_status),
                  patch.object(signal, 'OUT', out), patch.object(signal, 'frozen_market', return_value=self.raw),
                  patch.object(signal, 'read_daily', side_effect=lambda *args: self.daily) as reader,
                  patch('builtins.print')):
                signal.collect_signal_closes([entry], now, lambda: storage)
                reader.assert_not_called()
                target = out / f'{self.day}.json'
                self.assertEqual(json.loads(target.read_bytes())['status'], 'waiting_for_signal_daily_archive')
                publish_daily()
                signal.collect_signal_closes([entry], now, lambda: storage)
                self.assertEqual(json.loads(target.read_bytes())['states']['matched'], 2)
                original_blobs = dict(storage.blobs)
                reader.reset_mock()
                signal.collect_signal_closes([entry], now, lambda: storage)
                reader.assert_not_called()
                target.unlink()
                signal.collect_signal_closes([entry], now, lambda: storage)
                self.assertEqual(storage.blobs, original_blobs)
                self.daily['source_sha256']['KOSPI'] = 'd' * 64
                self.daily['rows']['005930'][6] = 101
                publish_daily()
                signal.collect_signal_closes([entry], now, lambda: storage)
                self.assertEqual(len(storage.blobs), 2)
                self.assertEqual(json.loads(target.read_bytes())['states']['close_mismatch'], 1)
                for path, blob in original_blobs.items():
                    self.assertEqual(storage.blobs[path], blob)
