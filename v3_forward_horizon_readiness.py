"""Fail-closed, OFFLINE forward-horizon *readiness* for frozen PIT cohorts.

This is not a backtest or a return calculator. A cohort is frozen at the
signal-date regular close, and 1/2/3/6/12 calendar-month outcome probes are
reconciled without omitting delisted or missing issuers. Even apparently complete
probes are unauthenticated and cannot certify adjusted returns or buy signals.
No provider calls, prices, issuer IDs, trading scores, or returns are exported.
"""
from __future__ import annotations

import hashlib
import json
import calendar
from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo

SCHEMA = "v3-pit-forward-horizon-readiness-research-0.1"
COHORT_SCHEMA = "v3-frozen-current-signal-cohort-unverified-0.1"
PROBE_SCHEMA = "v3-forward-outcome-structural-probe-unverified-0.1"
HORIZONS = (1, 2, 3, 6, 12)
MAX_ISSUERS = 6000
MAX_SETTLEMENT_LAG_DAYS = 10
KST = ZoneInfo("Asia/Seoul")
HEX = re.compile(r"[a-f0-9]{64}\Z")
CODE = re.compile(r"[0-9]{6}\Z")
ISSUER = re.compile(r"[0-9]{8}\Z")
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
MARKETS = frozenset(("KOSPI", "KOSDAQ"))
PROBE_KEYS = frozenset((
    "code", "issuer_id", "market", "horizon_months", "state",
    "source_sha256", "retrieved_at_utc",
    "observed_date", "delisted_on",
    "adjusted_regular_session_verified",
    "total_return_corporate_actions_verified",
))


class ForwardAuditError(ValueError):
    """Static reason codes only; never echo untrusted company observations."""


def _day(value):
    if type(value) is not str or len(value) != 10:
        raise ForwardAuditError("bad_horizon_day")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise ForwardAuditError("bad_horizon_day") from None
    if day.isoformat() != value:
        raise ForwardAuditError("bad_horizon_day")
    return day


def _utc(value):
    if type(value) is not str or not TIMESTAMP.fullmatch(value):
        raise ForwardAuditError("bad_horizon_timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ForwardAuditError("bad_horizon_timestamp") from None
    return parsed.replace(tzinfo=timezone.utc)


def _hash(value):
    if type(value) is not str or not HEX.fullmatch(value):
        raise ForwardAuditError("missing_untrusted_horizon_digest")


def _identity(row):
    if (type(row) is not dict or type(row.get("code")) is not str
        or not CODE.fullmatch(row["code"])
        or type(row.get("issuer_id")) is not str
        or not ISSUER.fullmatch(row["issuer_id"])
        or row.get("market") not in MARKETS):
        raise ForwardAuditError("bad_horizon_issuer_identity")
    return row["code"], row["issuer_id"], row["market"]


def _anniversary(day, months):
    """Calendar months clamp month-end; no fabricated exchange sessions."""
    m = day.month - 1 + months
    year = day.year + m // 12
    month = m % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def cohort_selection_digest(cohort):
    """Bind the exact membership and decision cutoff, not a caller's label.

    Canonical ordering allows harmless transport reorderings. This detects
    accidental/tampered membership under a saved digest, not independent PIT
    authenticity: a caller can still replace both payload and digest.
    """
    members = cohort.get("members")
    if type(members) is not list or not 1 <= len(members) <= MAX_ISSUERS:
        raise ForwardAuditError("invalid_horizon_issuer_or_probe_count")
    for row in members:
        if type(row) is not dict or set(row) != {"code", "issuer_id", "market"}:
            raise ForwardAuditError("invalid_frozen_cohort_identity_fields")
        _identity(row)
    _day(cohort.get("signal_date"))
    _utc(cohort.get("decision_at_utc"))
    payload = {
        "schema": COHORT_SCHEMA,
        "signal_date": cohort["signal_date"],
        "decision_at_utc": cohort["decision_at_utc"],
        "members": sorted(members, key=lambda r: (r["code"], r["issuer_id"], r["market"])),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True).encode("utf8")).hexdigest()


