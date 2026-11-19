"""Lightweight source contract for official-close technical observations."""
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path

from v3_ma_slope_transition import WEIGHTS
from v3_ma240_cross_research import MIN_HISTORY_SESSIONS, valid_observation as valid_ma240

LEGACY_SCHEMA = 'v3-official-regular-close-daily-indicators-1'
LEGACY_IMPLEMENTATION = '39208d82ec6498d4d4b3fae06aba9d2b052492239dc15a38673ae0bf082f9187'
DAILY_ONLY_SCHEMA = 'v3-official-regular-close-daily-indicators-2'
DAILY_ONLY_IMPLEMENTATION = '6f0982244f019573273212febc941e4c9c47d5d52f1fb3f473038c8dcfe1c205'
SHORT_WEEKLY_SCHEMA = 'v3-official-regular-close-indicators-3'
SHORT_WEEKLY_IMPLEMENTATION = 'c35327dfc89be5c460180d0b215257245a1882e3298da3df42bb92407b02ada6'
LONG_WEEKLY_SCHEMA = 'v3-official-regular-close-indicators-4'
LONG_WEEKLY_IMPLEMENTATION = 'b118ce9d6219894da0e6f73729419a377408961269bbf5ee89556efacb051548'
NO_TRADE_WEEK_SCHEMA = 'v3-official-regular-close-indicators-5'
NO_TRADE_WEEK_IMPLEMENTATION = 'af347512102b20846daa9e287cd35c72fc14018b730b6cf8e7abbe1cbbf53c5a'
SCHEMA = 'v3-official-regular-close-indicators-6'
OUTPUT = 'v3-regular-close-indicators.json'
DAILY_SESSIONS = 200
SESSIONS = MIN_HISTORY_SESSIONS
WEEKLY_MA_WINDOWS = {period: int(period) + 26 + 8 for period in WEIGHTS['weekly']}
WEEKLY_MA_TURN_WEEKS = WEEKLY_MA_WINDOWS['20']
WEEKLY_RSI_WEEKS = 90
WEEKLY_POSITION_WEEKS = 120
MAX_COMPLETED_WEEKS = max(WEEKLY_MA_WINDOWS.values())
MAX_HISTORY_SESSIONS = (MAX_COMPLETED_WEEKS + 1) * 5 + 4


def last_completed_friday(day):
    return day - timedelta(days=(day.weekday() - 4) % 7)


def implementation_digest():
    names = (__file__, str(Path(__file__).with_name('v3_regular_close_indicators.py')),
             str(Path(__file__).with_name('v3_ma_slope_transition.py')),
             str(Path(__file__).with_name('v3_ma240_cross_research.py')),
             str(Path(__file__).with_name('v3_pattern_first_observations.py')),
             str(Path(__file__).with_name('technical_v22.py')),
             str(Path(__file__).with_name('v3_krx_close_momentum_recovery.py')),
             str(Path(__file__).with_name('v3_krx_face_consolidation_notices.json')))
    return sha256(b''.join(Path(name).read_bytes() for name in names)).hexdigest()


def valid_rsi(rsi):
    return (isinstance(rsi, dict) and type(rsi.get('rsi14')) in (int, float)
            and math.isfinite(rsi['rsi14']) and 0 <= rsi['rsi14'] <= 100
            and all(type(rsi.get(key)) is bool for key in
                    ('rsi30_cross_recent', 'rsi_bullish_divergence_recent')))


def valid_turn(turn, window, minimum, weights):
    return (isinstance(turn, dict) and set(turn) == set(weights)
            and any(value is not None for value in turn.values())
            and all(value is None or (window >= minimum[period]
                    and type(value) in (int, float) and math.isfinite(value)
                    and 0 <= value <= weights[period]) for period, value in turn.items()))


def valid_weekly(weekly, friday, trade_status_sessions):
    if (not isinstance(weekly, dict) or weekly.get('asof') != friday
            or type(weekly.get('continuous_weeks')) is not int):
        return False
    n = weekly['continuous_weeks']
    turn, rsi, gap = weekly.get('ma_turn'), weekly.get('rsi'), weekly.get('weekly_ma120_gap_pct')
    return (WEEKLY_MA_TURN_WEEKS <= n <= min(trade_status_sessions, MAX_COMPLETED_WEEKS)
            and valid_turn(turn, n, WEEKLY_MA_WINDOWS, WEIGHTS['weekly'])
            and (rsi is None or (n >= WEEKLY_RSI_WEEKS and valid_rsi(rsi)))
            and (gap is None or (n >= WEEKLY_POSITION_WEEKS and type(gap) in (int, float)
                                 and math.isfinite(gap) and gap > -100)))


