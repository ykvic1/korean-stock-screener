"""Receipt-matched annual basic EPS comparatives, never inferred EPS.

All three amounts come from ONE annual statement/scope. These are reported
comparatives, not independent proof of share adjustments or historical PIT.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
from threading import local

from v3_dart_bulk_history import (_mapping, _valid_receipt, verified_company_mapping_cache,
                                 _stable_digest, write_json)
from v3_dart_2025_owner_profit_probe import _read_company_cache_from_root

SCHEMA = 'v3-reported-annual-basic-eps-comparatives-v1'
EPS_IDS = {'ifrs-full_BasicEarningsLossPerShare', 'ifrs_BasicEarningsLossPerShare'}
ORIGINAL_REPORTED_BASIC_EPS_ID = 'original-xbrl:explicit-reported-basic-eps-v2'
NONSTANDARD_ID = '-표준계정코드 미사용-'
# Only literal Korean statement labels disclosed by the issuer. Never infer
# an EPS amount from profit, share count, synonyms or punctuation changes.
# Only whitespace is ignored in the explicit missing-account recovery path.
NONSTANDARD_BASIC_EPS_NAMES = frozenset((
    '기본주당이익', '기본주당이익(손실)', '기본주당순이익',
    '기본주당순이익(손실)', '기본주당손익', '기본주당순손익',
))
LEGACY_NAME_RECHECK_VERSION = 'one_receipt_exact_basic_eps_name_v1'
WHITESPACE_NAME_RECHECK_VERSION = 'one_receipt_whitespace_basic_eps_name_v2'
NAME_RECHECK_VERSION = 'one_receipt_explicit_joint_basic_eps_name_v3'
NAME_RECHECK_VERSIONS = {LEGACY_NAME_RECHECK_VERSION, WHITESPACE_NAME_RECHECK_VERSION, NAME_RECHECK_VERSION}
# IAS 33.67 permits one-line presentation when basic and diluted EPS are
# equal. An explicit joint label reports BASIC EPS too; diluted-only labels
# remain excluded. No equality is inferred from another account's amount.
# https://www.ifrs.org/content/dam/ifrs/publications/pdf-standards/english/2022/issued/part-a/ias-33-earnings-per-share.pdf
JOINT_BASIC_DILUTED_EPS_NAMES = frozenset(
    name.replace('기본', '기본및희석', 1) for name in NONSTANDARD_BASIC_EPS_NAMES)
ALLOWED_BASIC_EPS_NAMES = NONSTANDARD_BASIC_EPS_NAMES | JOINT_BASIC_DILUTED_EPS_NAMES
MAX_PER_RUN = 100
MAX_WORKERS = 3
YEAR = 2025
UNUSABLE_REASONS = {'invalid_reported_eps_amount', 'eps_statement_identity_mismatch',
                   'eps_statement_receipt_or_currency_mismatch', 'conflicting_reported_basic_eps',
                   'official_corp_mapping_missing'}
SAFE_FAILURE_CODES = UNUSABLE_REASONS | {
    'invalid_eps_quarantine_reason', 'invalid_reported_eps_series',
    'official_eps_response_unavailable', 'invalid_eps_source_day',
    'unaligned_eps_annual_sources', 'duplicate_eps_issuer_census',
    'duplicate_eps_annual_issuer', 'untrusted_prior_eps_source',
    'invalid_saved_eps_source', 'missing_official_eps_api_key',
    'official_eps_issuer_identity_unavailable', 'official_eps_transport_unavailable',
    'eps_output_must_not_be_symlink', 'saved_eps_snapshot_differs_from_sources',
    'partial_eps_source_stopped_after_checkpoint',
    'invalid_eps_parallelism', 'parallel_eps_session_factory_required',
}


class EPSError(ValueError):
    """Fixed source-independent diagnostics only."""


def safe_failure_label(error):
    """Expose a fixed diagnostic code, never a provider response or secret."""
    code = str(error)
    if type(error) is EPSError and code in SAFE_FAILURE_CODES:
        return 'blocked: ' + code
    return 'blocked: reported_eps_source_not_verified'


def unusable(base, day, reason):
    if reason not in UNUSABLE_REASONS:
        raise EPSError('invalid_eps_quarantine_reason')
    return {**base, 'observed_on': day, 'source_account_id': None,
            'reported_basic_eps_2023_2025': [None]*3, 'metrics': metrics([None]*3),
            'comparative_basis': 'statement_not_usable', 'status': 'statement_not_usable',
            'source_reason': reason}


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


def canonical_basic_eps_name(value):
    """Finite basic/joint labels with optional exact won-unit annotation."""
    if type(value) is not str or len(value) > 120:
        return None
    compact = "".join(value.split())
    # Presentation-only suffix. Never rescale a thousand-won/million-won
    # figure or accept an unspecified unit; row currency must still be KRW.
    compact = compact.removesuffix("(단위:원)")
    return compact if compact in ALLOWED_BASIC_EPS_NAMES else None


def parse(payload, *, corp, base, on_day, allow_nonstandard_names=False):
    status = payload.get('status') if type(payload) is dict else None
    rows = payload.get('list') if status == '000' else [] if status == '013' else None
    if not isinstance(rows, list) or len(rows) > 18000 or (status == '000' and not rows):
        raise EPSError('official_eps_response_unavailable')
    matches = []
    name_candidates = []
    for row in rows:
        if (type(row) is not dict or row.get('corp_code') != corp
            or str(row.get('bsns_year')) != str(YEAR) or row.get('reprt_code') != '11011'
            or ('stock_code' in row and row['stock_code'] != base['code'])
            or ('fs_div' in row and row['fs_div'] != base['fs_div'])):
            raise EPSError('eps_statement_identity_mismatch')
        standard = row.get('account_id') in EPS_IDS
        exact_name = (allow_nonstandard_names
                      and row.get('account_id') == NONSTANDARD_ID
                      and canonical_basic_eps_name(row.get('account_nm')) is not None)
        if (not (standard or exact_name) or row.get('sj_div') not in ('IS', 'CIS')
            or row.get('account_detail', '-') not in ('', '-')):
            continue
        if row.get('currency') != 'KRW' or row.get('rcept_no') != base['annual_receipt_id']:
            raise EPSError('eps_statement_receipt_or_currency_mismatch')
        values = [amount(row.get(key)) for key in
                  ('bfefrmtrm_amount', 'frmtrm_amount', 'thstrm_amount')]
        if standard:
            matches.append((row['account_id'], values))
        elif exact_name:
            name_candidates.append((canonical_basic_eps_name(row['account_nm']), values))
    if len({tuple(values) for _, values in matches}) > 1:
        raise EPSError('conflicting_reported_basic_eps')
    # Do not rescue a malformed or conflicting IFRS account with an account
    # name. Fallback is allowed only when *no* standard row exists.
    if not matches and name_candidates:
        if len({tuple(values) for _, values in name_candidates}) > 1:
            raise EPSError('conflicting_reported_basic_eps')
        matches = [(NONSTANDARD_ID, name_candidates[0][1])]
    values = matches[0][1] if matches else [None, None, None]
    result = {**base, 'observed_on': on_day,
            'source_account_id': sorted(x[0] for x in matches)[0] if matches else None,
            'reported_basic_eps_2023_2025': values,
            'comparative_basis': 'one_annual_filing_same_scope',
            'status': 'reported_basic_eps' if matches else 'standard_eps_not_observed',
            'metrics': metrics(values)}
    if matches and matches[0][0] == NONSTANDARD_ID:
        result['source_account_name_fallback'] = sorted(x[0] for x in name_candidates)[0]
    return result


def plan(catalog, book, prior=None, limit=MAX_PER_RUN,
         recover_missing_names=False, priority_codes=(), priority_90_codes=()):
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
    if (type(priority_codes) not in (list, tuple, set, frozenset)
        or len(priority_codes)>6000 or any(
            type(code) is not str or code not in members for code in priority_codes)):
        raise EPSError('untrusted_eps_recovery_priority')
    if (type(priority_90_codes) not in (list, tuple, set, frozenset)
        or len(priority_90_codes)>6000 or any(
            type(code) is not str or code not in members for code in priority_90_codes)):
        raise EPSError('untrusted_eps_90_first_priority')
    priority_codes=set(priority_codes)
    priority_90_codes=set(priority_90_codes)
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
            quarantined = row.get('status') == 'statement_not_usable'
            if (observed_day > cutoff or row.get('comparative_basis') != (
                    'statement_not_usable' if quarantined else 'one_annual_filing_same_scope')
                or row.get('status') not in ('reported_basic_eps', 'standard_eps_not_observed', 'statement_not_usable')
                or row.get('source_account_id') not in EPS_IDS | {NONSTANDARD_ID, ORIGINAL_REPORTED_BASIC_EPS_ID, None}
                or row.get('metrics') != metrics(row.get('reported_basic_eps_2023_2025'))
                or (row['status'] in ('standard_eps_not_observed', 'statement_not_usable') and
                    (row['source_account_id'] is not None or row['reported_basic_eps_2023_2025'] != [None]*3))
                or (quarantined and row.get('source_reason') not in UNUSABLE_REASONS)
                or (row['status'] == 'reported_basic_eps'
                    and row['source_account_id'] not in EPS_IDS | {NONSTANDARD_ID, ORIGINAL_REPORTED_BASIC_EPS_ID})):
                raise EPSError('invalid_saved_eps_source')
            label=row.get('source_account_name_fallback')
            marker=row.get('source_name_recheck')
            original_recovered=False
            if row.get('original_xbrl_review') is not None or row.get('original_xbrl_proof') is not None:
                from v3_dart_xbrl_eps_recovery import verify_review
                original_recovered=verify_review(row)
            if row.get('source_account_id')==ORIGINAL_REPORTED_BASIC_EPS_ID and not (
                original_recovered and row.get('original_xbrl_context_recheck') is not None):
                raise EPSError('invalid_saved_eps_source')
            if (bool(label) != (row['source_account_id']==NONSTANDARD_ID)
                or (label is not None and label not in ALLOWED_BASIC_EPS_NAMES)
                or (marker is None and label is not None)):
                raise EPSError('invalid_saved_eps_source')
            if (label in JOINT_BASIC_DILUTED_EPS_NAMES and
                (type(marker) is not dict or marker.get('version') != NAME_RECHECK_VERSION)):
                raise EPSError('invalid_saved_eps_source')
            if marker is not None:
                if (type(marker) is not dict or set(marker)!={'version','receipt_id','result'}
                    or type(marker.get('version')) is not str
                    or marker.get('version') not in NAME_RECHECK_VERSIONS
                    or marker.get('receipt_id')!=row['annual_receipt_id']
                    or marker.get('result') not in ('enriched','checked_unavailable')
                    or (marker['result']=='enriched' and
                        row['status']!='reported_basic_eps')
                    or (marker['result']=='checked_unavailable' and
                        row['status']!='standard_eps_not_observed' and not original_recovered)):
                    raise EPSError('invalid_saved_eps_source')
            if row.get('source_reason') == 'official_corp_mapping_missing' and observed_day < cutoff:
                continue
            retained[code] = row
    groups = {m: sorted(code for code in expected if code not in retained and members[code][1] == m)
              for m in ('KOSPI', 'KOSDAQ')}
    selected = []
    while len(selected) < limit and any(groups.values()):
        for market in groups:
            if groups[market] and len(selected) < limit:
                selected.append(groups[market].pop(0))
    if recover_missing_names and len(selected)<limit:
        # One recheck per unchanged annual receipt; a negative answer is
        # recorded without making a missing EPS into observed zero.
        pending=[code for code,row in retained.items()
                 if row['status']=='standard_eps_not_observed'
                 and (row.get('source_name_recheck') or {}).get('version') != NAME_RECHECK_VERSION]
        pending.sort(key=lambda code:(code not in priority_90_codes,
                                     code not in priority_codes,
                                     members[code][1]!='KOSPI',code))
        selected.extend(pending[:limit-len(selected)])
    return day, members, expected, retained, selected


def snapshot(day, catalog, book, expected, records):
    report={'schema': SCHEMA, 'market_asof': day, 'fiscal_year': YEAR,
            'listed_total': catalog['listed_total'], 'eligible_annual_issuers': len(expected),
            'attempted_issuers': len(records), 'pending_issuers': len(expected)-len(records),
            'complete_three_year_eps_issuers': sum(r['metrics']['complete_three_years'] for r in records.values()),
            'unusable_source_issuers': sum(r['status']=='statement_not_usable' for r in records.values()),
            'investment_score_certified': False, 'corporate_actions_independently_verified': False,
            'raw_provider_rows_published': False,
            'source_sha256': {'catalog': _stable_digest(catalog), 'annual_book': _stable_digest(book)},
            'records': dict(sorted(records.items()))}
    reviewed=[row for row in records.values() if row.get('source_name_recheck') is not None]
    if reviewed:
        report['name_recheck_issuers']=len(reviewed)
        report['name_recheck_enriched_issuers']=sum(
            row['source_name_recheck']['result']=='enriched' for row in reviewed)
        report['name_recheck_complete_three_year_issuers']=sum(
            row['source_name_recheck']['result']=='enriched'
            and row['metrics']['complete_three_years'] for row in reviewed)
    original_reviews=[row for row in records.values() if row.get('original_xbrl_review') is not None]
    if original_reviews:
        from v3_dart_xbrl_eps_recovery import verify_review
        report['original_xbrl_reviewed_issuers']=len(original_reviews)
        report['original_xbrl_recovered_issuers']=sum(verify_review(row) for row in original_reviews)
    return report


def collect(catalog, book, prior, *, key, session, cache=None, limit=MAX_PER_RUN,
            on_progress=None, workers=1, session_factory=None,
            recover_missing_names=False, priority_codes=(), priority_90_codes=()):
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise EPSError('invalid_eps_parallelism')
    if workers > 1 and not callable(session_factory):
        raise EPSError('parallel_eps_session_factory_required')
    day, members, expected, retained, selected = plan(
        catalog, book, prior, limit, recover_missing_names,
        priority_codes, priority_90_codes)
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
        def fetch_issuer(code, client):
            if code not in mapping:
                return unusable(expected[code], day, 'official_corp_mapping_missing'), 0
            try:
                reply = client.get('https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',
                                    params={'crtfc_key': key, 'corp_code': mapping[code],
                                            'bsns_year': str(YEAR), 'reprt_code': '11011',
                                            'fs_div': expected[code]['fs_div']}, timeout=(20, 40))
                reply.raise_for_status()
                recheck=code in retained
                parsed = parse(reply.json(),corp=mapping[code],base=expected[code],
                               on_day=day,allow_nonstandard_names=recheck and recover_missing_names)
                if recheck:
                    if not recover_missing_names or retained[code]['status']!='standard_eps_not_observed':
                        raise EPSError('invalid_saved_eps_source')
                    parsed['source_name_recheck']={
                        'version':NAME_RECHECK_VERSION,
                        'receipt_id':expected[code]['annual_receipt_id'],
                        'result':'enriched' if parsed['status']=='reported_basic_eps'
                                 else 'checked_unavailable'}
            except EPSError as error:
                if str(error) not in UNUSABLE_REASONS:
                    raise
                parsed = unusable(expected[code], day, str(error))
            except Exception:
                raise EPSError('official_eps_transport_unavailable') from None
            return parsed, 1

        def checkpoint_issuer(code, parsed):
            retained[code] = parsed
            if on_progress:
                on_progress(snapshot(day, catalog, book, expected, retained))

        if workers == 1:
            for code in selected:
                parsed, used = fetch_issuer(code, session)
                calls += used
                checkpoint_issuer(code, parsed)
        else:
            # requests.Session is not shared across threads. Only the main
            # thread updates checkpoints, so a partial provider outage cannot
            # leave two concurrent writers or a synthetic zero-EPS record.
            worker_state = local()

            def fetch_isolated(code):
                if not hasattr(worker_state, 'client'):
                    worker_state.client = session_factory()
                return fetch_issuer(code, worker_state.client)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                # Bounded windows prevent an early 020/rate-limit response
                # from triggering the remaining 100 official requests.
                for offset in range(0, len(selected), workers):
                    futures = {executor.submit(fetch_isolated, code): code
                               for code in selected[offset:offset + workers]}
                    source_error = None
                    for future in as_completed(futures):
                        code = futures[future]
                        try:
                            parsed, used = future.result()
                        except EPSError as error:
                            source_error = source_error or error
                            continue
                        calls += used
                        checkpoint_issuer(code, parsed)
                    if source_error is not None:
                        raise source_error
    return snapshot(day, catalog, book, expected, retained), calls


def verified_eps_recovery_priority(priority, catalog):
    """Trust only same-day, same-census source-gap work hints; no new points.

    90-first candidates must require *only* the EPS workstream to bridge
    the 90-point available-weight threshold. Their provider recoverability
    is unknown; one-receipt negative checks still remain final.
    """
    empty = (set(), set())
    if (type(priority) is not dict or type(catalog) is not dict
        or priority.get('schema') != 'v3-r1d-observation-work-priority-v1'
        or priority.get('asof') != catalog.get('date')
        or priority.get('listed_total') != catalog.get('listed_total')
        or priority.get('missing_is_not_zero') is not True):
        return empty
    rows = catalog.get('rows')
    if (type(rows) is not list or len(rows) != catalog.get('listed_total')
        or not all(type(row) is dict and type(row.get('code')) is str
                   for row in rows)):
        return empty
    valid = {row['code'] for row in rows}
    if len(valid) != len(rows):
        return empty
    coverage = priority.get('coverage_90_first')
    if (type(coverage) is not dict or coverage.get('threshold') != 90
        or coverage.get('recovery_not_a_score_or_observation') is not True
        or type(coverage.get('family_conditional_priority_codes')) is not dict):
        return empty
    ninety = coverage['family_conditional_priority_codes'].get(
        'reported_eps_earnings_yield')
    single = priority.get('single_component_queue')
    if (type(ninety) is not list or type(single) is not list
        or len(ninety) > len(valid) or len(single) > len(valid)
        or any(type(code) is not str or code not in valid for code in ninety)
        or any(type(row) is not dict or row.get('code') not in valid
               or type(row.get('gaps')) is not list for row in single)):
        return empty
    full = {row['code'] for row in single
            if len(row['gaps']) == 1 and
            row['gaps'][0].get('area') == 'valuation' and
            row['gaps'][0].get('component') == 'per_eps_quality_growth'}
    return set(ninety), full


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs', default='docs')
    parser.add_argument('--cache', default='.cache/v3-dart-company-codes.json')
    parser.add_argument('--mode', choices=('dry-run', 'live', 'recover-exact-name',
                                          'verify', 'refresh'), default='dry-run')
    parser.add_argument('--limit', type=int, default=MAX_PER_RUN)
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= MAX_WORKERS:
        raise EPSError('invalid_eps_parallelism')
    root = Path(args.docs)
    catalog = json.loads((root/'v22-market-catalog.json').read_text())
    book = json.loads((root/'v3-dart-book-reference.json').read_text())
    target = root/'v3-dart-reported-eps.json'
    old = json.loads(target.read_text()) if target.is_file() else None
    recovery=args.mode=='recover-exact-name'
    priority_90,priority=set(),set()
    if recovery:
        priority_file=root/'v3-operational-evidence-priority.json'
        if priority_file.is_file():
            saved_priority=json.loads(priority_file.read_text())
            priority_90,priority=verified_eps_recovery_priority(saved_priority,catalog)
    day, _, expected, retained, selected = plan(catalog, book, old, args.limit,
        recover_missing_names=recovery,priority_codes=priority,
        priority_90_codes=priority_90)
    if args.mode == 'refresh':
        if target.is_symlink():
            raise EPSError('eps_output_must_not_be_symlink')
        write_json(target, snapshot(day, catalog, book, expected, retained))
        return
    if args.mode not in ('live','recover-exact-name'):
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
                                cache=_read_company_cache_from_root(args.cache), on_progress=checkpoint,
                                workers=args.workers,
                                session_factory=requests.Session if args.workers > 1 else None,
                                recover_missing_names=recovery,priority_codes=priority,
                                priority_90_codes=priority_90)
    except EPSError as error:
        if not saved:
            raise
        print(json.dumps({'status': 'partial_eps_checkpoint_saved', 'reason': str(error)}))
        # A saved subset is recoverable, but the upstream outage must still
        # stop the outer 25-segment workflow loop. Reporting success here would
        # retry the same failing source repeatedly within a single job.
        raise EPSError('partial_eps_source_stopped_after_checkpoint') from None
    checkpoint(output)
    print(json.dumps({'status': 'reported_eps_saved', 'provider_calls': calls,
                      'attempted': output['attempted_issuers'],
                      'complete_three_years': output['complete_three_year_eps_issuers'],
                      'name_recheck_issuers':output.get('name_recheck_issuers',0),
                      'name_recheck_enriched_issuers':output.get('name_recheck_enriched_issuers',0),
                      'name_recheck_complete_three_year_issuers':
                          output.get('name_recheck_complete_three_year_issuers',0),
                      'pending': output['pending_issuers']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(safe_failure_label(error)) from None
