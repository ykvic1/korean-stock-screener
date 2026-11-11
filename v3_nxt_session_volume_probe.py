"""Read-only NXT session-volume probe; never exports issuer-level volume.

NXT regular-market (pre/main/after) and closing-price trading are distinct
venue sessions. KRX venue volume is NOT included in either response. This
probe only validates the NXT component of the requested KRX+NXT full-day
volume, and never substitutes it for the screener's public volume or scores.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import requests


NXT_LAUNCH = '2025-03-04'
SOURCE = 'NXT_SESSION_VOLUMES_UNVERIFIED_FOR_SCORE'
HOST = 'https://nextrade.co.kr/'
ENDPOINTS = {
    'regular_market': ('brdinfoTime/brdinfoTimeList.do', 'brdinfoTimeList'),
    'closing_price': ('brdinfoEnd/brdinfoEndList.do', 'brdinfoEndList'),
}
MARKETS = {'STK': 'KOSPI', 'KSQ': 'KOSDAQ'}


class NXTProbeError(ValueError):
    """Static, safe error code: never expose response or URL."""


def quantity(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,3}(?:,\d{3})+|\d+', value):
        return int(value.replace(',', ''))
    raise NXTProbeError('invalid_nxt_quantity')


def canonical_day(day):
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise NXTProbeError('invalid_market_day')
    try:
        datetime.strptime(day, '%Y-%m-%d')
    except ValueError:
        raise NXTProbeError('invalid_market_day') from None
    return day.replace('-', '')


def fetch_session(session, kind, day):
    """Return validated private (market, code) -> quantity, not public data."""
    date_key = canonical_day(day)
    if kind not in ENDPOINTS or day < NXT_LAUNCH:
        raise NXTProbeError('nxt_session_not_applicable')
    endpoint, list_key = ENDPOINTS[kind]
    try:
        response = session.post(
            HOST + endpoint,
            data={'scAggDd': date_key, 'scMktId': '', 'searchKeyword': '',
                  'pageUnit': 10000, 'pageIndex': 1},
            headers={'X-Requested-With': 'XMLHttpRequest',
                     'Referer': HOST + 'menu/transactionStatusMain/menuList.do'},
            timeout=(8, 30), allow_redirects=False,
        )
    except requests.RequestException:
        raise NXTProbeError('nxt_transport_failure') from None
    if response.status_code != 200:
        raise NXTProbeError('nxt_http_failure')
    if not 0 < len(response.content) <= 10_000_000:
        raise NXTProbeError('nxt_response_size_invalid')
    try:
        envelope = response.json()
    except (ValueError, UnicodeError):
        raise NXTProbeError('nxt_invalid_json') from None
    if type(envelope) is not dict or type(envelope.get(list_key)) is not list:
        raise NXTProbeError('nxt_schema_invalid')
    rows = envelope[list_key]
    try:
        records = quantity(envelope.get('records'))
    except NXTProbeError:
        raise NXTProbeError('nxt_schema_invalid') from None
    if not 0 < records <= 5000 or len(rows) != records:
        raise NXTProbeError('nxt_incomplete_market_rows')
    if kind == 'regular_market':
        set_time = envelope.get('setTime', '')
        if (not isinstance(set_time, str) or not set_time.startswith(day)
            or not re.search(r'\b20:\d\d\b', set_time)):
            raise NXTProbeError('nxt_postclose_snapshot_unverified')
    observed = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get('aggDd', '')) != date_key:
            raise NXTProbeError('nxt_row_day_unverified')
        market = MARKETS.get(row.get('mktId'))
        code = row.get('isuSrdCd')
        if market is None or not isinstance(code, str) or not re.fullmatch(r'A\d{6}', code):
            raise NXTProbeError('nxt_row_identity_invalid')
        key = (market, code[1:])
        if key in observed:
            raise NXTProbeError('nxt_duplicate_market_identity')
        observed[key] = quantity(row.get('accTdQty'))
    return observed


def collect(session, day):
    """Join disjoint NXT sessions in memory, without claiming KRX coverage."""
    regular = fetch_session(session, 'regular_market', day)
    closing = fetch_session(session, 'closing_price', day)
    combined = regular.copy()
    for key, count in closing.items():
        combined[key] = combined.get(key, 0) + count
    return {
        'regular_market': regular,
        'closing_price': closing,
    'regular_plus_closing': combined,
        'overlap_issuers': len(regular.keys() & closing.keys()),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    p.add_argument('--docs', type=Path, default=Path('docs'))
    args = p.parse_args(argv)
    saved = json.loads((args.docs/'data.json').read_text(encoding='utf-8'))
    day = saved.get('date')
    canonical_day(day)
    if day < NXT_LAUNCH:
        print(json.dumps({'status':'not_applicable_before_nxt', 'asof':day,
                          'provider_calls':0, 'score_points_added':0}))
        return
    if args.mode == 'dry-run':
        print(json.dumps({'status':'dry_run', 'asof':day, 'provider_calls':0,
                          'score_points_added':0}))
        return
    # Must probe yesterday's *saved* trading day, not today's live snapshot.
    yesterday = (datetime.now(ZoneInfo('Asia/Seoul')).date()-timedelta(days=1)).isoformat()
    if day != yesterday:
        raise NXTProbeError('saved_market_not_previous_calendar_day')
    result = collect(requests.Session(), day)
    universe = {(s['market'],s['code']) for s in saved.get('stocks', [])
                if isinstance(s,dict) and s.get('market') in ('KOSPI','KOSDAQ')
                and isinstance(s.get('code'),str)}
    merged = result['regular_plus_closing']
    print(json.dumps({
        'status':'nxt_components_observed_no_score_change', 'asof':day,
        'source':SOURCE, 'provider_calls':2,
        'regular_issuer_count':len(result['regular_market']),
        'closing_issuer_count':len(result['closing_price']),
        'overlap_issuer_count':result['overlap_issuers'],
        'regular_plus_closing_issuer_count':len(merged),
        'regular_plus_closing_issuers_in_saved_universe':len(merged.keys() & universe),
        'krx_venue_volume_verified':False,
        'combined_krx_nxt_volume_verified':False,
        'other_nxt_sessions_included_verified':False,
        'original_nxt_volume_values_exported':False,
        'score_points_added':0,
    },sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (NXTProbeError, OSError, ValueError, KeyError, TypeError) as exc:
        allowed = frozenset((
            'invalid_market_day','nxt_session_not_applicable','invalid_nxt_quantity',
            'nxt_transport_failure','nxt_http_failure','nxt_response_size_invalid',
            'nxt_invalid_json','nxt_schema_invalid','nxt_incomplete_market_rows',
            'nxt_postclose_snapshot_unverified','nxt_row_day_unverified',
            'nxt_row_identity_invalid','nxt_duplicate_market_identity',
            'saved_market_not_previous_calendar_day',
        ))
        reason = str(exc) if isinstance(exc, NXTProbeError) and str(exc) in allowed else 'nxt_probe_not_verified'
        print(json.dumps({'status':'blocked','reason':reason,'score_points_added':0}))
        raise SystemExit(1)
