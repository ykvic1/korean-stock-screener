"""Research-only aggregation of PRIVATE five-session actual turnover cohorts.

Cohorts are supplied newest-first from the independently verified private
derived-only archive, not from a public stock price/volume feed.
Four/five/twelve/etc COMPLETE five-session buckets yield actual observed
20/25/60/120/240-session average money amounts, but NOT day-by-day OBV.
One five-session cohort is never silently called a calendar week.
"""
from __future__ import annotations

from datetime import date
import math
import re

from v3_krx_money_5day import SCHEMA as COHORT_SCHEMA

SCHEMA = "v3-actual-money-cohort-multihorizon-research-0.1"
DAILY = (20, 60, 120, 240)
WEEK_PROXY = (5, 10, 20, 60, 120)
MAX_COHORTS = 490
MONEY_BUCKET_KRW = 100_000


class MoneyHorizonError(ValueError):
    """Static provenance/integrity code, never include raw price or issuer."""


def _day(value):
    if type(value) is not str:
        raise MoneyHorizonError("invalid_money_horizon_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise MoneyHorizonError("invalid_money_horizon_date") from None
    if parsed.isoformat() != value:
        raise MoneyHorizonError("invalid_money_horizon_date")
    return parsed


def _valid_factors(records, market, code, asof):
    if (type(records) is not list or not 1 <= len(records) <= MAX_COHORTS
        or market not in ("KOSPI", "KOSDAQ") or type(code) is not str
        or not re.fullmatch(r"[0-9A-Z]{6}", code)):
        raise MoneyHorizonError("invalid_private_money_cohort_inputs")
    cutoff = _day(asof)
    checked = []
    prior_older_bound = None
    key = market + ":" + code
    for i, record in enumerate(records):
        if (type(record) is not dict or set(record) != {"index", "cohort"}
            or type(record["index"]) is not int or record["index"] != i
            or type(record["cohort"]) is not dict):
            raise MoneyHorizonError("missing_or_disordered_private_money_cohort")
        data = record["cohort"]
        if (data.get("schema") != COHORT_SCHEMA
            or data.get("investment_scoring_allowed") is not False
            or data.get("daily_amount_or_price_bars_retained") is not False
            or data.get("amount_bucket_krw") != MONEY_BUCKET_KRW
            or type(data.get("derived_issuer_money_factors")) is not dict):
            raise MoneyHorizonError("unverified_money_cohort_evidence")
        start, end = _day(data.get("window_start")), _day(data.get("window_end"))
        if start > end or end > cutoff:
            raise MoneyHorizonError("future_or_reversed_money_cohort")
        if i == 0 and end != cutoff:
            raise MoneyHorizonError("stale_money_cohort_asof")
        if prior_older_bound is not None and end >= prior_older_bound:
            raise MoneyHorizonError("overlapping_money_cohort_dates")
        prior_older_bound = start
        value = data["derived_issuer_money_factors"].get(key)
        if value is None:
            checked.append(None)
            continue
        if (type(value) is not dict or value.get("market") != market
            or value.get("issuer_code") != code
            or value.get("observed_sessions") != 5
            or value.get("score_authorized") is not False
            or value.get("money_amount_is_explicit_acc_trdval") is not True
            or type(value.get("total_actual_amount_100k_krw")) is not int
            or value["total_actual_amount_100k_krw"] < 0):
            raise MoneyHorizonError("invalid_issuer_money_factor")
        for field in ("money_pressure_intrabucket_bp",
                      "late2_vs_early2_money_pressure_bp"):
            pressure = value.get(field)
            if pressure is not None and (type(pressure) is not int
                                        or not -10000 <= pressure <= 10000):
                raise MoneyHorizonError("invalid_intrabucket_money_pressure")
        checked.append(value)
    return checked


def _window(buckets, n):
    if len(buckets) < n or any(r is None for r in buckets[:n]):
        return None
    recent = buckets[:n]
    total = sum(r["total_actual_amount_100k_krw"] for r in recent)
    average_eok = round(total * MONEY_BUCKET_KRW / (5 * n * 1e8), 4)
    money_signals = [r.get("money_pressure_intrabucket_bp") for r in recent]
    if any(v is None for v in money_signals) or total == 0:
        pressure_bp = None
    else:
        # A WEIGHTED RESEARCH PROXY across 4 intra-cohort intervals each.
        # 5th/boundary return sign is missing and cannot be recovered.
        pressure_bp = round(
            sum(r["total_actual_amount_100k_krw"] * v
                for r, v in zip(recent, money_signals)) / total)
    persistent = sum(r.get("late2_vs_early2_money_pressure_bp", 0) > 0
                     for r in recent
                     if type(r.get("late2_vs_early2_money_pressure_bp")) is int)
    assessable = sum(type(r.get("late2_vs_early2_money_pressure_bp")) is int
                     for r in recent)
    # Compare equal, DISJOINT spans; never compare the latest bucket with
    # an average that already includes that same bucket. Missing issuer
    # observations or a zero baseline cannot turn into an infinite ratio.
    baseline = buckets[n:2*n] if len(buckets) >= 2*n else None
    baseline_complete = baseline is not None and all(r is not None for r in baseline)
    baseline_total = (sum(r["total_actual_amount_100k_krw"] for r in baseline)
                      if baseline_complete else None)
    ratio = maintained = None
    if baseline_total is not None and baseline_total > 0:
        ratio = round(total / baseline_total, 4)
        # Integer comparison avoids rounding deciding whether an observed
        # five-session cohort really exceeded the previous-period average.
        maintained = round(sum(r["total_actual_amount_100k_krw"] * n > baseline_total
                               for r in recent) / n, 4)
    return {
        "average_reported_amount_eok_per_session": average_eok,
        "observed_five_session_cohorts": n,
        "observed_sessions": 5 * n,
        "money_intrabucket_pressure_proxy_bp": pressure_bp,
        "positive_money_persistence_cohort_ratio": (
            round(persistent / assessable, 4) if assessable == n else None),
        "disjoint_baseline_complete": baseline_complete,
        "disjoint_baseline_quantized_zero_amount": baseline_total == 0,
        "versus_previous_disjoint_window_ratio": ratio,
        "amount_increased_vs_disjoint_baseline": (
            total > baseline_total if ratio is not None else None),
        "recent_cohorts_above_previous_average_ratio": maintained,
        "largest_five_session_cohort_amount_share": (
            round(max(r["total_actual_amount_100k_krw"] for r in recent) / total, 4)
            if total else None),
        "exact_single_day_spike_concentration": None,
        "exact_day_by_day_money_obv": None,
        "regular_session_only_verified": False,
        "corporate_actions_verified": False,
        "score_authorized": False,
    }


def observe(records, *, market, code, asof):
    """No original time-series bars, investor-buy inference or live score."""
    buckets = _valid_factors(records, market, code, asof)
    daily = {str(days): _window(buckets, days // 5) for days in DAILY}
    # Calendar weeks require actual Mon-Sun aggregation and weekly closes.
    # N cohorts ~ N trading weeks only as an explicitly labeled PROXY.
    approx_weekly = {str(weeks): _window(buckets, weeks)
                     for weeks in WEEK_PROXY}
    exact_weekly = {str(weeks): None for weeks in WEEK_PROXY}
    return {
        "schema": SCHEMA,
        "asof": asof,
        "market": market,
        "issuer_code": code,
        "daily_research": daily,
        "calendar_weekly_verified": exact_weekly,
        "five_session_week_proxies_not_calendar_weeks": approx_weekly,
        "observed_latest_consecutive_cohorts": len(buckets),
        "source_direct_reported_krx_amount": True,
        "source_eod_scope_regular_only_verified": False,
        "cross_cohort_price_sign_missing": True,
        "issuer_code_historical_identity_verified": False,
        "exact_money_obv_certified": False,
        "historical_five_area_score_unchanged": True,
        "investment_points": None,
        "scoring_allowed": False,
    }
