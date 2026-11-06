"""Receipt-bound 2022 annual revenue recovery for missing DART major accounts.

Only 2022 revenue absent from the saved four-year major-account source is
eligible.  No source result, account amount, or 2022 comparative is invented.
The independent 2022 annual filing is queried once per unchanged receipt.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re

from v3_dart_bulk_history import (
    SCHEMA as BULK_SCHEMA, _amount, _day, _explicit_krw_currency,
    _mapping, _valid_receipt, verified_company_mapping_cache, write_json,
)

SCHEMA = 'v3-dart-2022-revenue-supplement-0.1'
YEAR = 2022
MAX_PER_RUN = 100
IDS = frozenset({
    'ifrs-full_Revenue', 'ifrs_Revenue',
    'ifrs-full_RevenueFromContractsWithCustomers',
    'ifrs_RevenueFromContractsWithCustomers',
})
NONSTANDARD = '-표준계정코드 미사용-'
EXACT_NAMES = frozenset({
    '매출액', '영업수익', '수익(매출액)', '매출액(수익)',
})
CODE = re.compile(r'[0-9]{6}\Z')
CORP = re.compile(r'[0-9]{8}\Z')


class Revenue2022Error(ValueError):
    """Never include provider payloads or credentials in an error."""


def expected_sources(catalog, history):
    day = _day(catalog.get('date'))
    if (history.get('schema') != BULK_SCHEMA or history.get('asof') != day.isoformat()
        or history.get('listed_total') != catalog.get('listed_total')
        or history.get('fiscal_years') != [2022, 2023, 2024, 2025]
        or history.get('historical_PIT_source_certified') is not False
        or type(catalog.get('rows')) is not list
        or catalog['listed_total'] != len(catalog['rows'])
        or type(history.get('records')) is not dict):
        raise Revenue2022Error('unaligned_revenue_2022_sources')
    expected = {}
    for member in catalog['rows']:
        code = member.get('code')
        if (type(code) is not str or not CODE.fullmatch(code)
            or code in expected or member.get('market') not in ('KOSPI', 'KOSDAQ')):
            raise Revenue2022Error('invalid_revenue_2022_market_identity')
        record = history['records'].get(code, {})
        years = record.get('years', {})
        if type(years) is not dict:
            continue
        choices = []
        for scope in ('CFS', 'OFS'):
            one = years.get('2022', {}).get(scope, {})
            if type(one) is not dict:
                continue
            if (type(one.get('values', {})) is not dict
                or one.get('values', {}).get('revenue') is not None):
                continue
            receipt = one.get('receipt_id')
            # A fully absent major-account 2022 scope can still be queried
            # from the single-company original annual statement. Its real
            # annual receipt must be supplied and validated by that provider.
            if receipt is not None and not _valid_receipt(receipt, YEAR, day):
                continue
            # Prioritize the source-complete 2023-25 chain, for which a real
            # 2022 revenue restores exactly the missing CAGR input.
            recent = [years.get(str(y), {}).get(scope, {}) for y in (2023, 2024, 2025)]
            ready = all(type(z) is dict and type(z.get('values')) is dict
                        and type(z['values'].get('revenue')) is int
                        and z['values']['revenue'] > 0
                        and _valid_receipt(z.get('receipt_id'), y, day)
                        for y, z in zip((2023, 2024, 2025), recent))
            if receipt is None and not ready:
                continue  # No independent same-scope year-chain anchor.
            candidate = {'code': code, 'name': member['name'],
                              'market': member['market'], 'scope': scope,
                              'fiscal_year': YEAR, 'receipt_id': receipt,
                              'recent_revenue_complete': ready}
            choices.append((not ready, scope != 'CFS', candidate))
        if choices:
            expected[code] = sorted(choices, key=lambda item: item[:2])[0][2]
    return expected


def verified_prior(prior, expected, day):
    if prior is None:
        return {}
    if (type(prior) is not dict or prior.get('schema') != SCHEMA
        or prior.get('missing_is_not_zero') is not True
        or prior.get('investment_score_certified') is not False
        or prior.get('raw_provider_rows_published') is not False
        or type(prior.get('records')) is not dict
        or type(prior.get('asof')) is not str or prior['asof'] > day):
        raise Revenue2022Error('untrusted_revenue_2022_previous')
    valid = {}
    for code, row in prior['records'].items():
        base = expected.get(code)
        if base is None or any(row.get(k) != v for k, v in base.items()):
            continue  # Changed 2022 annual receipt or accounting scope.
        if (row.get('status') not in ('observed', 'not_observed')
            or row.get('source_error') not in (None, 'conflicting_annual_accounts')
            or (row.get('source_error') is not None and row.get('status') != 'not_observed')
            or type(row.get('checked_on')) is not str
            or row['checked_on'] > day
            or (row['status'] == 'observed') != (
                type(row.get('revenue_krw')) is int and row['revenue_krw'] > 0)
            or row.get('source_account_id') not in IDS | {NONSTANDARD, None}
            or (row['status'] == 'not_observed' and row['source_account_id'] is not None)
            or (row['status'] == 'observed' and row['source_account_id'] is None)
            or (row['status'] == 'observed' and
                not _valid_receipt(row.get('source_receipt_id'), YEAR, _day(day)))
            or (base['receipt_id'] is not None and row['status'] == 'observed'
                and row['source_receipt_id'] != base['receipt_id'])
            or (row['status'] == 'not_observed' and row.get('source_receipt_id') is not None)
            or (row['source_account_id'] == NONSTANDARD and
                row.get('source_account_name') not in EXACT_NAMES)
            or (row['source_account_id'] != NONSTANDARD and
                row.get('source_account_name') is not None)):
            raise Revenue2022Error('tampered_revenue_2022_previous')
        valid[code] = row
    return valid


def parse(payload, base, corp, day):
    status = payload.get('status') if type(payload) is dict else None
    rows = payload.get('list') if status == '000' else [] if status == '013' else None
    if type(rows) is not list or len(rows) > 20000 or (status == '000' and not rows):
        raise Revenue2022Error('revenue_2022_provider_response_unusable')
    std, names = [], []
    for row in rows:
        if type(row) is not dict:
            raise Revenue2022Error('revenue_2022_malformed_statement_row')
        if (row.get('corp_code') != corp or str(row.get('bsns_year')) != str(YEAR)
            or row.get('reprt_code') != '11011'
            or ('stock_code' in row and row['stock_code'] != base['code'])
            or ('fs_div' in row and row['fs_div'] != base['scope'])):
            raise Revenue2022Error('revenue_2022_statement_identity_mismatch')
        standard = row.get('account_id') in IDS
        named = (row.get('account_id') == NONSTANDARD
                 and row.get('account_nm') in EXACT_NAMES)
        if (not (standard or named) or row.get('sj_div') not in ('IS', 'CIS')
            or row.get('account_detail', '-') not in ('', '-')):
            continue
        receipt = row.get('rcept_no')
        if (not _valid_receipt(receipt, YEAR, _day(day))
            or (base['receipt_id'] is not None and receipt != base['receipt_id'])
            or not _explicit_krw_currency(row.get('currency'))):
            raise Revenue2022Error('revenue_2022_receipt_or_unit_mismatch')
        amount = _amount(row.get('thstrm_amount'))
        if amount is None or amount <= 0:
            raise Revenue2022Error('revenue_2022_invalid_reported_amount')
        (std if standard else names).append((row['account_id'],
                                              row.get('account_nm'), amount, receipt))
    candidates = std if std else names
    if candidates and (len({a for _, _, a, _ in candidates}) != 1
                       or len({receipt for *_, receipt in candidates}) != 1):
        raise Revenue2022Error('revenue_2022_conflicting_annual_accounts')
    if candidates:
        account_id, name, value, receipt = candidates[0]
    else:
        account_id, name, value, receipt = None, None, None, None
    return {**base, 'checked_on': day,
            'status': 'observed' if candidates else 'not_observed',
            'revenue_krw': value, 'source_account_id': account_id,
            'source_receipt_id': receipt,
            'source_account_name': name if account_id == NONSTANDARD else None}


def snapshot(day, records, catalog):
    return {'schema': SCHEMA, 'asof': day, 'listed_total': catalog['listed_total'],
            'missing_is_not_zero': True, 'investment_score_certified': False,
            'raw_provider_rows_published': False, 'provider_calls': 0,
            'attempted_issuer_count': len(records),
            'observed_issuer_count': sum(r['status'] == 'observed' for r in records.values()),
            'records': dict(sorted(records.items()))}


def plan(catalog, history, prior=None, *, limit=MAX_PER_RUN, priority_codes=()):
    if type(limit) is not int or not 1 <= limit <= MAX_PER_RUN:
        raise Revenue2022Error('invalid_revenue_2022_limit')
    day = catalog['date']
    expected = expected_sources(catalog, history)
    retained = verified_prior(prior, expected, day)
    priority = set(priority_codes)
    if any(code not in expected for code in priority):
        # Priority is just a collection order. Never invent missing firms.
        priority.intersection_update(expected)
    selected = sorted((code for code in expected if code not in retained),
                      key=lambda code:(code not in priority,
                                       not expected[code]['recent_revenue_complete'], code))[:limit]
    return expected, retained, selected


def collect(catalog, history, prior, *, key, session, cache=None,
            limit=MAX_PER_RUN, priority_codes=(), on_progress=None):
    expected, records, selected = plan(catalog, history, prior, limit=limit,
                                       priority_codes=priority_codes)
    calls = 0
    if selected:
        if type(key) is not str or not re.fullmatch(r'[A-Za-z0-9]{40}', key):
            raise Revenue2022Error('missing_revenue_2022_dart_key')
        members = {row['code']: row for row in catalog['rows']}
        mapping = verified_company_mapping_cache(cache, catalog['date'], members)
        if mapping is None:
            try:
                resp = session.get('https://opendart.fss.or.kr/api/corpCode.xml',
                    params={'crtfc_key': key}, timeout=(20, 95))
                resp.raise_for_status()
                mapping = _mapping(resp.content, set(members))
                calls += 1
            except Exception:
                raise Revenue2022Error('revenue_2022_corp_mapping_unavailable') from None
        for code in selected:
            if code not in mapping:
                continue
            base = expected[code]
            try:
                resp = session.get('https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',
                    params={'crtfc_key': key, 'corp_code': mapping[code],
                            'bsns_year': str(YEAR), 'reprt_code': '11011',
                            'fs_div': base['scope']}, timeout=(20, 40))
                resp.raise_for_status()
                calls += 1
                try:
                    parsed = parse(resp.json(), base, mapping[code], catalog['date'])
                except Revenue2022Error as error:
                    if str(error) != 'revenue_2022_conflicting_annual_accounts':
                        raise
                    # A single issuer's contradictory annual accounts cannot
                    # earn score credit and must not halt other issuers' source
                    # collection.  Publish an explicit, receipt-bound gap.
                    parsed = {**base, 'checked_on': catalog['date'],
                              'status': 'not_observed', 'revenue_krw': None,
                              'source_account_id': None, 'source_receipt_id': None,
                              'source_account_name': None,
                              'source_error': 'conflicting_annual_accounts'}
            except Revenue2022Error:
                raise
            except Exception:
                raise Revenue2022Error('revenue_2022_transport_stopped') from None
            records[code] = parsed
            if on_progress:
                on_progress(snapshot(catalog['date'], records, catalog))
    return snapshot(catalog['date'], records, catalog), calls


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docs', default='docs')
    p.add_argument('--cache', default='.cache/v3-dart-company-codes.json')
    p.add_argument('--mode', choices=('dry-run', 'live', 'verify', 'refresh'), default='dry-run')
    p.add_argument('--limit', type=int, default=MAX_PER_RUN)
    a = p.parse_args(argv)
    root = Path(a.docs)
    read = lambda name: json.loads((root / name).read_text())
    catalog, history = read('v22-market-catalog.json'), read('v3-dart-bulk-history.json')
    path = root / 'v3-dart-revenue-2022-supplement.json'
    prior = read(path.name) if path.exists() else None
    priority = set()
    priority_path = root / 'v3-operational-evidence-priority.json'
    if priority_path.exists():
        q = read(priority_path.name)
        if (q.get('schema') == 'v3-r1d-observation-work-priority-v1'
            and q.get('asof') == catalog['date']):
            priority = {item['code'] for item in q.get('single_component_queue', [])
                        if len(item.get('gaps', [])) == 1 and
                        item['gaps'][0].get('component') == 'revenue_growth'}
    expected, retained, selected = plan(catalog, history, prior, limit=a.limit,
                                        priority_codes=priority)
    if a.mode == 'dry-run':
        print(json.dumps({'status': 'dry_run', 'eligible': len(expected),
                          'remaining': len(expected)-len(retained),
                          'selected': len(selected), 'provider_calls': 0}))
        return
    if a.mode in ('verify', 'refresh'):
        if prior is None:
            raise Revenue2022Error('revenue_2022_source_not_saved')
        refreshed = snapshot(catalog['date'], retained, catalog)
        if a.mode == 'refresh':
            write_json(path, refreshed)
        print(json.dumps({'status': a.mode, 'observed': refreshed['observed_issuer_count'],
                          'remaining': len(expected)-len(retained), 'provider_calls': 0}))
        return
    import requests
    cache = json.loads(Path(a.cache).read_text()) if Path(a.cache).exists() else None
    saved = 0
    def progress(state):
        nonlocal saved
        write_json(path, state)
        saved += 1
    try:
        result, calls = collect(catalog, history, prior, key=os.getenv('DART_API_KEY', ''),
            session=requests.Session(), cache=cache, limit=a.limit,
            priority_codes=priority, on_progress=progress)
    except Revenue2022Error as error:
        if not saved:
            raise
        print(json.dumps({'status': 'partial_receipt_bound_revenue_saved',
                          'saved': saved, 'halt': str(error), 'provider_calls': saved}))
        return
    write_json(path, result)
    print(json.dumps({'status': 'source_revenue_2022_only', 'provider_calls': calls,
                      'observed': result['observed_issuer_count'],
                      'remaining': len(expected)-result['attempted_issuer_count']}))


if __name__ == '__main__':
    main()
