"""Regress cutoff timing and the transition to private official evidence."""
from datetime import date
import gzip
import json
import unittest

from v3_forward_entry import (
    EntryError, classify_entry, first_open_after, read_official_openings, save_entry, timestamp,
)


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
