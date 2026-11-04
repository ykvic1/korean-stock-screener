"""Receipt-matched annual basic EPS comparatives, never inferred EPS.

All three amounts come from ONE annual statement/scope. These are reported
comparatives, not independent proof of share adjustments or historical PIT.
"""
from __future__ import annotations
import argparse
from datetime import date
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re

from v3_dart_bulk_history import (_mapping, _valid_receipt, verified_company_mapping_cache,
                                 _stable_digest, write_json)
from v3_dart_2025_owner_profit_probe import _read_company_cache_from_root

SCHEMA = 'v3-reported-annual-basic-eps-comparatives-v1'
EPS_IDS = {'ifrs-full_BasicEarningsLossPerShare', 'ifrs_BasicEarningsLossPerShare'}
MAX_PER_RUN = 100
YEAR = 2025


class EPSError(ValueError):
    """Fixed source-independent diagnostics only."""


def amount(raw):
    if raw is None or raw in ('', '-'):
        return None
    if type(raw) is not str:
        raise EPSError('invalid_reported_eps_amount')
    value = raw.strip()
    if value.startswith('(') and value.endswith(')'):
        value = '-' + value[1:-1]
    if not re.fullmatch(r'[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,8})?', value):
        raise EPSError('invalid_reported_eps_amount')
    number = Decimal(value.replace(',', ''))
    if not number.is_finite() or abs(number) > 10**9:
        raise EPSError('invalid_reported_eps_amount')
    return float(number)


def metrics(values):
    if (type(values) is not list or len(values) != 3 or any(
        v is not None and (type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 10**9)
        for v in values)):
        raise EPSError('invalid_reported_eps_series')
    complete = all(v is not None for v in values)
    positive = complete and all(v > 0 for v in values)
    cagr = 100 * (math.sqrt(values[2] / values[0]) - 1) if positive else None
    mean = sum(values) / 3 if positive else None
    cv = math.sqrt(sum((v - mean)**2 for v in values) / 3) / mean if positive else None
    return {'complete_three_years': complete,
            'positive_years': sum(v is not None and v > 0 for v in values),
            'cagr_2y_pct': round(cagr, 6) if cagr is not None else None,
            'positive_path_coefficient_of_variation': round(cv, 8) if cv is not None else None,
            'growth_basis': 'positive_reported_path' if positive else
                            'nonpositive_path_no_cagr' if complete else 'missing_comparatives'}


def parse(payload, *, corp, base, on_day):
    status = payload.get('status') if type(payload) is dict else None
    rows = payload.get('list') if status == '000' else [] if status == '013' else None
    if not isinstance(rows, list) or len(rows) > 18000 or (status == '000' and not rows):
        raise EPSError('official_eps_response_unavailable')
    matches = []
    for row in rows:
        if (type(row) is not dict or row.get('corp_code') != corp
            or str(row.get('bsns_year')) != str(YEAR) or row.get('reprt_code') != '11011'
            or ('stock_code' in row and row['stock_code'] != base['code'])
            or ('fs_div' in row and row['fs_div'] != base['fs_div'])):
            raise EPSError('eps_statement_identity_mismatch')
        if (row.get('account_id') not in EPS_IDS or row.get('sj_div') not in ('IS', 'CIS')
            or row.get('account_detail', '-') not in ('', '-')):
            continue
        if row.get('currency') != 'KRW' or row.get('rcept_no') != base['annual_receipt_id']:
            raise EPSError('eps_statement_receipt_or_currency_mismatch')
        matches.append((row['account_id'], [amount(row.get(key)) for key in
                       ('bfefrmtrm_amount', 'frmtrm_amount', 'thstrm_amount')]))
    if len({tuple(values) for _, values in matches}) > 1:
        raise EPSError('conflicting_reported_basic_eps')
    values = matches[0][1] if matches else [None, None, None]
    return {**base, 'observed_on': on_day,
            'source_account_id': sorted(x[0] for x in matches)[0] if matches else None,
            'reported_basic_eps_2023_2025': values,
            'comparative_basis': 'one_annual_filing_same_scope',
            'status': 'reported_basic_eps' if matches else 'standard_eps_not_observed',
            'metrics': metrics(values)}


