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
from statistics import median

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import (
    DailyEvidenceError, IncompleteOfficialPair, RETRYABLE_KRX_REASONS,
    collect_daily, pending_official_pair, read_daily,
)
from v3_forward_horizon_readiness import HORIZONS, _anniversary
from v3_forward_share_actions import ShareReviewError, collect_share_reviews, normalized_name
from v3_forward_signal_close import SignalCloseError, collect_signal_closes, compare_closes, frozen_market
from v3_regular_close_prices import RegularCloseError, publish_regular_closes
from v3_krx_derived_5day import DerivedSourceError, _private_new_blob_readback
from v3_krx_history_collection import MARKETS, HistoryCollectionError, _snapshot_market
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError
from v3_krx_regular_close import ANNOUNCED_CLOSES, KST, regular_close_utc
from v3_krx_session_candidates import CONFIRMED_KRX_2026_CLOSURES
from v3_krx_private_daily import IngestError, _whole
from v3_score_observation_capture import ARCHIVE, SCHEMA as SCORE_SCHEMA, positive

OUT = Path('ops/forward-entry-status')
PRIVATE_REPO = 'ykvic1/korean-stock-private-data'
SCHEMA = 'v3-forward-opening-evidence-1'
ROW_STATES = ('unranked', 'absent', 'identity_unresolved', 'not_traded',
              'open_unavailable', 'official_open_observed')
MAX_COHORTS_PER_RUN = 5
SHA = re.compile(r"[a-f0-9]{64}\Z")
REFERENCE_STATES = ('reference_unavailable', 'signal_basis_unmatched', 'share_basis_unresolved',
                    'open_at_or_below_reference', 'intraday_reference_touched', 'reference_not_touched')


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
    market_responses = {}
    first_unavailable = None
    for market in MARKETS:
        try:
            rows, raw = client.daily_market(market, day)
        except KRXProbeError as error:
            if error.reason not in RETRYABLE_KRX_REASONS:
                raise
            market_responses[market] = {'status': 'unavailable', 'reason': error.reason,
                                        'http_status': error.http_status}
            if first_unavailable is None:
                first_unavailable = error
            continue
        stats = _snapshot_market(rows, raw, market=market, asof=day)
        hashes[market] = stats['source_sha256']
        market_responses[market] = {'status': 'validated', 'row_count': len(rows), 'http_status': 200}
        for row in rows:
            code = row['ISU_CD']
            if code in observed:
                raise EntryError('ambiguous_official_market_code')
            observed[code] = (market, row)
    if first_unavailable is not None:
        raise IncompleteOfficialPair(first_unavailable, market_responses) from None
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


def entry_stop_state(member, opening, entry_row, signal_row, signal_state):
    """Compare a frozen structural reference on the same observed price basis."""
    reference = member[7] if len(member) > 7 else None
    if not positive(reference):
        return 'reference_unavailable'
    if signal_state != 'matched' or signal_row is None or reference >= signal_row[6]:
        return 'signal_basis_unmatched'
    if signal_row[-1] is None or signal_row[-1] != entry_row[-1]:
        return 'share_basis_unresolved'
    if opening <= reference:
        return 'open_at_or_below_reference'
    if entry_row[5] <= reference:
        return 'intraday_reference_touched'
    return 'reference_not_touched'


def entry_session_path(observation, evidence, daily, *, signal=None, signal_cases=None):
    """Summarize one matched session from its official open, including missing entries."""
    groups = {}
    for label, rank_limit in (('top20', 20), ('top100', 100), ('all_ranked', None)):
        selected = [code for code, member in observation['rows'].items()
                    if member[3] is not None and (rank_limit is None or member[3] <= rank_limit)]
        low_returns, high_returns, close_returns = [], [], []
        missing = Counter()
        stops = Counter()
        for code in selected:
            state, opening = evidence['rows'][code]
            if state != 'official_open_observed':
                missing[state] += 1
                continue
            row = daily['rows'][code]
            if signal is not None:
                stops[entry_stop_state(observation['rows'][code], opening, row,
                                       signal['rows'].get(code), signal_cases[code]['state'])] += 1
            low_returns.append(100 * (row[5] / opening - 1))
            high_returns.append(100 * (row[4] / opening - 1))
            close_returns.append(100 * (row[6] / opening - 1))
        groups[label] = {
            'selected_count': len(selected), 'observed_count': len(close_returns),
            'missing_entry_counts': {state: missing[state] for state in ROW_STATES
                                     if state != 'official_open_observed'},
            'worst_low_return_pct': round(min(low_returns), 4) if low_returns else None,
            'median_low_return_pct': round(median(low_returns), 4) if low_returns else None,
            'median_high_return_pct': round(median(high_returns), 4) if high_returns else None,
            'median_close_return_pct': round(median(close_returns), 4) if close_returns else None,
            'negative_close_count': sum(value < 0 for value in close_returns),
        }
        if signal is not None:
            groups[label]['structural_reference_counts'] = {state: stops[state] for state in REFERENCE_STATES}
    return {
        'schema': 'v3-forward-entry-session-path-2', 'session': daily['date'],
        'basis': 'official_open_to_same_session_ohlc', 'groups': groups,
        'fees_and_taxes_included': False, 'performance_validated': False,
    }


