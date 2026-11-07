"""Bounded OpenDART pilot for missing consolidated owners' equity.

Research-only: no market score or stored book is changed. A missing owners'
equity value cannot be replaced by total equity unless the same receipt and
scope explicitly report non-controlling interests (including an explicit 0).
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re

from v3_dart_bulk_history import _mapping, verified_company_mapping_cache
from v3_v30b_release_gate import R1D_PART_LIMITS, r1d_public_reconciles

TOTAL_IDS = frozenset({'ifrs-full_Equity', 'ifrs_Equity'})
OWNER_IDS = frozenset({'ifrs-full_EquityAttributableToOwnersOfParent',
                       'ifrs_EquityAttributableToOwnersOfParent'})
NCI_IDS = frozenset({'ifrs-full_NoncontrollingInterests', 'ifrs_NoncontrollingInterests'})
MAX_CALLS = 12
CODE = re.compile(r'[0-9]{6}\Z')


class ParentEquityError(ValueError):
    """Source-independent failure labels only."""


def parse(payload, *, corp, code, receipt, year, scope, expected_equity):
    """Return (observed owners equity, basis), or (None, fixed reason).

    Only direct reported IFRS facts or explicit TOTAL-NCI accounting identity.
    Do not infer NCI=0 from its absence; do not infer any missing fact.
    """
    if scope != 'CFS' or type(expected_equity) not in (int, float) or expected_equity <= 0:
        raise ParentEquityError('unverified_parent_equity_anchor')
    if type(payload) is not dict or payload.get('status') not in ('000', '013'):
        raise ParentEquityError('parent_equity_provider_unavailable')
    if payload['status'] == '013':
        return None, 'no_annual_statement'
    rows = payload.get('list')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 18000:
        raise ParentEquityError('parent_equity_provider_unavailable')
    facts = {kind: set() for kind in ('total', 'owner', 'nci')}
    for row in rows:
        if type(row) is not dict:
            raise ParentEquityError('parent_equity_invalid_statement_row')
        # Field-specific fixed labels expose no raw provider identifiers and
        # make a genuine version/scope discrepancy actionable on the next run.
        for name, actual, expected in (
                ('corporation',row.get('corp_code'),corp),
                ('fiscal_year',str(row.get('bsns_year')),str(year)),
                ('report_kind',row.get('reprt_code'),'11011'),
                ('scope',row.get('fs_div',scope),scope),
                ('stock',row.get('stock_code',code),code)):
            if actual != expected:
                raise ParentEquityError('parent_equity_'+name+'_mismatch')
        if row.get('sj_div') != 'BS' or row.get('account_detail', '-') not in ('', '-'):
            continue
        aid = row.get('account_id')
        kind = ('total' if aid in TOTAL_IDS else 'owner' if aid in OWNER_IDS
                else 'nci' if aid in NCI_IDS else None)
        if kind is None:
            continue
        # OpenDART returns many statement sections in one response. Match
        # the receipt of the *balance-sheet facts actually used*, just as the
        # established cashflow normalizer matches its selected CF facts.
        if row.get('rcept_no') != receipt:
            raise ParentEquityError('parent_equity_fact_receipt_mismatch')
        if row.get('currency') != 'KRW':
            raise ParentEquityError('unverified_parent_equity_currency')
        raw = row.get('thstrm_amount')
        if type(raw) is not str or not re.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)', raw):
            raise ParentEquityError('unverified_parent_equity_amount')
        value = int(raw.replace(',', ''))
        if abs(value) > 10**18:
            raise ParentEquityError('unverified_parent_equity_amount')
        facts[kind].add(value)
    if any(len(values) > 1 for values in facts.values()):
        raise ParentEquityError('conflicting_parent_equity_facts')
    total = next(iter(facts['total']), None)
    owner = next(iter(facts['owner']), None)
    nci = next(iter(facts['nci']), None)
    if total is None or total != expected_equity:
        return None, 'total_equity_anchor_missing_or_conflicting'
    if owner is not None and nci is not None and owner + nci != total:
        raise ParentEquityError('conflicting_parent_equity_identity')
    if owner is None and nci is not None:
        owner = total - nci
        basis = 'same_receipt_total_minus_explicit_nci'
    else:
        basis = 'same_receipt_direct_parent_equity'
    if owner is None:
        return None, 'neither_owner_nor_explicit_nci'
    if owner <= 0 or abs(owner) > 10**18:
        return None, 'unusable_parent_equity'
    return owner, basis


def targets(report, comparison, catalog, book, cash, limit=MAX_CALLS):
    if type(limit) is not int or not 1 <= limit <= MAX_CALLS:
        raise ParentEquityError('invalid_parent_equity_pilot_limit')
    if (r1d_public_reconciles(report, comparison, catalog) is None
        or report.get('asof') != catalog.get('date') or book.get('asof') != catalog.get('date')
        or cash.get('asof') != catalog.get('date')):
        raise ParentEquityError('unreconciled_parent_equity_priority')
    members = {r['code'] for r in catalog['rows']}
    needed = []
    for code, row in report['rows'].items():
        gaps = {(area, key) for area, parts in R1D_PART_LIMITS.items()
                for key, maximum in parts.items()
                if maximum - row['parts'][area].get(key, [0, 0])[1] > .00001}
        if gaps == {('valuation', 'pbr_equity_quality')}:
            needed.append(code)
    observed = {r['code']: r.get('annual') for r in book.get('records', [])}
    selected = []
    for code in sorted(needed):
        annual = observed.get(code)
        anchor = cash['records'].get(code, {}).get('years', {}).get('2025', {})
        if (code not in members or not isinstance(annual, dict)
            or annual.get('year') != 2025 or annual.get('fs_div') != 'CFS'
            or annual.get('currency') != 'KRW'
            or annual.get('values', {}).get('owners_equity') is not None
            or type(annual.get('values', {}).get('equity')) not in (int, float)
            or annual['values']['equity'] <= 0
            or anchor.get('major_annual_receipt_matched') is not True
            or anchor.get('annual_receipt_id') != annual.get('receipt_id')
            or anchor.get('scope') != 'CFS'
            or anchor.get('operating_cashflow_krw') is None):
            continue
        selected.append((code, annual))
    return selected[:limit], len(needed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    parser.add_argument('--limit', type=int, default=7)
    args = parser.parse_args()
    folder = Path('docs')
    read = lambda name: json.loads((folder / name).read_text(encoding='utf8'))
    report, comp, catalog, book, cash = [read(name) for name in (
        'v3-operational-research-scores.json', 'v3-operational-research-comparison.json',
        'v22-market-catalog.json', 'v3-dart-book-reference.json', 'v3-dart-cashflow-reference.json')]
    selected, priority_count = targets(report, comp, catalog, book, cash, args.limit)
    output = {'priority_pbr_only_issuers': priority_count, 'selected': len(selected),
              'provider_calls': 0, 'reviewed': 0, 'independently_recoverable': 0,
              'reasons': {}, 'writes': 0, 'investment_score_certified': False}
    if args.mode == 'dry-run' or not selected:
        print(json.dumps(output, sort_keys=True))
        return
    key = os.getenv('DART_API_KEY', '')
    if not re.fullmatch(r'[A-Za-z0-9]{40}', key):
        raise ParentEquityError('missing_parent_equity_key')
    import requests
    members = {r['code']: r for r in catalog['rows']}
    cache = Path('.cache/v3-dart-company-codes.json')
    mapping = verified_company_mapping_cache(
        json.loads(cache.read_text()) if cache.is_file() else None,
        catalog['date'], members)
    client = requests.Session()
    def get(endpoint, params):
        try:
            result = client.get('https://opendart.fss.or.kr/api/' + endpoint,
                                params={'crtfc_key': key, **params}, timeout=(20, 60))
            output['provider_calls'] += 1
            result.raise_for_status()
            return result
        except Exception:
            raise ParentEquityError('parent_equity_provider_unavailable') from None
    if mapping is None:
        mapping = _mapping(get('corpCode.xml', {}).content, set(members))
    reasons = Counter()
    for code, annual in selected:
        if code not in mapping:
            reasons['company_mapping_missing'] += 1
            continue
        payload = get('fnlttSinglAcntAll.json', {
            'corp_code': mapping[code], 'bsns_year': '2025',
            'reprt_code': '11011', 'fs_div': 'CFS'}).json()
        try:
            value, basis = parse(payload, corp=mapping[code], code=code,
                                 receipt=annual['receipt_id'], year=2025,
                                 scope='CFS', expected_equity=annual['values']['equity'])
        except ParentEquityError as exc:
            value, basis = None, str(exc)
        output['reviewed'] += 1
        output['independently_recoverable'] += int(value is not None)
        reasons[basis] += 1
    output['reasons'] = dict(sorted(reasons.items()))
    print(json.dumps(output, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status': 'blocked_parent_equity_pilot',
                          'reason': 'parent_equity_source_not_verified'}))
        raise SystemExit(1)
