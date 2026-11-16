"""Audit NXT session coverage without exporting issuer-level volume."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re

import requests

from official_probe.clients import KRXClient, KRXProbeError
from v3_aftermarket_cutoff import KST, NXT_START, target_policy
from v3_forward_daily_prices import number
from v3_krx_history_collection import MARKETS as KRX_MARKETS, HistoryCollectionError, _snapshot_market

NXT_LAUNCH = NXT_START.isoformat()
SOURCE = 'NXT_SESSION_VOLUMES_UNVERIFIED_FOR_SCORE'
HOST = 'https://nextrade.co.kr/'
ENDPOINTS = {
    'regular_market': ('brdinfoTime/brdinfoTimeList.do', 'brdinfoTimeList', 'Main'),
    'closing_price': ('brdinfoEnd/brdinfoEndList.do', 'brdinfoEndList', 'Closing'),
    'basket': ('bltdinfo/bltdinfoList.do', 'bltdinfoList', 'Basket'),
}
MARKETS = {'STK': 'KOSPI', 'KSQ': 'KOSDAQ'}


class NXTProbeError(ValueError):
    """Static, safe reason plus optional aggregate-only identity shape."""

    def __init__(self, reason, *, shape_counts=None):
        super().__init__(reason)
        self.shape_counts = shape_counts


def identity_shape_counts(rows):
    """Classify *only* invalid identity shapes; never export ticker values.

    Historical NXT feeds may include non-ordinary instruments. Do not simply
    skip them or recast them as listed stocks without provider evidence.
    """
    reasons = {
        'unexpected_market_id': 0,
        'missing_or_nonstring_short_code': 0,
        'code_missing_A_prefix': 0,
        'code_wrong_width': 0,
        'code_non_ascii_alphanumeric': 0,
    }
    for row in rows:
        if not isinstance(row, dict):
            reasons['unexpected_market_id'] += 1
            reasons['missing_or_nonstring_short_code'] += 1
            continue
        if row.get('mktId') not in MARKETS:
            reasons['unexpected_market_id'] += 1
        code = row.get('isuSrdCd')
        if not isinstance(code, str):
            reasons['missing_or_nonstring_short_code'] += 1
            continue
        if not code.startswith('A'):
            reasons['code_missing_A_prefix'] += 1
        if len(code) != 7:
            reasons['code_wrong_width'] += 1
        if not re.fullmatch(r'A[0-9A-Z]{6}', code):
            reasons['code_non_ascii_alphanumeric'] += 1
    return reasons



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


def session_observation(session, kind, day):
    """Validate a dated full page and retain a digest without publishing raw rows."""
    date_key = canonical_day(day)
    if kind not in ENDPOINTS or day < NXT_LAUNCH:
        raise NXTProbeError('nxt_session_not_applicable')
    endpoint, list_key, screen = ENDPOINTS[kind]
    data = {'scAggDd': date_key, 'scMktId': '', 'searchKeyword': '',
            'pageUnit': 10000, 'pageIndex': 1}
    if kind == 'basket':
        data['scSecuGroup'] = 'STOCK'
    try:
        response = session.post(
            HOST + endpoint,
            data=data,
            headers={'X-Requested-With': 'XMLHttpRequest',
                     'Referer': HOST + f'menu/transactionStatus{screen}/menuList.do'},
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
        total = quantity(envelope.get('totalCnt', records))
        pages = quantity(envelope.get('total', 1))
        page = quantity(envelope.get('page', 1))
    except NXTProbeError:
        raise NXTProbeError('nxt_schema_invalid') from None
    if (not 0 <= records <= 5000 or len(rows) != records or total != records
            or page != 1 or pages not in ((0, 1) if records == 0 else (1,))):
        raise NXTProbeError('nxt_incomplete_market_rows')
    digest = sha256(response.content).hexdigest()
    if not records:
        if kind == 'regular_market':
            raise NXTProbeError('nxt_incomplete_market_rows')
        # The empty envelope observed on the official site has no response
        # date. It cannot distinguish a true zero day from unavailable rows.
        return {'rows': {}, 'sha256': digest, 'status': 'empty_undated_response'}
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
        # Since 2026, some valid KRX/NXT short codes contain a letter, e.g.
        # A0008Z0. Numeric-only filtering silently excludes listed issuers.
        if market is None or not isinstance(code, str) or not re.fullmatch(r'A[0-9A-Z]{6}', code):
            raise NXTProbeError('nxt_row_identity_invalid',
                                shape_counts=identity_shape_counts(rows))
        key = (market, code[1:])
        if key in observed:
            raise NXTProbeError('nxt_duplicate_market_identity')
        observed[key] = quantity(row.get('bltdAccTdQty' if kind == 'basket' else 'accTdQty'))
    return {'rows': observed, 'sha256': digest, 'status': 'dated_rows_observed'}


def fetch_session(session, kind, day):
    return session_observation(session, kind, day)['rows']


def regular_volume_crosscheck(session, day, regular, *, share_capture=None):
    date_key = canonical_day(day)
    try:
        response = session.post(
            HOST + 'dailyInfo/dailyInfoList.do',
            data={'scBeginDe': date_key, 'scEndDe': date_key, 'pageUnit': 20, 'pageIndex': 1},
            headers={'X-Requested-With': 'XMLHttpRequest',
                     'Referer': HOST + 'menu/transactionStatusDaily/menuList.do'},
            timeout=(8, 30), allow_redirects=False,
        )
    except requests.RequestException:
        return {'status': 'unavailable', 'reason': 'nxt_aggregate_transport_failure'}
    try:
        if response.status_code != 200 or not 0 < len(response.content) <= 1_000_000:
            raise ValueError()
        envelope = response.json()
        rows = envelope['dailyInfoList']
        if (not isinstance(rows, list) or not 1 <= len(rows) <= 20
                or quantity(envelope['records']) != len(rows)
                or quantity(envelope['total']) != 1 or quantity(envelope['page']) != 1
                or any(not isinstance(row, dict) or row.get('aggDd') != date_key for row in rows)):
            raise ValueError()
        totals = []
        for row in rows:
            total = quantity(row.get('totalAccTdQty'))
            parts = sum(quantity(row.get(f'{period}AccTdQty')) for period in ('pre', 'main', 'aft'))
            active = quantity(row.get('totalIsuCnt'))
            if total or parts or active:
                totals.append((total, parts, active, row.get('mktShr')))
        if len(totals) != 1:
            raise ValueError()
        total, parts, active, market_share = totals[0]
        matches = total == parts == sum(regular.values()) and active == sum(v > 0 for v in regular.values())
        if matches and share_capture is not None:
            share_capture.update(regular_volume=total, reported_percent=market_share)
        return {'status': 'matched' if matches else 'mismatch', 'aggregate_rows': len(rows),
                'source_sha256': sha256(response.content).hexdigest()}
    except (ValueError, TypeError, KeyError, NXTProbeError):
        return {'status': 'unavailable', 'reason': 'nxt_aggregate_schema_or_date_unverified'}


def collect(session, day):
    """Retain session observations independently so overlaps stay auditable."""
    observations = {kind: session_observation(session, kind, day) for kind in ENDPOINTS}
    regular = observations['regular_market']['rows']
    closing = observations['closing_price']['rows']
    combined = regular.copy()
    for key, count in closing.items():
        combined[key] = combined.get(key, 0) + count
    market_share = {}
    crosscheck = regular_volume_crosscheck(session, day, regular, share_capture=market_share)
    return {
        'regular_market': regular,
        'closing_price': closing,
        'regular_plus_closing': combined,
        'overlap_issuers': len(regular.keys() & closing.keys()),
        'observations': observations,
        'regular_volume_crosscheck': crosscheck,
        'market_share_inputs': market_share,
    }


def aggregate(result, day, universe):
    observations = result['observations']
    merged = result['regular_plus_closing']
    return {
        'status':'nxt_components_observed_no_score_change', 'asof':day,
        'source':SOURCE, 'provider_calls':len(observations)+1,
        'regular_issuer_count':len(result['regular_market']),
        'closing_issuer_count':len(result['closing_price']),
        'overlap_issuer_count':result['overlap_issuers'],
        'regular_plus_closing_issuer_count':len(merged),
        'regular_plus_closing_issuers_in_saved_universe':(
            len(merged.keys() & universe) if universe is not None else None),
        'sessions':{kind: {'status':item['status'], 'issuer_count':len(item['rows']),
                           'source_sha256':item['sha256']}
                    for kind,item in observations.items()},
        'regular_volume_crosscheck':result['regular_volume_crosscheck'],
        'krx_venue_volume_verified':False,
        'combined_krx_nxt_volume_verified':False,
        'other_nxt_sessions_included_verified':False,
        'original_nxt_volume_values_exported':False,
        'score_points_added':0,
    }


def compare_market_share(krx_total, market_share):
    """Compare two aggregate denominator hypotheses with the published share."""
    try:
        text = str(market_share.get('reported_percent', ''))
        if not re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,2})?', text):
            raise ValueError()
        reported = Decimal(text)
        regular = market_share['regular_volume']
        if not 0 <= reported <= 100 or not 0 < regular or krx_total <= 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return {'status': 'unavailable'}
    # The data sometimes omit a trailing zero. Allow a one-decimal
    # rounding interval so apparent precision does not decide the result.
    tolerance = Decimal('0.05')
    numerator = Decimal(100 * regular)
    return {
        'status': 'compared',
        'reported_nxt_share_pct': float(reported),
        'api_plus_nxt_regular_matches': abs(numerator / (krx_total + regular) - reported) <= tolerance,
        'api_as_combined_denominator_matches': abs(numerator / krx_total - reported) <= tolerance,
        'same_security_universe_independently_verified': False,
    }


def compare_krx_panel(daily_market, day, nxt_keys, *, market_share=None):
    """Check dated identities and numeric coverage without inferring venue scope."""
    counts, hashes, by_code = {}, {}, {}
    for market in KRX_MARKETS:
        rows, raw = daily_market(market, day)
        stats = _snapshot_market(rows, raw, market=market, asof=day)
        hashes[market] = stats['source_sha256']
        counts[market] = Counter()
        for row in rows:
            code = row['ISU_CD']
            if code in by_code:
                raise NXTProbeError('krx_duplicate_market_identity')
            volume = number(row, 'ACC_TRDVOL')
            counts[market]['rows'] += 1
            counts[market]['numeric_volume_rows'] += volume is not None
            counts[market]['zero_volume_rows'] += volume == 0
            by_code[code] = market, volume
    joined = Counter()
    for market, code in sorted(nxt_keys):
        entry = by_code.get(code)
        state = ('absent' if entry is None else 'market_mismatch' if entry[0] != market else
                 'volume_missing' if entry[1] is None else 'volume_zero' if entry[1] == 0 else 'volume_positive')
        joined[state] += 1
    total = sum(value for _, value in by_code.values() if value is not None)
    scope = (compare_market_share(total, market_share) if market_share and not
             (joined['absent'] or joined['market_mismatch']
              or any(panel['rows'] != panel['numeric_volume_rows'] for panel in counts.values()))
             else {'status': 'unavailable'})
    return {'status':'dated_krx_openapi_rows_observed', 'source_sha256':hashes,
            'by_market':counts, 'nxt_identity_join':dict(joined),
            'market_share_denominator_check':scope,
            'krx_accumulated_volume_session_scope_verified':False}


def eligible_day(day, now, explicitly_requested):
    session_day = date.fromisoformat(day)
    if (session_day > now.date() or (session_day == now.date() and
            not target_policy(day, now=now)['complete_session_by_clock'])):
        raise NXTProbeError('nxt_session_not_complete')
    if not explicitly_requested and (now.date() - session_day).days > 7:
        raise NXTProbeError('saved_market_day_too_old')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    p.add_argument('--docs', type=Path, default=Path('docs'))
    p.add_argument('--asof', help='Completed NXT trading day (YYYY-MM-DD)')
    p.add_argument('--compare-krx', action='store_true', help='Audit the authenticated KRX panel')
    args = p.parse_args(argv)
    saved = json.loads((args.docs/'data.json').read_text(encoding='utf-8'))
    day = args.asof or saved.get('date')
    canonical_day(day)
    if day < NXT_LAUNCH:
        print(json.dumps({'status':'not_applicable_before_nxt', 'asof':day,
                          'provider_calls':0, 'score_points_added':0}))
        return
    if args.mode == 'dry-run':
        print(json.dumps({'status':'dry_run', 'asof':day, 'provider_calls':0,
                          'score_points_added':0}))
        return
    eligible_day(day, datetime.now(KST), args.asof is not None)
    with requests.Session() as session:
        result = collect(session, day)
    universe = {(s['market'],s['code']) for s in saved.get('stocks', [])
                if isinstance(s,dict) and s.get('market') in ('KOSPI','KOSDAQ')
                and isinstance(s.get('code'),str)}
    report = aggregate(result, day, universe if saved.get('date') == day else None)
    if args.compare_krx:
        key = os.getenv('KRX_API_KEY', '').strip()
        if key:
            panel_calls = 0
            client = KRXClient(key)

            def counted_daily_market(market, day):
                nonlocal panel_calls
                panel_calls += 1
                return client.daily_market(market, day)

            try:
                nxt_keys = set().union(*(item['rows'] for item in result['observations'].values()))
                report['krx_panel'] = compare_krx_panel(
                    counted_daily_market, day, nxt_keys, market_share=result['market_share_inputs'])
            except (KRXProbeError, HistoryCollectionError, NXTProbeError) as exc:
                report['krx_panel'] = {'status':'unavailable', 'reason':(
                    exc.reason if isinstance(exc, KRXProbeError) else str(exc))}
            report['provider_calls'] += panel_calls
        else:
            report['krx_panel'] = {'status':'unavailable', 'reason':'krx_key_unavailable'}
    print(json.dumps(report, sort_keys=True))


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
            'nxt_session_not_complete','saved_market_day_too_old','krx_duplicate_market_identity',
        ))
        reason = str(exc) if isinstance(exc, NXTProbeError) and str(exc) in allowed else 'nxt_probe_not_verified'
        report={'status':'blocked','reason':reason,'score_points_added':0}
        if (reason=='nxt_row_identity_invalid' and isinstance(exc,NXTProbeError)
            and isinstance(exc.shape_counts,dict)):
            # Aggregate shape categories only. No source values or example
            # tickers; a malformed row never becomes a zero-volume stock.
            report['invalid_identity_shape_counts']=exc.shape_counts
        print(json.dumps(report,sort_keys=True))
        raise SystemExit(1)
