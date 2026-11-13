"""Recover *observed* MA slope and unscored stop references from saved close.

Manual-only, 90-first source task.  The fetched Naver bars stay in memory;
only validated provisional observations can enter the saved market snapshot.
This does not certify KRX regular-session prices or create trading signals.
"""
from __future__ import annotations

from collections import Counter
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import tempfile

from v3_ma_slope_transition import WEIGHTS, observe, valid_observation
from v3_pattern_composite_score import _observed as observed_pattern, _weekly_trend
from v3_structural_stop_reference import (
    observe as observe_structural_stop, valid_reference,
)


class RecoveryError(ValueError):
    pass


def legacy_stop_needs_recheck(reference):
    """Old all-history rejection: one bounded recheck, never a retry loop."""
    return (type(reference) is dict
            and reference.get('warning') == '가격 원천값 무효 또는 결측'
            and reference.get('validation_window_bars') is None)


def availability(receipt, day):
    """Two independently checkable frame budgets, with unknown != zero."""
    if receipt is None:
        return (0., 0.)
    if not valid_observation(receipt, day):
        raise RecoveryError('invalid_retained_ma_receipt')
    result = []
    for frame in ('daily', 'weekly'):
        parts = receipt['parts'][frame] or {}
        result.append(round(sum(weight for period, weight in WEIGHTS[frame].items()
                                if parts.get(period) is not None), 6))
    return tuple(result)


def select_targets(market, priority, report, *, repair_legacy_stops=False,
                   include_weekly_pattern=False):
    """Take same-close 90-point recovery first, then near-100 recovery."""
    day = market['date']
    if (priority.get('asof') != day or report.get('asof') != day
        or priority.get('operational_source_digest') != report.get('source_digest')
        or priority.get('listed_total') != len(report.get('rows', {}))):
        raise RecoveryError('priority_or_score_source_date_mismatch')
    focus = priority['coverage_90_first']['family_conditional_priority_codes']['price_chart_windows']
    if type(focus) is not list or len(set(focus)) != len(focus):
        raise RecoveryError('invalid_priority_code_list')
    stocks = {stock['code']: stock for stock in market['stocks']}
    if len(stocks) != len(market['stocks']):
        raise RecoveryError('duplicate_saved_stock_codes')

    if repair_legacy_stops:
        first, second = [], []
        for code, stock in stocks.items():
            if (code not in report['rows'] or stock.get('price_date') != day
                or type(stock.get('price')) not in (int, float)
                or not math.isfinite(stock['price']) or stock['price'] <= 0):
                continue
            reference = stock.get('v3_structural_stop_reference')
            if reference is None:
                continue
            if not valid_reference(reference, day, stock['price']):
                raise RecoveryError('invalid_retained_structural_stop_reference')
            if legacy_stop_needs_recheck(reference):
                (first if report['rows'][code]['available'] < 90 else second).append(code)
        if len(first) + len(second) > 400:
            raise RecoveryError('unexpectedly_large_legacy_stop_cohort')
        return (sorted(first), sorted(second)), {
            'below_90_priority': len(first), 'already_90_priority': len(second)}

    def eligible(code):
        stock = stocks.get(code)
        if (stock is None or stock.get('price_date') != day
            or type(stock.get('price')) not in (int, float)
            or not math.isfinite(stock['price']) or stock['price'] <= 0):
            return False
        # Reuse the same source read for a previously missing stop reference.
        # This research field never makes a point available or changes rank.
        reference = stock.get('v3_structural_stop_reference')
        if reference is not None and not valid_reference(reference, day, stock['price']):
            raise RecoveryError('invalid_retained_structural_stop_reference')
        return True

    def missing_weight(code):
        stock = stocks[code]
        if availability(stock.get('v3_ma_slope_transition'), day) != (2., 3.):
            return True
        if include_weekly_pattern:
            weekly = observed_pattern(stock, 'weekly')
            return weekly is None or _weekly_trend(weekly) is None
        return False

    first_candidates = [code for code in focus if eligible(code)]
    # The score-first queue must not spend provider calls on a stock whose
    # MA availability is already complete merely because its informational,
    # zero-point stop reference is missing. The normal full-market scanner
    # remains responsible for those optional references.
    first = [code for code in first_candidates if missing_weight(code)]
    stop_only_deferred = sum(
        stocks[code].get('v3_structural_stop_reference') is None
        for code in set(first_candidates) - set(first))
    # Only current-date, not-yet-fully-observed 90+ issuers.  No all-market
    # rediscovery, historical full-market job, or future close requests.
    primary = set(first)
    # Do not fetch 90+ names solely for optional stop references: the regular
    # full-market scan handles those. Focus scarce calls on score gaps.
    second = [code for code, row in report['rows'].items()
              if 90 <= row['available'] < 100 and code not in primary
              and eligible(code) and missing_weight(code)]
    if not include_weekly_pattern and len(first) + len(second) > 400:
        raise RecoveryError('unexpectedly_large_target_cohort')
    return (sorted(first), sorted(second)), {
        'below_90_priority': len(first),
        'already_90_priority': len(second),
        'unscored_stop_only_deferred': stop_only_deferred,
    }


