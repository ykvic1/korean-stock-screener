"""v3 executable RESEARCH factor calculations from raw metric columns, offline only.

This is a working normalizer / six-pillar score *candidate* engine, not
source-certified or predictive. Same-date, same-market peer midrank values
are an EXPERIMENTAL choice; they cannot be used as real investment ranks
without PIT survivor-free history, independent calibration and approval.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date
import math

from v3_score_contract import (
    IAS_POINTS, PILLAR_POINTS, NET_BUY_PERIOD_WEIGHT, research_scorecard
)
from v3_ias_flow_ledger import audit_daily_flow, FlowLedgerError

MIN_PEERS = 30
MAX_ROWS = 20000
MARKETS = {"KOSPI", "KOSDAQ"}

# Field, weight within parent, direction (+1 = larger is better), optional
# admissible [min,max] range. All economic choices are EXPERIMENTAL.
IAS_METRICS = {
    "long_term_holding": [("ias_ownership_delta_250_pp", 20, 1, None)],
    "accumulated_net_buy": [
        (f"ias_flow{d}_pct_float", 25 * weight / 100, 1, None)
        for d, weight in NET_BUY_PERIOD_WEIGHT.items()
    ],
    "buy_persistence": [("ias_buy_days_60_ratio", 15, 1, (0, 1))],
    "investor_quality": [("ias_foreign_pension_cobuy_60_ratio", 10, 1, (0, 1))],
    "obv_and_volume": [("ias_obv_change_60_pct", 15, 1, None)],
    "price_not_reflected": [("ias_unreflected_price_60_pct", 15, 1, None)],
}
PILLAR_METRICS = {
    "financial": [
        ("financial_revenue_cagr_3y_pct", 4, 1, None),
        ("financial_operating_margin_3y_pct", 4, 1, None),
        ("financial_fcf_assets_3y_pct", 4, 1, None),
        ("financial_net_debt_equity_pct", 3, -1, None),
    ],
    "valuation": [
        ("valuation_pbr", 5, -1, (0, None)),
        ("valuation_earnings_yield_pct", 5, 1, None),
        ("valuation_fcf_yield_pct", 5, 1, None),
    ],
    "technical": [
        # Within the new technical25, observed pattern evidence is dominant.
        # Daily+fully completed weekly patterns = 15; RSI5, MA1203, volume2.
        ("technical_weekly_ma120_reclaim_strength", 3, 1, (0, 1)),
        ("technical_weekly_rsi_divergence_strength", 5, 1, (0, 1)),
        ("technical_pattern_confirmation_strength", 15, 1, (0, 1)),
        ("technical_volume_breakout_ratio", 2, 1, (0, None)),
    ],
    "industry": [
        ("industry_relative_strength_60_pct", 6, 1, None),
        ("industry_profit_growth_12m_pct", 5, 1, None),
        ("industry_breadth_above_ma_ratio", 4, 1, (0, 1)),
    ],
    "momentum": [
        # Avoid duplicating the strengthened technical pattern/trend pillar.
        ("momentum_stock_relative_strength_20_pct", 2, 1, None),
        ("momentum_stock_relative_strength_60_pct", 2, 1, None),
        ("momentum_positive_days_20_ratio", 1, 1, (0, 1)),
    ],
}
ALL_SPECS = [item for group in IAS_METRICS.values() for item in group]
ALL_SPECS += [item for group in PILLAR_METRICS.values() for item in group]
FIELD_SPECS = {name: (weight, direction, limit)
               for name, weight, direction, limit in ALL_SPECS}


class FactorEngineError(ValueError):
    """Static errors only; never echo source payloads or private flow values."""


def _validate_design():
    if len(FIELD_SPECS) != len(ALL_SPECS):
        raise FactorEngineError("duplicate_factor_feature")
    if (set(IAS_METRICS) != set(IAS_POINTS)
            or set(PILLAR_METRICS) != set(PILLAR_POINTS) - {"ias"}):
        raise FactorEngineError("factor_design_missing_six_pillars")
    for name, rows in IAS_METRICS.items():
        if not math.isclose(sum(item[1] for item in rows), IAS_POINTS[name]):
            raise FactorEngineError("ias_feature_weights_invalid")
    for name, rows in PILLAR_METRICS.items():
        if not math.isclose(sum(item[1] for item in rows), PILLAR_POINTS[name]):
            raise FactorEngineError("pillar_feature_weights_invalid")


_validate_design()


def _number(v, name):
    if v is None:
        return None
    if type(v) not in (int, float) or not math.isfinite(v):
        raise FactorEngineError("nonfinite_or_non_numeric_metric")
    bound = FIELD_SPECS[name][2]
    if bound is not None:
        low, high = bound
        if (low is not None and v < low) or (high is not None and v > high):
            raise FactorEngineError("metric_outside_declared_domain")
    return float(v)


def ias_raw_features_from_ledger(ledger, *, asof, float_shares,
                                 ownership_delta_250_pp=None,
                                 obv_change_60_pct=None,
                                 price_return_60_pct=None):
    """Actually derive 4 flow percentages and persistence from observed rows.

    Institution already INCLUDES pension. We never add pension twice.
    Missing full windows remain null, not 0. Float-share denominator must be
    externally valid for the same stock and historical cutoff (not certified
    here). Price-not-reflected is gated on positive 60d flow and OBV trend;
    a mere price crash does not earn 'unreflected' IAS points.
    """
    if type(float_shares) is not int or not 0 < float_shares <= 10**13:
        raise FactorEngineError("invalid_free_float_share_denominator")
    try:
        observed = audit_daily_flow(ledger, asof=asof)
    except (FlowLedgerError, TypeError, KeyError):
        raise FactorEngineError("invalid_investor_ledger") from None
    if observed["latest_observed_date"] != asof:
        raise FactorEngineError("stale_investor_ledger")
    out = {}
    flow_60 = None
    for d in NET_BUY_PERIOD_WEIGHT:
        segment = observed["periods"][str(d)]["categories"]
        foreign = segment["foreign_net_shares"]["cumulative_net_shares"]
        inst = segment["institution_total_net_shares"]["cumulative_net_shares"]
        total = foreign + inst if foreign is not None and inst is not None else None
        out[f"ias_flow{d}_pct_float"] = (
            100 * total / float_shares if total is not None else None
        )
        if d == 60:
            flow_60 = total
    rows = ledger["observations"]
    last60 = rows[-60:] if len(rows) >= 60 else []
    full_60 = len(last60) == 60
    def complete(*keys):
        return full_60 and all(all(r[key] is not None for key in keys)
                               for r in last60)
    out["ias_buy_days_60_ratio"] = (
        sum((r["foreign_net_shares"] + r["institution_total_net_shares"]) > 0
            for r in last60) / 60
        if complete("foreign_net_shares", "institution_total_net_shares")
        else None
    )
    out["ias_foreign_pension_cobuy_60_ratio"] = (
        sum(r["foreign_net_shares"] > 0 and r["pension_net_shares"] > 0
            for r in last60) / 60
        if complete("foreign_net_shares", "pension_net_shares")
        else None
    )
    out["ias_ownership_delta_250_pp"] = _number(
        ownership_delta_250_pp, "ias_ownership_delta_250_pp")
    out["ias_obv_change_60_pct"] = _number(
        obv_change_60_pct, "ias_obv_change_60_pct")
    price = _number(price_return_60_pct, "ias_unreflected_price_60_pct")
    out["ias_unreflected_price_60_pct"] = (
        -price if price is not None and flow_60 is not None and
        flow_60 > 0 and obv_change_60_pct is not None and
        obv_change_60_pct >= 0 else None
    )
    return out


def _midrank(v, ordered, direction):
    left = bisect_left(ordered, v)
    right = bisect_right(ordered, v)
    percentile = (left + right) / (2 * len(ordered))
    return percentile if direction == 1 else (1 - percentile)


def research_factor_batch(rows, *, asof, min_peers=MIN_PEERS):
    """Compute provisional components and full six-pillar research scorecards.

    No portfolio buy rank and no provider-backed score is ever returned.
    Peer percentiles are per MARKET to avoid mixing KOSPI/KOSDAQ and require
    enough observed eligible metric values; ties earn a neutral midrank.
    """
    try:
        if type(asof) is not str or date.fromisoformat(asof).isoformat() != asof:
            raise ValueError()
    except (ValueError, TypeError):
        raise FactorEngineError("invalid_snapshot_date") from None
    if (not isinstance(rows, list) or len(rows) > MAX_ROWS
            or type(min_peers) is not int or not 8 <= min_peers <= 1000):
        raise FactorEngineError("invalid_research_batch")
    checked, seen = [], set()
    by_market = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            raise FactorEngineError("invalid_research_candidate")
        code, issuer, market = row.get("code"), row.get("issuer_id"), row.get("market")
        if (type(code) is not str or not code.isascii() or not code.isdigit()
                or len(code) != 6 or type(issuer) is not str or
                not issuer.isascii() or not issuer.isdigit() or len(issuer) != 8
                or market not in MARKETS or row.get("asof") != asof):
            raise FactorEngineError("issuer_market_or_date_mismatch")
        key = (market, code)
        if key in seen:
            raise FactorEngineError("duplicate_ticker_market_in_snapshot")
        seen.add(key)
        features = row.get("features")
        if not isinstance(features, dict) or set(features) - set(FIELD_SPECS):
            raise FactorEngineError("invalid_raw_factor_columns")
        values = {name: _number(features.get(name), name) for name in FIELD_SPECS}
        candidate = {"code": code, "issuer_id": issuer, "market": market,
                     "features": values}
        checked.append(candidate)
        by_market[market].append(candidate)
    baselines = {}
    for market, peers in by_market.items():
        baselines[market] = {
            name: sorted(row["features"][name] for row in peers
                         if row["features"][name] is not None)
            for name in FIELD_SPECS
        }
    output = []
    for row in checked:
        baseline = baselines[row["market"]]
        def score_component(spec):
            values = []
            for name, max_pts, direction, _ in spec:
                value = row["features"][name]
                peers = baseline[name]
                if value is None or len(peers) < min_peers:
                    return None
                values.append(max_pts * _midrank(value, peers, direction))
            return round(sum(values), 6)
        # Optional technical reference: preserve the *original* 15-point
        # budget while reporting only observed same-market research points.
        # Missing weekly 120-MA never becomes a synthetic zero, receives a
        # bonus, or gets reweighted to other technical metrics.
        technical_components = {}
        for metric, possible, direction, _ in PILLAR_METRICS["technical"]:
            metric_value = row["features"][metric]
            comparable = baseline[metric]
            technical_components[metric] = (
                round(possible * _midrank(metric_value, comparable, direction), 6)
                if metric_value is not None and len(comparable) >= min_peers
                else None
            )
        technical_possible = sum(
            weight for metric, weight, _, _ in PILLAR_METRICS["technical"]
            if technical_components[metric] is not None
        )
        technical_partial = {
            "research_points_unscaled": (
                round(sum(v for v in technical_components.values() if v is not None), 6)
                if technical_possible else None
            ),
            "observed_maximum_points": technical_possible,
            "unavailable_maximum_points": PILLAR_POINTS["technical"] - technical_possible,
            "observed_metrics": sorted(
                name for name, value in technical_components.items()
                if value is not None),
            "missing_or_insufficient_peer_metrics": sorted(
                name for name, value in technical_components.items()
                if value is None),
            "individual_research_points": technical_components,
            "missing_120week_is_not_zero_or_reweighted": True,
            "equal_to_complete_technical_pillar": (
                technical_possible == PILLAR_POINTS["technical"]),
            "confirmed_technical_score": None,
            "v3_release_ready": False,
        }
        ias_parts = {
            name: score_component(spec) for name, spec in IAS_METRICS.items()
        }
        pillars = {
            name: score_component(spec) for name, spec in PILLAR_METRICS.items()
        }
        scorecard = research_scorecard(asof=asof, pillars=pillars, ias=ias_parts)
        output.append({
            "code": row["code"], "issuer_id": row["issuer_id"],
            "market": row["market"],
            "candidate_full_total_research_only":
                scorecard["candidate_full_total_research_only"],
            "candidate_coverage_pct": scorecard["candidate_coverage_pct"],
            "candidate_normalized_100_research_only":
                scorecard["candidate_normalized_100"],
            "ias_research_components": ias_parts,
            "pillar_research_candidates": {
                name: scorecard["pillars"][name]["candidate_points"]
                for name in PILLAR_POINTS
            },
            "technical_subfactor_partial_research_only": technical_partial,
            "per_metric_valid_peer_count": {
                name: len(baseline[name])
                for name in FIELD_SPECS
            },
            "missing_features": [
                name for name in FIELD_SPECS if row["features"][name] is None
            ],
            "confirmed_score": None,
            "confirmed_rank": None,
            "predicted_return_pct": None,
            "scoring_authorized_for_investment": False,
            "confirmed_buy": False,
        })
    return {
        "schema": "v3-research-feature-percentiles-0.1",
        "asof": asof,
        "min_metric_peer_observations": min_peers,
        "model_points": dict(PILLAR_POINTS),
        "ias_points": dict(IAS_POINTS),
        "ias_flow_period_weights": dict(NET_BUY_PERIOD_WEIGHT),
        "market_cohorts": {
            market: len(peers) for market, peers in sorted(by_market.items())
        },
        "rows": output,
        "cohort_is_point_in_time_authenticated": False,
        "original_flow_price_fundamental_data_verified": False,
        "normalization_calibrated_out_of_sample": False,
        "cross_pillar_signal_correlation_verified": False,
        "provisional_peer_scores_are_investment_ranks": False,
        "v3_release_ready": False,
        "confirmed_full_market_rank": None,
    }
