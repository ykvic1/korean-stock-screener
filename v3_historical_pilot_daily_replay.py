"""Recompute saved historical price paths from pinned public source files."""
import argparse
from collections import defaultdict
import csv
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import partial
from hashlib import sha256
import json
from pathlib import Path

from v3_historical_performance_pilot_audit import (
    CASH, ENTRY, HORIZONS, LIQUIDATION, PilotAuditError, audit, day, exchanged_units,
    holding_units, positive, require,
)

SOURCE_DIGESTS = (
    "2bfd93c217eb74263bc5020b23fa6debb6b02531c11eaccc2826639bc191559e",
    "da748e52cef9fe453ae300abff91c1e2bd1509daa653408ec54dede66a6eda2d",
    "842fb7a65532cd82e2cc3023fde21e8d6fcdc5008b45db3083f2504c5ead88cc",
)
QUOTE_FIELDS = ("Open", "High", "Low", "Close", "Volume", "Stocks")
CALENDAR_CODES = ("005930", "000660")


def source_rows(paths, symbols, last):
    import pyarrow.parquet as pq

    for path, digest in zip(paths, SOURCE_DIGESTS, strict=True):
        require(sha256(path.read_bytes()).hexdigest() == digest, "historical_public_source_digest_mismatch")
    for path in paths[:2]:
        table = pq.read_table(path, columns=["Code", "Date", *QUOTE_FIELDS],
                              filters=[("Code", "in", sorted(symbols))])
        for row in table.to_pylist():
            yield row["Code"], row["Date"].date(), [row[key] for key in QUOTE_FIELDS]
    with paths[2].open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["Code"] in symbols:
                yield row["Code"], last, [int(row[key]) for key in QUOTE_FIELDS]


def retained_bars(inputs, references, settlements, trades, last, share_actions):
    source_dates = {}
    for code in references:
        source_dates[code] = ENTRY, day(trades[code]["last_positive_volume_candidate"])
        term = settlements[code]
        successor = term["successor_code"]
        if successor:
            available = day(term["settlement_or_share_availability_on"])
            first = min(available, source_dates.get(successor, (available, last))[0])
            source_dates[successor] = first, last
    windows = {**source_dates, **dict.fromkeys(CALENDAR_CODES, (ENTRY, last))}
    rows = {}
    calendars = defaultdict(set)
    action_dates = {(code, day(session)) for (code, listing_on), row in share_actions.items()
                    for session in (listing_on, row["prior_trade_on"])}
    action_shares = {}
    for code, session, values in source_rows(inputs, windows.keys(), last):
        if (code, session) in action_dates and values[-2] is not None and values[-2] > 0:
            require((code, session) not in action_shares, "duplicate_action_trade_bar")
            action_shares[code, session] = int(values[-1])
        start, end = windows[code]
        if not start <= session <= end or not values[-2]:
            continue
        require(all(value is not None and int(value) == value for value in values[:-1]), "invalid_pilot_trade_bar")
        o, h, l, c, volume = map(int, values[:-1])
        require(volume > 0 and 0 < l <= min(o, c) <= max(o, c) <= h, "invalid_pilot_trade_bar")
        if code in CALENDAR_CODES:
            calendars[code].add(session)
        else:
            require((code, session) not in rows, "duplicate_pilot_trade_bar")
            rows[code, session] = o, h, l, c
    market = sorted(calendars[CALENDAR_CODES[0]])
    require(market and calendars[CALENDAR_CODES[0]] == calendars[CALENDAR_CODES[1]]
            and market[0] == ENTRY and market[-1] == last, "pilot_market_session_range_mismatch")
    require(all(session in calendars[CALENDAR_CODES[0]] for _, session in rows), "invalid_trade_calendar")
    for (code, session), row in share_actions.items():
        require(action_shares.get((code, day(row["prior_trade_on"]))) == int(row["prior_trade_listed_shares"])
                and action_shares.get((code, day(session))) == int(row["shares_after_action"]),
                "historical_action_share_count_mismatch")
    return market, rows


def replay_trade_bars(market, bars, references, settlements, trades, adjustments):
    holdings = {}
    used = set()
    for code in references:
        term = settlements[code]
        last_trade = day(trades[code]["last_positive_volume_candidate"])
        successor = term["successor_code"]
        available = day(term["settlement_or_share_availability_on"]) if successor else None
        observations = {}
        for session in market:
            if session <= last_trade:
                symbol = code
                units = holding_units(adjustments, code, ENTRY, session)
            elif available and session >= available:
                symbol = successor
                units = exchanged_units(adjustments, code, term, session)
            else:
                continue
            bar = bars.get((symbol, session))
            if bar:
                used.add((symbol, session))
                observations[session] = (symbol, *(price * units for price in bar))
        require(ENTRY in observations and last_trade in observations, "missing_pilot_entry_or_last_trade")
        holdings[code] = observations
    require(used == bars.keys(), "unused_pilot_trade_bar")
    return holdings


