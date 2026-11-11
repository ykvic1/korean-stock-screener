"""Compare saved closes with two official KRX day responses, aggregate only.

This is a *diagnostic* for failed provisional MA recovery, never a price
replacement or score promotion. KRX ACC_TRDVOL is not certified as regular-
session-only. No KRX rows, issuer identities, prices or API secrets are saved.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path

from official_probe.clients import KRXClient, KRXProbeError
from official_probe.validation import _numeric, ProbeError
from v3_krx_daily_anomaly_triage import classify_row
from v3_krx_history_collection import (
    _snapshot_market, HistoryCollectionError, MARKETS, validate_dates,
)
from v3_saved_close_ma_recovery import select_targets
from v3_price_session_capture import FEED, WINDOWS, capture_window

SCHEMA = 'v3-saved-close-official-krx-diagnostic-v1'
FORMAT_REVISION = 4
OUT = 'v3-krx-saved-close-crosscheck.json'
REASONS = ('exact_close', 'different_close', 'absent_in_krx',
           'invalid_krx_price_row', 'unusable_saved_close')


class CrosscheckError(ValueError):
    """Safe static cause only; do not echo response or issuer details."""


def check(market, first, second, client, *, snapshot_sha256):
    day = market.get('date')
    try:
        validate_dates([day])
    except (HistoryCollectionError, TypeError):
        raise CrosscheckError('invalid_or_unfinished_market_day') from None
    stocks = market.get('stocks')
    if (type(stocks) is not list or len(stocks) != market.get('candidate_count')
        or len({s.get('code') for s in stocks if isinstance(s, dict)}) != len(stocks)
        or not all(isinstance(s, dict) and s.get('market') in MARKETS
                   for s in stocks)):
        raise CrosscheckError('untrusted_saved_market')
    # The producer names its real source, not the session the user requested.
    # Old saved snapshots have no declaration and remain explicitly unknown.
    if 'price_feed' in market and (
        market['price_feed'] != FEED
        or market.get('price_regular_session_verified') is not False
        or market.get('volume_regular_session_verified') is not False
        or market.get('aftermarket_close_contamination_possible') is not True
    ):
        raise CrosscheckError('untrusted_price_feed_session_claim')
    by_code = {s['code']: s for s in stocks}
    if (not isinstance(first, list) or not isinstance(second, list)
        or len(set(first+second)) != len(first)+len(second)
        or any(code not in by_code for code in first+second)):
        raise CrosscheckError('untrusted_ma_recovery_priority')

    observed = {}
    source_sha = {}
    for exchange in MARKETS:
        rows, raw = client.daily_market(exchange, day)
        summary = _snapshot_market(rows, raw, market=exchange, asof=day)
        source_sha[exchange] = summary['source_sha256']
        for row in rows:
            key = (exchange, row['ISU_CD'])
            if key in observed:
                raise CrosscheckError('ambiguous_krx_market_identity')
            observed[key] = row

    def reason(code):
        stock = by_code[code]
        saved = stock.get('price')
        if (stock.get('price_date') != day or type(saved) not in (int, float)
            or not 0 < saved < 10**12):
            return 'unusable_saved_close'
        row = observed.get((stock['market'], code))
        if row is None:
            return 'absent_in_krx'
        quality, _ = classify_row(row)
        if quality != 'valid_numeric_observation':
            return 'invalid_krx_price_row'
        try:
            close = _numeric(row['TDD_CLSPRC'], 'TDD_CLSPRC', positive=True)
        except (ProbeError, KeyError, ValueError):
            return 'invalid_krx_price_row'
        return 'exact_close' if close == saved else 'different_close'

    def distance_profile(codes):
        """Aggregate differences without exporting source prices or identities."""
        bands = ('lt_0_1pct', '0_1_to_0_5pct', '0_5_to_1pct',
                 '1_to_3pct', '3_to_10pct', 'ge_10pct')
        gap = Counter()
        direction = Counter()
        intraday = Counter()
        alternative = Counter()
        for code in codes:
            if reason(code) != 'different_close':
                continue
            saved = by_code[code]['price']
            row = observed[(by_code[code]['market'], code)]
            close = _numeric(row['TDD_CLSPRC'], 'TDD_CLSPRC', positive=True)
            low = _numeric(row['TDD_LWPRC'], 'TDD_LWPRC', positive=True)
            high = _numeric(row['TDD_HGPRC'], 'TDD_HGPRC', positive=True)
            opening = _numeric(row['TDD_OPNPRC'], 'TDD_OPNPRC', positive=True)
            difference = abs(saved/close - 1) * 100
            threshold = (difference < .1, difference < .5, difference < 1,
                         difference < 3, difference < 10)
            band = next((name for name, below in zip(bands, threshold) if below),
                        bands[-1])
            gap[band] += 1
            direction['saved_above_krx' if saved > close else 'saved_below_krx'] += 1
            intraday['inside_krx_daily_low_high' if low <= saved <= high
                     else 'outside_krx_daily_low_high'] += 1
            for price_name, price in (('open', opening), ('high', high), ('low', low)):
                if saved == price:
                    alternative[price_name] += 1
        n = sum(gap.values())
        if (sum(direction.values()) != n or sum(intraday.values()) != n
            or sum(alternative.values()) > n*3):
            raise CrosscheckError('price_gap_accounting_mismatch')
        return {
            'different_close': n,
            'absolute_close_gap_pct': {name: gap[name] for name in bands},
            'direction': {name: direction[name] for name in
                          ('saved_above_krx','saved_below_krx')},
            'krx_daily_high_low_position': {name: intraday[name] for name in
                ('inside_krx_daily_low_high','outside_krx_daily_low_high')},
            'saved_equals_other_krx_ohlc': {name: alternative[name] for name in
                                           ('open','high','low')},
        }

    groups = {
        'saved_market': sorted(by_code),
        'below90_ma_priority': first,
        'ninety_plus_ma_priority': second,
    }
    counts = {}
    for name, codes in groups.items():
        c = Counter(reason(code) for code in codes)
        if sum(c.values()) != len(codes):
            raise CrosscheckError('crosscheck_count_mismatch')
        counts[name] = {'requested': len(codes),
                        **{k: c[k] for k in REASONS}}
    profile = {name: distance_profile(codes) for name, codes in groups.items()}
    for name in groups:
        if profile[name]['different_close'] != counts[name]['different_close']:
            raise CrosscheckError('price_gap_count_disagrees_with_saved_close')
    # Count mismatch exposure by the per-issuer capture time recorded during
    # the 100-stock batch. A 19:00 saved price is *not* proof its trades took
    # place after 16:00; it only identifies the feed risk window. Never
    # export issuer identities or the KRX source close to GitHub.
    by_window = {name: Counter() for name in WINDOWS}
    for code in groups['saved_market']:
        stamp = by_code[code].get('price_feed_observed_at_kst')
        window = capture_window(stamp)
        by_window[window][reason(code)] += 1
    window_counts = {name: {'requested': sum(by_window[name].values()),
                     **{reason_name: by_window[name][reason_name]
                        for reason_name in REASONS}}
                     for name in WINDOWS}
    if (sum(value['requested'] for value in window_counts.values())
        != counts['saved_market']['requested']
        or any(sum(value[name] for value in window_counts.values())
               != counts['saved_market'][name] for name in REASONS)):
        raise CrosscheckError('capture_window_counts_disagree')
    return {
        'schema': SCHEMA, 'format_revision': FORMAT_REVISION,
        'asof': day, 'snapshot_sha256': snapshot_sha256,
        'krx_market_source_sha256': source_sha,
        'source_requests': len(MARKETS), 'counts': counts,
        'mismatch_distance_profile': profile,
        'capture_time_exposure': {
            'price_feed_declared': market.get('price_feed') == FEED,
            'snapshot_published_window': capture_window(market.get('generated_kst')),
            'individual_quote_windows': window_counts,
            'aftermarket_capture_is_not_proof_of_trade_session': True,
            'changing_schedule_to_after_2000_does_not_certify_close': True,
        },
        'source_scope': 'KRX_DAILY_EOD_RAW_UNVERIFIED_SESSION_SPLIT',
        'krx_regular_only_volume_verified': False,
        'nxt_and_after_hours_exclusion_verified': False,
        'corporate_action_adjustments_verified': False,
        'historical_issuer_pit_verified': False,
        'changed_market_price_rows': 0, 'added_available_weight': 0,
        'investment_score_certified': False,
        'original_krx_rows_exported': False,
    }


def current_report(report, *, digest, day):
    """Avoid duplicate official calls on unchanged *validated* saved inputs."""
    try:
        c = report['counts']['saved_market']
        return (type(report) is dict and report['schema'] == SCHEMA
                and report['format_revision'] == FORMAT_REVISION
                and report['snapshot_sha256'] == digest and report['asof'] == day
                and report['source_requests'] == len(MARKETS)
                and report['investment_score_certified'] is False
                and report['changed_market_price_rows'] == 0
                and report['added_available_weight'] == 0
                and report['krx_regular_only_volume_verified'] is False
                and report['nxt_and_after_hours_exclusion_verified'] is False
                and type(c['requested']) is int and c['requested'] >= 1
                and sum(c[key] for key in REASONS) == c['requested']
                and report['mismatch_distance_profile']['saved_market']['different_close']
                == c['different_close']
                and sum(v['requested'] for v in report['capture_time_exposure']
                        ['individual_quote_windows'].values()) == c['requested'])
    except (KeyError, TypeError, ValueError):
        return False


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    p.add_argument('--docs', type=Path, default=Path('docs'))
    args = p.parse_args(argv)
    def read(name):
        raw = (args.docs/name).read_bytes()
        return json.loads(raw), sha256(raw).hexdigest()
    market, digest = read('data.json')
    priority, _ = read('v3-operational-evidence-priority.json')
    report, _ = read('v3-operational-research-scores.json')
    (first, second), _ = select_targets(market, priority, report)
    if args.mode == 'dry-run':
        print(json.dumps({'status':'dry_run','asof':market['date'],
            'below90':len(first),'ninety_plus':len(second),
            'provider_calls':0,'score_points_added':0}))
        return
    try:
        previous = json.loads((args.docs/OUT).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        previous = None
    if current_report(previous, digest=digest, day=market['date']):
        print(json.dumps({'status':'unchanged_saved_source',
            'asof':market['date'], 'provider_calls':0,'score_points_added':0}))
        return
    key = os.getenv('KRX_API_KEY', '')
    if not key.strip():
        raise CrosscheckError('krx_api_key_not_configured')
    try:
        result = check(market, first, second, KRXClient(key),
                       snapshot_sha256=digest)
    except (KRXProbeError, HistoryCollectionError, ProbeError, ValueError):
        raise CrosscheckError('krx_original_source_not_verified') from None
    target = args.docs/OUT
    target.write_text(json.dumps(result, ensure_ascii=False,
                       sort_keys=True, indent=2, allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'observed_source_counts_only',
        'asof':result['asof'], 'source_requests':result['source_requests'],
        'counts':result['counts'],'score_points_added':0},sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (CrosscheckError, OSError, KeyError, TypeError):
        print(json.dumps({'status':'blocked','reason':'krx_crosscheck_not_verified',
                          'score_points_added':0}))
        raise SystemExit(1)
