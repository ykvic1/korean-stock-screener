"""Reconcile actual frozen score cohorts with published KRX archive checkpoints."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from v3_forward_daily_prices import (
    MARKETS, OUT as DAILY, SCHEMA as DAILY_SCHEMA, SHA, completed_sessions,
    official_publish_time,
)
from v3_forward_entry import OUT as ENTRIES, ROW_STATES, entry_plan
from v3_forward_horizon_readiness import HORIZONS, MAX_SETTLEMENT_LAG_DAYS
from v3_forward_share_actions import OUT as SHARE_REVIEWS, SCHEMA as SHARE_SCHEMA, STATES, normalized_name
from v3_score_observation_capture import ARCHIVE

OUTPUT = Path('ops/v3-forward-observed-readiness.json')


def read_archived_days(root):
    days = {}
    for path in sorted((root / DAILY).glob('????-??-??.json')):
        row = json.loads(path.read_bytes())
        if row.get('schema') != DAILY_SCHEMA or row.get('date') != path.stem:
            raise ValueError('invalid_daily_archive_checkpoint')
        if row.get('status') == 'waiting_for_official_paired_response':
            continue
        hashes = row.get('source_sha256')
        if (row.get('status') != 'archived_privately' or not isinstance(hashes, dict)
                or set(hashes) != set(MARKETS)
                or any(not isinstance(value, str) or not SHA.fullmatch(value)
                       for value in hashes.values())):
            raise ValueError('invalid_daily_archive_checkpoint')
        days[path.stem] = hashes
    return days


def read_share_reviews(root, archived):
    """Count only reviews tied to both archived KRX responses."""
    reviews = {}
    for path in sorted((root / SHARE_REVIEWS).glob('????-??-??.json')):
        row = json.loads(path.read_bytes())
        earlier, current = row.get('previous_session'), row.get('session')
        counts = row.get('states')
        if (row.get('schema') != SHARE_SCHEMA or current != path.stem
                or not isinstance(earlier, str) or earlier >= current
                or row.get('status') != 'adjacent_share_counts_reviewed'
                or not isinstance(counts, dict) or set(counts) != set(STATES)
                or any(type(n) is not int or n < 0 for n in counts.values())
                or type(row.get('compared_distinct_codes')) is not int
                or not 0 < sum(counts.values()) == row['compared_distinct_codes']):
            raise ValueError('invalid_share_action_checkpoint')
        if (earlier in archived and current in archived
                and row.get('previous_source_sha256') == archived[earlier]
                and row.get('current_source_sha256') == archived[current]):
            reviews[earlier, current] = counts
    return reviews


def share_review_coverage(sessions, reviews):
    pairs = [(earlier.isoformat(), current.isoformat())
             for earlier, current in zip(sessions, sessions[1:])]
    missing = [pair for pair in pairs if pair not in reviews]
    unresolved_states = ('share_count_unavailable', 'identity_changed',
                         'change_needs_notice', 'notice_mismatch')
    return {
        'due_transition_count': len(pairs),
        'source_linked_review_count': len(pairs) - len(missing),
        'first_missing_review_session': missing[0][1] if missing else None,
        'unresolved_market_code_transitions_in_linked_reviews': sum(
            reviews[pair][state] for pair in pairs if pair in reviews
            for state in unresolved_states),
    }


def horizon_readiness(entry_day, target, sessions, archived, now, opening_ready, reviews):
    candidates = [day for day in sessions if day >= target]
    target_session = candidates[0] if candidates else None
    path_end = target_session or (target + timedelta(days=MAX_SETTLEMENT_LAG_DAYS))
    due = [day for day in sessions if entry_day <= day <= path_end
           and official_publish_time(day) <= now]
    gaps = [day for day in due if day.isoformat() not in archived]
    if target_session is None:
        state = 'waiting_for_target_session'
    elif official_publish_time(target_session) > now:
        state = 'waiting_for_official_publication'
    elif not opening_ready:
        state = 'waiting_for_matched_entry'
    elif gaps:
        state = 'daily_archive_gaps'
    else:
        state = 'daily_archive_ready_for_path_replay'
    return {
        'target_date': target.isoformat(),
        'target_session_candidate': target_session.isoformat() if target_session else None,
        'status': state,
        'due_session_count': len(due),
        'archived_session_count': len(due) - len(gaps),
        'first_missing_due_session': gaps[0].isoformat() if gaps else None,
        'share_action_review_coverage': share_review_coverage(due, reviews),
    }


def top_ranked_issuers(rows):
    top20, top100 = set(), set()
    for code, row in rows.items():
        rank = row[3]
        if rank is None or rank > 100:
            continue
        issuer = (code, row[1], normalized_name(row[0]))
        top100.add(issuer)
        if rank <= 20:
            top20.add(issuer)
    return {'top20': top20, 'top100': top100}


def sample_entry_sessions(cohorts, snapshots_by_session):
    sessions, primary = {}, []
    for entry_day, snapshots in sorted(snapshots_by_session.items()):
        # entry_plan already checks the frozen capture precedes this session's open.
        # Use the most recent score date even if its opening evidence is still missing.
        score_dates = sorted(snapshots)
        representative = score_dates[-1]
        primary.append(representative)
        sessions[entry_day] = {
            'representative_score_asof': representative,
            'score_dates': score_dates,
            'overlap_with_representative': {
                day: {label: len(snapshots[day][label] & snapshots[representative][label])
                      for label in ('top20', 'top100')}
                for day in score_dates[:-1]
            },
        }
        for day in score_dates:
            cohorts[day]['entry_session_sample'] = {
                'representative_score_asof': representative,
                'counts_toward_distinct_entry_sessions': day == representative,
            }
    return {
        'representative_rule': 'latest_score_asof_frozen_before_candidate_open',
        'distinct_entry_session_count': len(primary),
        'matched_representative_entry_session_count': sum(
            cohorts[day]['matched_opening_count'] is not None for day in primary),
        'by_entry_session': sessions,
        'horizon_readiness_counts': {
            str(months): dict(sorted(Counter(
                cohorts[day]['horizons'][str(months)]['status'] for day in primary).items()))
            for months in HORIZONS
        },
    }


def build(root, now):
    archived = read_archived_days(root)
    reviews = read_share_reviews(root, archived)
    cohorts = {}
    snapshots_by_session = {}
    for path in sorted((root / ARCHIVE).glob('????-??-??.json')):
        raw = path.read_bytes()
        observation = json.loads(raw)
        plan = entry_plan(observation, sha256(raw).hexdigest())
        if path.stem != plan['score_asof']:
            raise ValueError('invalid_frozen_score_filename')
        entry_path = root / ENTRIES / path.name
        entry = json.loads(entry_path.read_bytes()) if entry_path.exists() else {}
        if entry and any(entry.get(key) != value for key, value in plan.items()):
            raise ValueError('entry_checkpoint_does_not_match_frozen_score')
        entry_day = date.fromisoformat(plan['entry_session_candidate'])
        prices = archived.get(entry_day.isoformat())
        crosscheck = entry.get('entry_daily_crosscheck', {})
        opening_ready = bool(prices and entry.get('status') == 'official_openings_archived_privately'
            and crosscheck.get('status') == 'matched'
            and crosscheck.get('entry_source_sha256') == crosscheck.get('daily_source_sha256') == prices
            and type(crosscheck.get('observed_openings_checked')) is int
            and 0 < crosscheck['observed_openings_checked'] <= plan['ranked_count']
            and crosscheck.get('mismatched_openings') == 0)
        ranked_entries = None
        if opening_ready:
            counts = entry.get('entry_status_counts')
            if (not isinstance(counts, dict) or counts.keys() != set(ROW_STATES)
                    or any(type(count) is not int or count < 0 for count in counts.values())
                    or sum(counts.values()) != plan['listed_count']
                    or counts['unranked'] != plan['listed_count'] - plan['ranked_count']
                    or counts['official_open_observed'] != crosscheck['observed_openings_checked']):
                raise ValueError('invalid_ranked_entry_coverage_checkpoint')
            ranked_entries = {state: counts[state] for state in ROW_STATES if state != 'unranked'}
        targets = {key: date.fromisoformat(value) for key, value in plan['horizon_target_dates'].items()}
        sessions = [date.fromisoformat(value) for value in completed_sessions(
            entry_day - timedelta(days=14),
            max(targets.values()) + timedelta(days=MAX_SETTLEMENT_LAG_DAYS), now)]
        earlier = next((day for day in reversed(sessions) if day < entry_day), None)
        entry_boundary = (reviews.get((earlier.isoformat(), entry_day.isoformat()))
                          if earlier is not None and entry_day in sessions else None)
        cohorts[path.stem] = {
            'score_observation_sha256': plan['score_observation_sha256'],
            'ranked_count': plan['ranked_count'],
            'entry_session_candidate': entry_day.isoformat(),
            'matched_opening_count': crosscheck['observed_openings_checked'] if opening_ready else None,
            'ranked_entry_status_counts': ranked_entries,
            'entry_share_action_review': {
                'previous_session_candidate': earlier.isoformat() if earlier else None,
                'source_linked': entry_boundary is not None,
                'market_transition_counts': entry_boundary,
            },
            'horizons': {key: horizon_readiness(entry_day, target,
                         [day for day in sessions if day <= target + timedelta(days=MAX_SETTLEMENT_LAG_DAYS)],
                         archived, now, opening_ready, reviews) for key, target in targets.items()},
        }
        snapshots_by_session.setdefault(entry_day.isoformat(), {})[path.stem] = top_ranked_issuers(
            observation['rows'])
    return {'schema': 'v3-forward-observed-archive-readiness-1',
            'calendar_is_candidate_only': True, 'cohorts': cohorts,
            'entry_session_sampling': sample_entry_sessions(cohorts, snapshots_by_session),
            'full_corporate_action_history_certified': False,
            'performance_validated': False, 'provider_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--at-utc', type=datetime.fromisoformat)
    args = parser.parse_args()
    now = args.at_utc or datetime.now(timezone.utc)
    if now.utcoffset() != timedelta(0):
        parser.error('--at-utc must have UTC offset')
    if args.write and args.at_utc:
        parser.error('--at-utc is for read-only replay')
    payload = json.dumps(build(Path('.'), now), ensure_ascii=False, indent=2) + '\n'
    if args.write:
        OUTPUT.write_text(payload, encoding='utf-8')
    print(payload, end='')
