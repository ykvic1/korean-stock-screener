"""Classify unresolved current-listing price outcomes without source guesses.

Uses the existing saved full-market catalog, never queries KRX, fills a halt
price, or adds an investment-score point. A KRX-MARCAP listing's zero-share
reference is NOT independently verified 09:00-15:30 regular-session evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re

SCHEMA = 'v3-current-price-outcome-source-triage-0.1'
OUTPUT = 'v3-price-outcome-triage.json'
REASONS = (
    'price_observed_research_only',
    'price_error_zero_listing_volume_reference_unconfirmed',
    'price_error_positive_listing_volume_reference',
    'price_error_unknown_listing_volume_reference',
    'insufficient_history_listing_age_unverified',
    'unattempted_price_collection',
)
STATUS = {
    'priced', 'price_source_or_validation_error',
    'insufficient_history', 'unattempted_batch',
}


def classify(status, zero_reference):
    if status == 'priced':
        return REASONS[0]
    if status == 'price_source_or_validation_error':
        return (REASONS[1] if zero_reference is True else
                REASONS[2] if zero_reference is False else REASONS[3])
    if status == 'insufficient_history':
        return REASONS[4]
    if status == 'unattempted_batch':
        return REASONS[5]
    raise ValueError('invalid_price_outcome_status')


def build(catalog):
    if not isinstance(catalog, dict) or catalog.get('schema') != 'v22-current-listed-catalog-0.1':
        raise ValueError('untrusted_current_listing_catalog')
    asof = catalog.get('date')
    if type(asof) is not str or date.fromisoformat(asof).isoformat() != asof:
        raise ValueError('invalid_price_triage_asof')
    rows = catalog.get('rows')
    total = catalog.get('listed_total')
    if (type(total) is not int or not isinstance(rows, list) or
            not 1 <= total <= 20_000 or len(rows) != total):
        raise ValueError('incomplete_price_triage_population')
    counts = Counter()
    statuses = Counter()
    by_market = {'KOSPI': Counter(), 'KOSDAQ': Counter()}
    unresolved = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_price_triage_issuer')
        code, name = row.get('code'), row.get('name')
        market, status = row.get('market'), row.get('price_history_status')
        zero = row.get('listing_zero_volume_reference')
        if (type(code) is not str or re.fullmatch(r'[0-9A-Z]{6}', code) is None or
                code in seen or type(name) is not str or not name.strip() or
                market not in by_market or status not in STATUS or
                zero is not None and type(zero) is not bool):
            raise ValueError('invalid_price_triage_issuer')
        seen.add(code)
        reason = classify(status, zero)
        counts[reason] += 1
        statuses[status] += 1
        by_market[market][reason] += 1
        if status != 'priced':
            unresolved.append({'code': code, 'name': name, 'market': market,
                               'price_history_status': status,
                               'triage_reason': reason,
                               'listing_zero_volume_reference': zero})
    if (dict(statuses) != {k: v for k, v in catalog.get('status_counts', {}).items() if v}
            or counts[REASONS[0]] != catalog.get('priced_count')
            or counts[REASONS[1]] != catalog.get(
                'price_error_with_listing_zero_volume_reference_count')
            or len(unresolved) != total - catalog['priced_count']):
        raise ValueError('price_triage_census_mismatch')
    # Source triage, not an exchange halt/split/IPO determination. Official
    # status and market-session-specific traded volume remain unverified.
    return {
        'schema': SCHEMA, 'asof': asof, 'listed_total': total,
        'counts': {name: counts[name] for name in REASONS},
        'by_market': {m: {name: by_market[m][name] for name in REASONS}
                      for m in ('KOSPI', 'KOSDAQ')},
        'unresolved_count': len(unresolved),
        'unresolved': sorted(unresolved, key=lambda r: (r['triage_reason'], r['code'])),
        'source_scope': 'FDR_KRX_MARCAP_LISTING_REFERENCE_NOT_SESSION_CERTIFIED',
        'required_investment_session': 'KRX_REGULAR_0900_1530_ASIA_SEOUL',
        'krx_regular_only_volume_verified': False,
        'suspension_or_corporate_action_cause_verified': False,
        'missing_listing_start_does_not_prove_prelisting': True,
        'zero_trades_do_not_prove_exchange_suspension': True,
        'no_missing_close_carried_forward': True,
        'score_points_added': 0, 'investment_score_certified': False,
        'provider_calls': 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Saved-source price outcome triage')
    parser.add_argument('--mode', choices=('write', 'verify'), default='verify')
    parser.add_argument('--docs', type=Path, default=Path(__file__).resolve().parent / 'docs')
    args = parser.parse_args(argv)
    source = args.docs / 'v22-market-catalog.json'
    raw = source.read_bytes()
    report = build(json.loads(raw))
    report['catalog_sha256'] = sha256(raw).hexdigest()
    result = json.dumps(report, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
    target = args.docs / OUTPUT
    if args.mode == 'verify':
        if not target.is_file() or target.read_text(encoding='utf-8') != result:
            raise ValueError('price_outcome_triage_not_current')
    else:
        tmp = target.with_suffix('.tmp')
        tmp.write_text(result, encoding='utf-8')
        tmp.replace(target)
    print(json.dumps({'asof': report['asof'], 'listed': report['listed_total'],
                      'unresolved': report['unresolved_count'],
                      'zero_volume_reference_unconfirmed': report['counts'][REASONS[1]],
                      'provider_calls': 0, 'score_points_added': 0}))


if __name__ == '__main__':
    main()