def admitted_rows(source, catalog):
    if not isinstance(source, dict):
        return {}
    schema = source.get('schema')
    legacy = schema == LEGACY_SCHEMA
    limit = DAILY_SESSIONS if legacy else SESSIONS
    previous_digests = {LEGACY_SCHEMA: LEGACY_IMPLEMENTATION, DAILY_ONLY_SCHEMA: DAILY_ONLY_IMPLEMENTATION,
                        SHORT_WEEKLY_SCHEMA: SHORT_WEEKLY_IMPLEMENTATION,
                        LONG_WEEKLY_SCHEMA: LONG_WEEKLY_IMPLEMENTATION,
                        NO_TRADE_WEEK_SCHEMA: NO_TRADE_WEEK_IMPLEMENTATION}
    if not isinstance(schema, str) or schema not in (*previous_digests, SCHEMA):
        return {}
    digest = implementation_digest() if schema == SCHEMA else previous_digests[schema]
    long_weekly = schema in (LONG_WEEKLY_SCHEMA, NO_TRADE_WEEK_SCHEMA, SCHEMA)
    requested = source.get('requested_sessions')
    if (source.get('asof') != catalog['date']
            or source.get('source') != 'KRX_OFFICIAL_REGULAR_CLOSE'
            or source.get('source_catalog_sha256') != sha256(
                json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            or source.get('listed_total') != len(catalog['rows'])
            or type(requested) is not int
            or not (SESSIONS <= requested <= MAX_HISTORY_SESSIONS if long_weekly else requested == limit)
            or type(source.get('archived_sessions')) is not int
            or not 0 <= source['archived_sessions'] <= requested
            or source.get('implementation_sha256') != digest
            or not isinstance(source.get('rows'), dict)
            or source.get('full_corporate_action_history_certified') is not False
            or source.get('performance_validated') is not False):
        return {}
    trade_status_sessions = source.get('trade_status_sessions', 0) if not legacy else 0
    if type(trade_status_sessions) is not int or not 0 <= trade_status_sessions <= source['archived_sessions']:
        return {}
    daily_trade_status_sessions = source.get('daily_trade_status_sessions', trade_status_sessions)
    if type(daily_trade_status_sessions) is not int or not 0 <= daily_trade_status_sessions <= min(
            trade_status_sessions, SESSIONS):
        return {}
    friday = last_completed_friday(date.fromisoformat(catalog['date'])).isoformat()
    admitted = {}
    for member in catalog['rows']:
        row = source.get('rows', {}).get(member['code'])
        if not isinstance(row, dict) or any(row.get(key) != member[key] for key in ('name', 'market')):
            continue
        n = row.get('continuous_sessions')
        rsi, turn = row.get('daily_rsi'), row.get('daily_ma_turn')
        if type(n) is not int or not 1 <= n <= min(source['archived_sessions'], limit):
            continue
        if rsi is not None and (n < 90 or not valid_rsi(rsi)):
            continue
        if turn is not None and not valid_turn(turn, n, {p: int(p) + 80 for p in WEIGHTS['daily']}, WEIGHTS['daily']):
            continue
        cross = row.get('daily_ma240_cross')
        if cross is not None and (legacy or daily_trade_status_sessions != SESSIONS or n < SESSIONS
                                  or not valid_ma240(cross, catalog['date'])):
            continue
        weekly = row.get('weekly_ma20_turn')
        if weekly is not None and (schema != SHORT_WEEKLY_SCHEMA or not isinstance(weekly, dict)
                or weekly.get('asof') != friday
                or type(weekly.get('continuous_weeks')) is not int
                or not WEEKLY_MA_TURN_WEEKS <= weekly['continuous_weeks'] <= min(
                    trade_status_sessions, 57)
                or type(weekly.get('points')) not in (int, float)
                or not math.isfinite(weekly['points'])
                or not 0 <= weekly['points'] <= WEIGHTS['weekly']['20']):
            continue
        if weekly is not None:
            row = {**row, 'weekly': {'asof': friday, 'continuous_weeks': weekly['continuous_weeks'],
                                    'ma_turn': {'20': weekly['points'], '60': None, '120': None},
                                    'rsi': None, 'weekly_ma120_gap_pct': None}}
        elif row.get('weekly') is not None and (not long_weekly or not valid_weekly(
                row['weekly'], friday, trade_status_sessions)):
            continue
        admitted[member['code']] = row
    return admitted

