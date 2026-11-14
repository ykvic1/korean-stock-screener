"""Recover missing original annual sales from exact OpenDART annual receipts."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import islice
import json
import math
import os
from pathlib import Path
from time import monotonic

from v3_dart_bulk_history import (
    _day, _latest_revenue, _mapping, _midrank, _stable_digest, _valid_receipt, write_json,
)
from v3_dart_revenue_2022 import (
    EXACT_NAMES, IDS, NONSTANDARD, ISSUER_SOURCE_GAP_ERRORS, Revenue2022Error, parse,
)

OUTPUT = 'v3-dart-original-revenue-recovery.json'
SCHEMA = 'v3-dart-original-annual-sales-receipt-v1'
LIMIT = 96
REVIEWED = 'v3-dart-reviewed-original-revenue.json'
REVIEWED_SCHEMA = 'v3-dart-reviewed-original-annual-revenue-v1'


def choices(catalog, history):
    if history['asof'] != catalog['date'] or history['listed_total'] != catalog['listed_total']:
        raise ValueError('unaligned_original_revenue_sources')
    cutoff = _day(catalog['date'])
    expected = {}
    for member in catalog['rows']:
        code = member['code']
        years = history['records'].get(code, {}).get('years', {})
        for year in history['fiscal_years']:
            for scope in ('CFS', 'OFS'):
                row = years.get(str(year), {}).get(scope, {})
                receipt = row.get('receipt_id')
                if _valid_receipt(receipt, year, cutoff) and row['values'].get('revenue') is None:
                    expected[f'{code}:{year}:{scope}'] = dict(
                        code=code, scope=scope, fiscal_year=year, receipt_id=receipt)
    return expected


def verified(prior, expected, day):
    if prior is None:
        return {}
    if (prior['schema'] != SCHEMA or prior['asof'] > day
        or prior['raw_provider_rows_published'] is not False or type(prior['records']) is not dict):
        raise ValueError('invalid_original_revenue_checkpoint')
    retained = {}
    for key, record in prior['records'].items():
        base = expected.get(key)
        if base is None or any(record.get(k) != v for k, v in base.items()):
            continue
        digest = _stable_digest({k:v for k,v in record.items() if k != 'digest'})
        status = record.get('status')
        amount = record.get('revenue_krw')
        account = record.get('source_account_id')
        name = record.get('source_account_name')
        source_error = record.get('source_error')
        if (digest != record.get('digest') or _day(record['checked_on']) > _day(day)
            or status not in ('observed', 'not_observed')
            or (source_error is not None and (
                type(source_error) is not str or source_error not in ISSUER_SOURCE_GAP_ERRORS
                or status != 'not_observed'))
            or (status == 'observed' and (
                type(amount) is not int or not 0 < amount <= 10**18
                or record.get('source_receipt_id') != base['receipt_id']
                or not (account in IDS and name is None
                        or account == NONSTANDARD and name in EXACT_NAMES)))
            or (status == 'not_observed' and any(v is not None for v in (
                amount, account, name, record.get('source_receipt_id'))))):
            raise ValueError('invalid_original_revenue_record')
        retained[key] = record
    return retained


def review_statement(payload, base, corp, day):
    try:
        row = parse(payload, base, corp, day, fiscal_year=base['fiscal_year'])
    except Revenue2022Error as error:
        reason = str(error)
        if reason not in ISSUER_SOURCE_GAP_ERRORS:
            raise
        row = {
            **base, 'checked_on': day, 'status': 'not_observed', 'source_error': reason,
            'revenue_krw': None, 'source_account_id': None, 'source_account_name': None,
            'source_receipt_id': None,
        }
    row['digest'] = _stable_digest(row)
    return row


def reviewed_revenues(catalog, history, source, expected):
    if source is None:
        return {}
    if (source.get('schema') != REVIEWED_SCHEMA
            or type(source.get('records')) is not dict):
        raise ValueError('invalid_reviewed_original_revenue_registry')
    amounts = {}
    for key, entry in source['records'].items():
        base = expected.get(key)
        if base is None or entry['receipt_id'] != base['receipt_id']:
            continue
        annual = history['records'][base['code']]['years'][str(base['fiscal_year'])][base['scope']]
        saved_income = annual['values'].get('operating_income')
        if type(saved_income) is not int or saved_income != entry['operating_income_krw']:
            continue
        receipt = base['receipt_id']
        if (entry['account_name'] not in EXACT_NAMES
                or type(entry['revenue_krw']) is not int or not 0 < entry['revenue_krw'] <= 10**18
                or not entry['source_url'].startswith(f'https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&')
                or len(entry['source_sha256']) != 64
                or any(ch not in '0123456789abcdef' for ch in entry['source_sha256'])):
            raise ValueError('invalid_reviewed_original_revenue_statement')
        amounts[key] = entry['revenue_krw']
    return amounts


def overlay(catalog, history, source, *, reviewed=None):
    expected = choices(catalog, history)
    retained = verified(source, expected, catalog['date'])
    amounts = reviewed_revenues(catalog, history, reviewed, expected)
    amounts.update({key: entry['revenue_krw'] for key, entry in retained.items()
                    if entry['status'] == 'observed'})
    result = deepcopy(history)
    for key, amount in amounts.items():
        base = expected[key]
        row = result['records'][base['code']]['years'][str(base['fiscal_year'])][base['scope']]
        row['values']['revenue'] = amount
    return result, {expected[key]['code'] for key in amounts}


def sales_parts(catalog, history):
    ratios = {}
    markets = {r['code']:r['market'] for r in catalog['rows']}
    for member in catalog['rows']:
        code = member['code']
        cap = member.get('reference_market_cap_eok')
        revenue = _latest_revenue(history['records'].get(code), history['fiscal_years'], catalog['date'])
        if revenue is not None and type(cap) in (int, float) and math.isfinite(cap) and 0 < cap < 1e12:
            ratio = revenue / (cap*1e8)
            if 0 < ratio < 1e6:
                ratios[code] = round(ratio, 8)
    peers = {m:sorted(v for c,v in ratios.items() if markets[c] == m) for m in ('KOSPI','KOSDAQ')}
    return {code:{'points':round(round(5*_midrank(value, peers[markets[code]], 1), 6)/2, 6),
                  'available':2.5, 'asof':catalog['date']}
            for code,value in ratios.items() if len(peers[markets[code]]) >= 30}


def targets(report, expected, retained):
    result = []
    for key, base in expected.items():
        row = report['rows'][base['code']]
        score = row['available']
        if key in retained or not 83 <= score < 100:
            continue
        year = base['fiscal_year']
        missing_cagr = 'revenue_growth' not in row['parts']['financial']
        missing_margin = year >= 2023 and 'operating_growth_margin' not in row['parts']['financial']
        missing_sales = year == 2025 and 'sales_to_cap' not in row['parts']['valuation']
        if missing_cagr or missing_margin or missing_sales:
            result.append((score >= 90, (90 if score < 90 else 100)-score,
                           base['code'], base['scope'] != 'CFS', -year, key))
    return [item[-1] for item in sorted(result)[:LIMIT]]


def main():
    import requests
    root = Path('docs')
    def read(name):
        return json.loads((root/name).read_text(encoding='utf-8'))
    catalog, history, report = map(read, (
        'v22-market-catalog.json','v3-dart-bulk-history.json','v3-operational-research-scores.json'))
    day = catalog['date']
    if report['asof'] != day or len(report['rows']) != catalog['listed_total']:
        raise ValueError('unaligned_original_revenue_priority')
    path = root/OUTPUT
    expected = choices(catalog, history)
    retained = verified(read(OUTPUT) if path.exists() else None, expected, day)
    selected = targets(report, expected, retained)
    if not selected:
        print(json.dumps({'asof':day, 'selected':0, 'new_observed':0, 'provider_calls':0}))
        return
    key = os.environ['DART_API_KEY']
    calls = 1
    gaps = Counter()
    observed = 0
    def request(client, endpoint, **params):
        reply = client.get('https://opendart.fss.or.kr/api/'+endpoint,
                           params=dict(params, crtfc_key=key), timeout=(8,25))
        reply.raise_for_status()
        return reply

    with requests.Session() as client:
        mapping = _mapping(request(client,'corpCode.xml').content, {x['code'] for x in catalog['rows']})

    def fetch(identity):
        base = expected[identity]
        corp = mapping[base['code']]
        with requests.Session() as client:
            payload = request(client, 'fnlttSinglAcntAll.json', corp_code=corp,
                              bsns_year=str(base['fiscal_year']), reprt_code='11011',
                              fs_div=base['scope']).json()
        return identity, review_statement(payload, base, corp, day)

    queued = (identity for identity in selected if expected[identity]['code'] in mapping)
    deadline = monotonic()+420
    with ThreadPoolExecutor(max_workers=3) as pool:
        while monotonic() < deadline:
            batch = list(islice(queued, 3))
            if not batch:
                break
            for identity, row in pool.map(fetch, batch):
                calls += 1
                if row.get('source_error') is not None:
                    gaps[row['source_error']] += 1
                retained[identity] = row
                observed += row['status'] == 'observed'
                checkpoint = {'schema':SCHEMA, 'asof':day, 'raw_provider_rows_published':False,
                              'records':retained}
                verified(checkpoint, expected, day)
                write_json(path, checkpoint)
    print(json.dumps({'asof':day, 'selected':len(selected), 'new_observed':observed,
                      'issuer_source_gaps':sum(gaps.values()), 'issuer_source_gap_reasons':gaps,
                      'provider_calls':calls}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"original_annual_revenue_source_interrupted"}')
        raise SystemExit(1)
