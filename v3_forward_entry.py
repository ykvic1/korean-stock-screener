"""Link fixed score observations to first-session KRX open evidence privately."""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import re
import unicodedata

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import DailyEvidenceError, collect_daily
from v3_forward_horizon_readiness import HORIZONS, _anniversary
from v3_krx_derived_5day import DerivedSourceError, _private_new_blob_readback
from v3_krx_history_collection import MARKETS, HistoryCollectionError, _snapshot_market
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError
from v3_krx_regular_close import ANNOUNCED_CLOSES, KST, regular_close_utc
from v3_krx_session_candidates import CONFIRMED_KRX_2026_CLOSURES
from v3_krx_private_daily import IngestError, _whole
from v3_score_observation_capture import ARCHIVE, SCHEMA as SCORE_SCHEMA

OUT = Path('ops/forward-entry-status')
PRIVATE_REPO = 'ykvic1/korean-stock-private-data'
SCHEMA = 'v3-forward-opening-evidence-1'
ROW_STATES = ('unranked', 'absent', 'identity_unresolved', 'not_traded',
              'open_unavailable', 'official_open_observed')
MAX_COHORTS_PER_RUN = 5
SHA = re.compile(r"[a-f0-9]{64}\Z")


class EntryError(ValueError):
    """Static errors suitable for public workflow logs."""


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise EntryError('expected_utc_timestamp')
    return parsed


ANNOUNCED_OPENS = {day: time(10) for day in ANNOUNCED_CLOSES}


def first_open_after(captured):
    """Select the first candidate regular open strictly after capture."""
    import exchange_calendars as xc

    calendar = xc.get_calendar('XKRX')
    start = captured.astimezone(KST).date()
    for session in calendar.sessions_in_range(start, start + timedelta(days=14)):
        day = session.date()
        if day.isoformat() in CONFIRMED_KRX_2026_CLOSURES:
            continue
        opening = calendar.session_open(session).to_pydatetime()
        if day in ANNOUNCED_OPENS:
            opening = datetime.combine(day, ANNOUNCED_OPENS[day], KST)
        if opening > captured:
            closing = (regular_close_utc(day) if day in ANNOUNCED_CLOSES
                       else calendar.session_close(session).to_pydatetime())
            return day, opening.astimezone(timezone.utc), closing.astimezone(timezone.utc)
    raise EntryError('no_entry_calendar_candidate')


def normalized_name(value):
    if not isinstance(value, str):
        return ''
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def entry_plan(observation, source_digest):
    score_day = date.fromisoformat(observation['score_asof'])
    captured = timestamp(observation['captured_at_utc'])
    rows = observation['rows']
    if (observation['schema'] != SCORE_SCHEMA or not isinstance(rows, dict)
            or not rows or len(rows) != observation['listed_count']
            or sum(row[3] is not None for row in rows.values()) != observation['ranked_count']
            or any(row[1] not in MARKETS for row in rows.values())
            or timestamp(observation['regular_close_utc']) != regular_close_utc(score_day)
            or captured < regular_close_utc(score_day)):
        raise EntryError('invalid_archived_score_observation')
    day, opening, closing = first_open_after(captured)
    return {
        'schema': SCHEMA,
        'score_asof': score_day.isoformat(),
        'score_observation_sha256': source_digest,
        'score_captured_at_utc': captured.isoformat(),
        'listed_count': len(rows),
        'ranked_count': observation['ranked_count'],
        'entry_session_candidate': day.isoformat(),
        'regular_open_utc': opening.isoformat(),
        'regular_close_utc': closing.isoformat(),
        'horizon_target_dates': {str(n): _anniversary(day, n).isoformat() for n in HORIZONS},
        'session_calendar_source': 'xkrx_candidate_with_announced_overrides',
        'issuer_continuity_certified': False,
        'adjusted_entry_prices_certified': False,
        'performance_validated': False,
    }


def read_official_openings(client, day):
    observed = {}
    hashes = {}
    for market in MARKETS:
        rows, raw = client.daily_market(market, day)
        stats = _snapshot_market(rows, raw, market=market, asof=day)
        hashes[market] = stats['source_sha256']
        for row in rows:
            code = row['ISU_CD']
            if code in observed:
                raise EntryError('ambiguous_official_market_code')
            observed[code] = (market, row)
    return observed, hashes


def classify_entry(member, official):
    name, market, _, rank = member[:4]
    if rank is None:
        return 'unranked', None
    if official is None:
        return 'absent', None
    actual_market, row = official
    official_name = normalized_name(row.get('ISU_NM'))
    if actual_market != market or not official_name or normalized_name(name) != official_name:
        return 'identity_unresolved', None
    try:
        if _whole(row.get('ACC_TRDVOL'), 'volume') == 0:
            return 'not_traded', None
        price = _whole(row.get('TDD_OPNPRC'), 'open')
        low = _whole(row.get('TDD_LWPRC'), 'low')
        high = _whole(row.get('TDD_HGPRC'), 'high')
    except IngestError:
        return 'open_unavailable', None
    if not 0 < low <= price <= high:
        return 'open_unavailable', None
    return 'official_open_observed', price


def public_result(plan, evidence, members):
    basis = evidence['plan']
    rows = evidence['rows']
    hashes = evidence['source_sha256']
    if (basis != plan or rows.keys() != members.keys()
            or set(hashes) != set(MARKETS)
            or any(not isinstance(digest, str) or not SHA.fullmatch(digest)
                   for digest in hashes.values())
            or timestamp(evidence['retrieved_at_utc']) < timestamp(plan['regular_close_utc'])):
        raise EntryError('private_entry_readback_mismatch')
    counts = Counter()
    for code, (state, price) in rows.items():
        observed = state == 'official_open_observed'
        if (state not in ROW_STATES
                or (state == 'unranked') != (members[code][3] is None)
                or (observed and (type(price) is not int or not 0 < price <= 10**15))
                or (not observed and price is not None)):
            raise EntryError('invalid_entry_status_or_price')
        counts[state] += 1
    return {**plan, 'status': 'official_openings_archived_privately',
            'retrieved_at_utc': evidence['retrieved_at_utc'],
            'krx_market_source_sha256': hashes,
            'entry_status_counts': {state: counts[state] for state in ROW_STATES}}


def save_entry(plan, observation, storage, official_openings):
    path = f"derived/forward-entry/{plan['score_asof']}.json.gz"
    members = observation['rows']
    blob, _ = storage.get_content(path)
    if blob is not None:
        return public_result(plan, json.loads(gzip.decompress(blob)), members)
    official, hashes = official_openings(plan['entry_session_candidate'])
    evidence = {
        'plan': plan,
        'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': hashes,
        'rows': {code: classify_entry(member, official.get(code))
                 for code, member in members.items()},
    }
    result = public_result(plan, evidence, members)
    blob = gzip.compress(json.dumps(evidence, separators=(',', ':'), allow_nan=False).encode(), mtime=0)
    storage.put_new(path, blob, 'Archive KRX opening evidence for ' + plan['score_asof'])
    _private_new_blob_readback(storage, path, blob)
    return result


def main():
    now = datetime.now(timezone.utc)
    @lru_cache(maxsize=1)
    def storage_and_key():
        if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
            raise EntryError('private_destination_unavailable')
        key = os.getenv('KRX_API_KEY', '').strip()
        if not key:
            raise EntryError('krx_key_unavailable')
        storage = PrivateGitStorage(PRIVATE_REPO, os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', ''))
        storage.verify_private()
        return storage, key

    @lru_cache(maxsize=1)
    def official_client():
        return KRXClient(storage_and_key()[1])

    @lru_cache(maxsize=10)
    def official_openings(day):
        # Include empty responses in the per-run cache so the two collectors
        # do not repeat a call while KRX is still publishing a session.
        try:
            return read_official_openings(official_client(), day), None
        except KRXProbeError as error:
            if error.reason not in ('empty_body', 'empty_market_rows'):
                raise
            return None, error

    def official_pair(day):
        result, error = official_openings(day)
        if error is not None:
            raise error
        return result

    OUT.mkdir(parents=True, exist_ok=True)
    # Try new cohorts first and then rotate pending ones by last attempt.
    # A missing historical response must not monopolize the daily call budget.
    def work_priority(source):
        target = OUT / source.name
        if target.exists():
            prior = json.loads(target.read_bytes())
            return prior.get('last_source_attempt_at_utc', ''), source.name
        return '', source.name

    processed = 0
    for source in sorted(ARCHIVE.glob('????-??-??.json'), key=work_priority):
        raw = source.read_bytes()
        digest = sha256(raw).hexdigest()
        target = OUT / source.name
        if target.exists():
            previous = json.loads(target.read_bytes())
            if previous['score_observation_sha256'] != digest:
                raise EntryError('archived_score_hash_changed')
            if previous['status'] == 'official_openings_archived_privately':
                continue
        if processed >= MAX_COHORTS_PER_RUN:
            break
        processed += 1
        observation = json.loads(raw)
        plan = entry_plan(observation, digest)
        if source.stem != plan['score_asof']:
            raise EntryError('score_archive_filename_mismatch')
        if now < timestamp(plan['regular_close_utc']):
            result = {**plan, 'status': 'waiting_for_session_close'}
        else:
            try:
                result = save_entry(plan, observation, storage_and_key()[0], official_pair)
            except KRXProbeError as error:
                if error.reason not in ('empty_body', 'empty_market_rows'):
                    raise EntryError('krx_' + error.reason) from None
                result = {**plan, 'status': 'waiting_for_official_paired_response',
                          'last_source_attempt_at_utc': now.isoformat()}
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))

    entry_statuses = []
    for source in sorted(ARCHIVE.glob('????-??-??.json')):
        target = OUT / source.name
        if target.exists():
            status = json.loads(target.read_bytes())
            if status['score_observation_sha256'] != sha256(source.read_bytes()).hexdigest():
                raise EntryError('archived_score_hash_changed')
            entry_statuses.append(status)
    collect_daily(entry_statuses, now, lambda: storage_and_key()[0], official_pair)


if __name__ == '__main__':
    try:
        main()
    except (EntryError, DailyEvidenceError, HistoryCollectionError,
            PrivateHistoryError, DerivedSourceError) as error:
        raise SystemExit(str(error)) from None
