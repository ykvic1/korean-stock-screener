"""Verify frozen 17-issuer terminal *quoted* holder values against archived KRX CLOSE.

All 245 official daily archives are consumed by the existing close audit; this
module adds zero provider requests and no extra archive reads. An independently
observed KRX CLOSE is multiplied by the contemporaneous holder unit ratio from
reviewed split/merger terms, then compared with the 1/2/3/6/12-month ledger.
Cash entitlements, end-to-end adjusted OHLC LOW, PIT scores, and realized
returns remain outside this limited evidence boundary. Private issuers and
prices are never emitted to public reports.
"""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re

from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import ROOT, ENTRY, END, day, ledger_rows, traded
from v3_historical_performance_pilot_audit import (
    exchanged_units, holding_units,
)

SCHEMA = "v3-historical-official-holder-terminal-quote-v1"
QUOTE_METHODS = frozenset((
    "stock_delivery_mark", "listed_close", "adjusted_listed_close",
    "liquidation_close_scenario", "last_liquidation_close_then_cash",
))
CURRENCY = Decimal("0.0001")
SECURITY = re.compile(r"[0-9A-Z]{6}\Z")
HORIZONS = frozenset(("1", "2", "3", "6", "12"))
STATUSES = (
    "exact_official_holder_close",
    "official_archive_date_missing",
    "public_or_official_quote_missing",
    "official_quote_not_traded",
    "wrong_issuer_or_market",
    "listed_shares_changed",
    "official_close_differs_from_public",
    "quoted_close_differs_from_frozen_horizon",
    "holder_value_differs_from_frozen_horizon",
)


class OfficialHolderQuoteError(ValueError):
    """Static sanitized errors; never include issuer quotes or codes."""


def require(ok, reason):
    if not ok:
        raise OfficialHolderQuoteError(reason)


def _amount(value):
    try:
        x = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise OfficialHolderQuoteError("invalid_historical_holder_amount") from None
    require(x.is_finite() and x > 0, "invalid_historical_holder_amount")
    return x


def terminal_plan(root=ROOT):
    """Private session -> observation tuples, with an immutable 85-row scope."""
    prices = ledger_rows(root, "price-replay")
    actions = ledger_rows(root, "share-adjustments")
    settlements = ledger_rows(root, "delisting-settlement")
    require(len(prices) == 85 and len({(p["code"], p["horizon_months"])
                                      for p in prices}) == 85
            and len({p["code"] for p in prices}) == 17,
            "frozen_horizon_quotes_changed")
    require(len(actions) == 5 and len(settlements) >= 17,
            "frozen_corporate_action_scope_changed")
    changes = defaultdict(list)
    for a in actions:
        num, den = _amount(a["new_units"]), _amount(a["old_units"])
        code, on = a["code"], day(a["listing_on"])
        require(SECURITY.fullmatch(code) and num != den and on > ENTRY
                and not any(date == on for date, _ in changes[code]),
                "invalid_frozen_adjustment")
        changes[code].append((on, num / den))
    for values in changes.values():
        values.sort()
    settlement_by_code = {s["code"]: s for s in settlements}
    require(len(settlement_by_code) == len(settlements),
            "ambiguous_corporate_settlement")
    seen_horizons = defaultdict(set)
    observations = defaultdict(list)
    cash_count, origin_count = 0, 0
    for index, row in enumerate(prices):
        origin, symbol, horizon = row["code"], row["observed_code"], row["horizon_months"]
        require(SECURITY.fullmatch(origin) and horizon in HORIZONS
                and horizon not in seen_horizons[origin],
                "invalid_horizon_quote_identity")
        seen_horizons[origin].add(horizon)
        settlement = settlement_by_code.get(origin)
        require(settlement is not None, "missing_original_corporate_settlement")
        if not symbol:
            cash_count += 1
            continue
        require(SECURITY.fullmatch(symbol) and row["method"] in QUOTE_METHODS
                and row["entry_session"] == ENTRY.isoformat(),
                "unknown_frozen_horizon_quote_method")
        on, valuation = day(row["observation_on"]), day(row["valuation_session"])
        require(ENTRY <= on <= valuation <= END, "quoted_horizon_date_outside_archive")
        if symbol == origin:
            units = holding_units(changes, origin, ENTRY, on)
            require(row["method"] != "stock_delivery_mark",
                    "original_symbol_disguised_as_successor")
            origin_count += 1
        else:
            require(settlement["treatment"] == "stock_exchange"
                    and symbol == settlement["successor_code"]
                    and settlement["event_on"]
                    and settlement["settlement_or_share_availability_on"]
                    and on >= day(settlement["settlement_or_share_availability_on"]),
                    "unlinked_successor_quote")
            units = exchanged_units(changes, origin, settlement, on)
            require(row["method"] == "stock_delivery_mark",
                    "successor_quote_unrecognized_method")
        quoted_units = _amount(row["units_per_entry_share"])
        quote = _amount(row["quote_or_cash_krw"])
        holder_value = _amount(row["value_per_entry_share_krw"])
        # The historical ledger accepts either tie-breaking convention at
        # the fifth decimal place; never round a genuine half-step mismatch
        # into a fabricated official adjusted price.
        require(units == quoted_units and
                abs(units * quote - holder_value) <= Decimal("0.00005")
                and holder_value == holder_value.quantize(CURRENCY),
                "corporate_action_holder_ratio_or_value_drift")
        require(row["observation_on"] and row["observed_code"]
                and not any(x[0] == index for x in observations[on]),
                "duplicate_private_horizon_quote")
        observations[on].append((index, symbol, quote, holder_value, units))
    require(len(observations) > 0 and cash_count == 6
            and sum(len(x) for x in observations.values()) == 79
            and origin_count > 0
            and len(seen_horizons) == 17
            and all(v == HORIZONS for v in seen_horizons.values()),
            "incomplete_historical_terminal_quote_roster")
    return dict(observations), len(prices), cash_count


