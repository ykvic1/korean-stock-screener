"""Read-only official zero-trade lower bounds and coverage sensitivity.

No raw exchange rows, prices, source responses or issuer-level private observations
are exported. No score, roster, or production denominator is modified.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from v3_regular_close_indicators import history_dates, archive_path, read_day
from v3_forward_share_actions import normalized_name

WINDOW = 60


def trailing_zero(member, panels):
    """Return a lower bound; a missing observation/identity stops the streak."""
    count = 0
    for panel in reversed(panels):
        row = panel['rows'].get(member['code']) if panel else None
        if (row is None or row[0] != member['market'] or
                normalized_name(row[1]) != normalized_name(member['name']) or
                len(row) != 6 or row[5] is not False):
            break
        count += 1
    return count


def audit(catalog, scores, status, panels):
    day = catalog['date']
    members = {r['code']: r for r in catalog['rows']}
    if (len(members) != catalog['listed_total'] or set(scores) != set(members)
            or status['asof'] != day or status['listed_total'] != len(members)
            or len(panels) != WINDOW or any(p is None for p in panels)
            or panels[-1]['date'] != day):
        raise ValueError('incomplete_aligned_audit')
    if any(not 0 <= v <= 100 for v in scores.values()):
        raise ValueError('invalid_coverage_weight')
    streaks = {code: trailing_zero(member, panels) for code, member in members.items()}
    zero_ref = {code for code, row in status['rows'].items() if row['state'] == 'zero_volume'}
    if not zero_ref <= members.keys():
        raise ValueError('unknown_status_identity')
    sensitivity = {}
    for threshold in (5, 20, 60):
        excluded = {code for code, streak in streaks.items() if streak >= threshold}
        eligible = set(members) - excluded
        full = sum(scores[c] == 100 for c in eligible)
        weight = sum(scores[c] for c in eligible)
        sensitivity[str(threshold)] = {
            'verified_zero_trade_lower_bound_issuers': len(excluded),
            'among_public_zero_volume_reference': len(excluded & zero_ref),
            'eligible_if_excluded': len(eligible),
            'full_100_if_excluded': full,
            'full_100_rate_pct_if_excluded': round(full / len(eligible) * 100, 4),
            'aggregate_weight_pct_if_excluded': round(weight / len(eligible), 4),
        }
    return {'asof': day, 'listed': len(members), 'archived_sessions_examined': WINDOW,
            'public_zero_volume_reference': len(zero_ref),
            'official_zero_trade_today': sum(n >= 1 for n in streaks.values()),
            'public_reference_confirmed_today': sum(streaks[c] >= 1 for c in zero_ref),
            'public_reference_not_confirmed_today': sum(streaks[c] == 0 for c in zero_ref),
            'threshold_sensitivity': sensitivity, 'provider_calls': 0,
            'official_halt_reason_certified': False, 'exclusion_applied': False,
            'scores_changed': False}


def missing_boundary_class(records, index):
    # Absence before first retained record is not proof of listing date.
    if index == len(records) - 1:
        return 'current_issuer_observation_absent'
    if any(r is not None for r in records[:index]):
        return 'internal_missing_row'
    return 'no_prior_observation_in_window'


def continuity_audit(catalog, scores, panels):
    """Explain the first daily tail break exactly as the current scorer sees it.

    Diagnostic only: a reference discontinuity is NOT proof of a corporate action.
    The daily scorer does not pass face notices. Do not silently bridge gaps.
    """
    from v3_regular_close_indicators import continuous_tail
    from v3_regular_close_contract import SESSIONS
    if len(panels) != SESSIONS or any(p is None for p in panels):
        raise ValueError('incomplete_continuity_archive')
    groups = {}
    missing_subtypes = Counter()
    for member in catalog['rows']:
        code = member['code']
        records = [p['rows'].get(code) for p in panels]
        n = len(continuous_tail(records, member))
        reason = 'complete'
        if n < SESSIONS:
            index = SESSIONS - n - 1
            row = records[index]
            nxt = records[index + 1] if n else None
            if row is None:
                reason = 'missing_issuer_observation'
                missing_subtypes[missing_boundary_class(records, index)] += 1
            elif row[0] != member['market']:
                reason = 'market_identity_mismatch'
            elif normalized_name(row[1]) != normalized_name(member['name']):
                reason = 'name_identity_mismatch'
            elif nxt is not None and nxt[4] != row[2]:
                reason = ('reference_missing' if nxt[4] is None
                          else 'reference_close_discontinuity')
            elif nxt is not None and abs(nxt[2] / row[2] - 1) > .5:
                reason = 'large_price_jump'
            else:
                raise ValueError('unexplained_daily_tail_boundary')
        group = groups.setdefault(reason, {'issuers': 0, 'full_100': 0,
                                            'missing_weight': 0.0})
        group['issuers'] += 1
        group['full_100'] += scores[code] == 100
        group['missing_weight'] += 100 - scores[code]
    for group in groups.values():
        group['missing_weight'] = round(group['missing_weight'], 6)
    return {'sessions_examined': SESSIONS, 'first_boundary_groups': groups,
            'issuer_count': len(scores), 'missing_boundary_subtypes': dict(missing_subtypes),
            'listing_start_not_certified': True, 'cause_is_not_verified_corporate_action': True,
            'missing_weight_is_associated_not_attributable_to_boundary': True,
            'weekly_continuity_separately_unverified': True,
            'provider_calls': 0, 'scores_changed': False}



def daily_factor_observations(tail, days, asof):
    """Use the unchanged production daily formulas on an evidence-bound tail."""
    import numpy as np
    from datetime import date
    from v3_regular_close_contract import SESSIONS, DAILY_SESSIONS
    from v3_pattern_first_observations import rsi_observation
    from v3_ma_slope_transition import _series_points, WEIGHTS
    from v3_ma240_cross_research import observe
    closes = np.asarray([r[2] for r in tail[-DAILY_SESSIONS:]], dtype=float)
    result = {}
    if len(tail) >= 90:
        rsi = rsi_observation(closes)
        if rsi is not None:
            result['daily_rsi'] = (1.2, float(1.2 if rsi['rsi30_cross_recent'] or
                                            rsi['rsi_bullish_divergence_recent'] else 0))
    turn = _series_points(closes, prior=60, recent=20, hold=5, floor=.0001,
                          frame='daily', trusted_window=lambda n: len(closes) >= n)
    if turn:
        for period, value in turn.items():
            if value is not None:
                result['daily_ma_turn_' + period] = (WEIGHTS['daily'][period], value)
    if len(tail) == SESSIONS:
        traded = [int(r[5]) if r[5] is not None else None for r in tail]
        cross = observe([r[2] for r in tail], traded, asof=asof,
                        session_days=[date.fromisoformat(d) for d in days])
        if cross is not None:
            result['ma240_cross'] = (6., 6. if cross['qualified'] else 0.)
    return result


def face_shadow(catalog, scores, panels, operational=None):
    """Replay the already verified weekly face-action gate on daily tails.

    Measure improvement only. No issuer-level restricted values leave this audit,
    no raw bars are modified, and no production observations are promoted.
    """
    from v3_regular_close_indicators import continuous_tail
    from v3_krx_close_momentum_recovery import load_face_notices
    from v3_regular_close_contract import SESSIONS, DAILY_SESSIONS
    if len(panels) != SESSIONS or any(p is None for p in panels):
        raise ValueError('incomplete_face_shadow_archive')
    notices, _ = load_face_notices()
    positions = {p['date']: i for i, p in enumerate(panels)}
    if len(positions) != SESSIONS:
        raise ValueError('duplicate_face_shadow_session')
    counts = Counter()
    new_factors, changed_factors = Counter(), Counter()
    candidate_weight = 0.
    net_candidate_weight = 0.
    net_issuers = 0
    net_full_candidates = 0
    days = [p['date'] for p in panels]
    for member in catalog['rows']:
        code = member['code']
        records = [p['rows'].get(code) for p in panels]
        before = continuous_tail(records, member)
        matches = {positions[d]: n for d, n in notices.get(code, {}).items()
                   if d in positions}
        after = continuous_tail(records, member, matches)
        n, m = len(before), len(after)
        if m < n:
            raise ValueError('face_shadow_lost_existing_continuity')
        counts['issuers'] += 1
        if matches:
            counts['issuers_with_existing_dated_notice'] += 1
        if m > n:
            counts['verified_tail_extensions'] += 1
            counts['added_observed_sessions'] += m - n
            counts['incomplete_score_issuers_with_extension'] += scores[code] < 100
            old = daily_factor_observations(before, days, catalog['date'])
            new = daily_factor_observations(after, days, catalog['date'])
            if old.keys() - new.keys():
                raise ValueError('face_replay_lost_observed_factor')
            for key in new.keys() - old.keys():
                new_factors[key] += 1
                candidate_weight += new[key][0]
            for key in old.keys() & new.keys():
                changed_factors[key] += old[key][1] != new[key][1]
            if operational is not None:
                parts = operational['rows'][code]['parts']
                # Compare whole component weights, including the combined daily
                # MA turn. Do not double-count individual 20/60/120 contributions.
                groups = [
                    ('technical', 'daily_rsi', ('daily_rsi',)),
                    ('technical', 'ma20_ma60_cross_near_ma240', ('ma240_cross',)),
                    ('momentum', 'daily_ma_slope_turn',
                     ('daily_ma_turn_20', 'daily_ma_turn_60', 'daily_ma_turn_120')),
                ]
                delta = 0.
                for area, key, keys in groups:
                    before_weight = sum(old[k][0] for k in keys if k in old)
                    after_weight = sum(new[k][0] for k in keys if k in new)
                    existing = parts[area].get(key)
                    observed = float(existing[1]) if existing else 0.
                    # Count only the extension newly possible from this change,
                    # not gaps from unrelated source admission rules.
                    delta += max(0., after_weight - max(observed, before_weight))
                net_candidate_weight += delta
                if delta > 1e-6:
                    net_issuers += 1
                    net_full_candidates += scores[code] < 100 and scores[code] + delta >= 100 - 1e-5
            for threshold in (90, 120, DAILY_SESSIONS, SESSIONS):
                if n < threshold <= m:
                    counts['crossed_' + str(threshold) + '_session_threshold'] += 1
    return {'counts': dict(counts), 'new_official_daily_factor_counts': dict(new_factors),
            'changed_existing_daily_factor_counts': dict(changed_factors),
            'new_official_factor_weight_before_operational_overlap': round(candidate_weight, 6),
            'incremental_weight_candidate_after_current_operational_overlap': round(net_candidate_weight, 6) if operational is not None else None,
            'incremental_weight_candidate_issuers': net_issuers if operational is not None else None,
            'potential_full_100_candidates': net_full_candidates if operational is not None else None,
            'provider_calls': 0, 'production_changed': False,
            'score_recovery_not_proven': True}


def self_test():
    m = {'code': '123456', 'market': 'KOSPI', 'name': 'Example'}
    def p(v, name='Example'):
        return {'rows': {'123456': ['KOSPI', name, 1, 1, 1, v]}}
    assert trailing_zero(m, [p(True), p(False), p(False)]) == 2
    assert trailing_zero(m, [p(False), None, p(False)]) == 1
    assert trailing_zero(m, [p(False, 'Other'), p(False)]) == 1
    assert trailing_zero(m, [p(False), p(None)]) == 0
    assert trailing_zero(m, [p(False)] * WINDOW) == WINDOW
    assert missing_boundary_class([None, 1], 0) == 'no_prior_observation_in_window'
    assert missing_boundary_class([1, None, 1], 1) == 'internal_missing_row'
    assert missing_boundary_class([1, None], 1) == 'current_issuer_observation_absent'
    from v3_regular_close_contract import SESSIONS
    from datetime import date, timedelta
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(SESSIONS)]
    assert daily_factor_observations([], days, days[-1]) == {}
    assert 'ma240_cross' in daily_factor_observations([p(True)['rows']['123456']] * SESSIONS, days, days[-1])
    catalog = {'rows': [m]}
    scores = {'123456': 90}
    for boundary, reason in [(p(False, 'Other'), 'name_identity_mismatch'),
                             ({'rows': {}}, 'missing_issuer_observation'),
                             (p(False), 'complete')]:
        result = continuity_audit(catalog, scores, [boundary] + [p(False)] * (SESSIONS - 1))
        assert result['first_boundary_groups'][reason]['issuers'] == 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--archive', type=Path)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    self_test()
    if args.self_test:
        print('offline streak regression checks passed')
        return
    if args.archive is None:
        raise ValueError('archive_required')
    root = Path('docs')
    catalog = json.loads((root / 'v22-market-catalog.json').read_bytes())
    status = json.loads((root / 'v3-trading-status.json').read_bytes())
    with (root / 'v3-operational-research-scores.csv').open(encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    from datetime import date
    comparison = json.loads((root / 'v3-operational-research-comparison.json').read_bytes())
    day = catalog['date']
    if (comparison['asof'] != day
            or comparison['operational_coverage']['listed_total'] != catalog['listed_total']
            or len(rows) != catalog['listed_total']
            or any(date.fromisoformat(r['기준일']) > date.fromisoformat(day) for r in rows)):
        raise ValueError('misaligned_csv')
    scores = {r['종목코드']: float(r['확보배점']) for r in rows}
    if (len(scores) != len(rows) or sum(v == 100 for v in scores.values())
            != comparison['operational_coverage']['available_100_count']):
        raise ValueError('csv_comparison_mismatch')
    from v3_regular_close_contract import SESSIONS
    days = history_dates(day)[-SESSIONS:]
    panels = [read_day((args.archive / archive_path(d)).read_bytes(), d) for d in days]
    result = audit(catalog, scores, status, panels[-WINDOW:])
    result['daily_continuity'] = continuity_audit(catalog, scores, panels)
    operational = json.loads((root / 'v3-operational-research-scores.json').read_bytes())
    if (operational['asof'] != day or operational['format'] != 'compact-parts-v1'
            or set(operational['rows']) != set(scores)
            or any(abs(float(r['available']) - scores[c]) > 1e-5
                   for c, r in operational['rows'].items())):
        raise ValueError('misaligned_operational_overlap_source')
    result['verified_face_daily_shadow'] = face_shadow(catalog, scores, panels, operational)
    # A prior valid row is retained by design; audit sensitivity uses the actual
    # published weights, not an invented all-current data snapshot.
    result['score_row_asof_counts'] = dict(Counter(r['기준일'] for r in rows))
    result['retained_prior_date_score_rows'] = sum(r['기준일'] != day for r in rows)
    result['unadjusted_full_100_count'] = sum(v == 100 for v in scores.values())
    result['unadjusted_aggregate_weight_pct'] = round(sum(scores.values()) / len(scores), 4)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
