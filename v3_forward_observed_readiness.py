"""Reconcile actual frozen score cohorts with published KRX archive checkpoints."""
import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from v3_forward_daily_prices import (
    MARKETS, OUT as DAILY, SCHEMA as DAILY_SCHEMA, SHA, completed_sessions,
    official_publish_time,
)
from v3_forward_entry import OUT as ENTRIES, entry_plan
from v3_forward_horizon_readiness import MAX_SETTLEMENT_LAG_DAYS
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


def horizon_readiness(entry_day, target, sessions, archived, now, opening_ready):
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
    }


def build(root, now):
    archived = read_archived_days(root)
    cohorts = {}
    for path in sorted((root / ARCHIVE).glob('????-??-??.json')):
        raw = path.read_bytes()
        plan = entry_plan(json.loads(raw), sha256(raw).hexdigest())
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
        targets = {key: date.fromisoformat(value) for key, value in plan['horizon_target_dates'].items()}
        sessions = [date.fromisoformat(value) for value in completed_sessions(
            entry_day, max(targets.values()) + timedelta(days=MAX_SETTLEMENT_LAG_DAYS), now)]
        cohorts[path.stem] = {
            'score_observation_sha256': plan['score_observation_sha256'],
            'ranked_count': plan['ranked_count'],
            'entry_session_candidate': entry_day.isoformat(),
            'matched_opening_count': crosscheck['observed_openings_checked'] if opening_ready else None,
            'horizons': {key: horizon_readiness(entry_day, target,
                         [day for day in sessions if day <= target + timedelta(days=MAX_SETTLEMENT_LAG_DAYS)],
                         archived, now, opening_ready) for key, target in targets.items()},
        }
    return {'schema': 'v3-forward-observed-archive-readiness-1',
            'calendar_is_candidate_only': True, 'cohorts': cohorts,
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