def crosscheck_entry_daily(observation, source_digest, storage, *, review_reference=False):
    """Compare saved entry opens with their independently retained daily panel."""
    plan = entry_plan(observation, source_digest)
    path = f"derived/forward-entry/{plan['score_asof']}.json.gz"
    blob, _ = storage.get_content(path)
    if blob is None:
        raise EntryError('private_entry_missing_for_daily_crosscheck')
    evidence = json.loads(gzip.decompress(blob))
    entry = public_result(plan, evidence, observation['rows'])
    daily = read_daily(plan['entry_session_candidate'], storage)
    if daily is None:
        return {'status': 'waiting_for_daily_archive'}
    checked, mismatched = 0, 0
    for code, (state, price) in evidence['rows'].items():
        if state != 'official_open_observed':
            continue
        checked += 1
        member = observation['rows'][code]
        row = daily['rows'].get(code)
        if (row is None or row[0] != member[1]
                or normalized_name(row[1]) != normalized_name(member[0])
                or row[2] != 'traded' or row[3] != price):
            mismatched += 1
    same_source = entry['krx_market_source_sha256'] == daily['source_sha256']
    status = 'matched'
    if not same_source:
        status = 'source_mismatch'
    elif mismatched:
        status = 'opening_mismatch'
    elif not checked:
        status = 'no_observed_openings'
    result = {
        'status': status, 'observed_openings_checked': checked,
        'mismatched_openings': mismatched,
        'entry_source_sha256': entry['krx_market_source_sha256'],
        'daily_source_sha256': daily['source_sha256'],
        'corporate_actions_certified': False, 'performance_validated': False,
    }
    if status == 'matched':
        signal = None
        signal_cases = None
        if review_reference:
            signal = read_daily(plan['score_asof'], storage)
            market = frozen_market(observation) if signal is not None else None
            if market is None:
                result['structural_reference_status'] = 'waiting_for_signal_price_basis'
                signal = None
            else:
                _, signal_cases = compare_closes(observation, market, signal)
                result['structural_reference_status'] = 'compared'
                result['structural_reference_basis'] = {
                    'signal_published_market_sha256': observation['source_market_sha256'],
                    'signal_krx_source_sha256': signal['source_sha256'],
                    'corporate_actions_certified': False,
                    'stop_execution_validated': False,
                }
        result['entry_session_path'] = entry_session_path(
            observation, evidence, daily, signal=signal, signal_cases=signal_cases)
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
        # Cache retryable responses so both collectors share one attempt per session.
        try:
            return read_official_openings(official_client(), day), None
        except KRXProbeError as error:
            if error.reason not in RETRYABLE_KRX_REASONS:
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
                if error.reason not in RETRYABLE_KRX_REASONS:
                    raise EntryError('krx_' + error.reason) from None
                result = {**plan, **pending_official_pair(error, now)}
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
    def private_storage():
        return storage_and_key()[0]

    collect_daily(entry_statuses, now, private_storage, official_pair)
    collect_signal_closes(entry_statuses, now, private_storage)
    publish_regular_closes(private_storage)
    checked = 0
    for status in sorted(entry_statuses,
                         key=lambda item: item.get('last_entry_daily_crosscheck_at_utc', '')):
        if (status['status'] != 'official_openings_archived_privately'
                or status.get('entry_daily_crosscheck', {}).get('structural_reference_status') == 'compared'):
            continue
        if checked >= MAX_COHORTS_PER_RUN:
            break
        checked += 1
        source = ARCHIVE / f"{status['score_asof']}.json"
        raw = source.read_bytes()
        status['entry_daily_crosscheck'] = crosscheck_entry_daily(
            json.loads(raw), sha256(raw).hexdigest(), private_storage(), review_reference=True)
        status['last_entry_daily_crosscheck_at_utc'] = now.isoformat()
        target = OUT / source.name
        target.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(status, ensure_ascii=False))
    collect_share_reviews(now, private_storage)


if __name__ == '__main__':
    try:
        main()
    except (EntryError, DailyEvidenceError, ShareReviewError, SignalCloseError, RegularCloseError, HistoryCollectionError,
            PrivateHistoryError, DerivedSourceError) as error:
        raise SystemExit(str(error)) from None
