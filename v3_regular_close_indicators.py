"""Retain official close history privately and rebuild price-only factors."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import gzip
import json
import os
from pathlib import Path
import re
import time

import numpy as np

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import number, official_publish_time
from v3_forward_share_actions import normalized_name
from v3_krx_derived_5day import DerivedSourceError, _private_new_blob_readback
from v3_krx_history_collection import MARKETS, HistoryCollectionError, _snapshot_market
from v3_krx_money_forward import ForwardMoneyError, RetryingMoneyClient
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError
from v3_krx_regular_close import regular_close_utc
from v3_krx_session_candidates import candidate_dates
from v3_ma240_cross_research import observe as observe_ma240
from v3_ma_slope_transition import _series_points
from v3_pattern_first_observations import rsi_observation
from v3_regular_close_contract import (
    DAILY_SESSIONS, MAX_COMPLETED_WEEKS, OUTPUT, SCHEMA, SESSIONS,
    WEEKLY_MA_TURN_WEEKS, WEEKLY_POSITION_WEEKS, WEEKLY_RSI_WEEKS,
    admitted_rows, implementation_digest, last_completed_friday,
)

LEGACY_DAY_SCHEMA = 'v3-private-official-regular-close-day-1'
DAY_SCHEMA = 'v3-private-official-regular-close-day-2'
PROGRESS = Path('docs/v3-regular-close-history-progress.json')
PRIVATE_REPO = 'ykvic1/korean-stock-private-data'
MAX_NEW_SESSIONS = 80
MAX_BYTES = 2_000_000
SHA = re.compile(r'[a-f0-9]{64}\Z')


class CloseHistoryError(ValueError):
    """Static diagnostics suitable for public workflow logs."""


def history_dates(asof):
    end = date.fromisoformat(asof)
    friday = last_completed_friday(end)
    # One lead-in week keeps the oldest eligible week fully inside the window.
    start = friday - timedelta(weeks=MAX_COMPLETED_WEEKS, days=4)
    days = calendar_weekday_candidates(start.isoformat(), asof)
    selected = candidate_dates(days, source='xkrx')['dates']
    if len(selected) < SESSIONS or selected[-1] != asof:
        raise CloseHistoryError('invalid_regular_close_history_window')
    return selected


def archive_path(day, *, legacy=False):
    directory = 'official-regular-close-history' if legacy else 'official-regular-close-history-v2'
    return f'derived/{directory}/{day}.json.gz'


def read_day(blob, day, *, schema=DAY_SCHEMA):
    if blob is None:
        return None
    try:
        with gzip.GzipFile(fileobj=BytesIO(blob)) as stream:
            raw = stream.read(MAX_BYTES + 1)
        data = json.loads(raw)
        if (len(raw) > MAX_BYTES or data['schema'] != schema or data['date'] != day
                or set(data['source_sha256']) != set(MARKETS)
                or any(not SHA.fullmatch(value) for value in data['source_sha256'].values())
                or not 0 < len(data['rows']) <= 40_000):
            raise ValueError()
        for code, row in data['rows'].items():
            market, name, close, shares, reference = row[:5]
            if (not re.fullmatch(r'[0-9A-Z]{6}', code) or market not in MARKETS
                    or not normalized_name(name) or type(close) is not int or close <= 0
                    or type(shares) is not int or shares <= 0
                    or (reference is not None and (type(reference) is not int or reference <= 0))
                    or (schema == LEGACY_DAY_SCHEMA and len(row) != 5)
                    or (schema == DAY_SCHEMA and
                        (len(row) != 6 or (row[5] is not None and type(row[5]) is not bool)))):
                raise ValueError()
        if {row[0] for row in data['rows'].values()} != set(MARKETS):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, OSError, EOFError):
        raise CloseHistoryError('invalid_private_regular_close_day') from None


def observe_market(client, market, day):
    rows, raw = client.daily_market(market, day)
    stats = _snapshot_market(rows, raw, market=market, asof=day)
    found = {}
    for row in rows:
        close, shares = number(row, 'TDD_CLSPRC'), number(row, 'LIST_SHRS')
        name = row.get('ISU_NM')
        if not close or not shares or not normalized_name(name):
            continue
        change = str(row.get('CMPPREVDD_PRC', '')).strip().replace(',', '')
        reference = close - int(change) if re.fullmatch(r'[+-]?[0-9]+', change) else None
        reference = reference if reference is not None and reference > 0 else None
        volume = number(row, 'ACC_TRDVOL')
        # The price-only cross needs a verified traded status, not share volume.
        traded = volume > 0 if volume is not None else None
        found[row['ISU_CD']] = [market, name, close, shares, reference, traded]
    return found, stats['source_sha256']


def store_day(storage, day, data):
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise CloseHistoryError('oversized_regular_close_day')
    blob = gzip.compress(raw, mtime=0)
    read_day(blob, day)
    path = archive_path(day)
    for attempt in range(3):
        try:
            storage.put_new(path, blob, 'Archive official closing observations for ' + day)
            _private_new_blob_readback(storage, path, blob)
            return data
        except PrivateHistoryError:
            saved, _ = storage.get_content(path)
            recovered = read_day(saved, day)
            if recovered is not None:
                return recovered
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def collect(storage, clients, days, *, limit=MAX_NEW_SESSIONS):
    if not 1 <= limit <= MAX_NEW_SESSIONS:
        raise CloseHistoryError('invalid_regular_close_collection_budget')
    legacy_days = set(days[-SESSIONS:])

    def load(day):
        blob, _ = storage.get_content(archive_path(day))
        if blob is not None:
            return day, read_day(blob, day)
        if day not in legacy_days:
            return day, None
        legacy, _ = storage.get_content(archive_path(day, legacy=True))
        return day, read_day(legacy, day, schema=LEGACY_DAY_SCHEMA)

    with ThreadPoolExecutor(max_workers=6) as pool:
        panels = dict(pool.map(load, days))
    added, failures = 0, Counter()
    with ThreadPoolExecutor(max_workers=len(MARKETS)) as pool:
        for day in reversed(days):
            if panels[day] is not None and panels[day]['schema'] == DAY_SCHEMA:
                continue
            if added + sum(failures.values()) >= limit:
                break
            try:
                futures = [pool.submit(observe_market, clients[market], market, day) for market in MARKETS]
                wait(futures)
                pairs = [future.result() for future in futures]
            except KRXProbeError as error:
                if error.reason not in ('empty_body', 'empty_market_rows', 'transport_failure', 'http_server_error'):
                    raise
                failures[error.reason] += 1
                continue
            rows, hashes = {}, {}
            for market, (members, digest) in zip(MARKETS, pairs):
                if rows.keys() & members.keys():
                    raise CloseHistoryError('duplicate_regular_close_market_code')
                rows.update(members)
                hashes[market] = digest
            data = {'schema': DAY_SCHEMA, 'date': day, 'source_sha256': hashes, 'rows': rows}
            panels[day] = store_day(storage, day, data)
            added += 1
            print(json.dumps({'archived_session': day, 'added_this_run': added}))
    return panels, added, dict(failures)


def continuous_tail(records, member):
    """Use observed same-basis closes after the latest unresolved discontinuity."""
    tail = []
    next_row = None
    for row in reversed(records):
        if (row is None or row[0] != member['market']
                or normalized_name(row[1]) != normalized_name(member['name'])):
            break
        if next_row is not None and (next_row[4] != row[2]
                or abs(next_row[2] / row[2] - 1) > .5):
            break
        tail.append(row)
        next_row = row
    return tail[::-1]


def completed_week_ranges(session_days):
    friday = last_completed_friday(session_days[-1])
    weekly_ranges = {}
    for index, day in enumerate(session_days):
        week_end = day + timedelta(days=(4 - day.weekday()) % 7)
        if week_end > friday:
            break
        start, _ = weekly_ranges.get(week_end, (index, index))
        weekly_ranges[week_end] = (start, index + 1)
    # The first archived trading day may start in the middle of a week.
    return list(weekly_ranges.items())[1:][-MAX_COMPLETED_WEEKS:]


def weekly_indicators(records, member, weeks, friday):
    if len(weeks) < WEEKLY_MA_TURN_WEEKS or weeks[-1][0] != friday:
        return None
    tail_end = weeks[-1][1][1]
    tail_start = tail_end - len(continuous_tail(records[:tail_end], member))
    closes = []
    expected = friday
    for week_end, (start, end) in reversed(weeks):
        if week_end != expected or start < tail_start:
            break
        bars = records[start:end]
        if any(len(row) < 6 or row[5] is None for row in bars) or not any(row[5] for row in bars):
            break
        if any(row[5] is False and index > tail_start and row[2] != records[index - 1][2]
               for index, row in enumerate(bars, start)):
            break
        closes.append(bars[-1][2])
        expected -= timedelta(days=7)
    if len(closes) < WEEKLY_MA_TURN_WEEKS:
        return None
    values = np.asarray(closes[::-1], dtype=float)
    n = len(values)
    turn = _series_points(values, prior=26, recent=8,
                          hold=3, floor=.0005, frame='weekly', trusted_window=lambda _: True)
    rsi = rsi_observation(values) if n >= WEEKLY_RSI_WEEKS else None
    gap = (round(float(100 * (values[-1] / values[-WEEKLY_POSITION_WEEKS:].mean() - 1)), 4)
           if n >= WEEKLY_POSITION_WEEKS else None)
    return {'asof': friday.isoformat(), 'continuous_weeks': n, 'ma_turn': turn,
            'rsi': rsi, 'weekly_ma120_gap_pct': gap}


def build(catalog, catalog_digest, days, panels):
    rows, counts = {}, Counter()
    trade_status_sessions = sum(panel is not None and panel['schema'] == DAY_SCHEMA
                          for panel in panels.values())
    daily_trade_status_sessions = sum(panels[day] is not None and panels[day]['schema'] == DAY_SCHEMA
                                      for day in days[-SESSIONS:])
    session_days = [date.fromisoformat(day) for day in days]
    friday = last_completed_friday(session_days[-1])
    weeks = completed_week_ranges(session_days)
    archived_weeks = sum(all(panels[day] is not None and panels[day]['schema'] == DAY_SCHEMA
                             for day in days[start:end]) for _, (start, end) in weeks)
    for member in catalog['rows']:
        code = member['code']
        records = [panels[day]['rows'].get(code) if panels[day] else None for day in days]
        tail = continuous_tail(records[-SESSIONS:], member)
        if not tail:
            counts['no_current_identity_matched_close'] += 1
            continue
        closes = [row[2] for row in tail[-DAILY_SESSIONS:]]
        n = len(tail)
        ma = {str(period): round(float(np.mean(closes[-period:])), 6) if n >= period else None
              for period in (20, 60, 120)}
        rsi = rsi_observation(np.asarray(closes, dtype=float)) if n >= 90 else None
        turn = _series_points(np.asarray(closes, dtype=float), prior=60, recent=20,
                              hold=5, floor=.0001, frame='daily',
                              trusted_window=lambda required: len(closes) >= required)
        cross = None
        if daily_trade_status_sessions == SESSIONS and n == SESSIONS:
            trade_markers = [int(row[5]) if row[5] is not None else None for row in tail]
            cross = observe_ma240([row[2] for row in tail], trade_markers,
                                  asof=catalog['date'], session_days=session_days[-SESSIONS:])
        weekly = weekly_indicators(records, member, weeks, friday)
        rows[code] = {'name': member['name'], 'market': member['market'],
                      'continuous_sessions': n, 'moving_averages': ma, 'daily_rsi': rsi,
                      'daily_ma_turn': turn, 'daily_ma240_cross': cross, 'weekly': weekly}
        counts['current_close_observed'] += 1
        counts['full_200_session_continuity'] += n >= DAILY_SESSIONS
        counts['full_260_session_continuity'] += n == SESSIONS
        counts['daily_rsi_recomputed'] += rsi is not None
        counts['daily_ma_turn_recomputed'] += turn is not None
        counts['daily_ma240_cross_recomputed'] += cross is not None
        counts['daily_ma240_cross_qualified'] += cross is not None and cross['qualified']
        for period in ('20', '60', '120'):
            counts[f'weekly_ma{period}_turn_recomputed'] += weekly is not None and weekly['ma_turn'][period] is not None
        counts['weekly_rsi_recomputed'] += weekly is not None and weekly['rsi'] is not None
        counts['weekly_ma120_position_recomputed'] += weekly is not None and weekly['weekly_ma120_gap_pct'] is not None
    inputs = {day: panel['source_sha256'] for day, panel in panels.items() if panel is not None}
    return {'schema': SCHEMA, 'asof': catalog['date'], 'source': 'KRX_OFFICIAL_REGULAR_CLOSE',
            'source_catalog_sha256': catalog_digest, 'implementation_sha256': implementation_digest(),
            'requested_sessions': len(days), 'archived_sessions': len(inputs),
            'trade_status_sessions': trade_status_sessions,
            'daily_trade_status_sessions': daily_trade_status_sessions,
            'requested_completed_weeks': len(weeks),
            'archived_completed_weeks': archived_weeks,
            'session_calendar_source': 'XKRX_WITH_CONFIRMED_CLOSURE_OVERRIDES',
            'official_session_calendar_authenticated': False,
            'source_inputs_sha256': sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
            'listed_total': len(catalog['rows']), 'counts': dict(counts),
            'full_corporate_action_history_certified': False, 'performance_validated': False,
            'rows': rows}


def main():
    root = Path('docs')
    catalog = json.loads((root / 'v22-market-catalog.json').read_bytes())
    asof = catalog['date']
    now = datetime.now(timezone.utc)
    source_day = date.fromisoformat(asof)
    if regular_close_utc(source_day) > now:
        raise CloseHistoryError('regular_close_history_asof_not_complete')
    publish_at = official_publish_time(source_day)
    if now < publish_at:
        print(json.dumps({'status': 'waiting_for_official_publish_window',
                          'asof': asof,
                          'official_publish_candidate_utc': publish_at.isoformat(),
                          'source_requests': 0, 'published_indicators_changed': False}))
        return
    catalog_digest = sha256(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    target = root / OUTPUT
    prior = json.loads(target.read_bytes()) if target.exists() else {}
    days = history_dates(asof)
    if (prior.get('schema') == SCHEMA and prior.get('asof') == asof
            and prior.get('source_catalog_sha256') == catalog_digest
            and prior.get('implementation_sha256') == implementation_digest()
            and prior.get('archived_sessions') == prior.get('trade_status_sessions') == len(days)):
        print('regular_close_history_already_current')
        return
    if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
        raise CloseHistoryError('private_regular_close_destination_unavailable')
    storage = PrivateGitStorage(PRIVATE_REPO, os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', ''))
    storage.verify_private()
    clients = {market: RetryingMoneyClient(KRXClient(os.environ['KRX_API_KEY'])) for market in MARKETS}
    panels, added, failures = collect(storage, clients, days)
    report = build(catalog, catalog_digest, days, panels)
    target.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    progress = {key: report[key] for key in ('asof', 'requested_sessions', 'archived_sessions', 'trade_status_sessions',
                                            'daily_trade_status_sessions', 'requested_completed_weeks',
                                            'archived_completed_weeks',
                                            'listed_total', 'counts', 'source_inputs_sha256')}
    progress.update(added_this_run=added, pending_sessions=len(days)-report['trade_status_sessions'],
                    source_requests=sum(client.requests for client in clients.values()), failures=failures)
    PROGRESS.write_text(json.dumps(progress, indent=2) + '\n')
    print(json.dumps(progress))


if __name__ == '__main__':
    try:
        main()
    except (CloseHistoryError, PrivateHistoryError, KRXProbeError, HistoryCollectionError,
            DerivedSourceError, ForwardMoneyError) as error:
        reason = error.reason if isinstance(error, KRXProbeError) else str(error)
        raise SystemExit(reason) from None