def restrict_to_corroborated_close(cohorts, market, crosscheck, market_sha,
                                   listing, listing_sha):
    """Avoid calling the same chart feed for a known regular-close mismatch."""
    from v3_krx_saved_close_crosscheck import current_report
    from v3_fdr_cache_krx_close_corroboration import (
        cache_close_index, CacheCorroborationError,
    )

    if not current_report(crosscheck, digest=market_sha, day=market['date']):
        raise RecoveryError('current_saved_close_crosscheck_required')
    proof = crosscheck['dated_public_cache_official_crosscheck']
    counts = proof.get('counts', {})
    if (proof['status'] != 'observed'
        or proof.get('regular_close_full_market_corroborated') is not True
        or proof.get('public_cache_source_sha256') != listing_sha
        or counts.get('requested') != len(market['stocks'])
        or counts.get('exact_close') != counts.get('requested')):
        raise RecoveryError('current_corroborated_close_cache_required')

    try:
        closes = cache_close_index(listing)
    except CacheCorroborationError:
        raise RecoveryError('invalid_corroborated_close_cache') from None
    stocks = {stock['code']: stock for stock in market['stocks']}
    return tuple([code for code in cohort
                  if closes.get((stocks[code]['market'], code)) == stocks[code]['price']]
                 for cohort in cohorts)


def analyze(code, cfg, day, *, include_weekly_pattern=False):
    import screener
    frame, latest = screener.last_series(None, code, cfg, day)
    receipt = observe(frame)
    result = {'price_date': latest.isoformat(),
            'price_latest': float(frame['Close'].iloc[-1]),
            'v3_ma_slope_transition': receipt,
            'v3_structural_stop_reference': observe_structural_stop(
                frame, asof=latest.isoformat(),
                patterns=screener.daily_patterns(frame))}
    if include_weekly_pattern:
        from v3_pattern_first_observations import observe_completed_weekly
        result['pattern_weekly_research'] = observe_completed_weekly(frame)
    return result


def collect(codes, day, *, include_weekly_pattern=False):
    from functools import partial
    import full_market_batch as batch
    if not codes:
        return {'rows': [], 'errors': [], 'excluded': [], 'examined': 0}
    manifest = {'schema': batch.SCHEMA, 'asof': day, 'codes': codes,
                'batch_size': batch.CHUNK,
                'batch_count': math.ceil(len(codes) / batch.CHUNK),
                'digest': batch._digest(day, codes)}
    batch.validate_manifest(manifest)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        for part in range(manifest['batch_count']):
            batch.scan(manifest=manifest, part=part, out=target,
                       analyzer=partial(analyze, include_weekly_pattern=include_weekly_pattern),
                       workers=2)
        return batch.load_receipts(manifest, target)


