"""Replay one issuer's price path in units of one share bought at entry."""
from fractions import Fraction

from v3_forward_cash_dividends import dividend_sessions
from v3_forward_liquidations import OBSERVED as LIQUIDATION_OBSERVED, TREATMENT as LIQUIDATION
from v3_forward_share_actions import HOLDER_NEUTRAL_ACTIONS, normalized_name, share_transition


def holding_change(previous, bar, notice):
    transition = share_transition(previous, bar, notice)
    if transition == 'unchanged_share_count':
        return None, Fraction(1)
    if transition != 'notice_matched':
        return transition, None
    if notice['action'] in ('split', 'reverse_split'):
        return None, Fraction(notice['new_units'], notice['old_units'])
    if notice['action'] in HOLDER_NEUTRAL_ACTIONS | {'rename'}:
        return None, Fraction(1)
    return 'share_action_requires_settlement', None


def issuer_matches(bar, market, name):
    return bar[0] == market and normalized_name(bar[1]) == normalized_name(name)


def replay_liquidation(terms, sessions, bars, previous, units, entry_open, notices):
    first, last = terms['first_on'], terms['last_on']
    if sessions[-1] < last:
        return {'status': 'liquidation_final_session_pending', 'blocked_session': last}
    if last not in sessions:
        return {'status': 'liquidation_final_session_not_archived', 'blocked_session': last}
    market, name = terms['market'], terms['name']
    if previous is not None and not issuer_matches(previous, market, name):
        return {'status': 'liquidation_issuer_mismatch', 'blocked_session': sessions[0]}
    final_bar = bars.get(last)
    if final_bar is None or final_bar[2] != 'traded':
        return {'status': 'liquidation_final_trade_unavailable', 'blocked_session': last}
    if final_bar[-1] != terms['listed_shares']:
        return {'status': 'liquidation_final_share_count_mismatch', 'blocked_session': last}
    for day in sessions:
        bar = bars.get(day)
        if day > last:
            if (day <= terms['delisted_on'] and bar is not None and bar[2] == 'traded'
                    and issuer_matches(bar, market, name)):
                return {'status': 'liquidation_final_date_conflicts_with_archive', 'blocked_session': day}
            continue
        if day < first:
            # Only a reviewed halt can start the replay before liquidation trading.
            if bar is None:
                continue
            if bar[2] != 'no_reported_volume':
                return {'status': 'liquidation_halt_conflicts_with_archive', 'blocked_session': day}
        elif bar is None or bar[2] not in ('traded', 'no_reported_volume'):
            return {'status': 'liquidation_bar_unavailable', 'blocked_session': day}
        if not issuer_matches(bar, market, name):
            return {'status': 'liquidation_issuer_mismatch', 'blocked_session': day}
        if previous is not None:
            reason, multiplier = holding_change(previous, bar, notices.get((day, terms['code'])))
            if reason:
                return {'status': 'liquidation_' + reason, 'blocked_session': day}
            units *= multiplier
        elif bar[2] != 'traded' or bar[3] != entry_open or entry_open <= 0:
            return {'status': 'entry_open_mismatch', 'blocked_session': day}
        previous = bar
    return {'status': LIQUIDATION_OBSERVED, 'exit_session': last,
            'terminal_return_pct': float((units * final_bar[6] / entry_open - 1) * 100)}


