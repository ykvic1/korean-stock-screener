"""Pattern-heavy TECHNICAL 15/100 research adapter for the existing six pillars.

Preserve IAS25, finance20, valuation15, technical15, industry15, momentum10.
This module never substitutes a technical-only 100-point ranking for six-pillar
evidence or generates buy recommendations. Weekly candles must be completed.
"""
from datetime import date
import math

from v3_score_contract import PILLAR_POINTS, research_scorecard

SCHEMA = "v3-pattern-within-six-pillars-research-0.2"
SOURCE = "v3-pattern-first-daily-research-0.1"

# All of these are WITHIN the original 15-point technical pillar.
TECHNICAL_WEIGHTS = {
    "daily_pattern": 3, "weekly_pattern": 4,
    "daily_rsi": 1, "weekly_rsi": 2,
    "weekly_ma120": 3, "volume_confirmation": 2,
}
assert dict(PILLAR_POINTS) == {
    "ias": 25, "financial": 20, "valuation": 15,
    "technical": 15, "industry": 15, "momentum": 10,
}
assert sum(TECHNICAL_WEIGHTS.values()) == PILLAR_POINTS["technical"]

PATTERNS = {
    "bull_flag": {"forming": .72, "breakout_observed": 1.0},
    "double_bottom": {"forming": .70, "breakout_observed": .96},
}
GEOMETRY = {
    "falling_wedge_candidate": .88,
    "inverse_head_shoulders_breakout_candidate": .92,
    "triangle_candidate": .7,
    "post_rise_consolidation_candidate": .65,
}
RISK_MARKERS = (
    "예상 손절폭 과대", "손절선 과도하게 근접", "구조적 손절선 미산출",
    "단기 급등", "장기 추세 취약", "거래대금 부족",
    "급격한 거래량 집중", "52주 고점권",
)


