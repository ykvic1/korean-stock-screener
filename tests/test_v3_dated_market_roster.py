"""Offline contract for date-pinned market roster; never publishes raw KRX rows."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch
import json
import tempfile
import unittest

import pandas as pd

import full_market_batch as batches
import screener
from v3_dated_market_roster import load_dated_listing, DatedRosterError

DAY = '2026-11-11'


def fixture_listing(n=1000):
    codes = [f'{120000+i:06}' for i in range(n-1)] + ['0008Z0']
    return pd.DataFrame([{
        'Code': code, 'Name': f'Ordinary share {i}',
        'Market': 'KOSPI' if i % 2 else 'KOSDAQ',
        'Close': 8000 + i, 'Volume': 0 if i == 5 else 11,
        'Amount': 0 if i == 5 else 90000,
        'Marcap': 2_000_000_000 + i,
    } for i, code in enumerate(codes)])


class Response:
    def __init__(self, raw, status=200):
        self.status_code = status
        self.content = raw


class DatedRosterTests(unittest.TestCase):
    def test_krx_global_is_kosdaq_but_konex_and_spacs_stay_out(self):
        # Regression against 2026-11-11 dated FDR market labels:
        # a third-looking source label silently dropped 50 KOSDAQ GLOBAL
        # ordinary issuers before daily OHLCV or scoring even started.
        frame=fixture_listing(1000)
        samples=[
            ('196170','알테오젠','KOSDAQ GLOBAL'),
            ('247540','에코프로비엠','KOSDAQ GLOBAL'),
            ('458650','성우','KOSDAQ'),
            ('294090','이오플로우','KOSDAQ'),
            ('159910','에코글로우','KOSDAQ'),
            ('0126Z0','삼성에피스홀딩스','KOSPI'),
            ('0009K0','에임드바이오','KOSDAQ'),
            ('0205H0','에스케이증권제14호기업인수목적','KOSDAQ'),
            ('00104K','CJ4우(전환)','KOSPI'),
            ('00279K','아모레퍼시픽홀딩스3우C','KOSPI'),
            ('37550L','DL이앤씨2우(전환)','KOSPI'),
            ('008355','남선알미우','KOSPI'),
            ('496320','본시스템즈','KONEX'),
        ]
        for i,(code,name,market) in enumerate(samples):
            frame.loc[i,['Code','Name','Market']]=[code,name,market]
        selected=screener.market_snapshot(listing=frame)
        for code in ('196170','247540','458650','294090','159910',
                     '0126Z0','0009K0','0008Z0'):
            self.assertIn(code,selected)
        for code in ('0205H0','00104K','00279K','37550L',
                     '008355','496320'):
            self.assertNotIn(code,selected)
        self.assertEqual(selected['196170']['market'],'KOSDAQ')
        self.assertEqual(selected['247540']['market'],'KOSDAQ')
        self.assertEqual(selected['0126Z0']['market'],'KOSPI')
        # Exactly 6 excluded fixture positions, not zero-scored or rebranded.
        self.assertEqual(len(selected),1000-6)


    def test_immutable_requested_date_path_and_mixed_code_not_truncated(self):
        frame = fixture_listing(1001)
        raw = frame.to_csv(index=False).encode('utf-8-sig')
        seen = []
        def get(url, **kwargs):
            seen.append(url)
            self.assertIn('2026-11-11.csv', url)
            self.assertIn('timeout', kwargs)
            return Response(raw)
        parsed, digest = load_dated_listing(DAY, get=get, minimum=100)
        self.assertEqual(len(parsed), 1001)
        self.assertIn('0008Z0', parsed['Code'].tolist())
        self.assertEqual(len(digest), 64)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(screener.market_snapshot(listing=parsed)), 1001)
        self.assertEqual(sum(x['snapshot_volume'] == 0 for x in
                             screener.market_snapshot(listing=parsed).values()), 1)

    def test_fail_closed_on_missing_wrong_day_bad_schema_and_duplicates(self):
        frame = fixture_listing(1001)
        raw = frame.to_csv(index=False).encode()
        with self.assertRaisesRegex(DatedRosterError, 'invalid_dated_roster_day'):
            load_dated_listing('2026-11-31',get=lambda *a,**k: Response(raw))
        with self.assertRaisesRegex(DatedRosterError, 'dated_roster_unavailable'):
            load_dated_listing(DAY, get=lambda *a,**k: Response(b'', 404))
        with self.assertRaisesRegex(DatedRosterError, 'dated_roster_schema_invalid'):
            load_dated_listing(DAY, get=lambda *a,**k: Response(b'Code,Market,Note\nA,STK,' + b'x'*100 + b'\n'),minimum=1)
        same = pd.concat([frame,frame.iloc[[0]]],ignore_index=True).to_csv(index=False).encode()
        with self.assertRaisesRegex(DatedRosterError,'dated_roster_duplicate_identity'):
            load_dated_listing(DAY, get=lambda *a,**k: Response(same),minimum=1)

    def test_1001_stock_plan_exact_digest_and_republication_guard(self):
        frame = fixture_listing(1001)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(batches, 'load_dated_listing',
                              return_value=(frame, 'a'*64)) as fetch:
                response = batches.plan(asof=DAY, out=base/'plan',
                                        date_pinned_roster=True)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(response['eligible'], 1001)
            manifest = batches._read(base/'plan'/'manifest.json')
            self.assertEqual(manifest['roster_provenance'],'date_pinned_fdr_public_cache')
            self.assertEqual(manifest['roster_source_sha256'],'a'*64)
            self.assertIn('0008Z0',manifest['codes'])
            with patch.object(batches,'load_receipts',return_value={'rows':[],'errors':[]}):
                with patch.object(batches,'load_dated_listing',
                                  return_value=(frame,'b'*64)):
                    with self.assertRaisesRegex(batches.BatchError,
                                                 'date_pinned_roster_changed_between_jobs'):
                        batches.publish(manifest=manifest,parts_dir=base/'parts',
                                        use_flow=False,write_report=False)
            missing = dict(manifest)
            del missing['roster_source_sha256']
            with self.assertRaisesRegex(batches.BatchError,
                                         'untrusted_date_pinned_roster_digest'):
                batches.validate_manifest(missing)

    def test_transient_dated_cache_retries_with_fixed_date_before_plan(self):
        frame = fixture_listing(1001)
        attempts = []
        delays = []

        def fetch(day):
            attempts.append(day)
            if len(attempts) < 3:
                raise DatedRosterError('dated_roster_unavailable')
            return frame, 'c' * 64

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(batches, 'load_dated_listing', side_effect=fetch):
                with patch.object(batches.time, 'sleep', side_effect=delays.append):
                    result = batches.plan(
                        asof=DAY, out=Path(tmp) / 'plan',
                        date_pinned_roster=True)
        self.assertEqual(attempts, [DAY, DAY, DAY])
        self.assertEqual(delays, [1, 3])
        self.assertEqual(result['eligible'], 1001)

    def test_dated_roster_retries_only_source_unavailable(self):
        delays = []
        with patch.object(batches.time, 'sleep', side_effect=delays.append):
            for reason in ('dated_roster_schema_invalid',
                           'dated_roster_duplicate_identity',
                           'dated_roster_size_invalid'):
                calls = []
                def fail(day):
                    calls.append(day)
                    raise DatedRosterError(reason)
                with self.assertRaisesRegex(DatedRosterError, reason):
                    batches.dated_market_roster_with_retries(DAY, fetch=fail)
                self.assertEqual(calls, [DAY])
        self.assertEqual(delays, [])

    def test_dated_cache_retry_exhaustion_keeps_prior_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinel = root / 'prior-report.json'
            sentinel.write_text('retained', encoding='utf-8')
            calls = []
            with patch.object(batches, 'load_dated_listing',
                              side_effect=DatedRosterError('dated_roster_unavailable')) as fetch:
                with patch.object(batches.time, 'sleep', side_effect=calls.append):
                    with self.assertRaisesRegex(
                            batches.BatchError,
                            'date_pinned_roster_unavailable_or_invalid'):
                        batches.plan(asof=DAY, out=root / 'plan',
                                     date_pinned_roster=True)
                self.assertEqual(fetch.call_count, 3)
            self.assertEqual(calls, [1, 3])
            self.assertEqual(sentinel.read_text(), 'retained')
            self.assertFalse((root / 'plan' / 'manifest.json').exists())

    def test_unavailable_exact_day_never_falls_back_in_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(batches,'load_dated_listing',
                              side_effect=DatedRosterError('dated_roster_unavailable')):
                with patch.object(batches.time, 'sleep'):
                    with self.assertRaisesRegex(batches.BatchError,
                                                 'date_pinned_roster_unavailable_or_invalid'):
                        batches.plan(asof=DAY, out=Path(tmp),
                                     date_pinned_roster=True)
            self.assertFalse((Path(tmp)/'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