def replay_settlement(terms, sessions, bars, previous, units, entry_open, notices, panels):
    halt, target = terms['halt_on'], sessions[-1]
    if not issuer_matches(previous, terms['market'], terms['name']):
        return {'status': 'settlement_old_issuer_mismatch', 'blocked_session': halt}
    for day in sessions:
        if halt <= day <= terms['delisted_on'] and (bar := bars.get(day)) is not None:
            if bar[2] == 'traded' or not issuer_matches(bar, terms['market'], terms['name']):
                return {'status': 'settlement_halt_conflicts_with_archive', 'blocked_session': day}
    treatment = terms['treatment']
    if treatment == 'review_pending':
        return {'status': 'delisting_settlement_under_review', 'blocked_session': halt}
    if len(sessions) < 3 or terms['record_on'] < sessions[2]:
        return {'status': 'settlement_entry_entitlement_unconfirmed', 'blocked_session': halt}
    if units.denominator != 1:
        return {'status': 'settlement_old_fractional_units_unconfirmed', 'blocked_session': halt}
    paid = terms['paid_on']
    if treatment == 'cash_exchange':
        if not paid or paid > target:
            return {'status': 'settlement_cash_payment_unconfirmed', 'blocked_session': halt}
        terminal = units * Fraction(terms['cash_krw_per_old_share'])
    else:
        listed, code = terms['share_available_on'], terms['successor_code']
        if listed > target:
            return {'status': 'settlement_successor_awaiting_listing', 'blocked_session': halt}
        if listed not in sessions:
            return {'status': 'settlement_successor_listing_not_archived', 'blocked_session': listed}
        first = panels.get(listed, {}).get('rows', {}).get(code)
        if (first is None or first[2] != 'traded'
                or first[-1] != int(terms['successor_shares_on_listing'])
                or not issuer_matches(first, terms['successor_market'], terms['successor_name'])):
            return {'status': 'settlement_successor_listing_unconfirmed', 'blocked_session': listed}
        # Reviewed ratios must use the share basis of the last old trading session.
        entitlement = units * Fraction(terms['shares_per_old_share'])
        whole = int(entitlement)
        fraction = entitlement - whole
        terminal = Fraction(0)
        if fraction:
            basis = terms['fractional_cash_basis']
            if not basis or not paid or paid > target:
                return {'status': 'settlement_fractional_cash_unconfirmed', 'blocked_session': listed}
            terminal = fraction * first[6]
            if basis == 'listing_close_round_down':
                terminal = int(terminal)
            elif basis == 'listing_close_round_half_up':
                terminal = int(terminal + Fraction(1, 2))
            elif terminal.denominator != 1:
                return {'status': 'settlement_fractional_cash_rounding_unconfirmed',
                        'blocked_session': listed}
        if whole:
            successor_units, previous = Fraction(whole), first
            for day in sessions[sessions.index(listed) + 1:]:
                bar = panels.get(day, {}).get('rows', {}).get(code)
                if bar is None or bar[2] != 'traded':
                    return {'status': 'settlement_successor_price_unavailable', 'blocked_session': day}
                reason, multiplier = holding_change(previous, bar, notices.get((day, code)))
                if reason:
                    return {'status': 'settlement_successor_' + reason, 'blocked_session': day}
                successor_units *= multiplier
                if successor_units.denominator != 1:
                    return {'status': 'settlement_successor_fractional_units_unconfirmed',
                            'blocked_session': day}
                previous = bar
            terminal += successor_units * previous[6]
    return {'status': 'observed_delisting_settlement', 'treatment': treatment,
            'terminal_return_pct': float((terminal / entry_open - 1) * 100)}


def replay_price_path(code, sessions, bars, *, entry_open, notices, reference=None, dividends=(),
                      settlements=None, panels=None):
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
    cash_by_session = dividend_sessions(sessions, dividends)
    paid_cash = Fraction(0)
    paid_events = pending_events = unlinked_events = 0
    for index, day in enumerate(sessions):
        bar = bars.get(day)
        terms = settlements.get((day, code)) if settlements else None
        if terms and terms['treatment'] == LIQUIDATION:
            return replay_liquidation(terms, sessions[index:], bars, previous, units,
                                      entry_open, notices)
        if terms and previous is not None:
            return replay_settlement(terms, sessions, bars, previous, units,
                                     entry_open, notices, panels or {})
        if bar is None or bar[2] != 'traded':
            return {'status': 'missing_traded_bar', 'blocked_session': day}
        if previous is None:
            if bar[3] != entry_open or entry_open <= 0:
                return {'status': 'entry_open_mismatch', 'blocked_session': day}
        else:
            notice = notices.get((day, code))
            reason, multiplier = holding_change(previous, bar, notice)
            if reason:
                return {'status': reason, 'blocked_session': day}
            units *= multiplier
            if notice and notice['action'] in ('split', 'reverse_split'):
                adjusted_sessions += 1
        low = min(low, bar[5] * units)
        high = max(high, bar[4] * units)
        close = bar[6] * units
        for event in cash_by_session.get(day, ()):
            if bar[0] != event['market'] or normalized_name(bar[1]) != normalized_name(event['name']):
                unlinked_events += 1
            elif event['paid_on'] and event['paid_on'] <= sessions[-1]:
                paid_cash += units * Fraction(event['krw_per_common_share'])
                paid_events += 1
            else:
                pending_events += 1
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
        'confirmed_dividend_events': paid_events,
        'pending_dividend_events': pending_events,
        'unlinked_dividend_events': unlinked_events,
        'price_plus_confirmed_dividend_pct': float((close + paid_cash - entry_open) * 100 / entry_open)
        if paid_events else None,
    }
