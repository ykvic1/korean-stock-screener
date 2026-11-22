"""One-time historical first-reference-touch date: independently compare exact KRX OHLC.

This checks the stored first-touch day (not the claim that it was the first
chronological breach) against official original/successor security quotes and
notice-derived holding ratios. No trade signals or adjusted paths certified.
No underlying market prices, tickers or issuer identities in public output.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from v3_forward_entry import read_official_openings
from v3_forward_daily_prices import daily_row
from v3_historical_market_coverage import (
    ENTRY, ROOT, ledger_rows, require, traded,
)
from v3_historical_pilot_official_prices import (
    compare_market, compare_turnover, exact_traded_extreme_symbols,
    exact_market_prices, downside_extreme_trading_symbols,
)
from v3_historical_prior_direct_low_evidence import build as validate_prior
from v3_historical_downside_official_ohlc_evidence import EVIDENCE as BASELINE_FILE
from v3_historical_extreme_direct_low_evidence import EVIDENCE as LATER_FILE
from v3_historical_prior_direct_low_evidence import EVIDENCE as PRIOR_FILE
import json

SCHEMA = "v3-historical-reference-touch-official-low-v1"
UNIT = Decimal("0.0001")
CODE = re.compile(r"[0-9A-Z]{6}\Z")


def _money(raw):
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_frozen_reference_money") from None
    require(value.is_finite() and value > 0, "invalid_frozen_reference_money")
    return value


def pending_first_touch_targets(root=ROOT):
    """Select only 11 security/day pairs not already directly LOW-checked.

    Original company to successor mapping comes from frozen settlement notices,
    NOT a best-guess ticker mapping; previous 35-day exact-security proof is
    required before excluding earlier 3 already-verified touch records.
    """
    if root == ROOT:
        with BASELINE_FILE.open(encoding="utf-8") as f:
            base = json.load(f)
        with LATER_FILE.open(encoding="utf-8") as f:
            later = json.load(f)
        with PRIOR_FILE.open(encoding="utf-8") as f:
            prior = json.load(f)
        require(validate_prior(prior, base, later)[
                    "all_85_horizon_extreme_dates_directly_checked"] == 85,
                "prior_extreme_low_proof_missing")
    reference = ledger_rows(root, "downside-reference")
    traded_rows = {r["code"]: r for r in ledger_rows(root, "delisting-trade-observations")}
    settlement = {r["code"]: r for r in ledger_rows(root, "delisting-settlement")}
    require(len(reference) == 17 and len({r["code"] for r in reference}) == 17
            and {r["code"] for r in reference} <= traded_rows.keys()
            and {r["code"] for r in reference} <= settlement.keys(),
            "invalid_first_touch_reference_roster")
    extreme = downside_extreme_trading_symbols(root)
    touch = [r for r in reference if r.get("first_observed_touch_on")]
    require(len(touch) == 14, "invalid_frozen_first_touch_count")
    target = {}
    known_pairs = 0
    for r in touch:
        origin = r["code"]
        last = traded_rows[origin]["last_positive_volume_candidate"]
        on = r["first_observed_touch_on"]
        symbol = r.get("first_touch_code")
        require(re.fullmatch(CODE, symbol or "") and r.get("first_touch_type") == "intraday_low"
                and ENTRY.isoformat() <= on <= "2026-11-13",
                "invalid_first_touch_day_or_identity")
        if on <= last:
            require(symbol == origin, "wrong_original_touch_identity")
            ratio = Decimal(1)
        else:
            s = settlement[origin]
            require(symbol == s.get("successor_code")
                    and s.get("settlement_or_share_availability_on")
                    and on >= s["settlement_or_share_availability_on"],
                    "unlinked_successor_touch")
            ratio = _money(s.get("shares_per_old_share"))
        opening = _money(r.get("first_touch_open_value_krw"))
        low = _money(r.get("first_touch_low_value_krw"))
        reference_line = _money(r.get("reference_line_krw"))
        require(low <= reference_line and low <= opening,
                "stored_first_touch_does_not_breach_reference")
        if symbol in extreme.get(__import__("datetime").date.fromisoformat(on), ()):
            known_pairs += 1
            continue
        target.setdefault(on, {})[symbol] = (ratio, opening, low)
    require(known_pairs == 3 and len(target) == 10
            and sum(map(len, target.values())) == 11,
            "unexpected_reference_touch_direct_low_gap")
    return dict(sorted(target.items()))


def audit_saved_first_touch_date(public, public_turnover, raw, fingerprints, touches):
    """Verify one KRX response in-memory. Result is anonymized aggregate only."""
    require(type(touches) is dict and touches, "empty_touch_date_scope")
    official = {code: daily_row(market, item)
                for code, (market, item) in raw.items()}
    market = compare_market(public, official, public.keys() | official.keys())
    selected = compare_market(public, official, touches)
    require(exact_market_prices(market) and exact_traded_extreme_symbols(selected),
            "official_touch_security_ohlc_or_identity_mismatch")
    turnover = compare_turnover(public_turnover, raw)
    require(turnover["scope"] == market["scope"]
            and turnover["identity_mismatch"] == 0
            and all(turnover["fields"][field]["compared"] == market["scope"]
                    and turnover["fields"][field]["matched"] == market["scope"]
                    and turnover["fields"][field]["mismatched"] == 0
                    and turnover["fields"][field]["public_unavailable"] == 0
                    and turnover["fields"][field]["official_unavailable"] == 0
                    for field in ("volume", "amount")),
            "official_touch_date_turnover_mismatch")
    require(type(fingerprints) is dict and set(fingerprints) == {"KOSPI", "KOSDAQ"}
            and all(type(f) is str and re.fullmatch(r"[0-9a-f]{64}", f)
                    for f in fingerprints.values()),
            "missing_official_reference_touch_source_hash")
    verified = 0
    for symbol, (ratio, opening, low) in touches.items():
        quote = official[symbol]
        require(traded(public[symbol]) and quote[2] == "traded",
                "no_actual_trade_on_reference_touch_day")
        actual_open = (Decimal(str(quote[3])) * ratio).quantize(UNIT, rounding=ROUND_HALF_UP)
        actual_low = (Decimal(str(quote[5])) * ratio).quantize(UNIT, rounding=ROUND_HALF_UP)
        require(actual_open == opening.quantize(UNIT, rounding=ROUND_HALF_UP)
                and actual_low == low.quantize(UNIT, rounding=ROUND_HALF_UP),
                "historical_reference_holder_value_not_reproduced")
        verified += 1
    return {
        "schema": SCHEMA,
        "official_source_sha256": fingerprints,
        "market_wide": market,
        "turnover": turnover,
        "actual_touch_security_ohlc": selected,
        "touch_holder_open_low_exact": verified,
        "reported_touch_security_days": len(touches),
        "first_touch_is_earliest_trade_proven": False,
        "pre_touch_complete_adjusted_path_verified": False,
        "regular_only_volume_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def compare_bounded_batch(public_panels, turnovers, client, dates, targets):
    """At most 5 official dates (2 market API reads per date)."""
    require(1 <= len(dates) <= 5 and len(set(dates)) == len(dates)
            and all(date in targets for date in dates),
            "invalid_reference_touch_batch")
    result, seen = {}, set()
    for on in dates:
        raw, hashes = read_official_openings(client, on)
        checked = audit_saved_first_touch_date(
            public_panels[__import__("datetime").date.fromisoformat(on)],
            turnovers[__import__("datetime").date.fromisoformat(on)],
            raw, hashes, targets[on])
        for digest in hashes.values():
            require(digest not in seen, "replayed_official_touch_source")
            seen.add(digest)
        result[on] = checked
    return {
        "schema": SCHEMA,
        "requested_sessions": list(dates),
        "sessions": result,
        "target_security_days": sum(map(len, (targets[d] for d in dates))),
        "verified_security_days": sum(x["touch_holder_open_low_exact"] for x in result.values()),
        "maximum_official_api_requests": 2 * len(dates),
        "first_touch_is_earliest_trade_proven": False,
        "pre_touch_complete_adjusted_path_verified": False,
        "regular_only_volume_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }
