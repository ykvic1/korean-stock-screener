"""Strict OFFLINE research audit for historical listing universe and OHLCV event boundaries.

All inputs are untrusted historical *claims*. A structurally consistent archive
is NOT proof of authentic point-in-time membership, official regular session,
delisting completeness, corporate-action certification, or usable v3 scoring.
Never alter OHLCV, fabricate pre-listing bars, splice histories or issue trades.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date
import hashlib
import json
import math

SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"
MARKETS = frozenset(("KOSPI", "KOSDAQ"))
ACTION_KINDS = frozenset((
    "split", "reverse_split", "merger", "spin_off", "rights_issue",
    "symbol_change", "suspension", "resumption", "delisting"
))
MAX_REGISTRY = 20000
MAX_SNAPSHOTS = 5000
MAX_BARS = 6000
MAX_EVENTS = 250
LARGE_PRICE_JUMP = 0.35
GAP_CALENDAR_DAYS = 10
QUARANTINE_AROUND_BOUNDARY = 2


class IntegrityError(ValueError):
    """Sanitized exception names; do not print raw research payloads."""


def _date(value):
    if type(value) is not str or len(value) != 10:
        raise IntegrityError("invalid_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise IntegrityError("invalid_date") from None
    if parsed.isoformat() != value:
        raise IntegrityError("invalid_date")
    return parsed


def _code(value, digits):
    return (type(value) is str and len(value) == digits
            and value.isascii() and value.isdigit())


def _digest(value):
    return (type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _finite_positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _registry_record(row):
    if not isinstance(row, dict) or not _code(row.get("issuer_id"), 8):
        raise IntegrityError("invalid_issuer_identity")
    if not _code(row.get("code"), 6) or row.get("market") not in MARKETS:
        raise IntegrityError("invalid_symbol_market")
    if row.get("ordinary_share") is not True:
        raise IntegrityError("non_ordinary_share_registry")
    start = _date(row.get("listed_on"))
    end = _date(row["delisted_on"]) if row.get("delisted_on") is not None else None
    if end is not None and end <= start:
        raise IntegrityError("invalid_listing_interval")
    return start, end


def audit_universe(registry, snapshots, *, asof):
    """Compare date-specific claimed rosters to claimed issuer life cycles.

    Requires historical rosters rather than backfilling today's survivors.
    Complete matching among self-reported inputs is *internal* only.
    """
    cutoff = _date(asof)
    if not isinstance(registry, list) or not 0 < len(registry) <= MAX_REGISTRY:
        raise IntegrityError("invalid_registry")
    if not isinstance(snapshots, list) or not 0 < len(snapshots) <= MAX_SNAPSHOTS:
        raise IntegrityError("invalid_snapshots")
    by_issuer, intervals_by_ticker = {}, {}
    for row in registry:
        start, end = _registry_record(row)
        identity = row["issuer_id"]
        if identity in by_issuer:
            raise IntegrityError("issuer_duplicate_or_renamed")
        by_issuer[identity] = (row, start, end)
        intervals_by_ticker.setdefault((row["market"], row["code"]), []).append(
            (start, end, identity))
    reused = []
    for (market, code), series in intervals_by_ticker.items():
        series.sort(key=lambda x: (x[0], x[2]))
        for prev, later in zip(series, series[1:]):
            if prev[1] is None or prev[1] > later[0]:
                raise IntegrityError("concurrent_code_issuer_collision")
        if len(series) > 1:
            reused.append({"market": market, "code": code,
                           "distinct_issuer_count": len(series)})
    days_seen = set()
    daily = []
    included_delisted = set()
    for snap in snapshots:
        if not isinstance(snap, dict):
            raise IntegrityError("invalid_snapshot")
        day = _date(snap.get("asof"))
        if day > cutoff or day in days_seen:
            raise IntegrityError("future_or_duplicate_universe_snapshot")
        days_seen.add(day)
        rows = snap.get("members")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_REGISTRY:
            raise IntegrityError("missing_historical_roster")
        observed, tickers = set(), set()
        for member in rows:
            if not isinstance(member, dict):
                raise IntegrityError("invalid_roster_member")
            issuer = member.get("issuer_id")
            if issuer not in by_issuer or issuer in observed:
                raise IntegrityError("unknown_or_duplicate_issuer")
            reference, start, end = by_issuer[issuer]
            if member.get("code") != reference["code"] or member.get("market") != reference["market"]:
                raise IntegrityError("roster_symbol_identity_mismatch")
            if not (start <= day and (end is None or day < end)):
                raise IntegrityError("prelisting_or_postdelisting_member")
            ticker = (reference["market"], reference["code"])
            if ticker in tickers:
                raise IntegrityError("duplicate_ticker_same_day")
            tickers.add(ticker)
            observed.add(issuer)
            if end is not None and end <= cutoff:
                included_delisted.add(issuer)
        active_registry = {
            issuer for issuer, (_, start, end) in by_issuer.items()
            if start <= day and (end is None or day < end)
        }
        missing = active_registry - observed
        if missing:
            # A falsely 'complete' declared registry must never silently
            # filter symbols out of historical backtests.
            raise IntegrityError("active_registry_member_missing_from_roster")
        daily.append({"asof": day.isoformat(), "member_count": len(observed),
                      "includes_later_delisted_count": sum(
                          by_issuer[x][2] is not None and by_issuer[x][2] <= cutoff
                          for x in observed)})
    daily.sort(key=lambda r: r["asof"])
    canonical = {
        "registry": sorted(registry, key=lambda x: x["issuer_id"]),
        "snapshots": sorted(snapshots, key=lambda x: x["asof"])
    }
    raw_hash = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "schema": "v3-universe-internal-integrity-0.1",
        "asof": cutoff.isoformat(),
        "declared_registry_issuers": len(by_issuer),
        "date_snapshots": daily,
        "distinct_later_delisted_issuers_in_rosters": len(included_delisted),
        "ticker_reuse_observed": reused,
        "identity_grouping": "issuer_id_not_code",
        "input_canonical_sha256_for_change_detection": raw_hash,
        "internal_lifecycle_consistent": True,
        "historical_archives_authenticated": False,
        "all_delisted_issuers_covered_independently": False,
        "point_in_time_universe_certified": False,
        "survivorship_bias_resolved": False,
        "usable_for_v3_backtest": False,
        "confirmed_v3_score": None,
    }


def audit_price_events(record, *, asof):
    """Flag rather than 'fix' adjusted-price jumps, event days or suspensions.

    Event/source SHA strings are syntax-checked only, NOT trusted confirmations.
    Quarantines +/- two OBSERVED rows at a boundary: not true exchange sessions
    unless independently reconciled with a historical trading calendar.
    """
    cutoff = _date(asof)
    if not isinstance(record, dict):
        raise IntegrityError("invalid_price_record")
    if not _code(record.get("issuer_id"), 8) or not _code(record.get("code"), 6):
        raise IntegrityError("invalid_price_identity")
    if record.get("market") not in MARKETS:
        raise IntegrityError("invalid_market")
    listed = _date(record.get("listed_on"))
    delisted = _date(record["delisted_on"]) if record.get("delisted_on") else None
    if delisted is not None and delisted <= listed:
        raise IntegrityError("invalid_listing_interval")
    bars = record.get("bars")
    events = record.get("events")
    if not isinstance(bars, list) or not 0 < len(bars) <= MAX_BARS:
        raise IntegrityError("invalid_price_bars")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise IntegrityError("invalid_action_events")
    times = []
    last = None
    halted_indices = set()
    for i, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise IntegrityError("invalid_bar")
        d = _date(bar.get("date"))
        if d > cutoff or d < listed or (delisted is not None and d >= delisted):
            raise IntegrityError("out_of_listing_lifetime_bar")
        if last is not None and d <= last:
            raise IntegrityError("duplicate_or_unordered_bars")
        last = d
        if bar.get("session") != SESSION:
            raise IntegrityError("invalid_session_label")
        if not all(_finite_positive(bar.get(f)) for f in ("open", "high", "low", "close")):
            raise IntegrityError("invalid_ohlc")
        if bar["high"] < max(bar["open"], bar["close"]) or (
                bar["low"] > min(bar["open"], bar["close"]) or bar["low"] > bar["high"]):
            raise IntegrityError("ohlc_out_of_range")
        v = bar.get("volume")
        if type(v) is not int or v < 0:
            raise IntegrityError("invalid_volume")
        if bar.get("trading_status") not in ("active", "halted"):
            raise IntegrityError("unknown_trading_status")
        if bar["trading_status"] == "halted":
            if v != 0:
                raise IntegrityError("halted_nonzero_volume")
            halted_indices.add(i)
        times.append(d)
    seen_events, event_boundaries = set(), set()
    reasons = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("kind") not in ACTION_KINDS:
            raise IntegrityError("unsupported_or_invalid_action")
        d = _date(ev.get("effective_date"))
        if d > cutoff or d < listed or (delisted is not None and d > delisted):
            raise IntegrityError("event_outside_valid_period")
        if not _digest(ev.get("source_sha256")):
            raise IntegrityError("invalid_event_digest")
        key = (d, ev["kind"])
        if key in seen_events:
            raise IntegrityError("duplicate_action_event")
        seen_events.add(key)
        idx = bisect_left(times, d)
        if idx == len(bars):
            idx = len(bars) - 1
        event_boundaries.add(idx)
        reasons.setdefault(idx, set()).add("declared_" + ev["kind"])
    candidates = []
    calendar_gaps = []
    for i in range(1, len(bars)):
        prev, bar = bars[i - 1], bars[i]
        change = bar["open"] / prev["close"] - 1
        if abs(change) > LARGE_PRICE_JUMP:
            candidates.append({"date": times[i].isoformat(),
                               "absolute_open_gap_pct": round(abs(change) * 100, 3),
                               "reason": "large_price_move_not_verified_corporate_action"})
            reasons.setdefault(i, set()).add("large_unexplained_price_jump")
        days = (times[i] - times[i - 1]).days
        if days > GAP_CALENDAR_DAYS:
            calendar_gaps.append({"previous_date": times[i - 1].isoformat(),
                                  "next_date": times[i].isoformat(), "calendar_days": days})
            reasons.setdefault(i, set()).add("unverified_calendar_gap")
    for idx in halted_indices:
        reasons.setdefault(idx, set()).add("halted_observation")
        # The following resumed row requires verification of actual session.
        if idx + 1 < len(bars):
            reasons.setdefault(idx + 1, set()).add("after_halt")
    boundary_indices = sorted(set(reasons) | event_boundaries)
    excluded = set()
    for idx in boundary_indices:
        for j in range(max(0, idx - QUARANTINE_AROUND_BOUNDARY),
                       min(len(bars), idx + QUARANTINE_AROUND_BOUNDARY + 1)):
            excluded.add(j)
    # Split segments at every flagged boundary WITHOUT adjusting or stitching.
    edges = [0] + [i for i in boundary_indices if 0 < i < len(bars)] + [len(bars)]
    edges = sorted(set(edges))
    segments = []
    for a, b in zip(edges, edges[1:]):
        if b > a:
            segments.append({"start": times[a].isoformat(),
                             "end": times[b - 1].isoformat(), "observed_rows": b - a,
                             "unquarantined_rows": sum(j not in excluded for j in range(a, b))})
    digest = hashlib.sha256(json.dumps(
        bars, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "schema": "v3-price-corporate-action-triage-0.1",
        "asof": cutoff.isoformat(),
        "issuer_id": record["issuer_id"], "code": record["code"],
        "observed_bars": len(bars),
        "declared_actions": len(events),
        "large_move_candidates": candidates,
        "unverified_calendar_gap_candidates": calendar_gaps,
        "boundary_flags": [
            {"date": times[i].isoformat(), "reasons": sorted(reasons[i])}
            for i in boundary_indices
        ],
        "excluded_signal_dates": [times[j].isoformat() for j in sorted(excluded)],
        "segments_not_spliced": segments,
        "input_bar_sha256_for_change_detection": digest,
        "adjusted_or_filled_prices": None,
        "split_ratio_inferred_from_price": False,
        "official_corporate_events_verified": False,
        "regular_session_price_and_volume_verified": False,
        "full_trading_calendar_verified": False,
        "full_history_verified": False,
        "usable_for_v3_backtest": False,
        "confirmed_v3_score": None,
    }
