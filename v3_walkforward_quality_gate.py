"""Offline bridge: historical price integrity + point-in-time join + walk-forward.

A future corporate-action/quality boundary triggers LABEL QUALITY CENSORING,
not a claim that the event was predictable at the signal date. Such case
removal may produce informative censoring, so results are never authorized
forward returns or valid proof of strategy success.
"""
from __future__ import annotations

from datetime import date
from collections import Counter

from v3_historical_integrity import audit_price_events, IntegrityError
from v3_pit_join import join_asof_evidence, PITJoinError
from v3_walkforward_research import (
    HORIZONS, _check_case, evaluate, WalkForwardError
)

MAX_INPUT_CASES = 1500
SAFE_FIELDS = ("open", "high", "low", "close", "session", "date")


class ResearchGateError(ValueError):
    """No file content or private price values in raised messages."""


def gated_walkforward(candidates, *, split_date, embargo_days=252,
                      round_trip_cost_bps=30):
    """Separate unsafe/censored signal labels before ALL horizon aggregation.

    Candidate consists of:
      case: v3_walkforward_research case with issuer_id, decision_at_utc
      price_record: v3_historical_integrity audited-format unadjusted OHLCV
      pit_bundle: v3_pit_join raw declarations, validated here
    Case history must match its price_record's CONTIGUOUS future bars exactly.
    Even a fully internally aligned candidate is NOT provider authenticated.
    """
    if not isinstance(candidates, list) or len(candidates) > MAX_INPUT_CASES:
        raise ResearchGateError("invalid_gate_cohort")
    cases_for_period = {name: [] for name in HORIZONS}
    exclusions = {name: Counter() for name in HORIZONS}
    seen_signals = set()
    for payload in candidates:
        if not isinstance(payload, dict):
            raise ResearchGateError("invalid_gate_payload")
        case, record, pit = (
            payload.get("case"), payload.get("price_record"),
            payload.get("pit_bundle"))
        if not all(isinstance(x, dict) for x in (case, record, pit)):
            raise ResearchGateError("missing_integrity_inputs")
        if (type(case.get("issuer_id")) is not str
                or len(case["issuer_id"]) != 8
                or not case["issuer_id"].isascii()
                or not case["issuer_id"].isdigit()):
            raise ResearchGateError("missing_issuer_identity")
        identity = (case.get("issuer_id"), case.get("code"))
        if (record.get("issuer_id"), record.get("code")) != identity:
            raise ResearchGateError("price_issuer_mismatch")
        if (pit.get("issuer_id"), pit.get("code")) != identity:
            raise ResearchGateError("pit_issuer_mismatch")
        if pit.get("market") != record.get("market"):
            raise ResearchGateError("market_mismatch")
        signal = case.get("signal_date")
        signal_key = (*identity, signal)
        if signal_key in seen_signals:
            raise ResearchGateError("duplicate_issuer_signal")
        seen_signals.add(signal_key)
        try:
            _check_case(case)
            audit = audit_price_events(
                record, asof=record["bars"][-1]["date"])
            joined = join_asof_evidence(
                pit, asof=signal,
                decision_at_utc=case["decision_at_utc"])
        except (KeyError, IndexError, TypeError, PITJoinError,
                IntegrityError, WalkForwardError):
            raise ResearchGateError("unusable_historical_research_input") from None
        # A declared same issuer/cutoff join is not enough if its prices, flow,
        # roster, financials or sector arrived only after the signal.
        if (not joined["join_date_identity_consistent"]
                or not joined["all_inputs_retrieved_by_signal"]):
            for name in HORIZONS:
                exclusions[name]["point_in_time_metadata_unavailable"] += 1
            continue
        dates = [r["date"] for r in record["bars"]]
        try:
            signal_index = dates.index(signal)
        except ValueError:
            raise ResearchGateError("signal_not_in_source_price_series") from None
        bars = case["bars"]
        origin = record["bars"][signal_index + 1: signal_index + 1 + len(bars)]
        if len(origin) != len(bars):
            raise ResearchGateError("noncontiguous_or_insufficient_original_bars")
        for reference, claimed in zip(origin, bars):
            if any(claimed.get(f) != reference.get(f) for f in SAFE_FIELDS):
                raise ResearchGateError("adjusted_fabricated_or_gapped_price_history")
        unsafe_signal = signal in audit["excluded_signal_dates"]
        unsafe_dates = set(audit["excluded_signal_dates"])
        boundary_dates = {flag["date"] for flag in audit["boundary_flags"]}
        for name, horizon in HORIZONS.items():
            if unsafe_signal:
                exclusions[name]["signal_within_corporate_event_quarantine"] += 1
                continue
            # Future event boundaries cannot be assumed known at signal time.
            # Remove from retrospective labels, and count explicit censoring.
            label_days = {b["date"] for b in bars[:horizon]}
            if label_days & boundary_dates or label_days & unsafe_dates:
                exclusions[name]["post_signal_label_quality_censored"] += 1
                continue
            if len(bars) < horizon:
                exclusions[name]["right_censored_short_history"] += 1
            cases_for_period[name].append(case)

    periods = {}
    for name in HORIZONS:
        research = evaluate(
            cases_for_period[name], split_date=split_date,
            embargo_days=embargo_days,
            round_trip_cost_bps=round_trip_cost_bps)
        periods[name] = {
            "sessions_proxy": HORIZONS[name],
            "internally_eligible_cases_submitted": len(cases_for_period[name]),
            "quality_exclusion_counts": dict(sorted(exclusions[name].items())),
            "exploratory_walkforward": research["periods"][name],
            "informative_censoring_risk_unresolved": True,
            "usable_for_investment_decision": False,
        }
    return {
        "schema": "v3-event-quarantine-walkforward-research-0.1",
        "input_case_count": len(candidates),
        "periods": periods,
        "pit_provider_archive_authenticated": False,
        "corporate_actions_officially_certified": False,
        "survivorship_and_censoring_bias_resolved": False,
        "model_parameters_frozen_out_of_sample": False,
        "historical_results_are_not_expected_returns": True,
        "confirmed_forecast": None,
        "confirmed_v3_score": None,
        "v3_release_ready": False,
        "confirmed_buy": False,
    }