def plan(catalog, book, prior=None, limit=MAX_PER_RUN):
    day = catalog.get('date')
    try:
        cutoff = date.fromisoformat(day)
    except (ValueError, TypeError):
        raise EPSError('invalid_eps_source_day') from None
    try:
        book_day = date.fromisoformat(book.get('asof'))
    except (ValueError, TypeError):
        raise EPSError('unaligned_eps_annual_sources') from None
    # Annual EPS is matched to an exact filing, not to the market price.
    # A new close must not stall an unchanged annual-receipt acquisition.
    # The official response still has to match that same receipt/scope.
    if (book_day > cutoff or book.get('raw_dart_account_rows_published') is not False
        or book.get('historical_point_in_time_verified') is not False
        or not isinstance(catalog.get('rows'), list)
        or catalog.get('listed_total') != len(catalog['rows'])
        or type(limit) is not int or not 1 <= limit <= MAX_PER_RUN):
        raise EPSError('unaligned_eps_annual_sources')
    members = {r['code']: (r['name'], r['market']) for r in catalog['rows']}
    if (len(members) != len(catalog['rows']) or any(
        type(code) is not str or not re.fullmatch('[0-9A-Z]{6}', code)
        or type(name) is not str or not name.strip() or market not in ('KOSPI', 'KOSDAQ')
        for code, (name, market) in members.items())):
        raise EPSError('duplicate_eps_issuer_census')
    expected = {}
    for row in book.get('records', []):
        annual = row.get('annual') or {}
        code = row.get('code')
        if (row.get('status') != 'observed' or code not in members
            or annual.get('year') != YEAR or annual.get('fs_div') not in ('CFS', 'OFS')
            or annual.get('currency') != 'KRW'
            or not _valid_receipt(annual.get('receipt_id'), YEAR, cutoff)):
            continue
        if code in expected:
            raise EPSError('duplicate_eps_annual_issuer')
        expected[code] = {'code': code, 'name': members[code][0], 'market': members[code][1],
                          'fiscal_year': YEAR, 'fs_div': annual['fs_div'],
                          'annual_receipt_id': annual['receipt_id']}
    retained = {}
    if prior is not None:
        if (prior.get('schema') != SCHEMA or prior.get('investment_score_certified') is not False
            or prior.get('corporate_actions_independently_verified') is not False
            or prior.get('raw_provider_rows_published') is not False
            or not isinstance(prior.get('records'), dict) or prior.get('market_asof', '') > day):
            raise EPSError('untrusted_prior_eps_source')
        for code, row in prior['records'].items():
            if code not in expected or any(row.get(k) != v for k, v in expected[code].items()):
                continue
            try:
                observed_day = date.fromisoformat(row.get('observed_on'))
            except (ValueError, TypeError):
                raise EPSError('invalid_saved_eps_source') from None
            if (observed_day > cutoff or row.get('comparative_basis') != 'one_annual_filing_same_scope'
                or row.get('status') not in ('reported_basic_eps', 'standard_eps_not_observed')
                or row.get('source_account_id') not in EPS_IDS | {None}
                or row.get('metrics') != metrics(row.get('reported_basic_eps_2023_2025'))
                or (row['status'] == 'standard_eps_not_observed' and
                    (row['source_account_id'] is not None or row['reported_basic_eps_2023_2025'] != [None]*3))
                or (row['status'] == 'reported_basic_eps' and row['source_account_id'] not in EPS_IDS)):
                raise EPSError('invalid_saved_eps_source')
            retained[code] = row
    groups = {m: sorted(code for code in expected if code not in retained and members[code][1] == m)
              for m in ('KOSPI', 'KOSDAQ')}
    selected = []
    while len(selected) < limit and any(groups.values()):
        for market in groups:
            if groups[market] and len(selected) < limit:
                selected.append(groups[market].pop(0))
    return day, members, expected, retained, selected


