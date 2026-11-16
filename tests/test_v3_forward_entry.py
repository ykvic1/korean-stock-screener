"""Regress cutoff timing and the transition to private official evidence."""
from datetime import date, datetime
from hashlib import sha256
import gzip
import json
import unittest
from unittest.mock import patch

from v3_forward_entry import (
    EntryError, classify_entry, crosscheck_entry_daily, entry_plan, entry_session_path, first_open_after,
    read_official_openings, save_entry, timestamp,
)
from v3_forward_daily_prices import save_daily
from v3_score_observation_capture import SCHEMA as SCORE_SCHEMA


def quote(code='005930', name='삼성전자', day='20261116', opening='50,000', volume='100'):
    return {'ISU_CD': code, 'ISU_NM': name, 'BAS_DD': day, 'TDD_OPNPRC': opening,
            'TDD_HGPRC': '52,000', 'TDD_LWPRC': '48,000', 'TDD_CLSPRC': '51,000',
            'ACC_TRDVOL': volume, 'ACC_TRDVAL': '5000000', 'MKTCAP': '50000000'}


class MemoryStore:
    def __init__(self):
        self.blobs = {}

    def get_content(self, path):
        return self.blobs.get(path), None

    def put_new(self, path, blob, comment):
        if path in self.blobs:
            raise AssertionError('attempt to overwrite stored entry')
        self.blobs[path] = blob


class OfficialPair:
    def __init__(self, kosdaq_code='123456'):
        self.markets = []
        self.kosdaq_code = kosdaq_code

    def daily_market(self, market, day):
        self.markets.append(market)
        rows = [quote()] if market == 'KOSPI' else [quote(code=self.kosdaq_code, name='코스닥')]
        return rows, json.dumps({'OutBlock_1': rows}).encode()


