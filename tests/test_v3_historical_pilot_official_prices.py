from collections import Counter
import csv
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
    def test_batch_validation_blocks_repeated_dates_before_provider_access(self):
        first = prices.BATCHES['anchor'][0]
        second = prices.BATCHES['anchor'][1]
        prices.validate_unique_official_batches({'one': (first,), 'two': (second,)})
        for invalid in (
            {'one': (first,), 'two': (first,)},
            {'one': (first, first)},
            {'one': tuple(prices.BATCHES['anchor']) + (second,)},
            {'empty': ()},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                prices.validate_unique_official_batches(invalid)

    def test_official_batches_never_repeat_a_session_across_scopes(self):
        # Each official market-wide date costs authenticated calls. A session
        # already checked by a prior batch must not enter another batch.
        seen = {}
        for batch, sessions in prices.BATCHES.items():
            self.assertLessEqual(len(sessions), 5, batch)
            self.assertEqual(len(sessions), len(set(sessions)), batch)
            for session in sessions:
                self.assertNotIn(session, seen, (session, seen.get(session), batch))
                seen[session] = batch

    def test_remaining_downside_extremes_are_covered_once_by_existing_batches(self):
        historical = ('anchor', 'remaining', 'downside_priority')
        checked = {day.isoformat() for name in historical for day in prices.BATCHES[name]}
        with (prices.ROOT / 'ops/v3-performance-downside-horizons-20251113.csv').open(
                encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        remaining = {row['adverse_extreme_on'] for row in rows
                     if row['adverse_extreme_on'] and row['adverse_extreme_on'] not in checked}
        planned = [day.isoformat() for name, dates in prices.BATCHES.items()
                   if name == 'downside_followup' or name.startswith('downside_tail_')
                   for day in dates]
        self.assertEqual(len(planned), len(set(planned)))
        self.assertEqual(set(planned), remaining)
        self.assertEqual(len(remaining), 27)
        self.assertEqual(sum(row['adverse_extreme_on'] in remaining for row in rows), 44)

    def test_followup_targets_most_frequent_remaining_adverse_extremes(self):
        followup = prices.BATCHES['downside_followup']
        checked = {session.isoformat() for name in ('anchor', 'remaining', 'downside_priority')
                   for session in prices.BATCHES[name]}
        ledger = prices.ROOT / 'ops/v3-performance-downside-horizons-20251113.csv'
        with ledger.open(encoding='utf-8', newline='') as stream:
            extremes = Counter(row['adverse_extreme_on'] for row in csv.DictReader(stream)
                               if row['adverse_extreme_on'])
        remaining = sorted(extremes.keys() - checked, key=lambda session: (-extremes[session], session))
        self.assertEqual([session.isoformat() for session in followup], sorted(remaining[:5]))
        self.assertEqual(sum(extremes[session.isoformat()] for session in followup), 16)

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
        self.assertFalse(prices.exact_market_prices(result))

    def test_tail_batches_cover_every_unchecked_adverse_extreme_once(self):
        tail = [name for name in prices.BATCHES if name.startswith('downside_tail_')]
        checked = {date.isoformat() for name, dates in prices.BATCHES.items()
                   if name not in tail for date in dates}
        ledger = prices.ROOT / 'ops/v3-performance-downside-horizons-20251113.csv'
        with ledger.open(encoding='utf-8', newline='') as stream:
            remaining = Counter(row['adverse_extreme_on'] for row in csv.DictReader(stream)
                                if row['adverse_extreme_on'] and row['adverse_extreme_on'] not in checked)
        planned = [date.isoformat() for name in tail for date in prices.BATCHES[name]]
        self.assertEqual(planned, sorted(remaining))
        self.assertEqual(sum(remaining.values()), 28)
        self.assertTrue(set(tail) <= prices.STRICT_PRICE_BATCHES)
        for name in tail:
            prices.validate_dates([date.isoformat() for date in prices.BATCHES[name]])

    def test_reference_touches_cover_only_first_touch_dates_missing_from_prior_batches(self):
        checked = {date.isoformat() for name, dates in prices.BATCHES.items()
                   if name != 'reference_touches' for date in dates}
        ledger = prices.ROOT / 'ops/v3-performance-downside-reference-20251113.csv'
        with ledger.open(encoding='utf-8', newline='') as stream:
            touches = {row['first_observed_touch_on'] for row in csv.DictReader(stream)
                       if row['first_observed_touch_on']}
        planned = [date.isoformat() for date in prices.BATCHES['reference_touches']]
        self.assertEqual(planned, sorted(touches - checked))
        self.assertIn('reference_touches', prices.STRICT_PRICE_BATCHES)
        prices.validate_dates(planned)


    def test_frozen_adverse_extremes_resolve_to_actual_traded_symbols(self):
        targets = prices.downside_extreme_trading_symbols()
        self.assertEqual(len(targets), 35)
        self.assertEqual(sum(bool(codes) for codes in targets.values()), 35)
        self.assertTrue(all(len(code) == 6 for codes in targets.values() for code in codes))
        expected_dates = {
            prices.day(row['adverse_extreme_on'])
            for row in prices.ledger_rows(prices.ROOT, 'downside-horizons')
        }
        self.assertEqual(set(targets), expected_dates)
        self.assertEqual(sum(len(codes) for codes in targets.values()), 53)
        self.assertEqual(sum(
            prices.day(row['adverse_extreme_on']) in targets
            for row in prices.ledger_rows(prices.ROOT, 'downside-horizons')), 85)

    def test_specific_extreme_low_requires_exact_traded_security_and_shares(self):
        public = {'000001': ('KOSDAQ', 'Private Issuer', 100, 10, 13, 8, 12, 20)}
        official = {'000001': ('KOSDAQ', 'Private Issuer', 'traded', 10, 13, 8, 12, 100)}
        direct = prices.compare_market(public, official, {'000001'})
        self.assertTrue(prices.exact_traded_extreme_symbols(direct))
        for field in ('open', 'high', 'low', 'close'):
            tampered = {**direct, 'different_price_by_field': {
                **direct['different_price_by_field'], field: 1}}
            with self.subTest(aggregate_field=field):
                self.assertFalse(prices.exact_traded_extreme_symbols(tampered))
        for variant in (
            {'000001': ('KOSDAQ', 'Private Issuer', 'traded', 10, 13, 9, 12, 100)},
            {'000001': ('KOSDAQ', 'Private Issuer', 'traded', 10, 13, 8, 12, 101)},
            {'000002': official['000001']},
            {'000001': ('KOSDAQ', 'Private Issuer', 'no_reported_volume', None, None, None, None, 100)},
        ):
            with self.subTest(variant=variant):
                self.assertFalse(prices.exact_traded_extreme_symbols(
                    prices.compare_market(public, variant, {'000001'})))

    def test_extreme_issuer_low_is_aggregated_without_disclosing_codes(self):
        panel = {'000001': ('KOSDAQ', 'Private Issuer', 100, 10, 13, 8, 12, 20)}
        raw = {'000001': ('KOSDAQ', {
            'ISU_NM': 'Private Issuer', 'ACC_TRDVOL': '20',
            'ACC_TRDVAL': '240', 'LIST_SHRS': '100',
            'TDD_OPNPRC': '10', 'TDD_HGPRC': '13',
            'TDD_LWPRC': '8', 'TDD_CLSPRC': '12'})}
        with patch.dict(prices.BATCHES, {'one': (prices.SIGNAL,)}), \
             patch.object(prices, 'read_official_openings', return_value=(raw, {})):
            report = prices.audit(
                {prices.SIGNAL: panel}, set(), object(), 'one',
                extreme_symbols={prices.SIGNAL: {'000001'}})
        self.assertTrue(report['actual_extreme_trading_symbol_ohlc_matched'])
        self.assertEqual(report['actual_extreme_symbol_days_in_batch'], 1)
        self.assertEqual(report['actual_extreme_symbol_days_exact_ohlc'], 1)
        self.assertEqual(report['actual_extreme_target_sessions_expected'], 1)
        self.assertEqual(report['actual_extreme_target_sessions_compared'], 1)
        self.assertFalse(report['extreme_issuer_identity_pit_certified'])
        self.assertNotIn('000001', json.dumps(report))
        self.assertNotIn('Private Issuer', json.dumps(report))

    def test_market_match_requires_actual_zero_volume_in_both_sources(self):
        public = {'a': ('KOSDAQ', 'Issuer', 200, 10, 13, 8, 12, 20),
                  'b': ('KOSDAQ', 'Halted', 100, None, None, None, None, 0.0)}
        official = {'a': ('KOSDAQ', 'Issuer', 'traded', 10, 13, 8, 12, 200),
                    'b': ('KOSDAQ', 'Halted', 'no_reported_volume', None, None, None, None, 100)}
        self.assertTrue(prices.exact_market_prices(compare_market(public, official, public)))
        public['b'] = (*public['b'][:-1], None)
        self.assertFalse(prices.exact_market_prices(compare_market(public, official, public)))


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
        self.assertTrue(report['market_wide_ohlc_and_membership_matched'])
        self.assertFalse(report['volume_session_equivalence_verified'])
        self.assertFalse(report['performance_validated'])
        self.assertTrue(report['turnover_numeric_comparison_complete'])
        self.assertTrue(report['turnover_all_values_matched'])

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
                   'Volume': 20.0, 'Amount': 240.0}
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

    def test_exact_float_quantities_keep_integer_precision_boundaries(self):
        for value in (0.0, 20.0, float(2**53 - 1)):
            self.assertEqual(prices.public_quantity(value), int(value))
        for value in (True, False, -1.0, 1.5, float('nan'), float('inf'), float(2**53)):
            self.assertIsNone(prices.public_quantity(value))
        self.assertEqual(prices.public_quantity(str(2**60)), 2**60)

    def test_partial_or_mismatched_turnover_never_reports_a_successful_comparison(self):
        panel = {'000001': ('KOSDAQ', 'Private Issuer', 100, 10, 13, 8, 12, 20)}
        raw = {'000001': ('KOSDAQ', {
            **self.quote(), 'LIST_SHRS': '100',
            'TDD_OPNPRC': '10', 'TDD_HGPRC': '13', 'TDD_LWPRC': '8', 'TDD_CLSPRC': '12'})}
        for value, complete in ((None, False), (241, True)):
            with patch.dict(prices.BATCHES, {'single': (prices.SIGNAL,)}), \
                 patch.object(prices, 'read_official_openings', return_value=(raw, {})):
                report = prices.audit({prices.SIGNAL: panel}, set(), object(), 'single',
                    turnover_panels={prices.SIGNAL: {'000001': ('KOSDAQ', 'Private Issuer', 20.0, value)}})
            self.assertIs(report['turnover_numeric_comparison_complete'], complete)
            self.assertFalse(report['turnover_all_values_matched'])
