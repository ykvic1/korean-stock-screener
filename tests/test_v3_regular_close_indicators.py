"""Regress private history reuse, source continuity and unchanged factor arithmetic."""
from copy import deepcopy
from datetime import date, timedelta
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
from v3_ma240_cross_research import observe as observe_ma240
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
        self.days = [d.date().isoformat() for d in pd.bdate_range(end='2026-11-13', periods=history.SESSIONS)]
        self.catalog = {'date': self.days[-1], 'rows': [
            {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI'},
            {'code': '123456', 'name': '코스닥', 'market': 'KOSDAQ'},
        ]}
        self.digest = sha256(json.dumps(self.catalog, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
        self.close = [int(value) for value in np.r_[np.linspace(220, 90, 240), np.linspace(92, 170, 20)]]
        self.panels = {}
        for i, day in enumerate(self.days):
            self.panels[day] = {'schema': history.DAY_SCHEMA, 'date': day,
                'source_sha256': {'KOSPI': 'a' * 64, 'KOSDAQ': 'b' * 64},
                'rows': {'005930': ['KOSPI', '삼성전자', self.close[i], 500,
                                    self.close[i-1] if i else None, True],
                         '123456': ['KOSDAQ', '코스닥', self.close[i] * 2, 500,
                                    self.close[i-1] * 2 if i else None, True]}}

    def test_ma_rsi_and_turn_reuse_the_existing_formulas(self):
        source = history.build(self.catalog, self.digest, self.days, self.panels)
        row = source['rows']['005930']
        self.assertEqual(row['continuous_sessions'], history.SESSIONS)
        self.assertEqual(row['moving_averages']['20'], round(float(np.mean(self.close[-20:])), 6))
        self.assertEqual(row['daily_rsi'], rsi_observation(np.asarray(self.close[-200:], dtype=float)))
        self.assertEqual(row['daily_ma_turn'], _series_points(
            np.asarray(self.close, dtype=float), prior=60, recent=20, hold=5,
            floor=.0001, frame='daily', trusted_window=lambda required: True))
        self.assertEqual(len(history.admitted_rows(source, self.catalog)), 2)
        self.assertNotIn('close_series', json.dumps(source))
        self.assertEqual(source['counts']['full_200_session_continuity'], 2)
        expected = observe_ma240(self.close, [1] * history.SESSIONS, asof=self.days[-1],
                                 session_days=list(pd.to_datetime(self.days)))
        self.assertIsNotNone(expected)
        self.assertEqual(row['daily_ma240_cross'], expected)
        self.assertEqual(source['counts']['daily_ma240_cross_recomputed'], 2)

    def test_weekly_ma20_requires_54_completed_traded_weeks_and_keeps_older_daily_receipts(self):
        days = [date.fromisoformat(d) for d in history.history_dates('2026-11-13')[-history.SESSIONS:]]
        member = self.catalog['rows'][0]
        records = [[member['market'], member['name'], price, 500,
                    self.close[i-1] if i else None, True] for i, price in enumerate(self.close)]
        # The Monday close must have no effect on the completed Friday week.
        dates = [*days, days[-1] + timedelta(days=3)]
        records.append([member['market'], member['name'], 500, 500, 500, True])
        weeks = history.completed_week_ranges(dates)
        receipt = history.weekly_indicators(records, member, weeks, days[-1])
        required = history.WEEKLY_MA_TURN_WEEKS
        self.assertGreaterEqual(receipt['continuous_weeks'], required)
        self.assertEqual(receipt['asof'], days[-1].isoformat())
        expected = _series_points(
            np.asarray([records[end-1][2] for _, (_, end) in weeks], dtype=float),
            prior=26, recent=8, hold=3, floor=.0005, frame='weekly', trusted_window=lambda _: True)
        self.assertEqual(receipt['ma_turn'], expected)
        self.assertIsNone(expected['60'])
        for marker in (None, False):
            halted = deepcopy(records)
            for bar in halted[slice(*weeks[-2][1])]:
                bar[5] = marker
            self.assertIsNone(history.weekly_indicators(halted, member, weeks, days[-1]))
        self.assertIsNone(history.weekly_indicators(records, member, weeks[-(required-1):], days[-1]))
        incomplete = deepcopy(records)
        incomplete[weeks[-required][1][0]] = None
        self.assertIsNone(history.weekly_indicators(incomplete, member, weeks, days[-1]))
        legacy = deepcopy(history.build(self.catalog, self.digest, self.days, self.panels))
        from v3_regular_close_contract import DAILY_ONLY_SCHEMA, DAILY_ONLY_IMPLEMENTATION
        legacy.update(schema=DAILY_ONLY_SCHEMA, implementation_sha256=DAILY_ONLY_IMPLEMENTATION)
        self.assertEqual(len(history.admitted_rows(legacy, self.catalog)), 2)
        legacy['rows']['005930']['weekly'] = receipt
        self.assertNotIn('005930', history.admitted_rows(legacy, self.catalog))

    def test_long_week_windows_enable_each_factor_only_after_required_history(self):
        days = history.history_dates('2026-11-13')
        dates = list(map(date.fromisoformat, days))
        weeks = history.completed_week_ranges(dates)
        member = self.catalog['rows'][0]
        records = [[member['market'], member['name'], 1000 + i, 500, 999 + i, True]
                   for i in range(len(days))]
        self.assertEqual(len(weeks), 154)
        for window in (89, 90, 93, 94, 119, 120, 153, 154):
            with self.subTest(window=window):
                weekly = history.weekly_indicators(records, member, weeks[-window:], dates[-1])
                self.assertEqual(weekly['continuous_weeks'], window)
                self.assertEqual(weekly['rsi'] is not None, window >= 90)
                self.assertEqual(weekly['ma_turn']['60'] is not None, window >= 94)
                self.assertEqual(weekly['weekly_ma120_gap_pct'] is not None, window >= 120)
                self.assertEqual(weekly['ma_turn']['120'] is not None, window >= 154)
        from v3_regular_close_contract import valid_weekly
        self.assertTrue(valid_weekly(weekly, days[-1], len(days)))
        self.assertFalse(valid_weekly({**weekly, 'continuous_weeks': 153}, days[-1], len(days)))
        self.assertFalse(valid_weekly({**weekly, 'asof': '2026-11-06'}, days[-1], len(days)))

        # Older missing panels never suppress a fully observed recent daily window.
        panels = {day: self.panels.get(old_day) for day, old_day in
                  zip(days, [None] * (len(days) - history.SESSIONS) + self.days)}
        report = history.build(self.catalog, self.digest, days, panels)
        self.assertEqual(report['counts']['daily_ma240_cross_recomputed'], 2)
        self.assertEqual(report['counts']['weekly_rsi_recomputed'], 0)
        self.assertEqual(len(history.admitted_rows(report, self.catalog)), 2)

    def test_legacy_days_preserve_daily_scores_until_ma240_trade_evidence_is_complete(self):
        panels = deepcopy(self.panels)
        first = panels[self.days[0]]
        first['schema'] = history.LEGACY_DAY_SCHEMA
        for row in first['rows'].values():
            row.pop()
        partial = history.build(self.catalog, self.digest, self.days, panels)
        self.assertEqual(partial['trade_status_sessions'], history.SESSIONS - 1)
        self.assertIsNotNone(partial['rows']['005930']['daily_ma_turn'])
        self.assertIsNone(partial['rows']['005930']['daily_ma240_cross'])
        for marker in (None, False):
            with self.subTest(marker=marker):
                suspended = deepcopy(self.panels)
                suspended[self.days[-1]]['rows']['005930'][5] = marker
                report = history.build(self.catalog, self.digest, self.days, suspended)
                self.assertIsNone(report['rows']['005930']['daily_ma240_cross'])
                self.assertIsNotNone(report['rows']['123456']['daily_ma240_cross'])

    def test_migration_reads_legacy_day_and_stores_trade_status_at_a_new_path(self):
        storage = MemoryStore()
        clients = {market: FakeClient(market) for market in history.MARKETS}
        day = self.days[-1]
        old = deepcopy(self.panels[day])
        old['schema'] = history.LEGACY_DAY_SCHEMA
        for row in old['rows'].values():
            row.pop()
        old_path = history.archive_path(day, legacy=True)
        old_blob = gzip.compress(json.dumps(old).encode())
        storage.blobs[old_path] = old_blob
        with patch('builtins.print'):
            panels, added, failures = history.collect(storage, clients, [day])
        self.assertEqual((added, failures), (1, {}))
        self.assertIs(panels[day]['rows']['005930'][5], True)
        self.assertEqual(storage.blobs[old_path], old_blob)

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
            self.assertIsNone(row['daily_ma240_cross'])

    def test_forged_source_scope_or_unearned_periods_are_rejected(self):
        source = history.build(self.catalog, self.digest, self.days, self.panels)
        for field, value in (('source_catalog_sha256', 'f' * 64),
                             ('asof', '2026-11-12'), ('performance_validated', True)):
            self.assertEqual(history.admitted_rows({**source, field: value}, self.catalog), {})
        for mutation in (
            lambda row: row.update(continuous_sessions=90),
            lambda row: row['daily_ma_turn'].update({'120': 1.0}),
            lambda row: row['daily_rsi'].update(rsi14=float('nan')),
            lambda row: row['daily_ma240_cross'].update(qualified='true'),
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
        self.assertEqual((len(days), days[-1]), (721, '2026-11-13'))
        self.assertNotIn('2026-06-03', days)
        self.assertNotIn('2026-07-17', days)
