"""v3 full six-pillar score contract (RESEARCH ONLY; no scores released).

These are the agreed point budgets, not independently calibrated algorithms.
A caller-supplied numeric component is never proof that a provider, as-of
timestamp, market session, accounting scope, or normalization is verified.
All "candidate" numbers below remain provisional even when fully populated.
No OHLCV, financial, flow, or live API call is made in this module.
"""
from __future__ import annotations

from datetime import date, datetime
import math
from numbers import Real
from types import MappingProxyType
from typing import Mapping

VERSION = "v3-six-pillar-contract-0.3-research-only"
# User-approved whole-model weights, exactly 100 points.
# IAS internal 100-point evidence weights remain unchanged; scaled to 20/100.
# Values remain research-only until provenance and PIT calibration are verified.
PILLAR_POINTS = MappingProxyType({
    "ias": 20,
    "financial": 15,
    "valuation": 15,
    "technical": 25,
    "industry": 15,
    "momentum": 10,
})
IAS_POINTS = MappingProxyType({
    "long_term_holding": 20,
    "accumulated_net_buy": 25,
    "buy_persistence": 15,
    "investor_quality": 10,
    "obv_and_volume": 15,
    "price_not_reflected": 15,
})
# This is the AGREED weighting INSIDE the accumulated-net-buy signal,
# not a weighting of the entire IAS score or of all six pillars.
NET_BUY_PERIOD_WEIGHT = MappingProxyType({20: 15, 60: 25, 120: 30, 250: 30})
IAS_MAX = 100
MODEL_MAX = 100
MAX_OBSERVED_RECORDS = 250


class ScoreContractError(ValueError):
    """Static errors only; do not echo input data, credentials or raw feeds."""


def _check_design() -> None:
    if (sum(PILLAR_POINTS.values()) != MODEL_MAX
            or dict(PILLAR_POINTS) != {
                "ias": 20, "financial": 15, "valuation": 15,
                "technical": 25, "industry": 15, "momentum": 10,
            }
            or sum(IAS_POINTS.values()) != IAS_MAX
            or set(NET_BUY_PERIOD_WEIGHT) != {20, 60, 120, 250}
            or sum(NET_BUY_PERIOD_WEIGHT.values()) != 100):
        raise ScoreContractError("internal_points_contract_changed")


_check_design()


def _check_asof(asof: str) -> str:
    if type(asof) is not str or len(asof) != 10:
        raise ScoreContractError("invalid_snapshot_date")
    try:
        parsed = date.fromisoformat(asof)
    except ValueError:
        raise ScoreContractError("invalid_snapshot_date") from None
    if parsed.isoformat() != asof:
        raise ScoreContractError("invalid_snapshot_date")
    return asof


def _candidate(value: object, maximum: int) -> float | None:
    # bool, strings, NaN, +/-Inf, negative, out-of-scale and unverified
    # absent values cannot be converted into synthetic zero-point scores.
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ScoreContractError("invalid_candidate_component")
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= maximum:
        raise ScoreContractError("candidate_component_outside_points")
    return round(value, 6)


def _validate_inputs(inputs: Mapping[str, object] | None,
                     available: Mapping[str, int]) -> dict[str, float | None]:
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, Mapping):
        raise ScoreContractError("invalid_candidate_mapping")
    if set(inputs) - set(available):
        raise ScoreContractError("unknown_candidate_dimension")
    return {name: _candidate(inputs.get(name), maximum)
            for name, maximum in available.items()}


