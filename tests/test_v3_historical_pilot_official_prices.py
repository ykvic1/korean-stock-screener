from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import v3_historical_pilot_official_prices as prices
from official_probe.clients import KRXProbeError

from v3_historical_pilot_official_prices import compare_market


class HistoricalOfficialPricesTest(unittest.TestCase):
    def test_ohlc_differences_and_membership_reported_without_raw_data(self):
        public = dict.fromkeys(('a', 'b', 'c', 'd', 'e', 'f'), ('KOSDAQ', 'Name', 200, 10, 13, 8, 12, 20))
        official = {
            'a': ('KOSDAQ', 'Na me', 'traded', 10, 12, 9, 12, 200),
            'b': ('KOSPI', 'Name', 'traded', 10, 13, 8, 12, 200),
            'c': ('KOSDAQ', 'Name', 'no_reported_volume', None, None, None, None, 200),
            'd': ('KOSDAQ', 'Name', 'traded', 10, 13, 8, 12, 250),
            'e': ('KOSDAQ', 'Name', 'traded', 10, 13, 8, 12, None),
            'g': ('KOSDAQ', 'Name', 'traded', 10, 13, 8, 12, 200),
        }
        result = compare_market(public, official, public.keys() | official.keys())
        self.assertEqual(result['scope'], 7)
        self.assertEqual(result['absent_from_official'], 1)
        self.assertEqual(result['absent_from_public'], 1)
        self.assertEqual(result['identity_mismatch'], 1)
        self.assertEqual(result['listed_share_mismatch'], 1)
        self.assertEqual(result['listed_shares_unavailable'], 1)
        self.assertEqual(result['positive_trade_on_public_only'], 1)
        self.assertEqual(result['positive_trade_on_both'], 3)
        self.assertEqual(result['exact_ohlc'], 2)
        self.assertEqual(result['different_price_by_field'], dict(open=0, high=1, low=1, close=0))
        self.assertNotIn('Name', str(result))


