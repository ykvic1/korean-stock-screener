"""Pattern-first, loss-gated, source-observed 100-point RESEARCH ranking.

This is NOT the existing v2 observed score or the unreleased six-pillar v3
official score. It cannot certify a buy, forecast a return or infer investors.
Missing components are None, never zero or renormalized into a full score.
"""
import math
from datetime import date

SCHEMA = "v3-pattern-first-composite-research-0.1"
SOURCE = "v3-pattern-first-daily-research-0.1"
WEIGHTS = {
    "daily_pattern": 25, "weekly_pattern": 25,
    "daily_rsi": 10, "weekly_rsi": 10,
    "volume_obv": 10, "weekly_trend": 10, "downside": 10,
}
assert sum(WEIGHTS.values()) == 100
PATTERN_POINTS = {
    "bull_flag": {"forming": 18, "breakout_observed": 25},
    "double_bottom": {"forming": 17, "breakout_observed": 24},
}
GEOMETRY_POINTS = {
    "falling_wedge_candidate": 20,
    "inverse_head_shoulders_breakout_candidate": 22,
    "triangle_candidate": 15,
    "post_rise_consolidation_candidate": 14,
}
DAILY_LEGACY = {
    "is_wedge": 20,
    "is_ihs": 22,
    "is_triangle": 15,
}
RISK_WARNINGS = (
    "예상 손절폭 과대", "손절선 과도하게 근접", "구조적 손절선 미산출",
    "단기 급등", "장기 추세 취약", "거래대금 부족",
    "급격한 거래량 집중", "52주 고점권",
)


def _number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _source(r, timeframe):
    p = r.get("pattern_research" if timeframe == "daily"
              else "pattern_weekly_research")
    if not isinstance(p, dict) or p.get("schema") != SOURCE or (
            p.get("timeframe") != timeframe or
            p.get("status") != "heuristic_unverified" or
            p.get("buy_signal") is not False or
            p.get("scoring_allowed") is not False or
            p.get("official_regular_session_verified") is not False):
        return None
    try:
        market_day = date.fromisoformat(r["price_date"])
        observed = date.fromisoformat(p["asof"])
    except (KeyError, TypeError, ValueError):
        return None
    if timeframe == "daily":
        if market_day != observed:
            return None
    elif not (p.get("completed_week_only") is True and
              observed.weekday() == 4 and
              0 <= (market_day - observed).days <= 13):
        return None
    if not isinstance(p.get("patterns"), list) or len(p["patterns"]) > 5:
        return None
    found = set()
    for q in p["patterns"]:
        if not isinstance(q, dict) or q.get("type") not in PATTERN_POINTS or (
            q.get("stage") not in ("forming", "breakout_observed") or
            q["type"] in found):
            return None
        found.add(q["type"])
    rsi = p.get("rsi14")
    if _number(rsi) is None or not 0 <= rsi <= 100:
        return None
    if type(p.get("rsi30_cross_recent")) is not bool or (
            type(p.get("rsi_bullish_divergence_recent")) is not bool):
        return None
    if timeframe == "weekly":
        geom = p.get("pattern_geometry")
        if not isinstance(geom, dict) or geom.get("status") != "heuristic_unvalidated":
            return None
        if any(type(geom.get(key)) is not bool for key in GEOMETRY_POINTS):
            return None
    return p


def _pattern_score(p, r=None):
    ranked = [(PATTERN_POINTS[q["type"]][q["stage"]], q["type"], q["stage"])
              for q in p["patterns"]]
    if r is None:
        for key, value in GEOMETRY_POINTS.items():
            if p["pattern_geometry"][key]:
                ranked.append((value, key, "heuristic_unvalidated"))
    else:
        for key, value in DAILY_LEGACY.items():
            if r.get(key) is True:
                ranked.append((value, key, "legacy_heuristic"))
        if "상승 후 횡보·수렴 후보" in (r.get("patterns") or []):
            ranked.append((14, "post_rise_consolidation_candidate", "legacy_heuristic"))
    # Overlapping wedge, triangle, flag and reversal definitions must not be
    # stacked on the same timeframe; highest independently observed one wins.
    if not ranked:
        return 0, None
    ranked.sort(key=lambda x: (-x[0], x[1]))
    value, kind, stage = ranked[0]
    return value, {"type": kind, "stage": stage}


def _rsi_score(p):
    # RSI under 30 is not a positive signal by itself. A crossing and an
    # independently confirmed bullish pivot may reinforce each other.
    return min(10, (7 if p["rsi_bullish_divergence_recent"] else 0) +
               (6 if p["rsi30_cross_recent"] else 0))


