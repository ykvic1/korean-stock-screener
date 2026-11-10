"""Manual 90-first recovery of exact-date historical 121-session observations.

Keep all fetched OHLCV bars in memory. Reuse a dated market snapshot only
when its previously published final close matches the newly retrieved source.
Research availability is *not* independent KRX regular-session certification.
"""
from __future__ import annotations

from collections import Counter
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile

from v3_unscored_path_observations import (
    classify_unavailable, observe, valid_observation, valid_unavailable_reason,
)


class RecoveryError(ValueError):
    pass


def select_targets(market, report):
    day = market['date']
    if (report['asof'] != day or len(report['rows']) < len(market['stocks']) or
            any(s['code'] not in report['rows'] for s in market['stocks'])):
        raise RecoveryError('operational_market_date_or_issuer_mismatch')
    first, second = [], []
    for stock in market['stocks']:
        code = stock['code']
        if (stock.get('price_date') != day or
                stock.get('v3_unscored_path_observations') is not None or
                stock.get('v3_unscored_path_unavailable_reason') !=
                'nonpositive_or_invalid_price_volume'):
            continue
        price = stock.get('price')
        if (type(price) not in (int, float) or
                not math.isfinite(price) or price <= 0):
            continue
        (first if report['rows'][code]['available'] < 90 else second).append(code)
    if len(first) + len(second) > 400:
        raise RecoveryError('unexpectedly_large_path_recovery_cohort')
    return sorted(first), sorted(second)


def analyze(code, cfg, day):
    import screener
    frame, latest = screener.last_series(None, code, cfg, day)
    close = frame['Close'].to_numpy(dtype=float)
    volume = frame['Volume'].to_numpy(dtype=float)
    asof = latest.isoformat()
    result = observe(close, volume, asof=asof, session_days=frame.index)
    reason = (classify_unavailable(close, volume, asof=asof,
                                   session_days=frame.index)
              if result is None else None)
    return {'price_date': asof, 'price_latest': float(close[-1]),
            'v3_unscored_path_observations': result,
            'v3_unscored_path_unavailable_reason': reason}


def collect(codes, day):
    import full_market_batch as batch
    if not codes:
        return {'rows': [], 'errors': [], 'excluded': [], 'examined': 0}
    manifest = {'schema': batch.SCHEMA, 'asof': day, 'codes': codes,
                'batch_size': batch.CHUNK,
                'batch_count': math.ceil(len(codes) / batch.CHUNK),
                'digest': batch._digest(day, codes)}
    batch.validate_manifest(manifest)
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory)
        for part in range(manifest['batch_count']):
            batch.scan(manifest=manifest, part=part, out=destination,
                       analyzer=analyze, workers=2)
        return batch.load_receipts(manifest, destination)


def apply(market, collected):
    day = market['date']
    by_code = {s['code']: s for s in market['stocks']}
    counts = Counter()
    for row in collected['rows']:
        code, candidate = row['code'], row['metrics']
        saved = by_code.get(code)
        if saved is None:
            counts['stock_not_in_market'] += 1
            continue
        if (saved.get('price_date') != day or
                candidate.get('price_date') != day or
                candidate.get('price_latest') != saved.get('price')):
            counts['published_close_or_date_mismatch'] += 1
            continue
        current = saved.get('v3_unscored_path_observations')
        updated = candidate.get('v3_unscored_path_observations')
        reason = candidate.get('v3_unscored_path_unavailable_reason')
        if not valid_observation(updated, day) or not valid_unavailable_reason(updated, reason):
            raise RecoveryError('invalid_path_source_receipt')
        if current is not None:
            if not valid_observation(current, day):
                raise RecoveryError('invalid_retained_path_receipt')
            counts['previously_recovered'] += 1
            continue
        if updated is None:
            counts['still_unavailable'] += 1
            counts[f'reason_{reason}'] += 1
            # Historical reports used an aggregate cause. Retain the more
            # specific diagnostic only after a fresh exact-close source read.
            if saved.get('v3_unscored_path_unavailable_reason') != reason:
                saved['v3_unscored_path_unavailable_reason'] = reason
                counts['reclassified_without_points'] += 1
            continue
        saved['v3_unscored_path_observations'] = updated
        saved['v3_unscored_path_unavailable_reason'] = None
        counts['new_observed_paths'] += 1
    return dict(counts)


