"""Reconcile the saved 2025-11-13 delisting pilot; never certify performance."""
import csv
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path

from v3_forward_horizon_readiness import HORIZONS, _anniversary

ROOT = Path(__file__).resolve().parent
SIGNAL = date(2025, 11, 13)
ENTRY = date(2025, 11, 14)
TAG = "20251113"
UNPRICED = "awaiting_settlement_unpriced"
CASH = {"cash_delivery": "reported_completed", "scheduled_cash_entitlement": "announced"}
LIQUIDATION = {"liquidation_close_scenario", "last_liquidation_close_then_cash"}
TOUCH_STATES = ("touch_observed", "no_touch_observed", "no_touch_observed_partial_path")
CLOSE = {"listed_close", "adjusted_listed_close"}
TOUCH = "touch_observed"


class PilotAuditError(ValueError):
    """A fixed diagnostic for the read-only watch."""


def require(condition, diagnostic):
    if not condition:
        raise PilotAuditError(diagnostic)


def day(value):
    parsed = date.fromisoformat(value)
    require(parsed.isoformat() == value, "invalid_session")
    return parsed


def positive(value):
    number = Decimal(value)
    require(number.is_finite() and number > 0, "invalid_positive_value")
    return number


def check_price(price, settlement, last_trade, adjustments, valuation):
    method = price["method"]
    code = price["code"]
    observed_code = price["observed_code"]
    treatment = settlement["treatment"]
    event = settlement["event_on"]
    available = settlement["settlement_or_share_availability_on"]

    if method == UNPRICED:
        # Trading can halt before the exchange takes legal effect.
        require(
            treatment == "stock_exchange" and day(last_trade) < valuation < day(available)
            and not any(price[field] for field in (
                "observation_on", "observed_code", "units_per_entry_share", "quote_or_cash_krw",
                "value_per_entry_share_krw", "price_change_pct",
            )), "invalid_unpriced_settlement",
        )
        return

    observed = day(price["observation_on"])
    units = positive(price["units_per_entry_share"])
    quote = positive(price["quote_or_cash_krw"])
    value = units * quote
    price_change = (value / positive(price["entry_open_krw"]) - 1) * 100
    require(ENTRY <= observed <= valuation, "observation_outside_horizon")
    require(abs(positive(price["value_per_entry_share_krw"]) - value) <= Decimal("0.00005")
            and abs(Decimal(price["price_change_pct"]) - price_change) <= Decimal("0.005"),
            "value_or_return_mismatch")

    def holding_units(symbol, after, through):
        result = Decimal(1)
        for effective, factor in adjustments.get(symbol, []):
            if after < effective <= through:
                result *= factor
        return result

    if method in CLOSE:
        expected = holding_units(code, ENTRY, observed)
        require(observed == valuation and observed_code == code
                and (not event or observed < day(event))
                and observed < day(settlement["kind_delisted_on"])
                and units == expected and (method == "listed_close") == (units == 1),
                "listed_close_mismatch")
    elif method == "stock_delivery_mark":
        require(treatment == "stock_exchange" and observed_code == settlement["successor_code"]
                and observed == valuation and day(event) <= day(available) <= observed,
                "stock_delivery_date_or_identity_mismatch")
        expected = (holding_units(code, ENTRY, day(event))
                    * positive(settlement["shares_per_old_share"])
                    * holding_units(observed_code, day(event), observed))
        require(units == expected, "stock_delivery_ratio_mismatch")
    elif method in CASH:
        require(treatment == "cash_exchange" and not observed_code
                and observed == day(available) and day(event) <= observed
                and units == holding_units(code, ENTRY, day(event))
                and quote == positive(settlement["cash_krw_per_old_share"])
                and settlement["settlement_date_evidence"] == CASH[method],
                "cash_payment_evidence_mismatch")
    elif method in LIQUIDATION:
        require(treatment == "liquidation_trading" and observed_code == code
                and observed < day(settlement["kind_delisted_on"]) <= valuation
                and price["observation_on"] == last_trade == settlement["liquidation_last_on"]
                and units == holding_units(code, ENTRY, observed), "liquidation_scenario_mismatch")
    else:
        raise PilotAuditError("unknown_price_method")


