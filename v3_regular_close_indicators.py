"""Retain official close history privately and rebuild price-only daily factors."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import gzip
import json
import math
import os
from pathlib import Path
import re
import time

import numpy as np

from official_probe.clients import KRXClient, KRXProbeError
from v3_forward_daily_prices import number
from v3_forward_share_actions import normalized_name
from v3_krx_derived_5day import _private_new_blob_readback
from v3_krx_derived_5day import DerivedSourceError
from v3_krx_history_collection import MARKETS, HistoryCollectionError, _snapshot_market
from v3_krx_money_forward import ForwardMoneyError, RetryingMoneyClient
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError
from v3_krx_regular_close import regular_close_utc
from v3_krx_session_candidates import candidate_dates
from v3_ma_slope_transition import WEIGHTS, _series_points
from v3_pattern_first_observations import rsi_observation

SCHEMA = 'v3-official-regular-close-daily-indicators-1'
DAY_SCHEMA = 'v3-private-official-regular-close-day-1'
OUTPUT = 'v3-regular-close-indicators.json'
PROGRESS = Path('docs/v3-regular-close-history-progress.json')
PRIVATE_REPO = 'ykvic1/korean-stock-private-data'
SESSIONS = 200
MAX_NEW_SESSIONS = 80
MAX_BYTES = 2_000_000
SHA = re.compile(r'[a-f0-9]{64}\Z')


class CloseHistoryError(ValueError):
    """Static diagnostics suitable for public workflow logs."""


def implementation_digest():
    names = (__file__, str(Path(__file__).with_name('v3_ma_slope_transition.py')),
             str(Path(__file__).with_name('v3_pattern_first_observations.py')),
             str(Path(__file__).with_name('technical_v22.py')))
    return sha256(b''.join(Path(name).read_bytes() for name in names)).hexdigest()


def history_dates(asof):
    end = date.fromisoformat(asof)
    days = calendar_weekday_candidates((end - timedelta(days=400)).isoformat(), asof)
    selected = candidate_dates(days, source='xkrx')['dates'][-SESSIONS:]
    if len(selected) != SESSIONS or selected[-1] != asof:
        raise CloseHistoryError('invalid_regular_close_history_window')
    return selected


def archive_path(day):
    return f'derived/official-regular-close-history/{day}.json.gz'


def read_day(blob, day):
    if blob is None:
        return None
    try:
        with gzip.GzipFile(fileobj=BytesIO(blob)) as stream:
            raw = stream.read(MAX_BYTES + 1)
        data = json.loads(raw)
        if (len(raw) > MAX_BYTES or data['schema'] != DAY_SCHEMA or data['date'] != day
                or set(data['source_sha256']) != set(MARKETS)
                or any(not SHA.fullmatch(value) for value in data['source_sha256'].values())
                or not 0 < len(data['rows']) <= 40_000):
            raise ValueError()
        for code, row in data['rows'].items():
            market, name, close, shares, reference = row
            if (not re.fullmatch(r'[0-9A-Z]{6}', code) or market not in MARKETS
                    or not normalized_name(name) or type(close) is not int or close <= 0
                    or type(shares) is not int or shares <= 0
                    or (reference is not None and (type(reference) is not int or reference <= 0))):
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
        found[row['ISU_CD']] = [market, name, close, shares, reference]
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

    def load(day):
        blob, _ = storage.get_content(archive_path(day))
        return day, read_day(blob, day)

    with ThreadPoolExecutor(max_workers=6) as pool:
        panels = dict(pool.map(load, days))
    added, failures = 0, Counter()
    with ThreadPoolExecutor(max_workers=len(MARKETS)) as pool:
        for day in reversed(days):
            if panels[day] is not None:
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
        tail.append(row[2])
        next_row = row
    return tail[::-1]


def build(catalog, catalog_digest, days, panels):
    rows, counts = {}, Counter()
    for member in catalog['rows']:
        code = member['code']
        records = [panels[day]['rows'].get(code) if panels[day] else None for day in days]
        closes = continuous_tail(records, member)
        if not closes:
            counts['no_current_identity_matched_close'] += 1
            continue
        n = len(closes)
        ma = {str(period): round(float(np.mean(closes[-period:])), 6) if n >= period else None
              for period in (20, 60, 120)}
        rsi = rsi_observation(np.asarray(closes, dtype=float)) if n >= 90 else None
        turn = _series_points(np.asarray(closes, dtype=float), prior=60, recent=20,
                              hold=5, floor=.0001, frame='daily',
                              trusted_window=lambda required: n >= required)
        rows[code] = {'name': member['name'], 'market': member['market'],
                      'continuous_sessions': n, 'moving_averages': ma, 'daily_rsi': rsi,
                      'daily_ma_turn': turn}
        counts['current_close_observed'] += 1
        counts['full_200_session_continuity'] += n == SESSIONS
        counts['daily_rsi_recomputed'] += rsi is not None
        counts['daily_ma_turn_recomputed'] += turn is not None
    inputs = {day: panel['source_sha256'] for day, panel in panels.items() if panel is not None}
    return {'schema': SCHEMA, 'asof': catalog['date'], 'source': 'KRX_OFFICIAL_REGULAR_CLOSE',
            'source_catalog_sha256': catalog_digest, 'implementation_sha256': implementation_digest(),
            'requested_sessions': len(days), 'archived_sessions': len(inputs),
            'session_calendar_source': 'XKRX_WITH_CONFIRMED_CLOSURE_OVERRIDES',
            'official_session_calendar_authenticated': False,
            'source_inputs_sha256': sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
            'listed_total': len(catalog['rows']), 'counts': dict(counts),
            'full_corporate_action_history_certified': False, 'performance_validated': False,
            'rows': rows}


def admitted_rows(source, catalog):
    if (not isinstance(source, dict) or source.get('schema') != SCHEMA or source.get('asof') != catalog['date']
            or source.get('source') != 'KRX_OFFICIAL_REGULAR_CLOSE'
            or source.get('source_catalog_sha256') != sha256(
                json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            or source.get('listed_total') != len(catalog['rows'])
            or source.get('requested_sessions') != SESSIONS
            or type(source.get('archived_sessions')) is not int
            or not 0 <= source['archived_sessions'] <= SESSIONS
            or source.get('implementation_sha256') != implementation_digest()
            or not isinstance(source.get('rows'), dict)
            or source.get('full_corporate_action_history_certified') is not False
            or source.get('performance_validated') is not False):
        return {}
    admitted = {}
    for member in catalog['rows']:
        row = source.get('rows', {}).get(member['code'])
        if not isinstance(row, dict) or any(row.get(key) != member[key] for key in ('name', 'market')):
            continue
        n = row.get('continuous_sessions')
        rsi, turn = row.get('daily_rsi'), row.get('daily_ma_turn')
        if type(n) is not int or not 1 <= n <= SESSIONS:
            continue
        if rsi is not None and (not isinstance(rsi, dict) or n < 90 or type(rsi.get('rsi14')) not in (int, float)
                or not math.isfinite(rsi['rsi14']) or not 0 <= rsi['rsi14'] <= 100
                or any(type(rsi.get(key)) is not bool for key in
                       ('rsi30_cross_recent', 'rsi_bullish_divergence_recent'))):
            continue
        if turn is not None and (not isinstance(turn, dict) or set(turn) != set(WEIGHTS['daily'])
                or not any(value is not None for value in turn.values())
                or any(value is not None and (n < int(period) + 80
                    or type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= WEIGHTS['daily'][period]) for period, value in turn.items())):
            continue
        admitted[member['code']] = row
    return admitted


def main():
    root = Path('docs')
    catalog = json.loads((root / 'v22-market-catalog.json').read_bytes())
    asof = catalog['date']
    if regular_close_utc(date.fromisoformat(asof)) > datetime.now(timezone.utc):
        raise CloseHistoryError('regular_close_history_asof_not_complete')
    catalog_digest = sha256(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    target = root / OUTPUT
    prior = json.loads(target.read_bytes()) if target.exists() else {}
    if (prior.get('schema') == SCHEMA and prior.get('asof') == asof
            and prior.get('source_catalog_sha256') == catalog_digest
            and prior.get('implementation_sha256') == implementation_digest()
            and prior.get('archived_sessions') == SESSIONS):
        print('regular_close_history_already_current')
        return
    if os.getenv('V3_PRIVATE_ARCHIVE_REPO') != PRIVATE_REPO:
        raise CloseHistoryError('private_regular_close_destination_unavailable')
    storage = PrivateGitStorage(PRIVATE_REPO, os.getenv('V3_PRIVATE_ARCHIVE_TOKEN', ''))
    storage.verify_private()
    clients = {market: RetryingMoneyClient(KRXClient(os.environ['KRX_API_KEY'])) for market in MARKETS}
    days = history_dates(asof)
    panels, added, failures = collect(storage, clients, days)
    report = build(catalog, catalog_digest, days, panels)
    target.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    progress = {key: report[key] for key in ('asof', 'requested_sessions', 'archived_sessions',
                                            'listed_total', 'counts', 'source_inputs_sha256')}
    progress.update(added_this_run=added, pending_sessions=SESSIONS-report['archived_sessions'],
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
