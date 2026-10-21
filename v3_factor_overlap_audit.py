"""RESEARCH-ONLY cross-feature rank-correlation audit for v3 six-pillar raw metrics.

Detects duplicated empirical information (overlapping 20/60/120/250 flow
windows, trend-vs-OBV etc.) within SAME DATE + MARKET. No changes to v3
factor scoring, no predictive backtest claims, no automatically deweighted
score or investment approval. Only synthetic/offline inputs in CI.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import math

from v3_research_factor_engine import FIELD_SPECS

# Pairs are predefined economic overlap hypotheses: not mined on holdout.
SUSPECTED_PAIRS = (
    ("ias_flow20_pct_float", "ias_flow60_pct_float", "cumulative_flow_nested"),
    ("ias_flow60_pct_float", "ias_flow120_pct_float", "cumulative_flow_nested"),
    ("ias_flow120_pct_float", "ias_flow250_pct_float", "cumulative_flow_nested"),
    ("ias_flow20_pct_float", "ias_flow250_pct_float", "cumulative_flow_nested"),
    ("ias_obv_change_60_pct", "technical_volume_breakout_ratio", "volume_related"),
    ("ias_unreflected_price_60_pct", "momentum_stock_relative_strength_60_pct", "price_factor_overlap"),
    ("momentum_stock_relative_strength_20_pct", "momentum_stock_relative_strength_60_pct", "nested_momentum"),
    ("industry_early_rotation_signal", "momentum_stock_relative_strength_60_pct", "industry_stock_relative"),
    ("financial_fcf_assets_3y_pct", "valuation_fcf_yield_pct", "fcf_numerator_related"),
)
MIN_PAIRS = 30


class FeatureOverlapError(ValueError):
    """Static reason codes; raw market or financial values never logged."""


def _ranks(values):
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    j = 0
    while j < len(values):
        k = j + 1
        while k < len(values) and values[ordered[k]] == values[ordered[j]]:
            k += 1
        mean_rank = (j + k - 1) / 2
        for idx in ordered[j:k]:
            out[idx] = mean_rank
        j = k
    return out


def _spearman(pairs):
    if len(pairs) < 2:
        return None
    x, y = zip(*pairs)
    a, b = _ranks(x), _ranks(y)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((r - ma)**2 for r in a)
    vb = sum((r - mb)**2 for r in b)
    if va == 0 or vb == 0:
        return None  # Constant features: correlation is undefined.
    numerator = sum((u - ma)*(v - mb) for u, v in zip(a, b))
    return max(-1.0, min(1.0, numerator / math.sqrt(va*vb)))


def audit_factor_overlap(rows, *, asof, min_pairs=MIN_PAIRS,
                         absolute_rho_warning=0.85):
    """No metric values or company-level sensitive data in returned report.

    Warning threshold is a research convention, NOT a validated multivariate
    significance cutoff or an authorization to alter scoring weights.
    """
    if (type(asof) is not str or not isinstance(rows, list) or len(rows) > 20000
            or type(min_pairs) is not int or min_pairs < 8 or min_pairs > 1000
            or type(absolute_rho_warning) not in (float, int)
            or not math.isfinite(absolute_rho_warning)
            or not 0.5 <= absolute_rho_warning <= 1.0):
        raise FeatureOverlapError("invalid_overlap_audit_config")
    try:
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError()
    except ValueError:
        raise FeatureOverlapError("invalid_overlap_date") from None
    markets = defaultdict(list)
    seen = set()
    for item in rows:
        if not isinstance(item, dict) or item.get("asof") != asof:
            raise FeatureOverlapError("mixed_overlap_snapshot")
        market = item.get("market")
        code, issuer = item.get("code"), item.get("issuer_id")
        if (market not in ("KOSPI", "KOSDAQ") or type(code) is not str
                or len(code) != 6 or not code.isascii() or not code.isdigit()
                or type(issuer) is not str or len(issuer) != 8
                or not issuer.isascii() or not issuer.isdigit()):
            raise FeatureOverlapError("invalid_overlap_issuer")
        identity = (market, code)
        if identity in seen:
            raise FeatureOverlapError("duplicate_overlap_symbol")
        seen.add(identity)
        features = item.get("features")
        if not isinstance(features, dict) or set(features) - set(FIELD_SPECS):
            raise FeatureOverlapError("invalid_overlap_features")
        normalized = {}
        for field in FIELD_SPECS:
            v = features.get(field)
            if v is not None and (type(v) not in (float, int)
                                  or not math.isfinite(v)):
                raise FeatureOverlapError("invalid_overlap_numeric")
            normalized[field] = float(v) if v is not None else None
        markets[market].append(normalized)
    results = {}
    warnings = 0
    for market, values in sorted(markets.items()):
        pairs_result = []
        for feature_a, feature_b, category in SUSPECTED_PAIRS:
            valid = [
                (r[feature_a], r[feature_b]) for r in values
                if r[feature_a] is not None and r[feature_b] is not None
            ]
            rho = _spearman(valid) if len(valid) >= min_pairs else None
            warning = rho is not None and abs(rho) >= absolute_rho_warning
            warnings += int(warning)
            pairs_result.append({
                "left": feature_a, "right": feature_b,
                "overlap_hypothesis": category,
                "complete_pair_n": len(valid),
                "spearman_rho": round(rho, 6) if rho is not None else None,
                "research_redundancy_warning": bool(warning),
                "insufficient_or_constant": rho is None,
            })
        results[market] = {
            "market_cohort_size": len(values),
            "hypothesis_pairs": pairs_result,
        }
    return {
        "schema": "v3-raw-feature-overlap-research-0.1",
        "asof": asof,
        "min_complete_pair_n": min_pairs,
        "heuristic_rho_warning_abs": float(absolute_rho_warning),
        "markets": results,
        "research_redundancy_warning_count": warnings,
        "scores_reweighted_by_audit": False,
        "overlapping_windows_are_independent_observations": False,
        "industry_and_company_exposures_independent": False,
        "cross_pillar_signal_correlation_verified_out_of_sample": False,
        "underlying_source_provenance_authenticated": False,
        "v3_reweighting_authorized": False,
        "confirmed_scores": None,
        "confirmed_ranks": None,
        "confirmed_buy": False,
    }