def snapshot(day, catalog, book, expected, records):
    return {'schema': SCHEMA, 'market_asof': day, 'fiscal_year': YEAR,
            'listed_total': catalog['listed_total'], 'eligible_annual_issuers': len(expected),
            'attempted_issuers': len(records), 'pending_issuers': len(expected)-len(records),
            'complete_three_year_eps_issuers': sum(r['metrics']['complete_three_years'] for r in records.values()),
            'investment_score_certified': False, 'corporate_actions_independently_verified': False,
            'raw_provider_rows_published': False,
            'source_sha256': {'catalog': _stable_digest(catalog), 'annual_book': _stable_digest(book)},
            'records': dict(sorted(records.items()))}


def collect(catalog, book, prior, *, key, session, cache=None, limit=MAX_PER_RUN, on_progress=None):
    day, members, expected, retained, selected = plan(catalog, book, prior, limit)
    calls = 0
    if selected:
        if type(key) is not str or not re.fullmatch('[A-Za-z0-9]{40}', key):
            raise EPSError('missing_official_eps_api_key')
        mapping = verified_company_mapping_cache(cache, day, members)
        if mapping is None:
            try:
                reply = session.get('https://opendart.fss.or.kr/api/corpCode.xml',
                                    params={'crtfc_key': key}, timeout=(20, 95))
                calls += 1
                reply.raise_for_status()
                mapping = _mapping(reply.content, set(members))
            except Exception:
                raise EPSError('official_eps_issuer_identity_unavailable') from None
        for code in selected:
            if code not in mapping:
                continue
            try:
                reply = session.get('https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',
                                    params={'crtfc_key': key, 'corp_code': mapping[code],
                                            'bsns_year': str(YEAR), 'reprt_code': '11011',
                                            'fs_div': expected[code]['fs_div']}, timeout=(20, 40))
                calls += 1
                reply.raise_for_status()
                parsed = parse(reply.json(), corp=mapping[code], base=expected[code], on_day=day)
            except EPSError:
                raise
            except Exception:
                raise EPSError('official_eps_transport_unavailable') from None
            retained[code] = parsed
            if on_progress:
                on_progress(snapshot(day, catalog, book, expected, retained))
    return snapshot(day, catalog, book, expected, retained), calls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs', default='docs')
    parser.add_argument('--cache', default='.cache/v3-dart-company-codes.json')
    parser.add_argument('--mode', choices=('dry-run', 'live', 'verify', 'refresh'), default='dry-run')
    parser.add_argument('--limit', type=int, default=MAX_PER_RUN)
    args = parser.parse_args(argv)
    root = Path(args.docs)
    catalog = json.loads((root/'v22-market-catalog.json').read_text())
    book = json.loads((root/'v3-dart-book-reference.json').read_text())
    target = root/'v3-dart-reported-eps.json'
    old = json.loads(target.read_text()) if target.is_file() else None
    day, _, expected, retained, selected = plan(catalog, book, old, args.limit)
    if args.mode == 'refresh':
        if target.is_symlink():
            raise EPSError('eps_output_must_not_be_symlink')
        write_json(target, snapshot(day, catalog, book, expected, retained))
        return
    if args.mode != 'live':
        if args.mode == 'verify' and old != snapshot(day, catalog, book, expected, retained):
            raise EPSError('saved_eps_snapshot_differs_from_sources')
        print(json.dumps({'status': 'eps_'+args.mode, 'pending': len(expected)-len(retained),
                          'selected': len(selected), 'provider_calls': 0}))
        return
    import requests
    saved = []
    def checkpoint(report):
        if target.is_symlink():
            raise EPSError('eps_output_must_not_be_symlink')
        write_json(target, report)
        saved.append(True)
    try:
        output, calls = collect(catalog, book, old, key=os.getenv('DART_API_KEY', ''),
                                session=requests.Session(), limit=args.limit,
                                cache=_read_company_cache_from_root(args.cache), on_progress=checkpoint)
    except EPSError as error:
        if not saved:
            raise
        print(json.dumps({'status': 'partial_eps_checkpoint_saved', 'reason': str(error)}))
        return
    checkpoint(output)
    print(json.dumps({'status': 'reported_eps_saved', 'provider_calls': calls,
                      'attempted': output['attempted_issuers'],
                      'complete_three_years': output['complete_three_year_eps_issuers'],
                      'pending': output['pending_issuers']}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('blocked: reported_eps_source_not_verified') from None