def _num(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _observed(row, timeframe):
    p = row.get("pattern_research" if timeframe == "daily"
                else "pattern_weekly_research")
    if not isinstance(p, dict) or any((
        p.get("schema") != SOURCE,
        p.get("timeframe") != timeframe,
        p.get("status") != "heuristic_unverified",
        p.get("scoring_allowed") is not False,
        p.get("buy_signal") is not False,
        p.get("official_regular_session_verified") is not False,
    )):
        return None
    try:
        asof = date.fromisoformat(row["price_date"])
        source_day = date.fromisoformat(p["asof"])
    except (TypeError, ValueError, KeyError):
        return None
    if timeframe == "daily" and source_day != asof:
        return None
    if timeframe == "weekly" and not (
        p.get("completed_week_only") is True and source_day.weekday() == 4
        and 0 <= (asof - source_day).days <= 13
    ):
        return None
    if not isinstance(p.get("patterns"), list) or len(p["patterns"]) > 5:
        return None
    seen = set()
    for pattern in p["patterns"]:
        if (not isinstance(pattern, dict) or pattern.get("type") not in PATTERNS
            or pattern.get("stage") not in PATTERNS[pattern["type"]]
            or pattern["type"] in seen):
            return None
        seen.add(pattern["type"])
    geometry = p.get("pattern_geometry")
    if not isinstance(geometry, dict) or geometry.get("status") != "heuristic_unvalidated":
        return None
    if any(type(geometry.get(k)) is not bool for k in GEOMETRY):
        return None
    rsi = _num(p.get("rsi14"))
    if rsi is None or not 0 <= rsi <= 100:
        return None
    if (type(p.get("rsi30_cross_recent")) is not bool or
        type(p.get("rsi_bullish_divergence_recent")) is not bool):
        return None
    return p


def _pattern(p, limit):
    candidates = [(limit * PATTERNS[x["type"]][x["stage"]],
                   x["type"], x["stage"]) for x in p["patterns"]]
    candidates += [(limit * value, k, "geometric_candidate")
                   for k, value in GEOMETRY.items() if p["pattern_geometry"][k]]
    # Multiple overlapping patterns on one timeframe are not additive.
    if not candidates:
        return 0.0, None
    best = sorted(candidates, key=lambda x: (-x[0], x[1]))[0]
    return round(best[0], 4), {"type": best[1], "stage": best[2]}


def _weekly_trend(p):
    gap = _num(p.get("weekly_ma120_gap_pct"))
    if gap is None or gap <= -100:
        return None
    if 0 <= gap <= 15:
        return 3
    if -7 <= gap < 0:
        return 2
    if -15 <= gap < -7:
        return 1
    # Very extended or deeply below MA120 is not a favorable trend.
    return 0


def _risks(row):
    reasons = []
    current = _num(row.get("price_latest"))
    stop = _num(row.get("stop_line"))
    pct = _num(row.get("stop_pct"))
    if current is None or current <= 0 or stop is None or not 0 < stop < current:
        reasons.append("structural_stop_unverified")
    elif pct is None or abs(100 * (stop / current - 1) - pct) > 1.5:
        reasons.append("structural_stop_mismatch")
    elif not -15 <= pct <= -3:
        reasons.append("stop_distance_outside_3_to_15")
    recent20, recent60 = _num(row.get("price20")), _num(row.get("price60"))
    if recent20 is None or recent60 is None:
        reasons.append("recent_return_missing")
    elif recent20 > 18 or recent60 > 40:
        reasons.append("price_extension_chase_risk")
    for flag in (row.get("risk_flags") if isinstance(row.get("risk_flags"), list) else []):
        if isinstance(flag, str) and any(marker in flag for marker in RISK_MARKERS):
            reasons.append(flag)
    return list(dict.fromkeys(reasons))


def evaluate(row, *, other_pillars=None):
    """Use only source-observed technical evidence; never fabricate five pillars.

    other_pillars is a future independent candidate from the existing v3
    factor engine, not populated by this module or legacy v2 technical score.
    """
    daily = _observed(row, "daily")
    weekly = _observed(row, "weekly")
    d_pattern, d_label = _pattern(daily, 3) if daily else (None, None)
    w_pattern, w_label = _pattern(weekly, 4) if weekly else (None, None)
    d_rsi = (1 if daily["rsi30_cross_recent"] or
             daily["rsi_bullish_divergence_recent"] else 0) if daily else None
    w_rsi = (2 if weekly["rsi30_cross_recent"] or
             weekly["rsi_bullish_divergence_recent"] else 0) if weekly else None
    ma120 = _weekly_trend(weekly) if weekly else None
    vol = _num(row.get("one_day_volume_ratio"))
    vol_score = (2 if 1.2 <= vol <= 5 else 0) if vol is not None and vol >= 0 else None
    parts = {
        "daily_pattern": d_pattern, "weekly_pattern": w_pattern,
        "daily_rsi": d_rsi, "weekly_rsi": w_rsi,
        "weekly_ma120": ma120, "volume_confirmation": vol_score,
    }
    available = sum(TECHNICAL_WEIGHTS[k] for k, v in parts.items() if v is not None)
    technical = round(sum(parts.values()), 4) if available == 15 else None
    supplied = {} if other_pillars is None else dict(other_pillars)
    if "technical" in supplied:
        raise ValueError("technical_pillar_double_count_forbidden")
    if set(supplied) - (set(PILLAR_POINTS) - {"technical"}):
        raise ValueError("unknown_six_pillar")
    try:
        asof = date.fromisoformat(row["price_date"]).isoformat()
    except (TypeError, ValueError, KeyError):
        raise ValueError("invalid_market_date") from None
    pillars = {**supplied, "technical": technical}
    card = research_scorecard(asof=asof, pillars=pillars)
    reasons = _risks(row)
    # The full-six-pillar candidate is only populated when all six
    # independent values exist, and still never certified as a live ranking.
    full = card["candidate_full_total_research_only"]
    return {
        "schema": SCHEMA, "asof": asof,
        "six_pillar_maximum": dict(PILLAR_POINTS),
        "technical_maximum": 15,
        "technical_weights": dict(TECHNICAL_WEIGHTS),
        "technical_parts": parts,
        "technical_observed_maximum": available,
        "technical_research_candidate": technical,
        "daily_top_pattern": d_label, "weekly_top_pattern": w_label,
        "six_pillar_candidates": {
            k: card["pillars"][k]["candidate_points"] for k in PILLAR_POINTS
        },
        "six_pillar_coverage_pct": card["candidate_coverage_pct"],
        "missing_pillars": card["unscored_pillars"],
        "six_pillar_full_candidate_only": full,
        "provisional_full_six_pillar_sort_eligible": full is not None and not reasons,
        "risk_warnings": reasons,
        "investable_score": None, "confirmed_rank": None,
        "historical_success_probability": None,
        "buy_certified": False, "v3_release_ready": False,
    }


def compute_candidates(rows):
    """Attach technical+six-pillar evidence, retaining legacy order and grade.

    With no verified five external pillars, the provisional six-pillar sort
    correctly stays empty. Do not rank technical 15/15 as if it were 100.
    """
    for row in rows:
        row["v3_pattern_integrated"] = evaluate(row)
    eligible = [row for row in rows
                if row["v3_pattern_integrated"]["provisional_full_six_pillar_sort_eligible"]]
    eligible.sort(key=lambda row: (
        -row["v3_pattern_integrated"]["six_pillar_full_candidate_only"],
        -float(row["v3_pattern_integrated"]["technical_research_candidate"]),
        str(row.get("code", "")),
    ))
    for index, row in enumerate(eligible, 1):
        row["v3_pattern_integrated"]["provisional_rank"] = index
    return eligible
