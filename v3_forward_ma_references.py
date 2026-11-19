"""Measure entry-day contact with official 20/60-session closing averages."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

from v3_forward_daily_prices import completed_sessions
from v3_forward_share_actions import (
    HOLDER_NEUTRAL_ACTIONS, ShareReviewError, normalized_name, share_transition,
)
from v3_historical_market_share_actions import CONTINUITY_REGISTER, continuity_actions
from v3_regular_close_indicators import archive_path, read_day

SCHEMA = 'v3-forward-official-ma-reference-2'
PERIODS = (20, 60)
STATES = (
    'signal_trade_unavailable', 'signal_basis_unresolved', 'reviewed_entry_action',
    'history_session_missing', 'issuer_history_missing', 'issuer_identity_changed',
    'share_count_changed', 'reviewed_share_basis_unresolved', 'price_basis_discontinuous',
    'trade_state_unavailable',
    'average_at_or_above_signal_close', 'open_at_or_below_average',
    'intraday_average_touched', 'average_not_touched',
)


def load_ma_notices(forward, root=Path('.')):
    """Import reviewed historical events with a confirmed one-for-one holding."""
    try:
        historical = continuity_actions((root / CONTINUITY_REGISTER).read_bytes())
    except (OSError, KeyError, TypeError, ValueError):
        raise ShareReviewError('invalid_historical_ma_action_register') from None
    result = dict(forward)
    for key, notice in historical.items():
        if notice['action'] not in HOLDER_NEUTRAL_ACTIONS or 'resumed_on' in notice:
            continue
        accepted = {**notice, 'name': notice['name_after']}
        original = result.get(key)
        if original is not None:
            if any(original[field] != accepted[field] for field in (
                    'action', 'market', 'shares_before', 'shares_after', 'new_units', 'old_units',
            )) or normalized_name(original['name']) != normalized_name(accepted['name']):
                raise ShareReviewError('conflicting_ma_share_action')
        else:
            result[key] = accepted
    return result


def notice_digest(notices, start, end):
    window = [(session, code, row) for (session, code), row in sorted(notices.items())
              if start <= session <= end]
    return sha256(json.dumps(window, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def neutral_transition(previous, current, notice):
    return (notice is not None and notice['action'] in HOLDER_NEUTRAL_ACTIONS
            and notice['new_units'] == notice['old_units'] == 1
            and share_transition(previous, current, notice) == 'notice_matched')


def load_history(day, entry_day, now, storage, cache):
    end = date.fromisoformat(day)
    days = list(completed_sessions(end - timedelta(days=130), end, now))[-max(PERIODS):]

    def load(session):
        blob, _ = storage.get_content(archive_path(session))
        return read_day(blob, session)

    pending = [session for session in [*days, entry_day] if session not in cache]
    if pending:
        with ThreadPoolExecutor(max_workers=6) as pool:
            cache.update(zip(pending, pool.map(load, pending)))
    return [(session, cache[session]) for session in days], cache[entry_day]


def continuous_closes(code, member, history, signal_row, notices):
    market, name = member[1], normalized_name(member[0])
    closes = []
    first_reviewed_length = None
    newer = newer_day = None
    for day, panel in reversed(history):
        if panel is None:
            return closes, first_reviewed_length, 'history_session_missing'
        row = panel['rows'].get(code)
        if row is None:
            return closes, first_reviewed_length, 'issuer_history_missing'
        if row[0] != market or normalized_name(row[1]) != name:
            return closes, first_reviewed_length, 'issuer_identity_changed'
        close, shares, traded = row[2], row[3], row[5]
        if traded is None:
            return closes, first_reviewed_length, 'trade_state_unavailable'
        if newer is None:
            if not traded or close != signal_row[6] or shares != signal_row[-1]:
                return closes, first_reviewed_length, 'signal_basis_unresolved'
        else:
            notice = notices.get((newer_day, code))
            if shares != newer[3] or notice is not None:
                if not neutral_transition((market, row[1], shares), (market, newer[1], newer[3]), notice):
                    reason = 'share_count_changed' if notice is None else 'reviewed_share_basis_unresolved'
                    return closes, first_reviewed_length, reason
                if first_reviewed_length is None:
                    first_reviewed_length = len(closes) + 1
            if newer[4] != close or (not newer[5] and newer[2] != close):
                return closes, first_reviewed_length, 'price_basis_discontinuous'
        closes.append(close)
        newer, newer_day = row, day
    return closes, first_reviewed_length, 'history_session_missing'


def contact_states(code, member, opening, signal, entry, history, entry_basis, notices):
    signal_row = signal['rows'].get(code)
    if signal_row is None or signal_row[2] != 'traded':
        return [('signal_trade_unavailable', False)] * len(PERIODS)
    entry_row = entry['rows'][code]
    if (signal_row[0] != member[1] or normalized_name(signal_row[1]) != normalized_name(member[0])
            or signal_row[-1] is None or entry_row[-1] is None):
        return [('signal_basis_unresolved', False)] * len(PERIODS)
    entry_notice = notices.get((entry['date'], code))
    entry_reviewed = neutral_transition(signal_row, entry_row, entry_notice)
    if entry_notice is not None and not entry_reviewed:
        return [('reviewed_entry_action', False)] * len(PERIODS)
    if signal_row[-1] != entry_row[-1] and not entry_reviewed:
        return [('signal_basis_unresolved', False)] * len(PERIODS)
    basis_row = entry_basis['rows'].get(code)
    if (basis_row is None or basis_row[0] != member[1]
            or normalized_name(basis_row[1]) != normalized_name(member[0])
            or basis_row[3] != entry_row[-1] or basis_row[4] != signal_row[6]
            or basis_row[2] != entry_row[6] or basis_row[5] is not True):
        return [('price_basis_discontinuous', False)] * len(PERIODS)
    closes, first_reviewed_length, reason = continuous_closes(code, member, history, signal_row, notices)
    outcomes = []
    for period in PERIODS:
        if len(closes) < period:
            state = reason
        else:
            total = sum(closes[:period])
            # Exact comparisons to the unrounded average, without a stop buffer.
            if total >= signal_row[6] * period:
                state = 'average_at_or_above_signal_close'
            elif opening * period <= total:
                state = 'open_at_or_below_average'
            elif entry_row[5] * period <= total:
                state = 'intraday_average_touched'
            else:
                state = 'average_not_touched'
        used_review = len(closes) >= period and (
            entry_reviewed or first_reviewed_length is not None and first_reviewed_length <= period)
        outcomes.append((state, used_review))
    return outcomes


def summarize(observation, openings, signal, entry, history, entry_basis, notices):
    sources = [(day, panel['source_sha256'] if panel is not None else None)
               for day, panel in history]
    start = sources[0][0] if sources else observation['score_asof']
    result = {
        'schema': SCHEMA, 'score_asof': observation['score_asof'], 'entry_session': entry['date'],
        'basis': 'official_krx_closing_sma_to_official_entry_ohlc',
        'archived_history_sessions': sum(digest is not None for _, digest in sources),
        'history_start': start, 'reviewed_ma_notices_sha256': notice_digest(notices, start, entry['date']),
        'history_sources_sha256': sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        'signal_source_sha256': signal['source_sha256'], 'entry_source_sha256': entry['source_sha256'],
        'score_impact': 0, 'full_corporate_action_history_certified': False,
        'performance_validated': False,
    }
    if (not sources or sources[-1][0] != observation['score_asof']
            or sources[-1][1] != signal['source_sha256']):
        return {**result, 'status': 'waiting_for_matching_signal_archive'}
    if (entry_basis is None or entry_basis['date'] != entry['date']
            or entry_basis['source_sha256'] != entry['source_sha256']):
        return {**result, 'status': 'waiting_for_matching_entry_archive'}
    states = {}
    for code, member in observation['rows'].items():
        entry_state, opening = openings[code]
        if member[3] is not None and entry_state == 'official_open_observed':
            states[code] = contact_states(
                code, member, opening, signal, entry, history, entry_basis, notices)
    groups = {}
    for label, limit in (('top20', 20), ('top100', 100), ('all_ranked', None)):
        selected = [value for code, value in states.items()
                    if limit is None or observation['rows'][code][3] <= limit]
        groups[label] = {'matched_openings': len(selected)}
        groups[label]['using_reviewed_share_continuity'] = {
            str(period): sum(value[index][1] for value in selected)
            for index, period in enumerate(PERIODS)}
        for index, period in enumerate(PERIODS):
            counts = Counter(value[index][0] for value in selected)
            groups[label][str(period)] = {state: counts[state] for state in STATES}
    return {**result, 'status': 'compared', 'groups': groups}
