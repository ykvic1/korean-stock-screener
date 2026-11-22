"""Follow original-to-successor holder CLOSE paths through the KRX daily archive.

Shares are rebuilt from the frozen five split/consolidation terms and reviewed
exchange ratios. We do NOT equate KRX authenticated daily CLOSE with official
intraday LOW, prove all 09:00–15:30 volume, or certify continuous adjusted
total-return history. An absent/suspended quote is NEVER forward-filled.
Only anonymized aggregate counts may leave the private audit.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
import re

from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import ENTRY, END, ROOT, day, ledger_rows, traded
from v3_historical_performance_pilot_audit import (
    CASH, LIQUIDATION, exchanged_units, holding_units,
)
from v3_historical_official_holder_terminal import _amount, require

SCHEMA = "v3-historical-official-holder-daily-close-path-v1"
HORIZONS = frozenset(("1", "2", "3", "6", "12"))
SYMBOL = re.compile(r"[0-9A-Z]{6}\Z")
VALID_OBSERVATIONS = (
    "official_close_and_holder_units_match",
    "untraded_in_both",
    "missing_in_both",
    "missing_one_source",
    "issuer_or_market_mismatch",
    "listed_shares_mismatch",
    "trade_status_mismatch",
    "official_close_mismatch",
    "official_close_outside_public_candle",
)
UNPRICED = frozenset(("untraded_in_both", "missing_in_both"))
BAD = frozenset(VALID_OBSERVATIONS) - UNPRICED - {"official_close_and_holder_units_match"}


def plan(sessions, root=ROOT):
    """Create identity- and corporate-action-aware per-session holder schedule.

    The 17 fixed original issuers are the only expected candidates. Dates after
    a suspended/delisted original and before available exchange shares are
    deliberately unpriced, not assigned a fabricated quote.
    """
    require(len(sessions) == 245 and sessions[0].isoformat() == "2025-11-13"
            and sessions[1] == ENTRY and sessions[-1] == END
            and len(set(sessions)) == len(sessions),
            "unrecognized_official_historical_daily_sessions")
    references = ledger_rows(root, "downside-reference")
    prices = ledger_rows(root, "price-replay")
    paths = ledger_rows(root, "downside-horizons")
    trades = ledger_rows(root, "delisting-trade-observations")
    settlements = ledger_rows(root, "delisting-settlement")
    actions = ledger_rows(root, "share-adjustments")
    require(len(references) == 17 and len(prices) == len(paths) == 85
            and len(actions) == 5,
            "incomplete_frozen_historical_daily_path_inputs")
    ref = {r["code"]: r for r in references}
    trade = {r["code"]: r for r in trades if r["code"] in ref}
    settlement = {r["code"]: r for r in settlements if r["code"] in ref}
    require(len(ref) == len(trade) == len(settlement) == 17
            and all(trade[c]["next_session_status"] == "traded" for c in ref)
            and all(r["signal_session"] == "2025-11-13"
                    and r["entry_session"] == ENTRY.isoformat() for r in references),
            "missing_original_trading_identity")
    changes = defaultdict(list)
    for a in actions:
        key, when = a["code"], day(a["listing_on"])
        numerator, denominator = _amount(a["new_units"]), _amount(a["old_units"])
        require(SYMBOL.fullmatch(key) and when > ENTRY
                and numerator != denominator
                and (not changes[key] or when > changes[key][-1][0]),
                "invalid_historical_daily_action_order")
        changes[key].append((when, numerator / denominator))
    for entries in changes.values():
        entries.sort()
    for c in ref:
        require(SYMBOL.fullmatch(c)
                and _amount(ref[c]["entry_open_krw"]) > 0
                and _amount(ref[c]["reference_line_krw"]) > 0
                and ENTRY <= day(trade[c]["last_positive_volume_candidate"]) <= END,
                "untrusted_historical_holder_reference")
        s = settlement[c]
        if s["successor_code"]:
            require(s["treatment"] == "stock_exchange"
                    and SYMBOL.fullmatch(s["successor_code"])
                    and s["settlement_or_share_availability_on"]
                    and day(s["settlement_or_share_availability_on"])
                        > day(trade[c]["last_positive_volume_candidate"]),
                    "unresolved_successor_holder_schedule")
    price = {(r["code"], r["horizon_months"]): r for r in prices}
    path = {(r["code"], r["horizon_months"]): r for r in paths}
    require(len(price) == len(path) == 85 and price.keys() == path.keys()
            and {c for c, _ in price} == set(ref)
            and all({h for c, h in price if c == origin} == HORIZONS
                    for origin in ref),
            "historical_holder_horizon_roster_mismatch")

    cutoff = {}
    for key, p in price.items():
        c, h = key
        d = path[key]
        require(p["valuation_session"] == d["valuation_session"]
                and p["entry_open_krw"] == ref[c]["entry_open_krw"],
                "historical_holder_horizon_valuation_mismatch")
        end = day(p["valuation_session"])
        settlement_row = settlement[c]
        if p["method"] in CASH:
            end = day(settlement_row["settlement_or_share_availability_on"]) - timedelta(days=1)
        elif p["method"] in LIQUIDATION:
            end = day(settlement_row["liquidation_last_on"])
        require(ENTRY <= end <= END,
                "historical_holder_horizon_cutoff_invalid")
        cutoff[key] = end
    by_day = defaultdict(list)
    traded_before, traded_after = 0, 0
    for c in sorted(ref):
        last_trade = day(trade[c]["last_positive_volume_candidate"])
        s = settlement[c]
        successor = s["successor_code"]
        available = day(s["settlement_or_share_availability_on"]) if successor else None
        for session in sessions[1:]:
            if session <= last_trade:
                symbol = c
                units = holding_units(changes, c, ENTRY, session)
                traded_before += 1
            elif available and session >= available:
                symbol = successor
                units = exchanged_units(changes, c, s, session)
                traded_after += 1
            else:
                continue
            require(SYMBOL.fullmatch(symbol) and units > 0,
                    "untrusted_original_successor_daily_unit_transition")
            by_day[session].append((c, symbol, units))
    require(traded_before > 0 and traded_after > 0
            and len(by_day) >= 1 and sum(map(len, by_day.values())) > 2000,
            "historical_holder_close_path_too_sparse")
    return {
        "days": sessions[1:], "by_day": by_day,
        "reference": ref, "horizon": path, "cutoff": cutoff,
        "original_share_days_planned": traded_before,
        "successor_share_days_planned": traded_after,
    }


def observe(public, official, today, targets):
    """Keep all original and converted CLOSE prices private and transient."""
    out = []
    for origin, symbol, units in targets:
        p, o = public.get(symbol), official.get(symbol)
        if p is None and o is None:
            status, value = "missing_in_both", None
        elif p is None or o is None:
            status, value = "missing_one_source", None
        elif (p[0] != o[0] or not normalized_name(p[1])
              or normalized_name(p[1]) != normalized_name(o[1])):
            status, value = "issuer_or_market_mismatch", None
        elif (type(p[2]) not in (int, float) or type(o[3]) is not int
              or p[2] != o[3]):
            status, value = "listed_shares_mismatch", None
        elif not traded(p) and o[5] is False:
            status, value = "untraded_in_both", None
        elif traded(p) != (o[5] is True):
            status, value = "trade_status_mismatch", None
        elif p[6] != o[2] or type(o[2]) is not int:
            status, value = "official_close_mismatch", None
        else:
            lo, hi = Decimal(str(p[5])) * units, Decimal(str(p[4])) * units
            value = Decimal(o[2]) * units
            if not lo <= value <= hi:
                status, value = "official_close_outside_public_candle", None
            else:
                status = "official_close_and_holder_units_match"
        out.append((origin, today, status, value))
    return out


def summarize(p, observations):
    """Reconcile 85 saved horizon counts and official CLOSE downside bounds."""
    require(type(p) is dict and type(observations) is list
            and len(p["horizon"]) == 85 and len(p["reference"]) == 17,
            "invalid_holder_daily_path_summary_scope")
    outcomes = Counter(dict.fromkeys(VALID_OBSERVATIONS, 0))
    indexed = {}
    for origin, on, status, value in observations:
        require((origin, on) not in indexed and origin in p["reference"]
                and on in p["by_day"] and status in outcomes
                and (status == "official_close_and_holder_units_match")
                    == (type(value) is Decimal and value.is_finite() and value > 0),
                "ambiguous_historical_daily_holder_observation")
        indexed[origin, on] = (status, value)
        outcomes[status] += 1
    require(sum(outcomes.values()) <= sum(map(len, p["by_day"].values())),
            "unexpected_historical_daily_holder_observations")

    all_horizon = len(p["horizon"])
    fully_matched = observations_matched = bounds_matched = 0
    missing_counts = Counter()
    incompatible = Counter()
    no_touch_before_reference = 0
    for (origin, horizon), record in p["horizon"].items():
        eligible = [d for d in p["days"] if d <= p["cutoff"][origin, horizon]]
        require(len(eligible) == int(record["eligible_sessions"]),
                "historical_horizon_calendar_count_changed")
        found = {d: indexed[origin, d] for d in eligible
                 if (origin, d) in indexed
                 and indexed[origin, d][0] == "official_close_and_holder_units_match"}
        observed = len(found)
        gaps = len(eligible) - observed
        missing_counts["all_eligible_day_gaps"] += gaps
        if observed != int(record["observed_trade_bars"]) or gaps != int(record["unpriced_sessions"]):
            incompatible["horizon_trade_or_gap_count_mismatch"] += 1
            continue
        fully_matched += 1
        observations_matched += observed
        values = [v for _, v in found.values()]
        entry = _amount(p["reference"][origin]["entry_open_krw"])
        min_close, max_close = min(values), max(values)
        adverse_close = max(Decimal(0), (1 - min_close / entry) * 100)
        favorable_close = max(Decimal(0), (max_close / entry - 1) * 100)
        adverse_low_bound = Decimal(record["maximum_adverse_excursion_observed_pct"])
        favorable_high_bound = Decimal(record["maximum_favorable_excursion_observed_pct"])
        if (adverse_close > adverse_low_bound + Decimal("0.00015")
                or favorable_close > favorable_high_bound + Decimal("0.00015")):
            incompatible["official_close_exceeds_public_intraday_extreme_bound"] += 1
            continue
        bounds_matched += 1
        # CLOSE below the structural line is sufficient evidence of a breach
        # on that day, but absence of a CLOSE breach is NOT proof of no LOW.
        reference = p["reference"][origin]
        first = reference["first_observed_touch_on"]
        first_close_on = next(
            (d for d in eligible if d in found
             and found[d][1] <= _amount(reference["reference_line_krw"])),
            None,
        )
        if first_close_on and (not first or first_close_on < day(first)):
            incompatible["official_close_before_frozen_first_observed_low_touch"] += 1
        else:
            no_touch_before_reference += 1
    success = (
        len(indexed) == sum(map(len, p["by_day"].values()))
        and not any(outcomes[reason] for reason in BAD)
        and fully_matched == bounds_matched == no_touch_before_reference == all_horizon
        and not incompatible
    )
    return {
        "schema": SCHEMA,
        "original_issuers": len(p["reference"]),
        "horizon_rows": all_horizon,
        "daily_original_holder_rows_planned": p["original_share_days_planned"],
        "daily_successor_holder_rows_planned": p["successor_share_days_planned"],
        "daily_official_holder_observations": len(indexed),
        "daily_official_close_with_corporate_units": outcomes[
            "official_close_and_holder_units_match"],
        "frozen_horizon_observation_counts_reproduced": fully_matched,
        "frozen_horizon_intraday_extremes_bound_official_close": bounds_matched,
        "close_does_not_precede_frozen_first_low_touch": no_touch_before_reference,
        "horizon_traded_close_observations_replayed": observations_matched,
        "horizon_unpriced_eligible_sessions": missing_counts["all_eligible_day_gaps"],
        "outcomes": dict(outcomes),
        "incompatible_horizons": dict(incompatible),
        "daily_close_and_holder_schedule_matched": success,
        "new_krx_market_api_requests": 0,
        "new_private_archive_reads": 0,
        "independent_intraday_low_every_trading_day_verified": False,
        "earliest_intraday_low_touch_certified": False,
        "strict_regular_only_turnover_certified": False,
        "complete_adjusted_total_return_history_certified": False,
        "historical_pit_issuer_and_scoring_certified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def valid_summary(result):
    if type(result) is not dict or result.get("schema") != SCHEMA:
        return False
    outcomes = result.get("outcomes")
    total = result.get("daily_original_holder_rows_planned", 0) + result.get(
        "daily_successor_holder_rows_planned", 0)
    if (type(outcomes) is not dict or set(outcomes) != set(VALID_OBSERVATIONS)
            or any(type(n) is not int or n < 0 for n in outcomes.values())
            or result.get("original_issuers") != 17
            or result.get("horizon_rows") != 85
            or type(total) is not int or total < 2000
            or result.get("daily_official_holder_observations") != sum(outcomes.values())
            or result.get("daily_official_holder_observations") != total
            or result.get("daily_official_close_with_corporate_units") != outcomes[
                "official_close_and_holder_units_match"]
            or result.get("frozen_horizon_observation_counts_reproduced") != 85
            or result.get("frozen_horizon_intraday_extremes_bound_official_close") != 85
            or result.get("close_does_not_precede_frozen_first_low_touch") != 85
            or result.get("daily_close_and_holder_schedule_matched") is not True
            or result.get("incompatible_horizons") != {}
            or any(result.get(key) != 0 for key in (
                "new_krx_market_api_requests", "new_private_archive_reads"))
            or any(result.get(key) is not False for key in (
                "independent_intraday_low_every_trading_day_verified",
                "earliest_intraday_low_touch_certified",
                "strict_regular_only_turnover_certified",
                "complete_adjusted_total_return_history_certified",
                "historical_pit_issuer_and_scoring_certified",
                "performance_validated", "v30b_release_ready"))):
        return False
    if any(outcomes[key] for key in BAD):
        return False
    return True