def observe(public, official, expected):
    """Return transient (row_index, status) tuples; never publish these tuples."""
    result = []
    for index, symbol, quote, book, units in expected:
        p, o = public.get(symbol), official.get(symbol)
        if p is None or o is None:
            status = "public_or_official_quote_missing"
        elif (p[0] != o[0] or not normalized_name(p[1])
              or normalized_name(p[1]) != normalized_name(o[1])):
            status = "wrong_issuer_or_market"
        elif (type(p[2]) not in (int, float) or type(o[3]) is not int
              or p[2] != o[3]):
            status = "listed_shares_changed"
        elif not traded(p) or o[5] is not True or type(o[2]) is not int:
            status = "official_quote_not_traded"
        elif p[6] != o[2]:
            status = "official_close_differs_from_public"
        elif Decimal(o[2]) != quote:
            status = "quoted_close_differs_from_frozen_horizon"
        elif (abs(units * Decimal(o[2]) - book) > Decimal("0.00005")
              or book != book.quantize(CURRENCY)):
            status = "holder_value_differs_from_frozen_horizon"
        else:
            status = "exact_official_holder_close"
        result.append((index, status))
    return result


def summarize(total_rows, cash_rows, observation_rows):
    """Public aggregate with explicit excluded cash/unpriced horizons."""
    require(type(total_rows) is int and total_rows == 85
            and type(cash_rows) is int and cash_rows == 6
            and type(observation_rows) is list,
            "invalid_terminal_quote_coverage")
    states = Counter(dict.fromkeys(STATUSES, 0))
    seen = set()
    for index, status in observation_rows:
        require(type(index) is int and 0 <= index < total_rows
                and index not in seen and status in states,
                "repeated_or_invalid_official_terminal_quote")
        seen.add(index)
        states[status] += 1
    # Missing archive days remain unverified, rather than being promoted.
    missing = total_rows - cash_rows - len(seen)
    require(missing >= 0, "extra_horizon_observations")
    states["official_archive_date_missing"] = missing
    proven = states["exact_official_holder_close"]
    return {
        "schema": SCHEMA,
        "frozen_historical_horizons": total_rows,
        "quoted_price_horizons": total_rows - cash_rows,
        "excluded_cash_or_unpriced_horizons": cash_rows,
        "individually_verified_official_terminal_closes": proven,
        "all_quoted_horizons_have_exact_holder_close": proven == total_rows - cash_rows,
        "outcomes": dict(states),
        "new_provider_calls": 0,
        "not_a_continuous_adjusted_ohlc_path": True,
        "not_intraday_low_or_stop_breach_certification": True,
        "regular_only_volume_verified": False,
        "historical_point_in_time_scores_certified": False,
        "complete_corporate_action_history_certified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def valid_summary(report):
    if type(report) is not dict or report.get("schema") != SCHEMA:
        return False
    counts = report.get("outcomes")
    if (type(counts) is not dict or set(counts) != set(STATUSES)
            or any(type(x) is not int or x < 0 for x in counts.values())
            or report.get("frozen_historical_horizons") != 85
            or report.get("quoted_price_horizons") != 79
            or report.get("excluded_cash_or_unpriced_horizons") != 6
            or sum(counts.values()) != 79
            or report.get("individually_verified_official_terminal_closes") != counts[
                "exact_official_holder_close"]
            or report.get("all_quoted_horizons_have_exact_holder_close") is not (
                counts["exact_official_holder_close"] == 79)
            or report.get("new_provider_calls") != 0
            or report.get("not_a_continuous_adjusted_ohlc_path") is not True
            or report.get("not_intraday_low_or_stop_breach_certification") is not True
            or any(report.get(key) is not False for key in (
                "regular_only_volume_verified",
                "historical_point_in_time_scores_certified",
                "complete_corporate_action_history_certified",
                "performance_validated", "v30b_release_ready"))):
        return False
    return True
