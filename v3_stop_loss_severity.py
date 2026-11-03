"""Dated structural-stop loss-SEVERITY proxy for V3.0b research only.

A nearby stop is not proof of low stop-out PROBABILITY. No investment score,
rank, risk budget, or trading decision may consume this provisional adjustment.
Historical walk-forward bands must be independently PIT/corporate-action
validated before any live bonus/penalty is authorized.
"""
from __future__ import annotations

from datetime import date
import math

SCHEMA = "v3-stop-loss-severity-proxy-research-0.1"
MAX_ABSOLUTE_RESEARCH_ADJUSTMENT = 5.0


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
    """Return a non-additive risk *research* field, never live-score points.

    Missing or stale input is unknown, never zero. Downside severity uses
    distance to a user/legacy structural stop, NOT estimated stop-hit odds.
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

    flags = row.get("risk_flags")
    flags_clear = type(flags) is list and not any(
        type(value) is str and value.strip() for value in flags)
    price20, price60 = row.get("price20"), row.get("price60")
    not_extended = (_finite(price20) and _finite(price60)
                    and price20 <= 18 and price60 <= 40)
    # Narrow stops are prone to ordinary noise; large distance increases
    # loss if the stop actually executes. These are heuristic severity
    # bands, not empirically calibrated trigger probabilities.
    if distance < 3:
        adjustment = -3.0
        category = "too_close_to_market_noise"
    elif distance <= 8:
        if (result["reference_reward_risk_only"] is not None
                and result["reference_reward_risk_only"] >= 2
                and flags_clear and not_extended):
            adjustment = 3 * (8 - distance) / 5
            category = "bounded_downside_and_reference_room"
        else:
            adjustment = 0.0
            category = "low_distance_but_unconfirmed_setup"
    elif distance <= 12:
        adjustment = -(distance - 8) / 4
        category = "medium_loss_distance"
    elif distance <= 15:
        adjustment = -1 - 2 * (distance - 12) / 3
        category = "elevated_loss_distance"
    else:
        adjustment = -3 - min(2, (distance - 15) / 5)
        category = "wide_structural_loss_distance"
    result.update({
        "status": category,
        "reason_codes": [],
        "research_adjustment_points_not_applied": round(
            max(-MAX_ABSOLUTE_RESEARCH_ADJUSTMENT,
                min(MAX_ABSOLUTE_RESEARCH_ADJUSTMENT, adjustment)), 4),
    })
    return result