def apply(market, collected):
    """Accept only matching previously published date/close and extra facts."""
    day = market['date']
    by_code = {s['code']: s for s in market['stocks']}
    result = Counter()
    # An upstream historical feed can disagree with the published close for
    # several different reasons. Keep each rejected issuer traceable without
    # guessing whether the cause was KRX/NXT, adjustment, or a provider delay.
    source_mismatch_codes = {'saved_date': [], 'fetched_date': [],
                             'fetched_close': []}
    for row in collected['rows']:
        code, evidence = row['code'], row['metrics']
        stock = by_code.get(code)
        if stock is None:
            result['not_in_latest_market'] += 1
            continue
        mismatch = ('saved_date' if stock.get('price_date') != day else
                    'fetched_date' if evidence.get('price_date') != day else
                    'fetched_close' if evidence.get('price_latest') != stock.get('price')
                    else None)
        if mismatch is not None:
            result['source_close_or_date_mismatch'] += 1
            result[f'source_mismatch_{mismatch}'] += 1
            source_mismatch_codes[mismatch].append(code)
            continue
        # Stop reference and MA evidence have independent acceptance gates.
        # Even if MA cannot gain points, a valid unscored stop can be saved.
        reference = evidence.get('v3_structural_stop_reference')
        if reference is not None:
            if not valid_reference(reference, day, stock['price']):
                raise RecoveryError('invalid_recovered_structural_stop_reference')
            existing = stock.get('v3_structural_stop_reference')
            if existing is not None and not valid_reference(existing, day, stock['price']):
                raise RecoveryError('invalid_retained_structural_stop_reference')
            if existing is None:
                stock['v3_structural_stop_reference'] = reference
                result['new_unscored_stop_references'] += 1
            elif (legacy_stop_needs_recheck(existing)
                  and reference.get('validation_window_bars') is not None):
                stock['v3_structural_stop_reference'] = reference
                result['rechecked_legacy_stop_references'] += 1
                if reference.get('reference_line') is not None:
                    result['newly_usable_reference_lines'] += 1
        if 'pattern_weekly_research' in evidence:
            field = 'pattern_weekly_research'
            current_weekly = observed_pattern(stock, 'weekly')
            candidate_weekly = observed_pattern(
                dict(stock, **{field: evidence[field]}), 'weekly')
            if candidate_weekly is not None and (
                current_weekly is None or (
                    _weekly_trend(current_weekly) is None
                    and _weekly_trend(candidate_weekly) is not None)
            ):
                stock[field] = candidate_weekly
                result['more_observed_weekly_pattern_receipts'] += 1
        new = evidence.get('v3_ma_slope_transition')
        if new is None:
            result['still_unavailable'] += 1
            continue
        old_budget = availability(stock.get('v3_ma_slope_transition'), day)
        new_budget = availability(new, day)
        if not all(b >= a for a, b in zip(old_budget, new_budget)):
            result['lost_prior_window'] += 1
            continue
        if new_budget == old_budget:
            result['no_extra_observation'] += 1
            continue
        stock['v3_ma_slope_transition'] = new
        result['more_observed_receipts'] += 1
        result['new_daily_weight_tenths'] += round((new_budget[0]-old_budget[0])*10)
        result['new_weekly_weight_tenths'] += round((new_budget[1]-old_budget[1])*10)
    output = dict(result)
    if any(source_mismatch_codes.values()):
        output['source_mismatch_review_codes'] = {
            reason: sorted(codes) for reason, codes in source_mismatch_codes.items()
            if codes}
    return output


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair-legacy-stops', action='store_true',
                        help='One bounded review of old all-history stop failures')
    parser.add_argument('--corroborated-close-only', action='store_true',
                        help='Query only stocks matching the KRX-corroborated dated close')
    parser.add_argument('--include-weekly-pattern', action='store_true')
    args = parser.parse_args(argv)
    if args.include_weekly_pattern and not args.corroborated_close_only:
        parser.error('weekly pattern recovery requires the corroborated close option')
    docs = Path('docs')
    def load(name):
        return json.loads((docs/name).read_text(encoding='utf8'))
    market_raw = (docs/'data.json').read_bytes()
    market = json.loads(market_raw)
    priority = load('v3-operational-evidence-priority.json')
    report = load('v3-operational-research-scores.json')
    cohorts, cohort = select_targets(market, priority, report,
        repair_legacy_stops=args.repair_legacy_stops,
        include_weekly_pattern=args.include_weekly_pattern)
    if args.corroborated_close_only:
        from v3_dated_market_roster import load_dated_listing
        crosscheck = load('v3-krx-saved-close-crosscheck.json')
        listing, listing_sha = load_dated_listing(market['date'])
        matched = restrict_to_corroborated_close(
            cohorts, market, crosscheck, sha256(market_raw).hexdigest(), listing, listing_sha)
        cohort['below_90_close_mismatch_or_missing'] = len(cohorts[0]) - len(matched[0])
        cohort['already_90_close_mismatch_or_missing'] = len(cohorts[1]) - len(matched[1])
        cohorts = matched
        if sum(map(len, cohorts)) > 400:
            raise RecoveryError('unexpectedly_large_corroborated_target_cohort')
        cohort['below_90_priority'], cohort['already_90_priority'] = map(len, cohorts)
    print(json.dumps({'stage':'selected','asof':market['date'],
                      'count':sum(map(len,cohorts)),**cohort},sort_keys=True),flush=True)
    # Finish the potential <90 threshold recoveries before spending provider
    # time on already-90 issuers.  Each receipt remains source-date checked.
    first, second = [collect(codes, market['date'],
                            include_weekly_pattern=args.include_weekly_pattern)
                     for codes in cohorts]
    collected = {'rows': first['rows']+second['rows'],
                 'errors': first['errors']+second['errors'],
                 'excluded': first['excluded']+second['excluded'],
                 'examined': first['examined']+second['examined']}

    # Another bot may have published while source calls were in flight.
    # Re-read the latest actual saved market, never overwrite it blindly.
    subprocess.run(['git','pull','--ff-only','origin','main'],check=True)
    latest_raw = (docs/'data.json').read_bytes()
    latest = json.loads(latest_raw)
    if args.corroborated_close_only and latest_raw != market_raw:
        raise RecoveryError('corroborated_market_changed_during_collection')
    if latest['date'] != market['date']:
        raise RecoveryError('published_close_advanced_during_collection')
    changes = apply(latest, collected)
    print(json.dumps({'stage':'verified','asof':latest['date'],
                      'examined':collected['examined'],
                      'provider_errors':len(collected['errors']),
                      'insufficient_history':len(collected['excluded']),**changes},
                     sort_keys=True),flush=True)
    if changes.get('more_observed_weekly_pattern_receipts'):
        from v3_pattern_composite_score import compute_candidates
        compute_candidates(latest['stocks'])
        status = latest['v3_technical_pattern_status']
        status['technical_observed_25_count'] = sum(
            r['v3_pattern_integrated']['technical_research_candidate'] is not None
            for r in latest['stocks'])
        status['candidate_full_six_pillar_count'] = sum(
            r['v3_pattern_integrated']['six_pillar_full_candidate_only'] is not None
            for r in latest['stocks'])
    if (changes.get('more_observed_receipts', 0)
            or changes.get('more_observed_weekly_pattern_receipts', 0)
            or changes.get('new_unscored_stop_references', 0)
            or changes.get('rechecked_legacy_stop_references', 0)):
        out = docs/'data.json'
        out.write_text(json.dumps(latest,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


if __name__=='__main__':
    main()
