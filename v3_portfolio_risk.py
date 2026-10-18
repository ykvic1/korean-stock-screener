"""Research-only structural loss budget for a concentrated stock portfolio.

This is arithmetic on a USER-SUPPLIED PLAN, not a price provider, investment
forecast, stop execution guarantee, recommended allocation or buy signal.
"""
from __future__ import annotations

import math

MAX_POSITION_COUNT = 3


class PortfolioRiskError(ValueError):
    """Stable error codes; never expose source payloads or secrets."""


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _percent(value, *, allow_zero=True):
    if (not _finite(value) or value < 0 or value > 100
            or (not allow_zero and value == 0)):
        raise PortfolioRiskError("invalid_percent")
    return float(value)


def _price(value):
    if not _finite(value) or value <= 0:
        raise PortfolioRiskError("invalid_price")
    return float(value)


def _floor2(value):
    # Truncate suggested arithmetic capacity instead of rounding up risk.
    return math.floor((value + 1e-12) * 100) / 100


def assess_position(plan):
    """Compute stop distance and an explicitly HYPOTHETICAL extra-gap fill.

    stop is presumed frozen BEFORE entry; caller is responsible for evidence.
    A 5% gap stress means an execution price 5% BELOW the stop, not an
    estimate of market gaps. Stop placement itself can be unverified.
    """
    if not isinstance(plan, dict):
        raise PortfolioRiskError("invalid_position")
    code = plan.get("code")
    if not (type(code) is str and len(code) == 6 and code.isascii()
            and code.isdigit()):
        raise PortfolioRiskError("invalid_symbol")
    entry, stop = _price(plan.get("entry_price")), _price(plan.get("stop_price"))
    if stop >= entry:
        raise PortfolioRiskError("invalid_structural_stop")
    allocation = _percent(plan.get("planned_position_pct"), allow_zero=False)
    extra_gap = _percent(plan.get("stress_gap_below_stop_pct"))
    target = plan.get("reference_target_price")
    if target is not None:
        target = _price(target)
        if target <= entry:
            raise PortfolioRiskError("invalid_reference_target")
    stop_pct = 100 * (1 - stop / entry)
    hypothetical_fill = stop * (1 - extra_gap / 100)
    stress_loss_pct = 100 * (1 - hypothetical_fill / entry)
    if not 0 < stop_pct <= stress_loss_pct <= 100:
        raise PortfolioRiskError("invalid_risk_math")
    upside_pct = (100 * (target / entry - 1)) if target is not None else None
    planned_rr = upside_pct / stop_pct if upside_pct is not None else None
    level = ("too_close" if stop_pct < 3 else
             "wide" if stop_pct > 15 else
             "short_distance" if stop_pct <= 8 else
             "medium_distance")
    return {
        "code": code,
        "planned_position_pct": allocation,
        "stop_distance_pct": round(stop_pct, 4),
        "stop_distance_band": level,
        "reference_target_distance_pct": (
            round(upside_pct, 4) if upside_pct is not None else None),
        "reference_price_reward_risk_arithmetic_only": (
            round(planned_rr, 3) if planned_rr is not None else None),
        "hypothetical_extra_gap_below_stop_pct": extra_gap,
        "hypothetical_stress_fill_price": round(hypothetical_fill, 4),
        "hypothetical_stress_loss_per_position_pct": round(stress_loss_pct, 4),
        "planned_stop_loss_of_nav_pct": round(
            stop_pct * allocation / 100, 6),
        "hypothetical_gap_loss_of_nav_pct": round(
            stress_loss_pct * allocation / 100, 6),
        "structural_stop_execution_guaranteed": False,
        "expected_upside_pct": None,
        "actual_loss_probability": None,
        "independently_validated_return": False,
        "confirmed_buy": False,
    }


def evaluate_portfolio(plans, *, total_nav_risk_budget_pct):
    """Stress all user-planned positions simultaneously, no diversification credit.

    Budget and gap severity are explicit INPUTS, not AI selected tolerances.
    The resulting capacity is only under specified stress, not 'safe size'.
    """
    budget = _percent(total_nav_risk_budget_pct, allow_zero=False)
    if not isinstance(plans, list) or not plans:
        raise PortfolioRiskError("empty_or_invalid_portfolio")
    rows = [assess_position(p) for p in plans]
    codes = [x["code"] for x in rows]
    if len(set(codes)) != len(codes):
        raise PortfolioRiskError("duplicate_position")
    used_capital = sum(p["planned_position_pct"] for p in rows)
    if used_capital > 100 + 1e-9:
        raise PortfolioRiskError("portfolio_allocation_exceeds_100")
    # Use unrounded input calculations for gate/budget to avoid pass-by-rounding.
    exact_stop = sum(
        (1 - p["stop_price"] / p["entry_price"])
        * p["planned_position_pct"] for p in plans)
    exact_stress = sum(
        (1 - (p["stop_price"] / p["entry_price"])
         * (1 - p["stress_gap_below_stop_pct"] / 100))
        * p["planned_position_pct"] for p in plans)
    pass_budget = exact_stress <= budget + 1e-12
    # Under stress, report cap per each position as if all OTHERS stayed fixed.
    for i, row in enumerate(rows):
        remaining_for_this = max(0.0, budget - (exact_stress -
                               row["hypothetical_gap_loss_of_nav_pct"]))
        ratio = row["hypothetical_stress_loss_per_position_pct"]
        cap = remaining_for_this / ratio * 100
        # Floor arithmetic capacity, never recommend a buy/size.
        row["theoretical_max_pct_with_other_plans_unchanged"] = _floor2(
            min(100.0, max(0.0, cap)))
        row["actual_allocation_recommendation_pct"] = None
    return {
        "schema": "v3-portfolio-loss-budget-research-0.1",
        "number_of_positions": len(rows),
        "max_preferred_holdings": MAX_POSITION_COUNT,
        "concentration_limit_met": len(rows) <= MAX_POSITION_COUNT,
        "planned_invested_pct": round(used_capital, 4),
        "planned_cash_pct": round(max(0.0, 100 - used_capital), 4),
        "user_input_risk_budget_of_nav_pct": budget,
        "total_stop_loss_of_nav_pct": round(exact_stop, 6),
        "total_hypothetical_gap_loss_of_nav_pct": round(exact_stress, 6),
        "stress_budget_status": ("within_assumed_budget"
                                 if pass_budget else "exceeds_assumed_budget"),
        "stress_budget_met": pass_budget,
        "positions": rows,
        "assumes_simultaneous_stress": True,
        "gap_severity_is_user_scenario_not_forecast": True,
        "more_severe_losses_are_possible": True,
        "all_prices_and_stops_are_unverified_user_inputs": True,
        "portfolio_expected_return_pct": None,
        "probability_of_loss": None,
        "safe_position_size_pct": None,
        "trade_approval": False,
        "v3_scoring_authorized": False,
        "notices": [
            "Hypothetical extra-gap stress can understate real slippage or loss.",
            "Stop and target are unverified plan levels, not execution guarantees.",
            "Target-to-stop distance is arithmetic, NOT expected reward/risk.",
            "Data provenance, prices, corporate actions, PIT fundamentals, and "
            "institutional flows require independent verification.",
        ],
    }
