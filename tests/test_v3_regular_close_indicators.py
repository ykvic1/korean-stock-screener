"""Regress private history reuse, source continuity and unchanged factor arithmetic."""
from copy import deepcopy
from hashlib import sha256
import gzip
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests.test_v3_forward_entry import MemoryStore, quote
import v3_regular_close_indicators as history
from v3_pattern_first_observations import rsi_observation
from v3_ma_slope_transition import _series_points


class FakeClient:
    def __init__(self, market):
        self.market, self.calls = market, []

    def daily_market(self, market, day):
        self.calls.append(day)
        code, name = ('005930', '삼성전자') if market == 'KOSPI' else ('123456', '코스닥')
        rows = [{**quote(code=code, name=name, day=day.replace('-', '')),
                 'LIST_SHRS': '500000', 'CMPPREVDD_PRC': '1000'}]
        return rows, json.dumps({'OutBlock_1': rows}).encode()


class RegularCloseIndicatorsTest(unittest.TestCase):
    def test_operational_import_requires_no_collection_dependencies(self):
        subprocess.run([sys.executable, '-S', '-c', 'import v3_operational_research_score'],
                       cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True)

    def setUp(self):
        self.days = [d.date().isoformat() for d in pd.bdate_range(end='2026-11-13', periods=200)]
        self.catalog = {'date': self.days[-1], 'rows': [
            {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI'},
            {'code': '123456', 'name': '코스닥', 'market': 'KOSDAQ'},
        ]}
        self.digest = sha256(json.dumps(self.catalog, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
        self.close = [int(value) for value in np.r_[np.linspace(220, 90, 180), np.linspace(92, 130, 20)]]
        self.panels = {}
        for i, day in enumerate(self.days):
            self.panels[day] = {'schema': history.DAY_SCHEMA, 'date': day,
                'source_sha256': {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64},
                'rows': {'005930': ['KOSPI', '삼성전자', self.close[i], 500,
                                    self.close[i-1] if i else None],
                         '123456': ['KOSDAQ', '코스닥', self.close[i] * 2, 500,
                                    self.close[i-1] * 2 if i else None]}}

    def test_ma_rsi_and_turn_reuse_the_existing_formulas(self):
        source = history.build(self.catalog, self.digest, self.days, self.panels)
        row = source['rows']['005930']
        self.assertEqual(row['continuous_sessions'], 200)
        self.assertEqual(row['moving_averages']['20'], round(float(np.mean(self.close[-20:])), 6))
        self.assertEqual(row['daily_rsi'], rsi_observation(np.asarray(self.close, dtype=float)))
        self.assertEqual(row['daily_ma_turn'], _series_points(
            np.asarray(self.close, dtype=float), prior=60, recent=20, hold=5,
            floor=.0001, frame='daily', trusted_window=lambda required: True))
        self.assertEqual(len(history.admitted_rows(source, self.catalog)), 2)
        self.assertNotIn('close_series', json.dumps(source))
        self.assertEqual(source['counts']['full_200_session_continuity'], 2)

    def test_missing_session_or_unresolved_price_basis_is_never_bridged(self):
        for cause in ('missing', 'reference', 'identity'):
            panels = deepcopy(self.panels)
            day = self.days[-31]
            if cause == 'missing':
                panels[day] = None
            elif cause == 'reference':
                panels[day]['rows']['005930'][4] = 999
            else:
                panels[day]['rows']['005930'][1] = '다른회사'
            with self.subTest(cause=cause):
                source = history.build(self.catalog, self.digest, self.days, panels)
                row = source['rows']['005930']
                self.assertIn(row['continuous_sessions'], (30, 31))
                self.assertIsNotNone(row['moving_averages']['20'])
                self.assertIsNone(row['moving_averages']['60'])
                self.assertIsNone(row['daily_rsi'])
                self.assertIsNone(row['daily_ma_turn'])

    def test_forged_source_scope_or_unearned_periods_are_rejected(self):
        source = history.build(self.catalog, self.digest, self.days, self.panels)
        for field, value in (('source_catalog_sha256', 'f' * 64),
                             ('asof', '2026-11-12'), ('performance_validated', True)):
            self.assertEqual(history.admitted_rows({**source, field: value}, self.catalog), {})
        for mutation in (
            lambda row: row.update(continuous_sessions=90),
            lambda row: row['daily_ma_turn'].update({'120': 1.0}),
            lambda row: row['daily_rsi'].update(rsi14=float('nan')),
        ):
            changed = deepcopy(source)
            mutation(changed['rows']['005930'])
            self.assertNotIn('005930', history.admitted_rows(changed, self.catalog))

    def test_cache_round_trip_reuses_prices_and_resumes_after_partial_batch(self):
        storage = MemoryStore()
        clients = {market: FakeClient(market) for market in history.MARKETS}
        days = self.days[-3:]
        with patch('builtins.print'):
            panels, added, failed = history.collect(storage, clients, days, limit=2)
            self.assertEqual(added, 2)
            self.assertEqual(failed, {})
            self.assertIsNone(panels[days[0]])
            self.assertEqual(panels[days[-1]]['rows']['005930'][4], 50000)
            panels, added, _ = history.collect(storage, clients, days, limit=2)
            self.assertEqual(added, 1)
            self.assertTrue(all(panels.values()))
            _, added, _ = history.collect(storage, clients, days, limit=2)
            self.assertEqual(added, 0)
            self.assertEqual(sum(len(client.calls) for client in clients.values()), 6)
        self.assertEqual(len(storage.blobs), 3)
        path = history.archive_path(days[0])
        data = json.loads(gzip.decompress(storage.blobs[path]))
        data['rows']['005930'][2] = 0
        with self.assertRaisesRegex(history.CloseHistoryError, 'invalid_private_regular_close_day'):
            history.read_day(gzip.compress(json.dumps(data).encode()), days[0])

    def test_announced_closures_are_excluded_from_the_requested_window(self):
        days = history.history_dates('2026-11-13')
        self.assertEqual((len(days), days[-1]), (200, '2026-11-13'))
        self.assertNotIn('2026-06-03', days)
        self.assertNotIn('2026-07-17', days)
