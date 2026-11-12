"""Display-only daily zero-volume state. Never certifies an exchange halt.

The current market catalog has one KRX-MARCAP listing volume reference per
issuer. A zero reference is shown as a suspension candidate in the screener,
following the user's requested display convention. The exchange halt reason,
09:00–15:30 session certification, and historical suspension start are NOT
inferred. The active roster and every score denominator remain unchanged.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

SCHEMA = "v3-trading-status-display-v1"
CATALOG_SCHEMA = "v22-current-listed-catalog-0.1"
OUTPUT = "v3-trading-status.json"


def _day(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid_market_day")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("invalid_market_day")
    return parsed


def _adjacent_trading_weekdays(previous: date, current: date) -> bool:
    """Conservative calendar check; unknown Korean holidays break the streak."""
    if current.weekday() >= 5 or previous.weekday() >= 5:
        return False
    return (current - previous == timedelta(days=1) and previous.weekday() < 4 or
            current - previous == timedelta(days=3) and previous.weekday() == 4 and current.weekday() == 0)


def _source_state(row):
    zero = row.get("listing_zero_volume_reference")
    if zero is True:
        return "zero_volume"
    if zero is False:
        return "trade_observed"
    return "volume_unknown"


def build(catalog: dict, previous: dict | None = None) -> dict:
    if not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA:
        raise ValueError("untrusted_current_catalog")
    today = _day(catalog.get("date"))
    rows = catalog.get("rows")
    total = catalog.get("listed_total")
    if (type(total) is not int or total < 1 or total > 10000 or
            not isinstance(rows, list) or len(rows) != total):
        raise ValueError("incomplete_current_roster")
    identity_rows = []
    for row in rows:
        if isinstance(row, dict):
            identity_rows.append((row.get("code"), row.get("name"), row.get("market")))
    # A changed issuer/market roster makes prior positive-volume evidence
    # unsuitable for starting a new short-halt streak by code alone.
    identity_hash = sha256(json.dumps(sorted(identity_rows), ensure_ascii=False,
                                     separators=(",", ":")).encode("utf8")).hexdigest() if len(identity_rows) == total and all(
                                         type(item) is str for triplet in identity_rows
                                         for item in triplet) else None
    prior = {}
    adjacent = False
    same_day = False
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("schema") != SCHEMA or
                type(previous.get("listed_total")) is not int or
                previous.get("default_state") != "trade_observed" or
                not isinstance(previous.get("rows"), dict) or
                len(previous["rows"]) != previous.get("counts", {}).get("zero_volume", 0) +
                                         previous.get("counts", {}).get("volume_unknown", 0)):
            raise ValueError("untrusted_previous_status")
        prior_day = _day(previous.get("asof"))
        if prior_day > today:
            raise ValueError("future_previous_status")
        matching_roster = identity_hash is not None and previous.get("roster_sha256") == identity_hash
        adjacent = matching_roster and _adjacent_trading_weekdays(prior_day, today)
        same_day = matching_roster and prior_day == today
        prior = previous["rows"]
    result = {}
    seen = set()
    counts = {"zero_volume": 0, "trade_observed": 0, "volume_unknown": 0,
              "short_halt_1_to_2_observed_sessions": 0, "extended_halt_3_plus_observed_sessions": 0,
              "unknown_halt_duration": 0}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_catalog_issuer")
        code, name, market = row.get("code"), row.get("name"), row.get("market")
        zero = row.get("listing_zero_volume_reference")
        if (type(code) is not str or not re.fullmatch(r"[0-9A-Z]{6}", code) or
                code in seen or type(name) is not str or not name.strip() or
                market not in ("KOSPI", "KOSDAQ") or zero is not None and type(zero) is not bool):
            raise ValueError("invalid_catalog_issuer")
        seen.add(code)
        state = _source_state(row)
        counts[state] += 1
        past = prior.get(code)
        if past is None and previous is not None:
            past = {"name": name, "market": market, "state": "trade_observed"}
        streak = None
        if state == "zero_volume" and isinstance(past, dict) and (
                past.get("name") == name and past.get("market") == market):
            if same_day and past.get("state") == state:
                count = past.get("observed_zero_streak")
                streak = count if type(count) is int and count >= 1 else None
            elif adjacent:
                if past.get("state") == "trade_observed":
                    streak = 1
                elif past.get("state") == "zero_volume":
                    count = past.get("observed_zero_streak")
                    if type(count) is int and count >= 1:
                        streak = count + 1
        duration = ("short_1_to_2" if streak is not None and streak <= 2 else
                    "extended_3_plus" if streak is not None else "unknown")
        if state == "zero_volume":
            counts[{"short_1_to_2": "short_halt_1_to_2_observed_sessions",
                    "extended_3_plus": "extended_halt_3_plus_observed_sessions",
                    "unknown": "unknown_halt_duration"}[duration]] += 1
        # Sparse status records keep the daily public payload small. A code
        # absent from this dictionary explicitly means positive volume today.
        if state != "trade_observed":
            result[code] = {"name": name, "market": market,
                            "state": state, "observed_zero_streak": streak,
                            "duration": duration if state == "zero_volume" else None,
                            "reason": None, "official_reason_verified": False}
    if sum(counts[k] for k in ("zero_volume", "trade_observed", "volume_unknown")) != total:
        raise ValueError("status_count_mismatch")
    return {"schema": SCHEMA, "asof": catalog["date"], "listed_total": total,
            "default_state": "trade_observed",
            "roster_sha256": identity_hash,
            "rows": result, "counts": counts,
            "source_scope": "FDR_KRX_MARCAP_LISTING_REFERENCE_NOT_SESSION_CERTIFIED",
            "krx_regular_session_verified": False,
            "zero_volume_display_convention": "user_requested_suspension_not_exchange_confirmed",
            "duration_requires_observed_positive_to_zero_transition": True,
            "missing_sessions_reset_duration_to_unknown": True,
            "score_points_added": 0}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("write", "verify"), default="verify")
    ap.add_argument("--docs", type=Path, default=Path(__file__).resolve().parent / "docs")
    args = ap.parse_args(argv)
    catalog = json.loads((args.docs / "v22-market-catalog.json").read_text(encoding="utf8"))
    target = args.docs / OUTPUT
    previous = json.loads(target.read_text(encoding="utf8")) if target.is_file() else None
    report = build(catalog, previous)
    content = json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    if args.mode == "write":
        tmp = target.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf8")
        tmp.replace(target)
    elif previous != report:
        raise ValueError("trading_status_not_current")
    print(json.dumps({"asof": report["asof"], "counts": report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
