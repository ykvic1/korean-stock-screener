"""Research-only 4+7 candidate points from dated 121-session price-path facts.

Only the *provisional observable-weight* V3 report may use these heuristics.
The original FDR close/volume bars have not been independently reconciled
with official KRX adjustments, and this is NOT a certified investment score.
This function makes no provider calls and cannot mint a verified 100 score.
"""
from __future__ import annotations

from v3_unscored_path_observations import valid_observation

SCHEMA = "v3-path-121session-provisional-factor-preview-0.1"
VOLUME_MAX = 4
MOMENTUM_MAX = 7


class ProvisionalPathFactorError(ValueError):
    """Fixed source-contract error; never log raw quotes."""


def preview(stock: dict, *, asof: str):
    if type(stock) is not dict or stock.get("price_date") != asof:
        raise ProvisionalPathFactorError("path_issuer_date_mismatch")
    source = stock.get("v3_unscored_path_observations")
    if source is None:
        return None
    if not valid_observation(source, asof):
        raise ProvisionalPathFactorError("path_source_receipt_invalid")
    # The 4pt event is observed only after a qualifying high-volume UP day
    # and five fully completed follow-up sessions. Absent events earn 0,
    # NEVER a fabricated missing observation or a forward-tested outcome.
    absorption = 4 if source["post_spike_price_hold_with_volume_easing_events"] > 0 else 0
    up_days = source["up_close_days20"]
    positive_days = 2 if up_days >= 13 else 1 if up_days >= 10 else 0
    blocks = source["positive_nonoverlapping_five_interval_blocks60"]
    persistence = 3 if blocks >= 9 else 2 if blocks >= 7 else 1 if blocks >= 5 else 0
    prior = source["prior60_close_change_pct"]
    recent = source["recent60_close_change_pct"]
    # Two ordered 60-session paths yield a bounded 120-session rebound
    # candidate. It is NOT breadth across issuers or official wave counting.
    rebound = (2 if prior <= -5 and recent >= 5 else
               1 if prior < 0 and recent >= prior + 5 else 0)
    momentum = positive_days + persistence + rebound
    if not 0 <= absorption <= VOLUME_MAX or not 0 <= momentum <= MOMENTUM_MAX:
        raise ProvisionalPathFactorError("path_subfactor_weight_overflow")
    return {
        "schema": SCHEMA, "asof": asof,
        "source_schema": source["schema"],
        "research_volume_post_spike_absorption_points": absorption,
        "research_momentum_positive_days20_points": positive_days,
        "research_momentum_trend_persistence60_points": persistence,
        "research_momentum_two_segment_120day_rebound_points": rebound,
        "research_momentum_additional_points": momentum,
        "research_available_volume_weight": VOLUME_MAX,
        "research_available_momentum_weight": MOMENTUM_MAX,
        "source_independently_krx_adjusted_verified": False,
        "official_additional_investment_points_authorized": 0,
        "investable_100_point_score": None,
        "historical_hindsight_used": False,
        "release_ready": False,
    }
