"""Recover missing FY2022 revenue only from FY2023 official annual comparatives.

The FY2023 current-year figure must exactly equal the same-scope FY2023
annual major-account evidence. This is contemporary research evidence as of
2026, not source proof that the 2022 value was known in 2022 or 2023.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from v3_dart_bulk_history import (_amount, _day, _explicit_krw_currency,
                                  _mapping, _valid_receipt,
                                  verified_company_mapping_cache, write_json)
from v3_dart_revenue_2022 import (IDS, NONSTANDARD, EXACT_NAMES,
                                  expected_sources as missing_2022,
                                  verified_prior as verified_direct_2022)

SCHEMA = 'v3-dart-fy2022-revenue-from-fy2023-comparative-v1'
MAX_PER_RUN = 100
ISSUER_GAPS = frozenset({'comparative_revenue_conflict', 'comparative_anchor_mismatch',
                         'comparative_receipt_or_currency_mismatch',
                         'comparative_statement_identity_mismatch'})
MAX_ISSUER_GAPS_PER_RUN = 20


class ComparativeRevenueError(ValueError):
    """Fixed diagnostic strings only: never expose API payloads or secrets."""


def expected_sources(catalog, history, direct=None):
    possible = missing_2022(catalog, history)
    direct_rows = verified_direct_2022(direct, possible, catalog['date'])
    expected = {}
    for code, base in possible.items():
        if direct_rows.get(code, {}).get('status') == 'observed':
            continue
        row = history['records'][code]['years'].get('2023', {}).get(base['scope'], {})
        values = row.get('values', {})
        receipt = row.get('receipt_id')
        revenue = values.get('revenue') if type(values) is dict else None
        if (type(revenue) is not int or not 0 < revenue <= 10**18
            or not _valid_receipt(receipt, 2023, _day(catalog['date']))):
            continue
        expected[code] = {**base, 'comparative_filing_year': 2023,
                          'source_2023_receipt_id': receipt,
                          'source_2023_revenue_krw': revenue}
    return expected


def verified_prior(prior, expected, day):
    if prior is None:
        return {}
    if (type(prior) is not dict or prior.get('schema') != SCHEMA
        or prior.get('missing_is_not_zero') is not True
        or prior.get('investment_score_certified') is not False
        or prior.get('historical_PIT_source_certified') is not False
        or prior.get('raw_provider_rows_published') is not False
        or type(prior.get('records')) is not dict
        or type(prior.get('asof')) is not str or prior['asof'] > day):
        raise ComparativeRevenueError('untrusted_comparative_prior')
    accepted = {}
    for code, row in prior['records'].items():
        base = expected.get(code)
        if base is None or any(row.get(k) != v for k, v in base.items()):
            continue  # Direct FY2022 proof arrived, or scope/receipt changed.
        observed = row.get('status') == 'observed'
        if (row.get('status') not in ('observed', 'not_observed')
            or type(row.get('checked_on')) is not str or row['checked_on'] > day
            or row.get('source_error') not in (None, *ISSUER_GAPS)
            or (row.get('source_error') is not None and observed)
            or observed != (type(row.get('revenue_2022_krw')) is int
                            and 0 < row['revenue_2022_krw'] <= 10**18)
            or row.get('source_account_id') not in IDS | {NONSTANDARD, None}
            or (observed and row['source_account_id'] is None)
            or (not observed and row['source_account_id'] is not None)
            or row.get('source_receipt_id') != (
                base['source_2023_receipt_id'] if observed else None)
            or (row.get('source_account_id') == NONSTANDARD and
                row.get('source_account_name') not in EXACT_NAMES)
            or (row.get('source_account_id') != NONSTANDARD and
                row.get('source_account_name') is not None)):
            raise ComparativeRevenueError('tampered_comparative_prior')
        accepted[code] = row
    return accepted


def parse(payload, base, corp, day):
    status = payload.get('status') if type(payload) is dict else None
    rows = payload.get('list') if status == '000' else [] if status == '013' else None
    if type(rows) is not list or len(rows) > 20000 or (status == '000' and not rows):
        raise ComparativeRevenueError('comparative_provider_response_unusable')
    standard, named = [], []
    for row in rows:
        if (type(row) is not dict or row.get('corp_code') != corp
            or str(row.get('bsns_year')) != '2023' or row.get('reprt_code') != '11011'
            or ('stock_code' in row and row['stock_code'] != base['code'])
            or ('fs_div' in row and row['fs_div'] != base['scope'])):
            raise ComparativeRevenueError('comparative_statement_identity_mismatch')
        is_std = row.get('account_id') in IDS
        is_named = (row.get('account_id') == NONSTANDARD and
                    row.get('account_nm') in EXACT_NAMES)
        if (not (is_std or is_named) or row.get('sj_div') not in ('IS', 'CIS')
            or row.get('account_detail', '-') not in ('', '-')):
            continue
        if (row.get('rcept_no') != base['source_2023_receipt_id']
            or not _explicit_krw_currency(row.get('currency'))):
            raise ComparativeRevenueError('comparative_receipt_or_currency_mismatch')
        current, prior = _amount(row.get('thstrm_amount')), _amount(row.get('frmtrm_amount'))
        (standard if is_std else named).append((row['account_id'], row.get('account_nm'),
                                                current, prior))
    candidates = standard if standard else named
    observed = False
    source_error = None
    if candidates:
        if len({(c, p) for _, _, c, p in candidates}) > 1:
            source_error = 'comparative_revenue_conflict'
        elif (candidates[0][2] != base['source_2023_revenue_krw']
              or type(candidates[0][3]) is not int or not 0 < candidates[0][3] <= 10**18):
            source_error = 'comparative_anchor_mismatch'
        else:
            observed = True
    account_id, name, current, prior = candidates[0] if observed else (None, None, None, None)
    return {**base, 'checked_on': day,
            'status': 'observed' if observed else 'not_observed',
            'revenue_2022_krw': prior if observed else None,
            'source_receipt_id': base['source_2023_receipt_id'] if observed else None,
            'source_account_id': account_id,
            'source_account_name': name if account_id == NONSTANDARD else None,
            'source_error': source_error}


def snapshot(day, records, catalog):
    return {'schema': SCHEMA, 'asof': day, 'listed_total': catalog['listed_total'],
            'missing_is_not_zero': True, 'investment_score_certified': False,
            'historical_PIT_source_certified': False, 'raw_provider_rows_published': False,
            'provider_calls': 0, 'attempted_issuer_count': len(records),
            'observed_issuer_count': sum(r['status'] == 'observed' for r in records.values()),
            'records': dict(sorted(records.items()))}


def plan(catalog, history, direct, prior, *, priority_codes=(), limit=MAX_PER_RUN):
    if type(limit) is not int or not 1 <= limit <= MAX_PER_RUN:
        raise ComparativeRevenueError('invalid_comparative_limit')
    expected = expected_sources(catalog, history, direct)
    retained = verified_prior(prior, expected, catalog['date'])
    priority = set(priority_codes)
    selected = sorted((c for c in expected if c not in retained),
                      key=lambda c:(c not in priority, c))[:limit]
    return expected, retained, selected


def collect(catalog, history, direct, prior, *, key, session, cache=None,
            priority_codes=(), limit=MAX_PER_RUN, on_progress=None):
    expected, records, selected = plan(catalog, history, direct, prior,
        priority_codes=priority_codes, limit=limit)
    calls = 0
    issuer_gaps = 0
    if selected:
        if type(key) is not str or not re.fullmatch('[A-Za-z0-9]{40}', key):
            raise ComparativeRevenueError('missing_comparative_dart_key')
        members = {row['code']: row for row in catalog['rows']}
        mapping = verified_company_mapping_cache(cache, catalog['date'], members)
        if mapping is None:
            try:
                response = session.get('https://opendart.fss.or.kr/api/corpCode.xml',
                                       params={'crtfc_key':key}, timeout=(20, 95))
                response.raise_for_status()
                mapping = _mapping(response.content, set(members))
                calls += 1
            except Exception:
                raise ComparativeRevenueError('comparative_corp_mapping_unavailable') from None
        for code in selected:
            if code not in mapping:
                continue
            base = expected[code]
            try:
                response = session.get('https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',
                    params={'crtfc_key':key, 'corp_code':mapping[code],
                            'bsns_year':'2023', 'reprt_code':'11011',
                            'fs_div':base['scope']}, timeout=(20, 40))
                response.raise_for_status()
                calls += 1
                parsed = parse(response.json(), base, mapping[code], catalog['date'])
            except ComparativeRevenueError as error:
                if str(error) not in ISSUER_GAPS:
                    raise
                issuer_gaps += 1
                if issuer_gaps > MAX_ISSUER_GAPS_PER_RUN:
                    raise ComparativeRevenueError('too_many_comparative_issuer_source_anomalies') from None
                parsed = {**base, 'checked_on':catalog['date'],
                          'status':'not_observed', 'revenue_2022_krw':None,
                          'source_receipt_id':None, 'source_account_id':None,
                          'source_account_name':None, 'source_error':str(error)}
            except Exception:
                raise ComparativeRevenueError('comparative_transport_stopped') from None
            records[code] = parsed
            if on_progress:
                on_progress(snapshot(catalog['date'], records, catalog))
    return snapshot(catalog['date'], records, catalog), calls


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docs',default='docs')
    p.add_argument('--cache',default='.cache/v3-dart-company-codes.json')
    p.add_argument('--mode',choices=('dry-run','live','verify','refresh'),default='dry-run')
    p.add_argument('--limit',type=int,default=MAX_PER_RUN)
    args = p.parse_args(argv)
    root = Path(args.docs)
    read = lambda name: json.loads((root/name).read_text())
    catalog, history = read('v22-market-catalog.json'), read('v3-dart-bulk-history.json')
    direct_path = root/'v3-dart-revenue-2022-supplement.json'
    direct = read(direct_path.name) if direct_path.exists() else None
    path = root/'v3-dart-revenue-2023-comparative.json'
    prior = read(path.name) if path.exists() else None
    priority = set()
    source = root/'v3-operational-evidence-priority.json'
    if source.exists():
        row = read(source.name)
        if (row.get('schema')=='v3-r1d-observation-work-priority-v1'
            and row.get('asof')==catalog['date']):
            priority = {item['code'] for item in row.get('single_component_queue',[])
                if len(item.get('gaps',[]))==1 and
                item['gaps'][0].get('component')=='revenue_growth'}
    expected, retained, selected = plan(catalog,history,direct,prior,
                                        priority_codes=priority,limit=args.limit)
    if args.mode == 'dry-run':
        print(json.dumps({'status':'dry_run', 'eligible':len(expected),
                          'remaining':len(expected)-len(retained),
                          'selected':len(selected), 'provider_calls':0}))
        return
    if args.mode in ('verify','refresh'):
        if prior is None:
            raise ComparativeRevenueError('missing_comparative_source')
        state = snapshot(catalog['date'],retained,catalog)
        if args.mode == 'refresh':
            write_json(path,state)
        print(json.dumps({'status':args.mode,'observed':state['observed_issuer_count'],
                          'remaining':len(expected)-len(retained),'provider_calls':0}))
        return
    import requests
    cache = json.loads(Path(args.cache).read_text()) if Path(args.cache).exists() else None
    saved = 0
    def checkpoint(state):
        nonlocal saved
        write_json(path,state)
        saved += 1
        if saved % 10 == 0:
            print(json.dumps({'status':'comparative_revenue_checkpoint',
                              'newly_checked_issuers':saved,
                              'observed_total':state['observed_issuer_count']}),
                  flush=True)
    try:
        result, calls = collect(catalog,history,direct,prior,
            key=os.getenv('DART_API_KEY',''), session=requests.Session(),cache=cache,
            priority_codes=priority,limit=args.limit,on_progress=checkpoint)
    except ComparativeRevenueError as error:
        if not saved:
            raise
        print(json.dumps({'status':'partial_comparative_saved','saved':saved,
                          'halt':str(error),'provider_calls':saved}))
        return
    write_json(path,result)
    print(json.dumps({'status':'official_2023_comparatives_checked',
                      'provider_calls':calls,'observed':result['observed_issuer_count'],
                      'remaining':len(expected)-len(result['records'])}))


if __name__ == '__main__':
    main()
