"""Research prior-year CAPEX facts in the verified FY2025 original XBRL.

The FY2023/FY2024 cash-flow amount must match the independently saved CFO of
that year, issuer and accounting scope. Only standard, actually disclosed
same-context PPE/intangible purchases may be retained as research candidates.
This module deliberately NEVER changes FCF observations or investment scores.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import time

import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

SCHEMA = 'v3-fcf-prior-comparative-research-v1'
CONTRACT = 'original_fy2025_explicit_prior_year_cfo_and_capex_v1'
PARTS = ('ppe_capex_krw', 'intangible_capex_krw')
REASONS = frozenset(('complete_comparative_candidate', 'partial_comparative_candidate',
                     'no_additional_comparative_capex', 'no_verified_prior_cfo_scope',
                     'reported_comparative_conflict', 'original_archive_unavailable'))


class ComparativeError(ValueError):
    pass


def eligible_missing_years(years, scope):
    missing = []
    for year in ('2023', '2024'):
        y = years.get(year, {})
        if (y.get('free_cashflow_krw') is None
            and y.get('year') == int(year)
            and y.get('scope') == scope
            and y.get('major_annual_receipt_matched') is True
            and type(y.get('operating_cashflow_krw')) is int
            and re.fullmatch(f'{int(year)+1}[0-9]{{10}}', str(y.get('annual_receipt_id')))):
            missing.append((year, y))
    return missing


def below_90_cashflow_priority(priority):
    """Return conditional 90-crossing issuer codes; not source evidence."""
    coverage = priority.get('coverage_90_first')
    if coverage is None:
        return set()  # Older minimal source-work fixtures.
    if type(coverage) is not dict:
        raise ComparativeError('invalid_fcf_90first_priority_contract')
    families = coverage.get('family_conditional_priority_codes')
    if (coverage.get('recovery_not_a_score_or_observation') is not True
        or coverage.get('missing_data_never_zero_filled') is not True
        or type(families) is not dict
        or type(families.get('dart_cashflow_capex')) is not list):
        raise ComparativeError('invalid_fcf_90first_priority_contract')
    codes = families['dart_cashflow_capex']
    if (any(type(code) is not str or not re.fullmatch('[0-9A-Z]{6}', code)
            for code in codes) or len(codes) != len(set(codes))):
        raise ComparativeError('invalid_fcf_90first_priority_codes')
    return set(codes)


def targets(priority, cash, previous=None):
    if (type(priority) is not dict or type(cash) is not dict
        or type(previous or {}) is not dict or not isinstance(priority.get('single_component_queue'), list)):
        raise ComparativeError('untrusted_fcf_comparative_queue')
    previous = previous or {}
    selected = []
    # Full-observation candidates go first, but multiple missing components
    # must not prevent acquiring independently verifiable FCF evidence.
    codes = []
    for item in priority['single_component_queue']:
        if (len(item.get('gaps', [])) != 1
            or item['gaps'][0].get('component') != 'cashflow_fcf_persistence'):
            continue
        codes.append(item['code'])
    multiple = priority.get('multiple_missing_index', [])
    shapes = priority.get('shape_names', [])
    if type(multiple) is not list or type(shapes) is not list:
        raise ComparativeError('untrusted_fcf_comparative_multiple_queue')
    for item in multiple:
        if (type(item) is not list or len(item) != 5
            or type(item[3]) is not int or not 0 <= item[3] < len(shapes)
            or type(shapes[item[3]]) is not str):
            raise ComparativeError('invalid_fcf_comparative_gap_shape')
        if 'financial:cashflow_fcf_persistence' in shapes[item[3]].split('+'):
            codes.append(item[0])
    # The historical queue listed one-gap / 100-completion candidates first.
    # Move below-90 recovery candidates to the front without inventing a
    # value or changing the original receipt-bound collection conditions.
    below_90 = below_90_cashflow_priority(priority)
    codes.sort(key=lambda code:code not in below_90)
    seen = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        years = cash.get('records', {}).get(code, {}).get('years', {})
        latest = years.get('2025', {})
        if (latest.get('year') != 2025 or latest.get('major_annual_receipt_matched') is not True
            or latest.get('scope') not in ('CFS', 'OFS')
            or type(latest.get('operating_cashflow_krw')) is not int
            or not re.fullmatch('2026[0-9]{10}', str(latest.get('annual_receipt_id')))):
            continue
        missing = eligible_missing_years(years, latest['scope'])
        if not missing:
            continue
        receipt = latest['annual_receipt_id']
        old = previous.get(code)
        if old is not None:
            verify_record(old)
            if (old['fy2025_receipt'] == receipt and old['scope'] == latest['scope']
                and anchors_unchanged(old, years)):
                continue
        selected.append((code, latest, missing))
    return selected


def anchors_digest(missing):
    return cf.fingerprint([{'year': year, 'receipt': data['annual_receipt_id'],
                            'scope': data['scope'], 'cfo': data['operating_cashflow_krw'],
                            'ppe': data.get('ppe_capex_krw'),
                            'intangible': data.get('intangible_capex_krw')}
                           for year, data in missing])


def anchors_unchanged(record, years):
    """Compare original anchors even after a verified individual-year repair.

    Only this exact research record's verified additions may be removed for
    comparison. A new missing year or changed receipt/CFO/component forces a
    new review rather than silently reusing a stale source marker.
    """
    from v3_dart_fcf_comparative_promotion import PROOF, verify_promoted_year
    missing = {year for year, _ in eligible_missing_years(years, record['scope'])}
    if not missing <= set(record['years']):
        return False
    original_anchors = []
    for year in sorted(record['years']):
        row = years.get(year)
        if type(row) is not dict:
            return False
        if row.get(PROOF) is not None:
            verify_promoted_year(row, years, record['code'], year)
            if row[PROOF]['source_record_digest'] != record['digest']:
                return False
            row = {**row, **row[PROOF]['existing_parts']}
        original_anchors.append((year, row))
    return record['prior_anchor_digest'] == anchors_digest(original_anchors)


def reconcile_prior(anchor, observed, source_reason):
    """Compare anchored original amounts, rejecting any saved-field conflict."""
    if type(observed) is not dict or type(source_reason) is not str:
        raise ComparativeError('invalid_prior_comparative_evidence')
    if not observed or observed.get('operating_cashflow_krw') is None:
        return {'reason': 'no_verified_prior_cfo_scope', 'candidate_parts': {}}
    if observed['operating_cashflow_krw'] != anchor['operating_cashflow_krw']:
        return {'reason': 'reported_comparative_conflict', 'candidate_parts': {}}
    if any(anchor.get(k) is not None and observed.get(k) is not None
           and anchor[k] != observed[k] for k in PARTS):
        return {'reason': 'reported_comparative_conflict', 'candidate_parts': {}}
    added = {k: observed[k] for k in PARTS
             if anchor.get(k) is None and type(observed.get(k)) is int and observed[k] >= 0}
    if not added:
        return {'reason': 'no_additional_comparative_capex', 'candidate_parts': {}}
    complete = all(anchor.get(k) is not None or k in added for k in PARTS)
    return {'reason': ('complete_comparative_candidate' if complete else
                       'partial_comparative_candidate'), 'candidate_parts': added}


def make_record(code, latest, missing, *, research, archive_hash, profile_hash, day):
    if set(research) != {year for year, _ in missing}:
        raise ComparativeError('unreviewed_prior_comparative_year')
    year_records = {}
    for year, anchor in missing:
        result = research[year]
        if (result['reason'] not in REASONS or type(result['candidate_parts']) is not dict
            or not set(result['candidate_parts']) <= set(PARTS)
            or any(anchor.get(k) is not None or type(v) is not int or v < 0
                   for k, v in result['candidate_parts'].items())):
            raise ComparativeError('invalid_prior_comparative_result')
        year_records[year] = {'original_receipt': anchor['annual_receipt_id'],
                              'anchor_cfo_krw': anchor['operating_cashflow_krw'], **result}
    record = {'contract': CONTRACT, 'code': code,
              'fy2025_receipt': latest['annual_receipt_id'], 'scope': latest['scope'],
              'prior_anchor_digest': anchors_digest(missing), 'years': year_records,
              'archive_sha256': archive_hash, 'profile_sha256': profile_hash,
              'observed_on': day, 'scores_unchanged': True}
    record['digest'] = cf.fingerprint(record)
    verify_record(record)
    return record


def verify_record(record):
    if (type(record) is not dict or set(record) != {'contract', 'code', 'fy2025_receipt',
            'scope', 'prior_anchor_digest', 'years', 'archive_sha256', 'profile_sha256',
            'observed_on', 'scores_unchanged', 'digest'}
        or record['contract'] != CONTRACT or record['scores_unchanged'] is not True
        or not re.fullmatch('[0-9A-Z]{6}', str(record['code']))
        or not re.fullmatch('2026[0-9]{10}', str(record['fy2025_receipt']))
        or record['scope'] not in ('CFS', 'OFS')
        or type(record['years']) is not dict or not record['years']
        or not set(record['years']) <= {'2023', '2024'}
        or any(type(record[k]) is not str or not re.fullmatch('[a-f0-9]{64}', record[k])
               for k in ('prior_anchor_digest', 'archive_sha256', 'profile_sha256'))
        or record['digest'] != cf.fingerprint({k: v for k, v in record.items() if k != 'digest'})):
        raise ComparativeError('invalid_fcf_comparative_record')
    for year, y in record['years'].items():
        if (type(y) is not dict or set(y) != {'original_receipt', 'anchor_cfo_krw',
                                            'reason', 'candidate_parts'}
            or not re.fullmatch(f'{int(year)+1}[0-9]{{10}}', str(y['original_receipt']))
            or type(y['anchor_cfo_krw']) is not int or y['reason'] not in REASONS
            or type(y['candidate_parts']) is not dict or not set(y['candidate_parts']) <= set(PARTS)
            or any(type(v) is not int or v < 0 for v in y['candidate_parts'].values())
            or (y['reason'] in ('complete_comparative_candidate', 'partial_comparative_candidate'))
                != bool(y['candidate_parts'])):
            raise ComparativeError('invalid_fcf_comparative_year')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    parser.add_argument('--limit', type=int, default=16)
    a = parser.parse_args()
    if not 1 <= a.limit <= 16:
        raise ComparativeError('invalid_comparative_limit')
    root = Path('docs')
    output_path = root / 'v3-fcf-prior-comparative-research.json'
    if output_path.is_symlink():
        raise ComparativeError('unsafe_comparative_output')
    read = lambda name: json.loads((root / name).read_text())
    market, catalog, bulk, cash, priority = [read(n) for n in (
        'data.json', 'v22-market-catalog.json', 'v3-dart-bulk-history.json',
        'v3-dart-cashflow-reference.json', 'v3-operational-evidence-priority.json')]
    cash = cf.retained_current_market_cashflow(market, catalog, bulk, cash)
    if (priority.get('asof') != catalog.get('date')
        or priority.get('listed_total') != catalog.get('listed_total')):
        raise ComparativeError('unaligned_fcf_comparative_priority')
    prior = read(output_path.name) if output_path.is_file() else {}
    if prior and (prior.get('schema') != SCHEMA or prior.get('scores_unchanged') is not True
                  or type(prior.get('records')) is not dict):
        raise ComparativeError('invalid_fcf_comparative_snapshot')
    records = prior.get('records', {})
    choices = targets(priority, cash, records)
    below_90 = below_90_cashflow_priority(priority)
    result = {'selected': min(a.limit, len(choices)), 'remaining': len(choices),
              'below90_mathematical_cashflow_candidates': len(below_90),
              'below90_unreviewed_source_eligible_issuers':
                  sum(code in below_90 for code, _, _ in choices),
              'reviewed': 0, 'complete_year_candidates': 0, 'provider_calls': 0,
              'reasons': {}, 'scores_unchanged': True}
    if a.mode == 'dry-run' or not choices:
        print(json.dumps(result, sort_keys=True))
        return
    import requests
    key = os.getenv('DART_API_KEY', '')
    if not re.fullmatch('[A-Za-z0-9]{40}', key):
        raise ComparativeError('missing_comparative_key')
    members = {row['code']: (row['name'], row['market']) for row in catalog['rows']}
    client = requests.Session()
    cache = Path('.cache/v3-dart-company-codes.json')
    mapping = verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,
                                             catalog['date'], members)
    def get(endpoint, params, stream=False):
        try:
            response = client.get('https://opendart.fss.or.kr/api/' + endpoint,
                                  params={'crtfc_key': key, **params},
                                  timeout=(20, 90), stream=stream)
            result['provider_calls'] += 1
            response.raise_for_status()
            return response
        except Exception:
            raise ComparativeError('comparative_provider_unavailable') from None
    if mapping is None:
        mapping = _mapping(get('corpCode.xml', {}).content, set(members))
    deadline = time.monotonic() + 360
    reasons = Counter()
    for code, latest, missing in choices[:a.limit]:
        if time.monotonic() + 120 >= deadline:
            break
        if code not in mapping:
            continue
        try:
            profile = get('company.json', {'corp_code': mapping[code]}).json()
            ids = original.company_ids(profile, mapping[code], code)
        except (ValueError, TypeError):
            raise ComparativeError('comparative_identity_unavailable') from None
        raw = xp._read_response_limited(get('fnlttXbrl.xml', {
            'rcept_no': latest['annual_receipt_id'], 'reprt_code': '11011'}, True))
        reviewed = {}
        for year, anchor in missing:
            try:
                observations, source_reason = original.archive_values(raw,
                    year=int(year), scope=latest['scope'], identifiers=ids,
                    expected_cfo=anchor['operating_cashflow_krw'])
                proposed = reconcile_prior(anchor, observations, source_reason)
            except (ValueError, TypeError, xp.XbrlPilotError):
                proposed = {'reason': 'original_archive_unavailable', 'candidate_parts': {}}
            reviewed[year] = proposed
        record = make_record(code, latest, missing, research=reviewed,
            archive_hash=hashlib.sha256(raw).hexdigest(),
            profile_hash=cf.fingerprint(profile), day=catalog['date'])
        records[code] = record
        for stored in records.values():
            verify_record(stored)
        snapshot = {'schema': SCHEMA, 'asof': catalog['date'],
                    'listed_total': catalog['listed_total'],
                    'scores_unchanged': True, 'reviewed_total': len(records),
                    'complete_year_candidates': sum(
                        y['reason'] == 'complete_comparative_candidate'
                        for rec in records.values() for y in rec['years'].values()),
                    'records': records}
        write_json(output_path, snapshot)
        result['reviewed'] += 1
        for y in reviewed.values():
            result['complete_year_candidates'] += int(y['reason'] == 'complete_comparative_candidate')
            reasons[y['reason']] += 1
    result['reasons'] = dict(reasons)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'blocked_comparative_source',
                          'reason': 'comparative_source_not_verified'}))
        raise SystemExit(1)
