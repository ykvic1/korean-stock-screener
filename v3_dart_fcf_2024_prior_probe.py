"""Verify missing FY2023 CAPEX in the original FY2024 annual XBRL.

Both FY2024 and FY2023 CFO must match independently receipt-bound cashflow
observations. Missing components never become zero. Collection only records
research; a separate proof-checked promotion step updates current observations.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import zipfile

import v3_dart_cashflow_enrichment as cf
import v3_dart_fcf_prior_comparative_probe as prior
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

SCHEMA = 'v3-fcf-fy2024-original-prior-year-research-v1'
CONTRACT = 'original_fy2024_both_years_cfo_matched_2023_capex_v1'
OUTPUT = 'v3-fcf-2024-prior-research.json'
REASONS = prior.REASONS | {'source_2024_cfo_unverified', 'source_2024_account_conflict'}


class Prior2024Error(ValueError):
    pass


def verify_record(record):
    keys = {'contract', 'code', 'scope', 'fy2024_receipt', 'source_cfo_2024_krw',
            'prior_anchor_digest', 'years', 'archive_sha256', 'profile_sha256',
            'observed_on', 'scores_unchanged', 'digest'}
    if (type(record) is not dict or set(record) != keys
        or record['contract'] != CONTRACT or record['scores_unchanged'] is not True
        or not re.fullmatch('[0-9A-Z]{6}', str(record['code']))
        or record['scope'] not in ('CFS', 'OFS')
        or not re.fullmatch('2025[0-9]{10}', str(record['fy2024_receipt']))
        or type(record['source_cfo_2024_krw']) is not int
        or type(record['years']) is not dict or set(record['years']) != {'2023'}
        or any(type(record[k]) is not str or not re.fullmatch('[a-f0-9]{64}', record[k])
               for k in ('prior_anchor_digest', 'archive_sha256', 'profile_sha256'))
        or record['digest'] != cf.fingerprint({k:v for k,v in record.items() if k != 'digest'})):
        raise Prior2024Error('invalid_fy2024_prior_research')
    date.fromisoformat(record['observed_on'])
    y = record['years']['2023']
    if (type(y) is not dict or set(y) != {'original_receipt', 'anchor_cfo_krw', 'reason', 'candidate_parts'}
        or not re.fullmatch('2024[0-9]{10}', str(y['original_receipt']))
        or type(y['anchor_cfo_krw']) is not int or y['reason'] not in REASONS
        or type(y['candidate_parts']) is not dict or not set(y['candidate_parts']) <= set(prior.PARTS)
        or any(type(v) is not int or v < 0 for v in y['candidate_parts'].values())
        or (y['reason'] in ('complete_comparative_candidate', 'partial_comparative_candidate'))
             != bool(y['candidate_parts'])):
        raise Prior2024Error('invalid_fy2024_prior_year')
    return True


def targets(cash, priority=(), previous=None):
    previous = previous or {}
    selected = []
    for code, entry in sorted(cash.get('records', {}).items(),
                              key=lambda item:(item[0] not in priority, item[0])):
        years = entry.get('years', {})
        anchor, source = years.get('2023', {}), years.get('2024', {})
        if (anchor.get('free_cashflow_krw') is not None
            or any(row.get('year') != year or row.get('major_annual_receipt_matched') is not True
                   or row.get('scope') not in ('CFS', 'OFS')
                   or type(row.get('operating_cashflow_krw')) is not int
                   or not re.fullmatch(f'{year+1}[0-9]{{10}}', str(row.get('annual_receipt_id')))
                   for row, year in ((anchor, 2023), (source, 2024)))
            or anchor['scope'] != source['scope']):
            continue
        old = previous.get(code)
        if old is not None:
            verify_record(old)
            if old['code'] != code:
                raise Prior2024Error('fy2024_prior_issuer_key_mismatch')
            if (old['fy2024_receipt'] == source['annual_receipt_id']
                and old['scope'] == source['scope']
                and old['source_cfo_2024_krw'] == source['operating_cashflow_krw']
                and old['prior_anchor_digest'] == prior.anchors_digest([('2023', anchor)])):
                continue
        selected.append((code, anchor, source))
    return selected


def probe_archive(raw, anchor, source, identifiers):
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        try:
            absent = xp.inspect_binary_archive(raw, year=2024, include_extension_evidence=False)
        except xp.XbrlPilotError:
            # Quota/auth/service errors are not evidence of missing accounts.
            # Stop before saving an unavailable marker for this issuer.
            raise Prior2024Error('fy2024_prior_provider_status_unavailable') from None
        if absent['file_absent']:
            return {'reason': 'original_archive_unavailable', 'candidate_parts': {}}
    try:
        values, _ = original.archive_values(raw, year=2024, scope=source['scope'],
            identifiers=identifiers, expected_cfo=source['operating_cashflow_krw'])
        if values.get('operating_cashflow_krw') != source['operating_cashflow_krw']:
            return {'reason': 'source_2024_cfo_unverified', 'candidate_parts': {}}
        if prior.reconcile_prior(source, values, '')['reason'] == 'reported_comparative_conflict':
            return {'reason': 'source_2024_account_conflict', 'candidate_parts': {}}
        values, reason = original.archive_values(raw, year=2023, scope=anchor['scope'],
            identifiers=identifiers, expected_cfo=anchor['operating_cashflow_krw'])
        return prior.reconcile_prior(anchor, values, reason)
    except (ValueError, TypeError, xp.XbrlPilotError):
        return {'reason': 'original_archive_unavailable', 'candidate_parts': {}}


def make_record(code, anchor, source, proposal, raw, profile, day):
    row = {'contract': CONTRACT, 'code': code, 'scope': source['scope'],
           'fy2024_receipt': source['annual_receipt_id'],
           'source_cfo_2024_krw': source['operating_cashflow_krw'],
           'prior_anchor_digest': prior.anchors_digest([('2023', anchor)]),
           'years': {'2023': {'original_receipt': anchor['annual_receipt_id'],
                             'anchor_cfo_krw': anchor['operating_cashflow_krw'], **proposal}},
           'archive_sha256': hashlib.sha256(raw).hexdigest(),
           'profile_sha256': cf.fingerprint(profile), 'observed_on': day,
           'scores_unchanged': True}
    row['digest'] = cf.fingerprint(row)
    verify_record(row)
    return row


def snapshot(day, total, records):
    for code, record in records.items():
        verify_record(record)
        if record['code'] != code:
            raise Prior2024Error('fy2024_prior_issuer_key_mismatch')
    return {'schema': SCHEMA, 'asof': day, 'listed_total': total,
            'scores_unchanged': True, 'reviewed_total': len(records),
            'complete_year_candidates': sum(r['years']['2023']['reason'] ==
                'complete_comparative_candidate' for r in records.values()), 'records': records}


def apply(cash, research_state):
    from v3_dart_fcf_comparative_promotion import promote_year
    if (research_state.get('schema') != SCHEMA or research_state.get('scores_unchanged') is not True
        or type(research_state.get('records')) is not dict):
        raise Prior2024Error('untrusted_fy2024_promotion_source')
    for code, record in research_state['records'].items():
        verify_record(record)
        if code != record['code'] or record['observed_on'] > research_state.get('asof', ''):
            raise Prior2024Error('fy2024_prior_issuer_or_date_mismatch')
    if cash.get('asof') and research_state.get('asof', '') > cash['asof']:
        raise Prior2024Error('future_fy2024_prior_snapshot')
    output = deepcopy(cash)
    codes = []
    for code, anchor, source in targets(cash):
        record = research_state['records'].get(code)
        if (record is None or record['fy2024_receipt'] != source['annual_receipt_id']
            or record['source_cfo_2024_krw'] != source['operating_cashflow_krw']
            or record['scope'] != source['scope']
            or record['prior_anchor_digest'] != prior.anchors_digest([('2023', anchor)])
            or record['years']['2023']['reason'] != 'complete_comparative_candidate'):
            continue
        output['records'][code]['years']['2023'] = promote_year(code, '2023',
            anchor, source, record, record['years']['2023'], source_fiscal_year=2024)
        codes.append(code)
    return output, codes


def collect(choices, previous, *, get, mapping, day, total, limit=8,
            on_progress=None, clock=time.monotonic):
    if type(limit) is not int or not 1 <= limit <= 16:
        raise Prior2024Error('invalid_fy2024_prior_limit')
    records = deepcopy(previous)
    deadline = clock() + 360
    reasons = Counter()
    reviewed = 0
    for code, anchor, source in choices:
        if reviewed >= limit or clock() + 120 >= deadline:
            break
        if code not in mapping:
            continue
        profile = get('company.json', {'corp_code': mapping[code]}).json()
        identifiers = original.company_ids(profile, mapping[code], code)
        raw = xp._read_response_limited(get('fnlttXbrl.xml', {
            'rcept_no': source['annual_receipt_id'], 'reprt_code': '11011'}, True))
        proposal = probe_archive(raw, anchor, source, identifiers)
        records[code] = make_record(code, anchor, source, proposal, raw, profile, day)
        result = snapshot(day, total, records)
        if on_progress is not None:
            on_progress(result)
        reviewed += 1
        reasons[proposal['reason']] += 1
    return {'reviewed': reviewed, 'reasons': dict(reasons)}, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    parser.add_argument('--limit', type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.limit <= 16:
        raise Prior2024Error('invalid_fy2024_prior_limit')
    root = Path('docs')
    output = root / OUTPUT
    if output.is_symlink():
        raise Prior2024Error('unsafe_fy2024_prior_output')
    read = lambda name: json.loads((root / name).read_text())
    market, catalog, bulk, cash, report, comparison = [read(n) for n in (
        'data.json', 'v22-market-catalog.json', 'v3-dart-bulk-history.json',
        'v3-dart-cashflow-reference.json', 'v3-operational-research-scores.json',
        'v3-operational-research-comparison.json')]
    cash = cf.retained_current_market_cashflow(market, catalog, bulk, cash)
    priority = xp.operational_gap_priority(report, comparison, catalog, bulk)
    from v3_v30b_release_gate import R1D_PART_LIMITS
    priority = {code for code in priority if {
        (area, key) for area, parts in R1D_PART_LIMITS.items()
        for key, maximum in parts.items()
        if maximum - report['rows'][code]['parts'][area].get(key, [0, 0])[1] > .00001
    } == {('financial', 'cashflow_fcf_persistence')}}
    saved = read(OUTPUT) if output.is_file() else {}
    if saved and (saved.get('schema') != SCHEMA or saved.get('scores_unchanged') is not True
                  or type(saved.get('records')) is not dict):
        raise Prior2024Error('invalid_fy2024_prior_checkpoint')
    records = saved.get('records', {})
    choices = targets(cash, priority, records)
    result = {'selected': min(args.limit, len(choices)), 'remaining': len(choices),
              'priority_targets': sum(code in priority for code, _, _ in choices),
              'provider_calls': 0, 'scores_unchanged': True}
    if args.mode == 'dry-run' or not choices:
        print(json.dumps(result, sort_keys=True))
        return
    import requests
    key = os.getenv('DART_API_KEY', '')
    if not re.fullmatch('[A-Za-z0-9]{40}', key):
        raise Prior2024Error('missing_fy2024_prior_key')
    client = requests.Session()
    def get(endpoint, params, stream=False):
        try:
            result['provider_calls'] += 1
            response = client.get('https://opendart.fss.or.kr/api/' + endpoint,
                params={'crtfc_key': key, **params}, timeout=(20, 90), stream=stream)
            response.raise_for_status()
            return response
        except Exception:
            raise Prior2024Error('fy2024_prior_provider_unavailable') from None
    members = {row['code']: (row['name'], row['market']) for row in catalog['rows']}
    cache = Path('.cache/v3-dart-company-codes.json')
    mapping = verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,
                                             catalog['date'], members)
    if mapping is None:
        mapping = _mapping(get('corpCode.xml', {}).content, set(members))
    collected, _ = collect(choices, records, get=get, mapping=mapping,
        day=catalog['date'], total=catalog['listed_total'], limit=args.limit,
        on_progress=lambda state: write_json(output, state))
    result.update(collected)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'blocked_fy2024_prior_source',
                          'reason': 'fy2024_prior_source_not_verified'}))
        raise SystemExit(1)
