"""Dated structural-stop reference for V3.0b, with no score or risk tier.

The user removed all stop-loss bonuses/penalties, including research-only
ones. A structural invalidation level is display-only, not a trading gate.
"""
from __future__ import annotations

from datetime import date
import math

SCHEMA = "v3-stop-loss-severity-proxy-research-0.1"


def _finite(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and not isinstance(value, bool))


def _date(value):
    if type(value) is not str:
        return None
    try:
        return date.fromisoformat(value).isoformat() if len(value) == 10 else None
    except ValueError:
        return None


def preview(row, *, asof):
    """Return a dated distance reference, never ranking/bonus/penalty points.

    Missing or stale input is unknown, never zero. Distance uses
    a user/legacy structural stop, NOT estimated stop-hit odds.
    Existing 2 percentage-point client-side distance tolerance is retained.
    """
    if type(row) is not dict or _date(asof) is None:
        raise ValueError("invalid_stop_research_input")
    source_date = _date(row.get("price_date"))
    reasons = []
    if source_date != asof:
        reasons.append("missing_or_stale_price_date")
    price = row.get("price")
    stop = row.get("stop_line")
    reported = row.get("stop_pct")
    if not (_finite(price) and price > 0):
        reasons.append("missing_price")
    if not (_finite(stop) and stop > 0):
        reasons.append("missing_stop_reference")
    distance = None
    if not reasons and stop >= price:
        reasons.append("nonprotective_stop_reference")
    if not reasons:
        distance = 100 * (price - stop) / price
        if (not _finite(reported) or reported >= 0
                or abs(distance + reported) > 2):
            reasons.append("stop_reference_distance_mismatch")

    result = {
        "schema": SCHEMA, "asof": asof, "reference_asof": source_date,
        "interpretation": "arithmetic_loss_distance_proxy_not_stop_probability",
        "source_regular_session_independently_verified": False,
        "corporate_actions_independently_verified": False,
        "historical_stop_hit_probability": None,
        "historical_gap_loss_rate": None,
        "research_adjustment_applied_to_total": False,
        "scoring_authorized": False,
        "stop_distance_pct": round(distance, 4) if distance is not None else None,
        "historical_resistance_room_pct": None,
        "reference_reward_risk_only": None,
        # Legacy CSV column remains blank for existing consumers; no
        # stop-derived adjustment is calculated, even as an offline proxy.
        "research_adjustment_points_not_applied": None,
        "status": "source_unavailable",
        "reason_codes": reasons,
    }
    if reasons:
        return result

    reference = row.get("reference_resistance")
    if _finite(reference) and reference > price:
        room = 100 * (reference - price) / price
        if math.isfinite(room):
            result["historical_resistance_room_pct"] = round(room, 4)
            result["reference_reward_risk_only"] = round(room / distance, 4)

    result.update({
        "status": "reference_only",
        "reason_codes": [],
    })
    return result
