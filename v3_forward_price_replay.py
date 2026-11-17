"""Replay one issuer's price path in units of one share bought at entry."""
from fractions import Fraction

from v3_forward_share_actions import share_transition


def replay_price_path(code, sessions, bars, *, entry_open, notices, reference=None):
    """Use an ordered exchange-session window and independently reviewed notices.

    The caller supplies validated KRX rows and a reference already reconciled
    to the entry share basis. Unresolved sessions withhold the entire result.
    """
    if not sessions or list(sessions) != sorted(set(sessions)):
        raise ValueError('invalid_forward_replay_sessions')
    units = Fraction(1)
    previous = None
    low = high = Fraction(entry_open)
    first_touch = None
    adjusted_sessions = 0
    for day in sessions:
        bar = bars.get(day)
        if bar is None or bar[2] != 'traded':
            return {'status': 'missing_traded_bar', 'blocked_session': day}
        if previous is None:
            if bar[3] != entry_open or entry_open <= 0:
                return {'status': 'entry_open_mismatch', 'blocked_session': day}
        else:
            notice = notices.get((day, code))
            transition = share_transition(previous, bar, notice)
            if transition == 'notice_matched':
                if notice['action'] not in ('split', 'reverse_split'):
                    return {'status': 'share_action_requires_settlement', 'blocked_session': day}
                units *= Fraction(notice['new_units'], notice['old_units'])
                adjusted_sessions += 1
            elif transition != 'unchanged_share_count':
                return {'status': transition, 'blocked_session': day}
        low = min(low, bar[5] * units)
        high = max(high, bar[4] * units)
        close = bar[6] * units
        if reference is not None and first_touch is None and bar[5] * units <= reference:
            first_touch = day
        previous = bar
    return {
        'status': 'observed_price_path',
        'terminal_return_pct': float((close / entry_open - 1) * 100),
        'mae_pct': float((low / entry_open - 1) * 100),
        'mfe_pct': float((high / entry_open - 1) * 100),
        'reference_compared': reference is not None,
        'first_reference_touch_session': first_touch,
        'adjusted_split_sessions': adjusted_sessions,
    }