def _volume_score(r):
    vr = _number(r.get("one_day_volume_ratio"))
    if vr is None or vr < 0:
        return None
    # OBV can add at most 2 points; never mandatory for inclusion.
    volume = 6 if 1.5 <= vr <= 5 else 4 if 1.2 <= vr < 1.5 else 1 if vr < 1.2 else 0
    obv = 2 if r.get("obv_rising") is True else 0
    divergence = 2 if r.get("obv_rising") is True and r.get("obv_divergence") is True else 0
    return min(10, volume + obv + divergence)


def _trend_score(r):
    gap = _number(r.get("ma120w_gap"))
    if gap is None:
        return None
    if r.get("weekly_turn") is True:
        return 10
    if r.get("weekly_aligned") is True and gap >= 0:
        return 8
    if -7 <= gap <= 15:
        return 5
    if -15 <= gap < -7:
        return 2
    return 0


def _downside_score(r):
    stop = _number(r.get("stop_pct"))
    level = _number(r.get("stop_line"))
    close = _number(r.get("price"))
    # During score_one, only price_latest exists; published snapshots use price.
    if close is None:
        close = _number(r.get("price_latest"))
    if stop is None or level is None or close is None or close <= 0:
        return None
    if not (-100 < stop < 0) or not 0 < level < close:
        return None
    if abs(100 * (level / close - 1) - stop) > 1.5:
        return None
    distance = -stop
    if 5 <= distance <= 9:
        return 10
    if 3 <= distance < 5:
        return 8
    if 9 < distance <= 12:
        return 7
    if 12 < distance <= 15:
        return 4
    return 0


def evaluate(r):
    """Calculate a strictly research-only score without changing any old grade."""
    day = _source(r, "daily")
    week = _source(r, "weekly")
    components = {
        "daily_pattern": _pattern_score(day, r)[0] if day else None,
        "weekly_pattern": _pattern_score(week)[0] if week else None,
        "daily_rsi": _rsi_score(day) if day else None,
        "weekly_rsi": _rsi_score(week) if week else None,
        "volume_obv": _volume_score(r),
        "weekly_trend": _trend_score(r),
        "downside": _downside_score(r),
    }
    observed = sum(WEIGHTS[k] for k, v in components.items() if v is not None)
    notes = []
    flags = r.get("risk_flags")
    flags = flags if isinstance(flags, list) else []
    warnings = [x for x in flags if isinstance(x, str) and
                any(reason in x for reason in RISK_WARNINGS)]
    if warnings:
        notes.extend(warnings)
    recent = _number(r.get("price60"))
    recent20 = _number(r.get("price20"))
    if recent is None or recent20 is None:
        notes.append("recent_price_return_missing")
    elif recent >= 40 or recent20 > 18:
        notes.append("price_extension_chase_risk")
    stop = _number(r.get("stop_pct"))
    if stop is None or not -15 <= stop <= -3:
        notes.append("stop_distance_outside_3_to_15")
    if observed != 100:
        notes.append("incomplete_observed_components")
    complete = observed == 100
    score = int(sum(components.values())) if complete else None
    ready = bool(complete and not [x for x in notes if x != "incomplete_observed_components"])
    daily_top = _pattern_score(day, r)[1] if day else None
    weekly_top = _pattern_score(week)[1] if week else None
    return {
        "schema": SCHEMA, "asof": r.get("price_date"),
        "status": "rank_eligible" if ready else (
            "risk_excluded" if complete else "incomplete_unscored"),
        "weights": dict(WEIGHTS), "components": components,
        "observed_weight": observed, "coverage_pct": observed,
        "research_priority_score": score,
        "rank": None, "ready_for_ranking": ready,
        "daily_top_pattern": daily_top, "weekly_top_pattern": weekly_top,
        "risk_warnings": list(dict.fromkeys(notes)),
        "v2_score_changed": False,
        "v3_six_pillar_score": None,
        "historical_success_probability": None,
        "buy_certified": False,
    }


def rank_eligible_rows(rows):
    """Set deterministic total-market research rank, risk-excluded rows unranked."""
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    for r in rows:
        r["pattern_composite"] = evaluate(r)
    eligible = [r for r in rows if r["pattern_composite"]["ready_for_ranking"]]
    eligible.sort(key=lambda r: (-r["pattern_composite"]["research_priority_score"],
                                 -(r["pattern_composite"]["components"]["weekly_pattern"] or 0),
                                 -(r["pattern_composite"]["components"]["daily_pattern"] or 0),
                                 str(r.get("code", ""))))
    for index, row in enumerate(eligible, 1):
        row["pattern_composite"]["rank"] = index
    return eligible