def research_scorecard(*, asof: str,
                       pillars: Mapping[str, object] | None = None,
                       ias: Mapping[str, object] | None = None) -> dict:
    """Build research-only scored candidates; never certify readiness.

    Full six-pillar framework includes finance/valuation/industry/momentum.
    IAS subscore is normalized 100 => pillar max20 ONLY when all six IAS
    candidate sub-scores exist. When IAS is missing, no computed IAS or 0
    is inserted. Do not pass both an externally supplied IAS score and
    separate internal IAS components; avoids double counting.

    Candidate scores MUST have already been computed on agreed subscales by
    a future independent algorithm. This contract does NOT calculate those
    scores from KRX, DART, OBV or historical performance.
    """
    asof = _check_asof(asof)
    candidates = _validate_inputs(pillars, PILLAR_POINTS)
    components = _validate_inputs(ias, IAS_POINTS)
    if ias is not None and ("ias" in (pillars or {})):
        raise ScoreContractError("ias_double_counting_forbidden")
    ias_fully_observed = all(v is not None for v in components.values())
    if ias is not None and ias_fully_observed:
        candidates["ias"] = round(
            sum(components.values()) * PILLAR_POINTS["ias"] / IAS_MAX, 6)
    else:
        candidates["ias"] = candidates.get("ias")

    # User-facing provisional normalisation, not a verified trading score.
    # A missing category contributes neither points nor maximum potential.
    # For partially observed IAS, preserve only the *observed share* of its
    # 25pt total (no inflation for missing pension/holding/other signals).
    observed = sum(v for v in candidates.values() if v is not None)
    available = sum(PILLAR_POINTS[k] for k,v in candidates.items() if v is not None)
    ias_observed = sum(v for v in components.values() if v is not None)
    ias_available = sum(IAS_POINTS[k] for k,v in components.items() if v is not None)
    if ias is not None and not ias_fully_observed:
        observed += ias_observed * (PILLAR_POINTS["ias"] / IAS_MAX)
        available += ias_available * (PILLAR_POINTS["ias"] / IAS_MAX)
    if available > 0:
        # One-decimal truncation (70/85 => 82.3), never round up claimed score.
        normalized = math.floor((observed / available * 100 + 1e-12) * 10) / 10
    else:
        normalized = None

    pillar_rows = {
        key: {
            "maximum": maximum,
            "candidate_points": candidates[key],
            "status": "research_candidate_unverified" if candidates[key] is not None
                      else "missing_unscored",
        }
        for key, maximum in PILLAR_POINTS.items()
    }
    ia_rows = {
        key: {
            "maximum_ias_100": maximum,
            "candidate_ias_points": components[key],
            "status": "research_candidate_unverified" if components[key] is not None
                      else "missing_unscored",
        }
        for key, maximum in IAS_POINTS.items()
    }
    all_pillars = all(value is not None for value in candidates.values())
    complete_ias = ias is not None and ias_fully_observed

    return {
        "schema": VERSION,
        "asof": asof,
        "model_maximum": MODEL_MAX,
        "pillar_points": dict(PILLAR_POINTS),
        "ias_subpillar_maximum": IAS_MAX,
        "ias_subpillars": ia_rows,
        "accumulated_net_buy_periods": {
            str(d): {
                "weight_percent_within_accumulated_net_buy": pct,
                "available_observed_records": None,
                "verified_trading_calendar": False,
            }
            for d, pct in NET_BUY_PERIOD_WEIGHT.items()
        },
        "pillars": pillar_rows,
        "candidate_nonmissing_weight": sum(
            PILLAR_POINTS[name] for name, v in candidates.items()
            if v is not None
        ),
        "candidate_observed_points": round(observed, 6) if available > 0 else None,
        "candidate_available_points": round(available, 6),
        "candidate_missing_points": round(MODEL_MAX - available, 6),
        "candidate_coverage_pct": round(100 * available / MODEL_MAX, 2),
        "candidate_normalized_100": normalized,
        "candidate_score_type": (
            "provisional_partial_coverage" if available < MODEL_MAX
            else "provisional_full_coverage"
        ) if available > 0 else "insufficient_data",
        "ias_candidate_observed_100": (
            round(ias_observed, 6) if ias is not None and ias_available > 0 else None
        ),
        "ias_candidate_available_100": ias_available if ias is not None else None,
        "unscored_pillars": [name for name, v in candidates.items() if v is None],
        "ias_internal_six_components_complete": complete_ias,
        "candidate_full_total_research_only": (
            round(sum(candidates.values()), 6) if all_pillars else None
        ),
        # Critical fail-closed guard: candidate normalization numbers and
        # alleged provider flags cannot release a confirmed investable score.
        "model_normalization_verified": False,
        "all_source_asof_and_session_provenance_verified": False,
        "confirmed_score": None,
        "confirmed_rank": None,
        "ias_confirmed": None,
        "v3_release_ready": False,
        "scoring_authorized_for_investment": False,
        "note": (
            "Six-pillar design fixed. Numeric inputs are unverified research "
            "candidates; not approved buy/sell signals. Missing is excluded "
            "from the denominator and shown explicitly, never assigned zero."
        ),
    }


def ea_candidate_conditions() -> dict:
    """Previous EA hypothesis, NOT a threshold-calibrated executable signal."""
    return {
        "status": "research_hypothesis_only",
        "current_ias_threshold_candidate": 60,
        "four_week_price_change_limit_candidate_pct": 10,
        "eight_week_price_change_preference_pct": 15,
        "future_four_week_return_study_target_pct": 8,
        "future_eight_week_return_study_target_pct": 12,
        "prior_ias_approximate_band": [50, 60],
        "obv_weekly_not_falling_expected": True,
        "recent_20_vs_60_120_flow_improvement_required": True,
        "unresolved_definitions": [
            "exact_ias_normalization",
            "ea_prior_ias_date_range",
            "obv_flat_or_rising_tolerance",
            "recent_flow_improvement_metric",
            "prediction_timeframe_threshold_backtest",
        ],
        "signal_verified": False,
        "deployable_rule": False,
    }
