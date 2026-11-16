"""Lightweight source contract for official-close daily technical observations."""
from hashlib import sha256
import json
import math
from pathlib import Path

from v3_ma_slope_transition import WEIGHTS
from v3_ma240_cross_research import MIN_HISTORY_SESSIONS, valid_observation as valid_ma240

LEGACY_SCHEMA = 'v3-official-regular-close-daily-indicators-1'
LEGACY_IMPLEMENTATION = '39208d82ec6498d4d4b3fae06aba9d2b052492239dc15a38673ae0bf082f9187'
SCHEMA = 'v3-official-regular-close-daily-indicators-2'
OUTPUT = 'v3-regular-close-indicators.json'
DAILY_SESSIONS = 200
SESSIONS = MIN_HISTORY_SESSIONS


def implementation_digest():
    names = (__file__, str(Path(__file__).with_name('v3_regular_close_indicators.py')),
             str(Path(__file__).with_name('v3_ma_slope_transition.py')),
             str(Path(__file__).with_name('v3_ma240_cross_research.py')),
             str(Path(__file__).with_name('v3_pattern_first_observations.py')),
             str(Path(__file__).with_name('technical_v22.py')))
    return sha256(b''.join(Path(name).read_bytes() for name in names)).hexdigest()


def admitted_rows(source, catalog):
    if not isinstance(source, dict):
        return {}
    legacy = source.get('schema') == LEGACY_SCHEMA
    limit = DAILY_SESSIONS if legacy else SESSIONS
    digest = LEGACY_IMPLEMENTATION if legacy else implementation_digest()
    if (source.get('schema') not in (LEGACY_SCHEMA, SCHEMA) or source.get('asof') != catalog['date']
            or source.get('source') != 'KRX_OFFICIAL_REGULAR_CLOSE'
            or source.get('source_catalog_sha256') != sha256(
                json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            or source.get('listed_total') != len(catalog['rows'])
            or source.get('requested_sessions') != limit
            or type(source.get('archived_sessions')) is not int
            or not 0 <= source['archived_sessions'] <= limit
            or source.get('implementation_sha256') != digest
            or not isinstance(source.get('rows'), dict)
            or source.get('full_corporate_action_history_certified') is not False
            or source.get('performance_validated') is not False):
        return {}
    trade_status_sessions = source.get('trade_status_sessions', 0) if not legacy else 0
    if type(trade_status_sessions) is not int or not 0 <= trade_status_sessions <= source['archived_sessions']:
        return {}
    admitted = {}
    for member in catalog['rows']:
        row = source.get('rows', {}).get(member['code'])
        if not isinstance(row, dict) or any(row.get(key) != member[key] for key in ('name', 'market')):
            continue
        n = row.get('continuous_sessions')
        rsi, turn = row.get('daily_rsi'), row.get('daily_ma_turn')
        if type(n) is not int or not 1 <= n <= source['archived_sessions']:
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
        cross = row.get('daily_ma240_cross')
        if cross is not None and (legacy or trade_status_sessions != SESSIONS or n < SESSIONS
                                  or not valid_ma240(cross, catalog['date'])):
            continue
        admitted[member['code']] = row
    return admitted

