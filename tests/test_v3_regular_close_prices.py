"""Regress current public-price corroboration and publication source identity."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
import v3_regular_close_prices as prices
from v3_forward_daily_prices import SCHEMA as DAILY_SCHEMA


class RegularClosePricesTest(unittest.TestCase):
    def setUp(self):
        self.day = '2026-11-13'
        self.market = {'date': self.day, 'stocks': [
            {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI', 'price_date': self.day, 'price': 110},
            {'code': '123456', 'name': '코스닥', 'market': 'KOSDAQ', 'price_date': self.day, 'price': 200},
        ]}
        self.frame = pd.DataFrame([
            {'Code': '005930', 'Name': '삼성전자', 'Market': 'KOSPI', 'Close': 100},
            {'Code': '123456', 'Name': '코스닥', 'Market': 'KOSDAQ GLOBAL', 'Close': 200},
        ])
        self.daily = {'date': self.day, 'source_sha256': {'KOSPI': 'c' * 64, 'KOSDAQ': 'd' * 64},
                      'rows': {'005930': ['KOSPI', '삼성전자', 'traded', 90, 110, 80, 100, 500],
                               '123456': ['KOSDAQ', '코스닥', 'traded', 190, 220, 180, 200, 500]}}

    def test_only_identical_public_and_official_closes_are_published(self):
        report = prices.build(self.market, 'a' * 64, self.frame, 'b' * 64, self.daily)
        self.assertEqual(report['matched_count'], 2)
        self.assertEqual(report['changed_display_price_count'], 1)
        self.assertEqual(report['rows']['005930']['close'], 100)
        self.assertEqual(report['rows']['005930']['analysis_close'], 110)
        self.assertEqual(report['rows']['005930']['public_source'], 'FDR_DATED_LISTING')
        self.assertFalse(report['historical_ohlcv_regular_session_verified'])
        self.assertFalse(report['score_recalculated'])
        for mutation, expected in (
            (lambda d: d['rows']['005930'].__setitem__(6, 101), 'public_official_close_mismatch'),
            (lambda d: d['rows']['005930'].__setitem__(1, '다른회사'), 'identity_unresolved'),
            (lambda d: d['rows']['005930'].__setitem__(2, 'no_reported_volume'), 'official_traded_price_unavailable'),
            (lambda d: d['rows'].pop('005930'), 'official_row_absent'),
        ):
            daily = deepcopy(self.daily)
            mutation(daily)
            with self.subTest(expected=expected):
                report = prices.build(self.market, 'a' * 64, self.frame, 'b' * 64, daily)
                self.assertNotIn('005930', report['rows'])
                self.assertEqual(report['states'][expected], 1)
                self.assertEqual(sum(report['states'].values()), 2)

    def test_verified_published_price_fills_unavailable_or_incomplete_cache(self):
        self.market['stocks'][0]['price'] = 100
        self.frame.loc[0, 'Close'] = 99
        for frame, digest, counts in (
            (self.frame, 'b' * 64, {'FDR_DATED_LISTING': 1, 'PUBLISHED_SCREENER_PRICE': 1}),
            (None, None, {'FDR_DATED_LISTING': 0, 'PUBLISHED_SCREENER_PRICE': 2}),
        ):
            report = prices.build(self.market, 'a' * 64, frame, digest, self.daily)
            self.assertEqual(report['matched_count'], 2)
            self.assertEqual(report['matches_by_public_source'], {**counts, prices.NEXT_REFERENCE: 0})
            self.assertEqual(report['rows']['005930']['close'], 100)
            self.assertEqual(report['rows']['005930']['public_source'], 'PUBLISHED_SCREENER_PRICE')

        self.daily['rows']['005930'][1] = '다른회사'
        report = prices.build(self.market, 'a' * 64, None, None, self.daily)
        self.assertNotIn('005930', report['rows'])
        self.assertEqual(report['states']['identity_unresolved'], 1)

    def test_day_mismatch_cannot_promote_current_prices(self):
        self.daily['date'] = '2026-11-12'
        with self.assertRaisesRegex(prices.RegularCloseError, 'invalid_regular_close_sources'):
            prices.build(self.market, 'a' * 64, self.frame, 'b' * 64, self.daily)

    def test_next_session_signed_reference_requires_exact_official_match_and_identity(self):
        listing = self.frame.assign(Close=[95, 220], Changes=[-5, 20], Volume=[10, 20])
        for name, change, volume, matched in (
            ('삼성전자', -5, 10, True), ('삼성전자', -4, 10, False),
            ('다른회사', -5, 10, False), ('삼성전자', -5, 0, False),
        ):
            candidate = listing.copy()
            candidate.loc[0, ['Name', 'Changes', 'Volume']] = [name, change, volume]
            report = prices.build(self.market, 'a' * 64, None, None, self.daily,
                next_listing=('2026-11-16', candidate, 'b' * 64))
            with self.subTest(name=name, change=change, volume=volume):
                self.assertEqual('005930' in report['rows'], matched)
                self.assertEqual(report['matches_by_public_source'][prices.NEXT_REFERENCE], int(matched))
                self.assertEqual(report['rows']['123456']['public_source'], 'PUBLISHED_SCREENER_PRICE')
                self.assertFalse(report['score_recalculated'])
                self.assertEqual(report['next_session_date'], '2026-11-16')
                if matched:
                    self.assertEqual(report['rows']['005930']['close'], 100)

    def test_incomplete_publication_reuses_official_archive_after_next_session_close(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            market, out = root / 'data.json', root / 'quotes.json'
            market.write_text(json.dumps(self.market))
            (root / f'{self.day}.json').write_text(json.dumps({'schema': DAILY_SCHEMA,
                'date': self.day, 'status': 'archived_privately',
                'source_sha256': self.daily['source_sha256']}))
            today = self.frame.assign(Close=[99, 200])
            next_frame = self.frame.assign(Close=[105, 220], Changes=[5, 20], Volume=[10, 20])
            def load(day):
                return (today, 'b' * 64) if day == self.day else (next_frame, 'e' * 64)
            with (patch.object(prices, 'MARKET', market), patch.object(prices, 'OUT', out),
                  patch.object(prices, 'DAILY_STATUS', root),
                  patch.object(prices, 'read_daily', return_value=self.daily) as reader,
                  patch.object(prices, 'load_dated_listing', side_effect=load) as loader,
                  patch.object(prices, 'official_publish_time',
                               return_value=datetime(2026, 11, 15, 23, tzinfo=timezone.utc)),
                  patch('builtins.print')):
                prices.publish_regular_closes(lambda: object(),
                    now=datetime(2026, 11, 16, 6, tzinfo=timezone.utc))
                previous = json.loads(out.read_bytes())
                self.assertEqual(previous['matched_count'], 1)
                self.assertEqual(loader.call_count, 1)
                previous['schema'] = prices.PREVIOUS_SCHEMA
                previous.pop('next_session_date')
                previous.pop('next_session_cache_sha256')
                previous['matches_by_public_source'].pop(prices.NEXT_REFERENCE)
                out.write_text(json.dumps(previous))
                now = datetime(2026, 11, 16, 7, tzinfo=timezone.utc)
                prices.publish_regular_closes(lambda: object(), now=now)
                report = json.loads(out.read_bytes())
                self.assertEqual(report['matched_count'], 2)
                self.assertEqual(report['next_session_cache_sha256'], 'e' * 64)
                self.assertEqual(reader.call_count, 2)
                prices.publish_regular_closes(lambda: object(), retry_public_cache=True, now=now)
                self.assertEqual(reader.call_count, 2)

    def test_repeated_publication_reuses_evidence_and_source_drift_rebuilds(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            market, out, daily_dir = root / 'data.json', root / 'quotes.json', root / 'daily'
            daily_dir.mkdir()
            market.write_text(json.dumps(self.market))
            status = {'schema': DAILY_SCHEMA, 'date': self.day, 'status': 'archived_privately',
                      'source_sha256': self.daily['source_sha256']}
            (daily_dir / f'{self.day}.json').write_text(json.dumps(status))
            with (patch.object(prices, 'MARKET', market), patch.object(prices, 'OUT', out),
                  patch.object(prices, 'DAILY_STATUS', daily_dir),
                  patch.object(prices, 'read_daily', return_value=self.daily) as reader,
                  patch.object(prices, 'load_dated_listing', return_value=(self.frame, 'b' * 64)) as loader,
                  patch('builtins.print')):
                prices.publish_regular_closes(lambda: object())
                self.assertEqual(json.loads(out.read_bytes())['matched_count'], 2)
                prices.publish_regular_closes(lambda: object())
                self.assertEqual(reader.call_count, 1)
                self.assertEqual(loader.call_count, 1)
                self.market['stocks'][0]['price'] = 120
                market.write_text(json.dumps(self.market))
                prices.publish_regular_closes(lambda: object())
                report = json.loads(out.read_bytes())
                self.assertEqual(loader.call_count, 2)
                self.assertEqual(report['rows']['005930']['analysis_close'], 120)
                self.assertEqual(report['source_market_sha256'], sha256(market.read_bytes()).hexdigest())

                loader.return_value = (self.frame.assign(Close=[99, 200]), 'c' * 64)
                market.write_text(json.dumps(self.market))
                out.unlink()
                prices.publish_regular_closes(lambda: object())
                self.assertEqual(json.loads(out.read_bytes())['matched_count'], 1)
                before = reader.call_count
                prices.publish_regular_closes(lambda: object(), retry_public_cache=True)
                self.assertEqual(reader.call_count, before)
                loader.return_value = (self.frame, 'd' * 64)
                prices.publish_regular_closes(lambda: object(), retry_public_cache=True)
                self.assertEqual(json.loads(out.read_bytes())['matched_count'], 2)