def reconcile_source_receipt(market, newly_observed):
    """Keep the published source counts aligned with the recovered issuer rows.

    The receipt may advance only by the number of newly checked paths in
    this invocation. Unexpected prior drift is an integrity error, not an
    invitation to silently rewrite source provenance.
    """
    source = market.get('v3_unscored_path_source')
    stocks = market.get('stocks')
    candidates = market.get('candidate_count')
    day = market.get('date')
    if (type(source) is not dict or type(stocks) is not list or
            type(candidates) is not int or len(stocks) != candidates or
            type(newly_observed) is not int or newly_observed < 0):
        raise RecoveryError('invalid_recovery_source_receipt')
    actual = 0
    for stock in stocks:
        if stock.get('price_date') != day:
            raise RecoveryError('mixed_recovery_market_session')
        observation = stock.get('v3_unscored_path_observations')
        reason = stock.get('v3_unscored_path_unavailable_reason')
        if (not valid_observation(observation, day) or
                not valid_unavailable_reason(observation, reason)):
            raise RecoveryError('invalid_recovery_path_evidence')
        actual += observation is not None
    previous = source.get('observed_issuer_count')
    missing = source.get('unavailable_issuer_count')
    if (type(previous) is not int or type(missing) is not int or
            previous + missing != candidates or
            previous + newly_observed != actual):
        raise RecoveryError('source_coverage_receipt_mismatch')
    source['observed_issuer_count'] = actual
    source['unavailable_issuer_count'] = candidates - actual
    return actual


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--reconcile-only', action='store_true',
                        help='Repair a documented recovery commit whose source counters were not advanced')
    parser.add_argument('--expected-new-paths', type=int, default=0)
    parser.add_argument('--expected-asof')
    args = parser.parse_args(argv)
    docs = Path('docs')
    def read(name):
        return json.loads((docs / name).read_text(encoding='utf8'))
    initial = read('data.json')
    if args.reconcile_only:
        if (not args.expected_asof or initial['date'] != args.expected_asof or
                args.expected_new_paths <= 0):
            raise RecoveryError('explicit_dated_recovery_delta_required')
        total = reconcile_source_receipt(initial, args.expected_new_paths)
        (docs / 'data.json').write_text(json.dumps(
            initial, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
        print(json.dumps({'stage': 'reconciled_existing_dated_observations',
                          'asof': initial['date'],
                          'newly_recovered_paths': args.expected_new_paths,
                          'observed_paths': total}, sort_keys=True), flush=True)
        return
    if args.expected_new_paths or args.expected_asof:
        raise RecoveryError('reconciliation_arguments_require_reconcile_only')
    report = read('v3-operational-research-scores.json')
    first, second = select_targets(initial, report)
    print(json.dumps({'stage': 'selected', 'asof': initial['date'],
                      'below90': len(first), 'other': len(second)},
                     sort_keys=True), flush=True)
    # 90-first; never fetch unfinished same-day prices.
    primary = collect(first, initial['date'])
    other = collect(second, initial['date'])
    collected = {key: primary[key] + other[key]
                 for key in ('rows', 'errors', 'excluded')}
    counts = {'examined': primary['examined'] + other['examined'],
              'provider_errors': len(collected['errors']),
              'excluded': len(collected['excluded'])}
    subprocess.run(['git', 'pull', '--ff-only', 'origin', 'main'], check=True)
    latest = read('data.json')
    if latest['date'] != initial['date']:
        raise RecoveryError('market_close_changed_while_collecting')
    results = apply(latest, collected)
    reconcile_source_receipt(latest, results.get('new_observed_paths', 0))
    print(json.dumps({'stage': 'verified', 'asof': latest['date'],
                      **counts, **results}, sort_keys=True), flush=True)
    if results.get('new_observed_paths') or results.get('reclassified_without_points'):
        (docs / 'data.json').write_text(json.dumps(
            latest, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


if __name__ == '__main__':
    main()