def audit_forward_readiness(cohort, probes, *, assessed_at_utc):
    """Summarize every selected issuer at every horizon, including all gaps.

    No source-provided "verified" flag is treated as independent evidence.
    A missing/delisted outcome NEVER becomes a zero return or a dropped member.
    """
    if type(cohort) is not dict or type(probes) is not dict:
        raise ForwardAuditError("bad_horizon_sources")
    if (cohort.get("schema") != COHORT_SCHEMA or
        probes.get("schema") != PROBE_SCHEMA or
        cohort.get("signal_date") != probes.get("signal_date") or
        cohort.get("selection_sha256") != probes.get("selection_sha256") or
        cohort.get("historical_issuer_pit_verified") is not False or
        cohort.get("signal_score_certified") is not False or
        probes.get("adjusted_returns_certified") is not False or
        probes.get("corporate_actions_certified") is not False):
        raise ForwardAuditError("horizon_source_contract_or_certification_invalid")
    signal = _day(cohort.get("signal_date"))
    decision = _utc(cohort.get("decision_at_utc"))
    assessed = _utc(assessed_at_utc)
    _hash(cohort.get("selection_sha256"))
    if (decision.astimezone(KST).date() != signal or
        decision.astimezone(KST).time() < time(15, 30) or
        assessed < decision):
        raise ForwardAuditError("horizon_signal_or_assessment_time_invalid")
    members = cohort.get("members")
    if (type(members) is not list or not 1 <= len(members) <= MAX_ISSUERS or
        type(probes.get("rows")) is not list or
        len(probes["rows"]) > len(members) * len(HORIZONS)):
        raise ForwardAuditError("invalid_horizon_issuer_or_probe_count")
    identities = {}
    codes = set()
    issuers = set()
    for row in members:
        if type(row) is not dict or set(row) != {"code", "issuer_id", "market"}:
            raise ForwardAuditError("invalid_frozen_cohort_identity_fields")
        code, issuer, market = _identity(row)
        if code in codes or issuer in issuers:
            raise ForwardAuditError("duplicate_or_remapped_frozen_issuer")
        codes.add(code)
        issuers.add(issuer)
        identities[code] = (issuer, market)

    if cohort_selection_digest(cohort) != cohort["selection_sha256"]:
        raise ForwardAuditError("frozen_cohort_selection_digest_mismatch")

    buckets = {n: {
        "full_frozen_cohort_count": len(members),
        "not_yet_matured": 0,
        "observed_close_unverified": 0,
        "delisted_return_unresolved": 0,
        "source_missing_or_unverified": 0,
    } for n in HORIZONS}
    by_key = {}
    for row in probes["rows"]:
        if type(row) is not dict or set(row) - PROBE_KEYS:
            raise ForwardAuditError("unknown_or_malformed_horizon_probe")
        code, issuer, market = _identity(row)
        if code not in identities or identities[code] != (issuer, market):
            raise ForwardAuditError("cross_issuer_forward_outcome_contamination")
        n = row.get("horizon_months")
        if type(n) is not int or n not in HORIZONS:
            raise ForwardAuditError("unknown_forward_horizon")
        key = (code, n)
        if key in by_key:
            raise ForwardAuditError("duplicate_horizon_outcome")
        by_key[key] = row
        if (row.get("state") not in ("observed_close", "delisted_unresolved") or
            row.get("adjusted_regular_session_verified") is not False or
            row.get("total_return_corporate_actions_verified") is not False):
            raise ForwardAuditError("untrusted_forward_outcome_certification")
        _hash(row.get("source_sha256"))
        retrieved = _utc(row.get("retrieved_at_utc"))
        if retrieved > assessed or retrieved <= decision:
            raise ForwardAuditError("outcome_before_signal_or_after_assessment")
        target = _anniversary(signal, n)
        if assessed.astimezone(KST).date() < target:
            raise ForwardAuditError("immature_forward_horizon_cannot_have_outcome")
        if row["state"] == "observed_close":
            if set(row) != PROBE_KEYS - {"delisted_on"}:
                raise ForwardAuditError("observed_horizon_field_shape_invalid")
            day = _day(row.get("observed_date"))
            if not target <= day <= target + timedelta(days=MAX_SETTLEMENT_LAG_DAYS):
                raise ForwardAuditError("observed_horizon_outside_target_window")
            if retrieved < datetime.combine(day, time(15, 30), KST).astimezone(timezone.utc):
                raise ForwardAuditError("outcome_retrieved_before_regular_close")
        else:
            if set(row) != PROBE_KEYS - {"observed_date"}:
                raise ForwardAuditError("delisted_horizon_field_shape_invalid")
            day = _day(row.get("delisted_on"))
            if not signal < day <= target:
                raise ForwardAuditError("delisted_outcome_outside_signal_window")
            if retrieved < datetime.combine(day, time(15, 30), KST).astimezone(timezone.utc):
                raise ForwardAuditError("delisting_observed_before_event")

    for n, summary in buckets.items():
        target = _anniversary(signal, n)
        if assessed.astimezone(KST).date() < target:
            summary["not_yet_matured"] = len(members)
            continue
        for code in identities:
            row = by_key.get((code, n))
            if row is None:
                summary["source_missing_or_unverified"] += 1
            elif row["state"] == "observed_close":
                summary["observed_close_unverified"] += 1
            else:
                summary["delisted_return_unresolved"] += 1
        if sum(v for k, v in summary.items() if k != "full_frozen_cohort_count") != len(members):
            raise ForwardAuditError("forward_horizon_census_not_reconciled")

    return {
        "schema": SCHEMA,
        "signal_date": signal.isoformat(),
        "assessed_at_utc": assessed_at_utc,
        "frozen_cohort_count": len(members),
        "horizons": {str(k): v for k, v in buckets.items()},
        "all_issuers_retained_in_denominator": True,
        "selection_payload_digest_recomputed": True,
        "delisted_outcomes_not_ignored_or_zero_filled": True,
        "future_or_unobserved_outcomes_not_fabricated": True,
        "independent_pit_universe_verified": False,
        "regular_session_adjusted_returns_verified": False,
        "corporate_action_total_returns_verified": False,
        "outcome_source_provenance_verified": False,
        "backtest_performance_authorized": False,
        "investment_score_or_buy_signal_authorized": False,
        "provider_calls": 0,
        "issuer_ids_or_prices_published": False,
        "note": "Strict structural readiness only. Month anniversaries are not "
                "verified exchange sessions; missing/delisted outcomes remain "
                "in the frozen denominator. No return, win rate or rank is issued.",
    }
