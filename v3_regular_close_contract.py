"""Lightweight source contract for official-close daily technical observations."""
from hashlib import sha256
import json
import math
from pathlib import Path

from v3_ma_slope_transition import WEIGHTS

SCHEMA = 'v3-official-regular-close-daily-indicators-1'
OUTPUT = 'v3-regular-close-indicators.json'
SESSIONS = 200


def implementation_digest():
    names = (__file__, str(Path(__file__).with_name('v3_regular_close_indicators.py')),
             str(Path(__file__).with_name('v3_ma_slope_transition.py')),
             str(Path(__file__).with_name('v3_pattern_first_observations.py')),
             str(Path(__file__).with_name('technical_v22.py')))
    return sha256(b''.join(Path(name).read_bytes() for name in names)).hexdigest()


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

