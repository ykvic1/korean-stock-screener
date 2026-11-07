"""Read-only FY2024 original-XBRL EPS overlap research for missing FY2023 EPS.

This never adds a scored EPS observation.  A verified FY2024 original annual
receipt may corroborate a 2023 basic-EPS comparative only when its FY2024
basic EPS agrees exactly with the FY2025 filing's FY2024 comparative.
Mixing the two filing series into a scored 3-year record is forbidden here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import zipfile

import v3_dart_reported_eps as eps
import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_eps_recovery as current
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

SCHEMA = 'v3-eps-prior-original-receipt-research-v1'
CONTRACT = 'fy2024_original_eps_fy2024_overlap_cfo_v1'
REASONS = frozenset(('candidate_2023_verified', 'original_file_absent',
                     'no_verified_cfo_scope', 'missing_2023_basic_eps',
                     'missing_2024_overlap', '2024_overlap_conflict',
                     'unusable_original_archive'))
X = original.X


class PriorEPSError(ValueError):
    pass


def candidates(reported, cash, already=None):
    """Only issuer/scope/receipt-matched 2023-only EPS gaps with FY2024 CFO."""
    already = already or {}
    if type(reported) is not dict or type(cash) is not dict or type(already) is not dict:
        raise PriorEPSError('invalid_prior_eps_inputs')
    choices = []
    for code, row in sorted(reported.get('records', {}).items()):
        series = row.get('reported_basic_eps_2023_2025')
        if (not isinstance(series, list) or len(series) != 3 or series[0] is not None
            or type(series[1]) not in (float, int) or type(series[2]) not in (float, int)
            or row.get('fs_div') not in ('CFS', 'OFS')):
            continue
        anchor = cash.get('records', {}).get(code, {}).get('years', {}).get('2024', {})
        receipt = anchor.get('annual_receipt_id')
        if (anchor.get('major_annual_receipt_matched') is not True
            or anchor.get('year') != 2024 or anchor.get('scope') != row['fs_div']
            or type(anchor.get('operating_cashflow_krw')) is not int
            or not isinstance(receipt, str) or not re.fullmatch(r'2025\d{10}', receipt)):
            continue
        identity = (receipt, row.get('annual_receipt_id'), row['fs_div'],
                    anchor['operating_cashflow_krw'], series[1])
        prior = already.get(code)
        if prior is not None:
            verify_record(prior)
            if (prior['receipt_2024'], prior['receipt_2025'], prior['scope'],
                prior['cfo_2024_krw'], prior['saved_eps_2024']) == identity:
                continue
        choices.append((code, row, anchor))
    return choices


def extract_instance(raw, *, scope, identifiers, cfo_anchor, diagnostic=None):
    diagnostic = diagnostic if diagnostic is not None else Counter()
    root, bindings, scoped = original.verified_contexts(
        raw, year=2024, scope=scope, identifiers=identifiers, diagnostic=diagnostic)
    if root.tag != X + 'xbrl':
        return {}, False
    won, per_share = current.units(root, bindings)
    anchors = []
    for fact in root:
        uri, local = xp._qualified_name(fact.tag)
        if (scoped.get(fact.get('contextRef')) and fact.get('unitRef') in won
            and xp._ifrs_full_namespace(uri)
            and local in {name.split('_', 1)[1] for name in cf.CFO_IDS}):
            anchors.append(original._number(fact))
    if not anchors:
        return {}, False
    if any(value is None for value in anchors) or set(anchors) != {cfo_anchor}:
        raise PriorEPSError('original_2024_cfo_scope_conflict')
    diagnostic['verified_2024_cfo_instances'] += 1
    out = {}
    for year in (2023, 2024):
        _, _, contexts = original.verified_contexts(
            raw, year=year, scope=scope, identifiers=identifiers)
        values = []
        for fact in root:
            uri, local = xp._qualified_name(fact.tag)
            if (contexts.get(fact.get('contextRef')) and xp._ifrs_full_namespace(uri)
                and local == 'BasicEarningsLossPerShare'):
                diagnostic['candidate_original_eps_facts'] += 1
                if fact.get('unitRef') not in per_share:
                    continue
                if fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true', '1'):
                    raise PriorEPSError('nil_original_2024_eps')
                number = eps.amount((fact.text or '').strip())
                if number is None:
                    raise PriorEPSError('blank_original_2024_eps')
                values.append(number)
        if values:
            if len(set(values)) != 1:
                raise PriorEPSError('original_2024_eps_conflict')
            out[str(year)] = values[0]
    return out, True


def extract_archive(raw, *, scope, identifiers, cfo_anchor, diagnostic=None):
    checked = xp.inspect_binary_archive(raw, year=2024)
    if checked['file_absent']:
        return {}, 'original_file_absent'
    found = {}
    anchored = False
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml', '.xbrl')):
                continue
            values, verified = extract_instance(
                archive.read(entry), scope=scope, identifiers=identifiers,
                cfo_anchor=cfo_anchor, diagnostic=diagnostic)
            anchored |= verified
            for year, value in values.items():
                if year in found and found[year] != value:
                    raise PriorEPSError('conflicting_original_2024_instances')
                found[year] = value
    return found, 'candidate_2023_verified' if anchored else 'no_verified_cfo_scope'


def make_record(code, row, anchor, *, values, reason, archive_sha256, profile_sha256, asof):
    if reason not in REASONS or type(values) is not dict or not set(values) <= {'2023', '2024'}:
        raise PriorEPSError('invalid_prior_eps_result')
    overlap = row['reported_basic_eps_2023_2025'][1]
    if reason == 'candidate_2023_verified':
        if '2023' not in values:
            reason = 'missing_2023_basic_eps'
        elif '2024' not in values:
            reason = 'missing_2024_overlap'
        elif values['2024'] != overlap:
            reason = '2024_overlap_conflict'
    record = {
        'contract': CONTRACT, 'code': code, 'scope': row['fs_div'],
        'receipt_2024': anchor['annual_receipt_id'],
        'receipt_2025': row['annual_receipt_id'],
        'cfo_2024_krw': anchor['operating_cashflow_krw'],
        'saved_eps_2024': overlap,
        'original_eps_2024': values.get('2024'),
        'candidate_eps_2023': values.get('2023') if reason == 'candidate_2023_verified' else None,
        'reason': reason, 'archive_sha256': archive_sha256,
        'profile_sha256': profile_sha256, 'observed_on': asof,
        'scored_eps_unchanged': True,
    }
    record['digest'] = cf.fingerprint(record)
    verify_record(record)
    return record


def verify_record(record):
    keys = {'contract', 'code', 'scope', 'receipt_2024', 'receipt_2025',
            'cfo_2024_krw', 'saved_eps_2024', 'original_eps_2024',
            'candidate_eps_2023', 'reason', 'archive_sha256',
            'profile_sha256', 'observed_on', 'scored_eps_unchanged', 'digest'}
    if (type(record) is not dict or set(record) != keys
        or record['contract'] != CONTRACT or record['reason'] not in REASONS
        or record['scope'] not in ('CFS', 'OFS')
        or not re.fullmatch('[0-9A-Z]{6}', str(record['code']))
        or not re.fullmatch('2025[0-9]{10}', str(record['receipt_2024']))
        or not re.fullmatch('2026[0-9]{10}', str(record['receipt_2025']))
        or type(record['cfo_2024_krw']) is not int
        or record['scored_eps_unchanged'] is not True
        or any(type(record[k]) is not str or not re.fullmatch('[a-f0-9]{64}', record[k])
               for k in ('archive_sha256', 'profile_sha256'))
        or any(type(record[k]) not in (float, int) and record[k] is not None
               for k in ('saved_eps_2024', 'original_eps_2024', 'candidate_eps_2023'))
        or type(record['saved_eps_2024']) not in (float, int)
        or (record['reason'] == 'candidate_2023_verified') != (record['candidate_eps_2023'] is not None)
        or (record['reason'] == 'candidate_2023_verified'
            and record['original_eps_2024'] != record['saved_eps_2024'])
        or record['digest'] != cf.fingerprint({k: v for k, v in record.items() if k != 'digest'})):
        raise PriorEPSError('invalid_prior_eps_record')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    parser.add_argument('--limit', type=int, default=9)
    args = parser.parse_args()
    if not 1 <= args.limit <= 16:
        raise PriorEPSError('invalid_prior_eps_limit')
    root = Path('docs')
    target = root / 'v3-eps-prior-original-receipt-research.json'
    if target.is_symlink():
        raise PriorEPSError('unsafe_prior_eps_output')
    read = lambda name: json.loads((root / name).read_text())
    market, catalog, bulk, cash, book, state = [read(name) for name in (
        'data.json', 'v22-market-catalog.json', 'v3-dart-bulk-history.json',
        'v3-dart-cashflow-reference.json', 'v3-dart-book-reference.json',
        'v3-dart-reported-eps.json')]
    cash = cf.retained_current_market_cashflow(market, catalog, bulk, cash)
    eps.plan(catalog, book, state)
    before = read(target.name) if target.is_file() else {}
    if before and (before.get('schema') != SCHEMA or before.get('scores_unchanged') is not True
                   or not isinstance(before.get('records'), dict)):
        raise PriorEPSError('invalid_prior_eps_snapshot')
    records = before.get('records', {})
    choices = candidates(state, cash, records)
    output = {'eligible_total': len(choices) + len(records),
              'selected': min(args.limit, len(choices)),
              'reviewed': 0, 'candidate_2023_verified': 0, 'provider_calls': 0,
              'reasons': {}, 'scored_eps_unchanged': True}
    if args.mode == 'dry-run' or not choices:
        print(json.dumps(output, sort_keys=True))
        return
    import requests
    key = os.getenv('DART_API_KEY', '')
    if not re.fullmatch('[A-Za-z0-9]{40}', key):
        raise PriorEPSError('missing_prior_eps_key')
    day, members, *_ = eps.plan(catalog, book, state)
    session = requests.Session()
    cache = Path('.cache/v3-dart-company-codes.json')
    mapping = verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None, day, members)

    def get(endpoint, params, stream=False):
        try:
            response = session.get('https://opendart.fss.or.kr/api/' + endpoint,
                                   params={'crtfc_key': key, **params},
                                   timeout=(20, 90), stream=stream)
            output['provider_calls'] += 1
            response.raise_for_status()
            return response
        except Exception:
            raise PriorEPSError('prior_eps_provider_unavailable') from None

    if mapping is None:
        mapping = _mapping(get('corpCode.xml', {}).content, set(members))
    reasons = Counter()
    deadline = time.monotonic() + 360
    for code, row, anchor in choices[:args.limit]:
        if time.monotonic() + 120 >= deadline:
            break
        if code not in mapping:
            continue
        try:
            profile = get('company.json', {'corp_code': mapping[code]}).json()
            identifiers = original.company_ids(profile, mapping[code], code)
        except (ValueError, TypeError):
            raise PriorEPSError('prior_eps_identity_unavailable') from None
        raw = xp._read_response_limited(get('fnlttXbrl.xml', {
            'rcept_no': anchor['annual_receipt_id'], 'reprt_code': '11011'}, True))
        try:
            values, reason = extract_archive(raw, scope=row['fs_div'],
                identifiers=identifiers, cfo_anchor=anchor['operating_cashflow_krw'])
        except (PriorEPSError, eps.EPSError, original.RecoveryError, xp.XbrlPilotError, zipfile.BadZipFile):
            values, reason = {}, 'unusable_original_archive'
        record = make_record(code, row, anchor, values=values, reason=reason,
            archive_sha256=hashlib.sha256(raw).hexdigest(),
            profile_sha256=cf.fingerprint(profile), asof=day)
        records[code] = record
        snapshot = {'schema': SCHEMA, 'asof': day,
                    'listed_total': catalog['listed_total'], 'scores_unchanged': True,
                    'records': records, 'reviewed_total': len(records),
                    'candidate_2023_verified_total': sum(
                        r['reason'] == 'candidate_2023_verified' for r in records.values())}
        for saved in records.values():
            verify_record(saved)
        write_json(target, snapshot)
        output['reviewed'] += 1
        output['candidate_2023_verified'] += int(reason == 'candidate_2023_verified')
        reasons[record['reason']] += 1
    output['reasons'] = dict(reasons)
    print(json.dumps(output, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'blocked_prior_eps_source',
                          'reason': 'prior_eps_source_not_verified'}))
        raise SystemExit(1)