def check_replayed_horizon(market, observations, bars, path, price, settlement, valuation, entry, line):
    end = valuation
    if price["method"] in CASH:
        end = day(settlement["settlement_or_share_availability_on"]) - timedelta(days=1)
    elif price["method"] in LIQUIDATION:
        end = day(settlement["liquidation_last_on"])
    sessions = [session for session in market if session <= end]
    trades = {session: observations[session] for session in sessions if session in observations}
    require(len(sessions) == int(path["eligible_sessions"])
            and len(trades) == int(path["observed_trade_bars"]), "pilot_bar_session_count_mismatch")

    observed = price["observation_on"]
    if price["observed_code"]:
        quote = bars.get((price["observed_code"], day(observed)))
        require(quote and Decimal(quote[3]) == positive(price["quote_or_cash_krw"]),
                "pilot_bar_terminal_quote_mismatch")
    require(trades[ENTRY][1] == entry, "pilot_bar_entry_quote_mismatch")

    low_day = min(trades, key=lambda session: trades[session][3])
    high_day = max(trades, key=lambda session: trades[session][2])
    low, high = trades[low_day][3], trades[high_day][2]
    step = Decimal("0.0001")
    adverse = (max(Decimal(0), 1 - low / entry) * 100).quantize(step, rounding=ROUND_HALF_UP)
    favorable = (max(Decimal(0), high / entry - 1) * 100).quantize(step, rounding=ROUND_HALF_UP)
    require(low_day == day(path["adverse_extreme_on"]) and high_day == day(path["favorable_extreme_on"])
            and adverse == Decimal(path["maximum_adverse_excursion_observed_pct"])
            and favorable == Decimal(path["maximum_favorable_excursion_observed_pct"]),
            "pilot_bar_path_extremes_mismatch")

    first = next((session for session, row in trades.items() if row[3] <= line), None)
    require((first.isoformat() if first else "") == path["first_observed_touch_on"],
            "pilot_bar_first_touch_mismatch")
    return trades, low_day, first


def check_replayed_reference(reference, market, trades, low_day, first):
    symbol, _, _, low, _ = trades[low_day]
    step = Decimal("0.0001")
    require(symbol == reference["lowest_observed_value_code"]
            and low.quantize(step, rounding=ROUND_HALF_UP) == positive(reference["lowest_observed_value_krw"]),
            "pilot_bar_reference_low_mismatch")
    if first:
        symbol, o, _, low, _ = trades[first]
        require(symbol == reference["first_touch_code"]
                and o.quantize(step, rounding=ROUND_HALF_UP) == positive(reference["first_touch_open_value_krw"])
                and low.quantize(step, rounding=ROUND_HALF_UP) == positive(reference["first_touch_low_value_krw"])
                and reference["first_touch_type"] == (
                    "gap_open" if o <= positive(reference["reference_line_krw"]) else "intraday_low")
                and sum(session < first and session not in trades for session in market)
                == int(reference["unpriced_sessions_before_observed_touch"]),
                "pilot_bar_reference_touch_mismatch")


def replay(inputs, references, prices, paths, settlements, trades, adjustments, share_actions):
    last = max(day(row["valuation_session"]) for row in prices.values())
    market, bars = retained_bars(inputs, references, settlements, trades, last, share_actions)
    holdings = replay_trade_bars(market, bars, references, settlements, trades, adjustments)
    for code, reference in references.items():
        entry = positive(reference["entry_open_krw"])
        line = positive(reference["reference_line_krw"])
        for months in HORIZONS:
            horizon = str(months)
            price, path = prices[code, horizon], paths[code, horizon]
            valuation = day(price["valuation_session"])
            require(valuation in market, "pilot_valuation_not_a_market_session")
            mark_bars, low_day, touch_day = check_replayed_horizon(
                market, holdings[code], bars, path, price, settlements[code], valuation, entry, line,
            )
        check_replayed_reference(reference, market, mark_bars, low_day, touch_day)
    return {"replayed_source_trade_bars": len(bars), "replayed_market_sessions": len(market),
            "replayed_horizon_rows": len(prices), "replayed_share_action_transitions": len(share_actions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prices_2025", type=Path)
    parser.add_argument("prices_2026", type=Path)
    parser.add_argument("dated_close_csv", type=Path)
    args = parser.parse_args()
    try:
        report = audit(daily_replay=partial(replay, (args.prices_2025, args.prices_2026, args.dated_close_csv)))
    except (OSError, ImportError, PilotAuditError) as exc:
        parser.exit(1, f"Historical daily replay failed: {exc}\n")
    print(json.dumps({key: val for key, val in report.items() if key.startswith("replayed_")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