def check_path(path, reference, valuation, previous):
    eligible, bars, gaps = (int(path[field]) for field in (
        "eligible_sessions", "observed_trade_bars", "unpriced_sessions",
    ))
    require(bars > 0 and gaps >= 0 and bars + gaps == eligible
            and bars >= previous[0] and gaps >= previous[1], "invalid_path_session_counts")
    first_touch = reference["first_observed_touch_on"]
    observed_touch = first_touch and day(first_touch) <= valuation
    status = (TOUCH if observed_touch else
              "no_touch_observed_partial_path" if gaps else "no_touch_observed")
    require(path["touch_status"] == status
            and path["first_observed_touch_on"] == (first_touch if observed_touch else ""),
            "reference_touch_mismatch")

    adverse = Decimal(path["maximum_adverse_excursion_observed_pct"])
    favorable = Decimal(path["maximum_favorable_excursion_observed_pct"])
    require(0 <= adverse < 100 and adverse >= previous[2] and favorable >= previous[3]
            and ENTRY <= day(path["adverse_extreme_on"]) <= valuation
            and ENTRY <= day(path["favorable_extreme_on"]) <= valuation, "invalid_path_extremes")
    return bars, gaps, adverse, favorable


def audit(root=ROOT):
    hashes = {}

    def load(ledger, *keys):
        raw = (root / "ops" / f"v3-performance-{ledger}-{TAG}.csv").read_bytes()
        hashes[ledger] = sha256(raw).hexdigest()
        reader = csv.DictReader(StringIO(raw.decode("utf-8"), newline=""), strict=True)
        rows = {}
        for row in reader:
            require(None not in row and None not in row.values(), "invalid_pilot_csv_row")
            key = tuple(row[field] for field in keys) if len(keys) > 1 else row[keys[0]]
            require(key not in rows, "duplicate_pilot_key")
            rows[key] = row
        require(bool(rows), "empty_pilot_ledger")
        return rows

    references = load("downside-reference", "code")
    prices = load("price-replay", "code", "horizon_months")
    paths = load("downside-horizons", "code", "horizon_months")
    roster = load("delistings", "code")
    trades = load("delisting-trade-observations", "code")
    settlements = load("delisting-settlement", "code")
    adjustments = defaultdict(list)
    for (code, listing_on), row in load("share-adjustments", "code", "listing_on").items():
        require(row["source_url"].startswith("https://") and ENTRY < day(listing_on),
                "invalid_share_adjustment_evidence")
        adjustments[code].append((day(listing_on), positive(row["units_multiplier"])))

    members = {code for code, row in roster.items() if row["anchor_membership"] == "present"}
    require(references.keys() <= members and settlements.keys() <= members
            and trades.keys() == members, "pilot_membership_mismatch")
    expected_keys = {(code, str(months)) for code in references for months in HORIZONS}
    require(prices.keys() == paths.keys() == expected_keys, "pilot_horizon_join_mismatch")
    for code, settlement in settlements.items():
        require(settlement["kind_delisted_on"] == roster[code]["delisted_on"]
                and settlement["market"] == roster[code]["market"]
                and settlement["term_source_url"].startswith("https://"),
                "settlement_roster_mismatch")
        if settlement["treatment"] == "liquidation_trading":
            start = day(settlement["liquidation_first_on"])
            end = day(settlement["liquidation_last_on"])
            require(start <= end < day(settlement["kind_delisted_on"])
                    and trades[code]["last_positive_volume_candidate"] == end.isoformat(),
                    "liquidation_notice_date_mismatch")

    horizons = {str(months): Counter() for months in HORIZONS}
    methods = Counter()
    valuations = {}
    for code, reference in references.items():
        entry = positive(reference["entry_open_krw"])
        line = positive(reference["reference_line_krw"])
        require(day(reference["signal_session"]) == SIGNAL and day(reference["entry_session"]) == ENTRY
                and trades[code]["next_session_status"] == "traded", "invalid_pilot_entry")
        previous = (0, 0, Decimal(0), Decimal(0))
        previous_valuation = ENTRY
        for months in HORIZONS:
            horizon = str(months)
            price, path = prices[code, horizon], paths[code, horizon]
            valuation = day(price["valuation_session"])
            anniversary = _anniversary(SIGNAL, months)
            require(day(price["anniversary"]) == anniversary <= valuation
                    and valuation > previous_valuation
                    and valuations.setdefault(horizon, valuation) == valuation,
                    "pilot_valuation_session_mismatch")
            require(price["name"] == path["name"] == reference["name"]
                    and day(price["entry_session"]) == ENTRY and positive(price["entry_open_krw"]) == entry
                    and path["valuation_session"] == price["valuation_session"]
                    and path["terminal_price_status"] == price["method"], "pilot_identity_or_path_mismatch")
            check_price(price, settlements[code], trades[code]["last_positive_volume_candidate"],
                        adjustments, valuation)
            previous = check_path(path, reference, valuation, previous)
            previous_valuation = valuation
            methods[price["method"]] += 1
            counts = horizons[horizon]
            counts["with_value"] += price["method"] != UNPRICED
            counts["unpriced_valuations"] += price["method"] == UNPRICED
            counts["scheduled_cash_entitlements"] += price["method"] == "scheduled_cash_entitlement"
            counts["liquidation_scenarios"] += price["method"] in LIQUIDATION
            counts["paths_with_unpriced_sessions"] += previous[1] > 0
            counts[path["touch_status"]] += 1

        low = positive(reference["lowest_observed_value_krw"])
        require(ENTRY <= day(reference["lowest_observed_value_on"]) <= previous_valuation
                and abs((1 - low / entry) * 100 - previous[2]) <= Decimal("0.000051")
                and reference["lowest_observed_value_on"] == path["adverse_extreme_on"],
                "reference_low_mismatch")
        first_touch = reference["first_observed_touch_on"]
        require(bool(first_touch) == (low <= line), "reference_touch_threshold_mismatch")
        if first_touch:
            require(ENTRY <= day(first_touch) <= previous_valuation
                    and low <= positive(reference["first_touch_low_value_krw"]) <= line,
                    "reference_first_touch_invalid")

    non_spac = {code for code in members if roster[code]["kind_spac_name"] == "false"}
    non_spac_entry_traded = {code for code in non_spac if trades[code]["next_session_status"] == "traded"}
    return {
        "schema": "v3-historical-delisting-pilot-audit-1",
        "signal_session": SIGNAL.isoformat(),
        "pilot_issuers": len(references),
        "pilot_horizon_rows": len(prices),
        "delisted_anchor_issuers": len(members),
        "delisted_anchor_non_spac": len(non_spac),
        "next_session_traded_non_spac": len(non_spac_entry_traded),
        "next_session_traded_non_spac_outside_pilot": len(non_spac_entry_traded - references.keys()),
        "settlement_register_issuers": len(settlements),
        "liquidation_notice_issuers": sum(
            row["treatment"] == "liquidation_trading" for row in settlements.values()
        ),
        "unresolved_distribution_register_issuers": sum(
            row["treatment"] == "distribution_unresolved" for row in settlements.values()
        ),
        "methods": dict(methods),
        "horizons": {h: {**dict.fromkeys(TOUCH_STATES, 0), **counts}
                     for h, counts in horizons.items()},
        "ledger_sha256": hashes,
        "source_prices_independently_verified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
    }


def main():
    try:
        report = audit()
    except PilotAuditError as exc:
        raise SystemExit(f"Historical pilot audit failed: {exc}") from None
    except (OSError, UnicodeError, csv.Error, InvalidOperation, ValueError, KeyError):
        raise SystemExit("Historical pilot audit failed: unreadable or malformed evidence") from None
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
