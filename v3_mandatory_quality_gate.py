"""Mandatory v3 research quality gate, NOT a certification of market facts.

Any missing proof blocks escalation. Every supplied evidence bundle is checked
against the exact price bars used for scoring, the same issuer and the exact
as-of decision instant. Internal consistency never proves official provenance.
"""
from __future__ import annotations

from v3_pit_join import join_asof_evidence, PITJoinError
from v3_historical_integrity import audit_price_events, IntegrityError

FIELDS = ("date", "session", "open", "high", "low", "close", "volume")
class InputQualityError(ValueError):
    """Fixed code, never echo source payloads."""


def check_research_quality(*, asof, code, issuer_id, market,
                           price_bars, evidence=None):
    reasons = []
    if evidence is None:
        reasons.append("missing_historical_pit_and_event_evidence")
        return _report(reasons, False)
    if not isinstance(evidence, dict):
        raise InputQualityError("invalid_quality_bundle")
    pit = evidence.get("pit_bundle")
    events = evidence.get("price_record")
    decision = evidence.get("decision_at_utc")
    if not isinstance(pit, dict) or not isinstance(events, dict) or type(decision) is not str:
        raise InputQualityError("incomplete_quality_evidence")
    for record in (pit, events):
        if any(record.get(k) != v for k, v in (
                ("code", code), ("issuer_id", issuer_id), ("market", market))):
            raise InputQualityError("quality_issuer_or_market_mismatch")
    if not isinstance(price_bars, list) or not price_bars:
        raise InputQualityError("missing_original_price_for_verification")
    recorded = events.get("bars")
    if not isinstance(recorded, list) or len(recorded) < len(price_bars):
        raise InputQualityError("missing_independent_price_observations")
    expected = recorded[-len(price_bars):]
    for original, claimed in zip(expected, price_bars):
        if not isinstance(original, dict) or not isinstance(claimed, dict):
            raise InputQualityError("invalid_quality_price_row")
        if any(original.get(k) != claimed.get(k) for k in FIELDS):
            raise InputQualityError("price_scoring_and_evidence_disagree")
    if expected[-1].get("date") != asof:
        raise InputQualityError("wrong_quality_signal_asof")
    try:
        audit = audit_price_events(events, asof=asof)
        joined = join_asof_evidence(pit, asof=asof, decision_at_utc=decision)
    except (IntegrityError, PITJoinError, KeyError, TypeError, IndexError):
        raise InputQualityError("invalid_pit_or_corporate_action_evidence") from None
    if asof in audit["excluded_signal_dates"]:
        reasons.append("signal_near_unverified_corporate_event")
    if not joined["all_inputs_retrieved_by_signal"]:
        reasons.append("snapshot_not_available_at_decision")
    return _report(reasons, not reasons)


def _report(reasons, internally_aligned):
    return {
        "schema": "v3-mandatory-quality-gate-research-0.1",
        "status": ("internally_aligned_not_authenticated"
                   if internally_aligned else "blocked"),
        "blocking_reasons": sorted(reasons),
        "historical_evidence_structurally_aligned": bool(internally_aligned),
        "independently_authenticated": False,
        "official_regular_session_verified": False,
        "point_in_time_universe_certified": False,
        "corporate_action_source_certified": False,
        "eligible_for_confirmed_scoring": False,
        "eligible_for_real_backtesting": False,
        "confirmed_buy": False,
    }