class HistoricalTurnoverTest(unittest.TestCase):
    def quote(self, volume='20', amount='240'):
        return {'ISU_NM': 'Private Issuer', 'ACC_TRDVOL': volume, 'ACC_TRDVAL': amount}

    def test_exact_and_mismatched_amounts_are_counted_without_exporting_values(self):
        public = {'000001': ('KOSDAQ', 'PrivateIssuer', 20, 240),
                  '000002': ('KOSDAQ', 'PrivateIssuer', 20, 241)}
        raw = {code: ('KOSDAQ', self.quote()) for code in public}
        result = prices.compare_turnover(public, raw)
        self.assertEqual(result['exact_volume_and_amount'], 1)
        self.assertEqual(result['fields']['volume']['matched'], 2)
        self.assertEqual(result['fields']['amount']['mismatched'], 1)
        encoded = json.dumps(result)
        self.assertNotIn('000001', encoded)
        self.assertNotIn('Private', encoded)
        self.assertTrue(result['numeric_equivalence_is_not_session_certification'])

    def test_missing_values_are_distinct_from_observed_zero(self):
        public = {'a': ('KOSDAQ', 'PrivateIssuer', 0, 0),
                  'b': ('KOSDAQ', 'PrivateIssuer', None, True),
                  'c': ('KOSDAQ', 'PrivateIssuer', 2, 3)}
        raw = {'a': ('KOSDAQ', self.quote('0', '0')),
               'b': ('KOSDAQ', self.quote('0', '0')),
               'c': ('KOSDAQ', self.quote('', ''))}
        result = prices.compare_turnover(public, raw)
        for field in ('volume', 'amount'):
            self.assertEqual(result['fields'][field]['both_zero'], 1)
            self.assertEqual(result['fields'][field]['public_unavailable'], 1)
            self.assertEqual(result['fields'][field]['official_unavailable'], 1)
            self.assertEqual(result['fields'][field]['compared'], 1)
        self.assertEqual(result['exact_volume_and_amount'], 1)

    def test_identity_and_missing_membership_exclude_numeric_comparison(self):
        public = {'a': ('KOSPI', 'PrivateIssuer', 20, 240),
                  'b': ('KOSDAQ', 'DifferentIssuer', 20, 240),
                  'public_only': ('KOSDAQ', 'PrivateIssuer', 20, 240)}
        raw = {code: ('KOSDAQ', self.quote()) for code in ('a', 'b', 'official_only')}
        result = prices.compare_turnover(public, raw)
        self.assertEqual(result['scope'], 4)
        self.assertEqual(result['identity_mismatch'], 2)
        self.assertEqual(result['absent_from_public'], 1)
        self.assertEqual(result['absent_from_official'], 1)
        self.assertEqual(result['fields']['amount']['compared'], 0)

    def test_price_and_turnover_checks_reuse_one_official_pair(self):
        panel = {'000001': ('KOSDAQ', 'Private Issuer', 100, 10, 13, 8, 12, 20)}
        raw = {'000001': ('KOSDAQ', {
            **self.quote(), 'LIST_SHRS': '100',
            'TDD_OPNPRC': '10', 'TDD_HGPRC': '13', 'TDD_LWPRC': '8', 'TDD_CLSPRC': '12'})}
        with patch.dict(prices.BATCHES, {'single': (prices.SIGNAL,)}), \
             patch.object(prices, 'read_official_openings', return_value=(raw, {})) as read:
            report = prices.audit({prices.SIGNAL: panel}, set(), object(), 'single',
                turnover_panels={prices.SIGNAL: {'000001': ('KOSDAQ', 'Private Issuer', 20, 240)}})
        read.assert_called_once()
        self.assertEqual(report['sessions'][prices.SIGNAL.isoformat()]['turnover']['exact_volume_and_amount'], 1)
        self.assertEqual(report['sessions'][prices.SIGNAL.isoformat()]['market_wide']['exact_ohlc'], 1)
        self.assertFalse(report['volume_session_equivalence_verified'])
        self.assertFalse(report['performance_validated'])

    def test_rate_limit_stops_followup_dates(self):
        with patch.dict(prices.BATCHES, {'two': (prices.SIGNAL, prices.ENTRY)}), \
             patch.object(prices, 'read_official_openings',
                          side_effect=KRXProbeError('KOSPI', 'http_rate_limited', 429)) as read:
            report = prices.audit({prices.SIGNAL: {}}, set(), object(), 'two')
        read.assert_called_once()
        self.assertEqual(report['failed_sessions'][prices.SIGNAL.isoformat()]['reason'], 'http_rate_limited')

    def test_pinned_turnover_reader_handles_csv_missing_fields_without_fabricating_zero(self):
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('a.parquet', 'b.parquet', 'end.csv')]
            paths[0].write_bytes(b'first')
            paths[1].write_bytes(b'second')
            paths[2].write_text('Code,Market,Name,Volume,Amount\n000002,KOSPI,Endpoint,0,\n')
            digests = tuple(sha256(p.read_bytes()).hexdigest() for p in paths)
            row = {'Date': datetime.combine(prices.SIGNAL, datetime.min.time()),
                   'Code': '000001', 'Market': 'KOSDAQ GLOBAL', 'Name': 'Historic',
                   'Volume': 20, 'Amount': 240}
            batch = SimpleNamespace(to_pylist=lambda: [row])
            table = SimpleNamespace(to_batches=lambda: [batch])
            empty = SimpleNamespace(to_batches=lambda: [])
            parquet = SimpleNamespace(read_table=lambda *a, **kw: table if a[0] == paths[0] else empty)
            with patch.dict(sys.modules, {'pyarrow': SimpleNamespace(parquet=parquet),
                                          'pyarrow.parquet': parquet}), \
                 patch.object(prices, 'SOURCE_DIGESTS', digests):
                report = prices.read_turnover_panels(paths, (prices.SIGNAL, prices.END))
            self.assertEqual(report[prices.SIGNAL]['000001'], ('KOSDAQ', 'Historic', 20, 240))
            self.assertEqual(report[prices.END]['000002'], ('KOSPI', 'Endpoint', 0, None))
            paths[0].write_bytes(b'changed')
            with patch.object(prices, 'SOURCE_DIGESTS', digests):
                with self.assertRaisesRegex(ValueError, 'historical_public_source_digest_mismatch'):
                    prices.read_turnover_panels(paths, (prices.SIGNAL,))
