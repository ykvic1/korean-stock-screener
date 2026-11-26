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


def self_test():
    m = {'code': '123456', 'market': 'KOSPI', 'name': 'Example'}
    def p(v, name='Example'):
        return {'rows': {'123456': ['KOSPI', name, 1, 1, 1, v]}}
    assert trailing_zero(m, [p(True), p(False), p(False)]) == 2
    assert trailing_zero(m, [p(False), None, p(False)]) == 1
    assert trailing_zero(m, [p(False, 'Other'), p(False)]) == 1
    assert trailing_zero(m, [p(False), p(None)]) == 0
    assert trailing_zero(m, [p(False)] * WINDOW) == WINDOW


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
    if len(rows) != catalog['listed_total'] or any(r['기준일'] != catalog['date'] for r in rows):
        raise ValueError('misaligned_csv')
    scores = {r['종목코드']: float(r['확보배점']) for r in rows}
    days = history_dates(catalog['date'])[-WINDOW:]
    panels = [read_day((args.archive / archive_path(day)).read_bytes(), day) for day in days]
    print(json.dumps(audit(catalog, scores, status, panels), sort_keys=True))


if __name__ == '__main__':
    main()