class EntryEvidenceTest(unittest.TestCase):
    def test_saved_entry_and_daily_prices_must_match_sources_identity_and_open(self):
        observation = {
            'schema': SCORE_SCHEMA, 'score_asof': '2026-11-13',
            'captured_at_utc': '2026-11-15T23:03:00+00:00',
            'regular_close_utc': '2026-11-13T06:30:00+00:00',
            'listed_count': 2, 'ranked_count': 1,
            'rows': {'005930': ['삼성전자', 'KOSPI', 60, 1],
                     '123456': ['코스닥', 'KOSDAQ', None, None]},
        }
        digest, storage = 'd' * 64, MemoryStore()
        plan = entry_plan(observation, digest)
        official = read_official_openings(OfficialPair(), '2026-11-16')
        now = timestamp('2026-11-16T11:45:00+00:00')
        with patch('v3_forward_entry.datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            save_entry(plan, observation, storage, lambda day: official)
        self.assertEqual(crosscheck_entry_daily(observation, digest, storage),
                         {'status': 'waiting_for_daily_archive'})
        save_daily('2026-11-16', storage, lambda day: official, clock=lambda: now)
        original = dict(storage.blobs)
        result = crosscheck_entry_daily(observation, digest, storage)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['observed_openings_checked'], 1)
        self.assertEqual(result['mismatched_openings'], 0)
        self.assertEqual(result['entry_session_path']['groups']['all_ranked']['observed_count'], 1)
        self.assertEqual(storage.blobs, original)
        self.assertNotIn('005930', json.dumps(result))
        self.assertNotIn('50000', json.dumps(result))
        daily_path = 'derived/forward-daily-prices/2026-11-16.json.gz'
        for field, value in ((1, '다른회사'), (3, 51000)):
            daily = json.loads(gzip.decompress(original[daily_path]))
            daily['rows']['005930'][field] = value
            storage.blobs[daily_path] = gzip.compress(json.dumps(daily).encode())
            with self.subTest(field=field):
                result = crosscheck_entry_daily(observation, digest, storage)
                self.assertEqual(result['status'], 'opening_mismatch')
                self.assertEqual(result['mismatched_openings'], 1)
                self.assertNotIn('entry_session_path', result)
        daily = json.loads(gzip.decompress(original[daily_path]))
        daily['source_sha256']['KOSPI'] = 'e' * 64
        storage.blobs[daily_path] = gzip.compress(json.dumps(daily).encode())
        result = crosscheck_entry_daily(observation, digest, storage)
        self.assertEqual(result['status'], 'source_mismatch')
        self.assertEqual(result['mismatched_openings'], 0)
        self.assertFalse(result['performance_validated'])
        self.assertNotIn('entry_session_path', result)

    def test_entry_session_path_preserves_selected_members_and_empty_groups(self):
        observation = {'rows': {
            '005930': ['삼성전자', 'KOSPI', 60, 1],
            '123456': ['코스닥', 'KOSDAQ', 40, 60],
            '999998': ['미관측', 'KOSPI', 55, 8],
            '999999': ['미평가', 'KOSDAQ', None, None],
        }}
        entry = {'rows': {
            '005930': ['official_open_observed', 100],
            '123456': ['official_open_observed', 200],
            '999998': ['absent', None], '999999': ['unranked', None],
        }}
        daily = {'date': '2026-11-16', 'rows': {
            '005930': ['KOSPI', '삼성전자', 'traded', 100, 110, 90, 105, 500],
            '123456': ['KOSDAQ', '코스닥', 'traded', 200, 220, 160, 180, 500],
        }}
        result = entry_session_path(observation, entry, daily)
        top20, top100 = result['groups']['top20'], result['groups']['top100']
        self.assertEqual((top20['selected_count'], top20['observed_count']), (2, 1))
        self.assertEqual(top20['missing_entry_counts']['absent'], 1)
        self.assertEqual(top20['median_close_return_pct'], 5)
        self.assertEqual((top100['selected_count'], top100['observed_count']), (3, 2))
        self.assertEqual(top100['worst_low_return_pct'], -20)
        self.assertEqual(top100['median_low_return_pct'], -15)
        self.assertEqual(top100['median_high_return_pct'], 10)
        self.assertEqual(top100['median_close_return_pct'], -2.5)
        self.assertEqual(top100['negative_close_count'], 1)
        self.assertNotIn('005930', json.dumps(result))
        observation['rows']['005930'][3] = 50
        empty = entry_session_path(observation, entry, daily)['groups']['top20']
        self.assertEqual((empty['selected_count'], empty['observed_count']), (1, 0))
        self.assertIsNone(empty['worst_low_return_pct'])
        self.assertIsNone(empty['median_close_return_pct'])

    def test_structural_reference_counts_use_matched_signal_price_and_share_basis(self):
        signal = {'rows': {
            str(i): ['KOSPI', f'종목{i}', 'traded', 100, 115, 95, 110, 500]
            for i in range(6)
        }}
        daily = {'date': '2026-11-16', 'rows': {
            str(i): ['KOSPI', f'종목{i}', 'traded', 100, 115, 90, 105, 500]
            for i in range(6)
        }}
        daily['rows']['3'][-1] = 1000
        observation = {'rows': {
            str(i): [f'종목{i}', 'KOSPI', 55, i+1, 90, '2026-11-13', '2026-11-13', reference]
            for i, reference in enumerate((100, 90, 89, 100, 100, None))
        }}
        entry = {'rows': {code: ['official_open_observed', 100] for code in daily['rows']}}
        cases = {code: {'state': 'matched'} for code in daily['rows']}
        cases['4']['state'] = 'close_mismatch'
        result = entry_session_path(observation, entry, daily, signal=signal, signal_cases=cases)
        counts = result['groups']['all_ranked']['structural_reference_counts']
        self.assertEqual(len(counts), 6)
        self.assertEqual(list(counts.values()), [1] * 6)
        self.assertEqual(sum(counts.values()), result['groups']['all_ranked']['observed_count'])
        self.assertNotIn('structural_reference_counts',
                         entry_session_path(observation, entry, daily)['groups']['all_ranked'])

    def test_reference_review_waits_for_frozen_basis_then_adds_contact_counts(self):
        market = json.dumps({'date':'2026-11-13','stocks':[
            {'code':'005930','name':'삼성전자','market':'KOSPI','price_date':'2026-11-13','price':51000}
        ]}).encode()
        observation = {
            'schema':SCORE_SCHEMA, 'score_asof':'2026-11-13',
            'captured_at_utc':'2026-11-15T23:03:00+00:00',
            'regular_close_utc':'2026-11-13T06:30:00+00:00',
            'source_market_sha256':sha256(market).hexdigest(), 'listed_count':2, 'ranked_count':1,
            'rows':{'005930':['삼성전자','KOSPI',60,1,80,'2026-11-13','2026-11-13',49000],
                    '123456':['코스닥','KOSDAQ',None,None]},
        }
        digest, storage = 'd'*64, MemoryStore()
        plan = entry_plan(observation, digest)
        def pair(day):
            rows, hashes = read_official_openings(OfficialPair(), '2026-11-16')
            return {code:(market, {**row, 'BAS_DD':day.replace('-',''), 'LIST_SHRS':'500000'})
                    for code,(market,row) in rows.items()}, hashes
        now = timestamp('2026-11-16T11:45:00+00:00')
        with patch('v3_forward_entry.datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            save_entry(plan, observation, storage, pair)
        save_daily('2026-11-16', storage, pair, clock=lambda:now)
        waiting = crosscheck_entry_daily(observation, digest, storage, review_reference=True)
        self.assertEqual(waiting['structural_reference_status'], 'waiting_for_signal_price_basis')
        self.assertEqual(waiting['entry_session_path']['groups']['top20']['observed_count'],1)
        save_daily('2026-11-13', storage, pair, clock=lambda:now)
        with patch('v3_forward_entry.frozen_market', return_value=market):
            checked = crosscheck_entry_daily(observation, digest, storage, review_reference=True)
        self.assertEqual(checked['structural_reference_status'],'compared')
        self.assertFalse(checked['structural_reference_basis']['stop_execution_validated'])
        self.assertEqual(checked['entry_session_path']['groups']['top20']
                         ['structural_reference_counts']['intraday_reference_touched'],1)
        self.assertNotIn('49000',json.dumps(checked))

    def test_late_capture_and_open_boundary(self):
        monday, opening, closing = first_open_after(timestamp('2026-11-15T23:03:26.504597+00:00'))
        self.assertEqual(monday, date(2026, 11, 16))
        self.assertEqual(opening.isoformat(), '2026-11-16T00:00:00+00:00')
        self.assertEqual(closing.isoformat(), '2026-11-16T06:30:00+00:00')
        self.assertEqual(first_open_after(opening)[0], date(2026, 11, 17))

    def test_exam_day_uses_announced_open(self):
        day, opening, closing = first_open_after(timestamp('2026-11-19T00:59:59+00:00'))
        self.assertEqual(day, date(2026, 11, 19))
        self.assertEqual(opening.hour, 1)
        self.assertEqual(closing.isoformat(), '2026-11-19T07:30:00+00:00')

    def test_gaps_and_unknown_identity_are_never_prices(self):
        member = ['삼성전자', 'KOSPI', 50, 1]
        for official, status in (
            (None, 'absent'),
            (('KOSDAQ', quote()), 'identity_unresolved'),
            (('KOSPI', quote(name='다른종목')), 'identity_unresolved'),
            (('KOSPI', quote(volume='0')), 'not_traded'),
            (('KOSPI', quote(opening='0')), 'open_unavailable'),
        ):
            self.assertEqual(classify_entry(member, official), (status, None))
        self.assertEqual(classify_entry(member, ('KOSPI', quote())),
                         ('official_open_observed', 50000))
        self.assertEqual(classify_entry(member[:3] + [None], ('KOSPI', quote())),
                         ('unranked', None))

    def test_two_market_duplicate_rejected(self):
        with self.assertRaisesRegex(EntryError, 'ambiguous_official_market_code'):
            read_official_openings(OfficialPair('005930'), '2026-11-16')

    def test_private_archive_reused_and_summary_has_no_prices(self):
        plan = {'score_asof': '2026-11-13', 'listed_count': 3, 'ranked_count': 2,
                'regular_close_utc': '2026-11-13T06:30:00+00:00',
                'entry_session_candidate': '2026-11-16'}
        observation = {'rows': {'005930': ['삼성전자', 'KOSPI', 60, 1],
                                '999998': ['미관측', 'KOSPI', 60, 2],
                                '999999': ['미평가', 'KOSDAQ', None, None]}}
        storage, client = MemoryStore(), OfficialPair()
        def official_openings(day):
            return read_official_openings(client, day)

        result = save_entry(plan, observation, storage, official_openings)
        self.assertEqual(client.markets, ['KOSPI', 'KOSDAQ'])
        self.assertEqual(result['entry_status_counts']['official_open_observed'], 1)
        self.assertEqual(result['entry_status_counts']['unranked'], 1)
        self.assertEqual(result['entry_status_counts']['absent'], 1)
        self.assertNotIn('rows', result)
        self.assertNotIn('50000', json.dumps(result['entry_status_counts']))
        self.assertEqual(result, save_entry(plan, observation, storage, official_openings))
        self.assertEqual(len(client.markets), 2)
        path, blob = next(iter(storage.blobs.items()))
        saved = json.loads(gzip.decompress(blob))
        self.assertEqual(saved['rows']['005930'], ['official_open_observed', 50000])
        saved['rows']['999998'] = ['absent', 0]
        storage.blobs[path] = gzip.compress(json.dumps(saved).encode())
        with self.assertRaisesRegex(EntryError, 'invalid_entry_status_or_price'):
            save_entry(plan, observation, storage, official_openings)
