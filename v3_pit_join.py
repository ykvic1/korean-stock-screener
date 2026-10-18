"""Fail-closed, OFFLINE point-in-time join of future v3 research evidence.

The inputs are untrusted declarations, not authenticated historical archives.
This module tests timestamp / issuer / session consistency and refuses
look-ahead. No price, financial values, flow values, portfolio orders or
credentials are returned. Even all internally matching inputs are NOT scored.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
import re

KST = ZoneInfo("Asia/Seoul")
SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"
MARKETS = frozenset({"KOSPI", "KOSDAQ"})
HEX = re.compile(r"^[a-f0-9]{64}$")
RECEIPT = re.compile(r"^[0-9]{14}$")
MAX_FILINGS = 400
MAX_MEMBERS = 20000
SCHEMA = "v3-pit-evidence-join-research-0.1"


class PITJoinError(ValueError):
    """Static reason codes only, with no sensitive raw payloads."""


def _day(value):
    if type(value) is not str or len(value) != 10:
        raise PITJoinError("invalid_trade_day")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise PITJoinError("invalid_trade_day") from None
    if d.isoformat() != value:
        raise PITJoinError("invalid_trade_day")
    return d


def _when(value):
    if type(value) is not str or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            value):
        raise PITJoinError("utc_second_timestamp_required")
    try:
        x = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise PITJoinError("utc_second_timestamp_required") from None
    return x.replace(tzinfo=timezone.utc)


def _identity(record, identity):
    if not isinstance(record, dict):
        raise PITJoinError("invalid_source_record")
    if any(record.get(k) != identity[k] for k in ("code", "issuer_id", "market")):
        raise PITJoinError("cross_issuer_or_market_contamination")


def _digest(value):
    if type(value) is not str or not HEX.fullmatch(value):
        raise PITJoinError("missing_untrusted_source_digest")


def _receipt(record, cutoff):
    if type(record.get("rcept_no")) is not str or not RECEIPT.fullmatch(
            record["rcept_no"]):
        raise PITJoinError("invalid_filing_receipt")
    _digest(record.get("source_sha256"))
    pub = _when(record.get("published_at_utc"))
    observed = _when(record.get("observed_at_utc"))
    if pub > observed:
        raise PITJoinError("filing_observed_before_publication")
    period_end = _day(record.get("period_end"))
    if period_end > pub.astimezone(KST).date():
        raise PITJoinError("financial_period_after_publication")
    if record.get("scope") not in ("CFS", "OFS"):
        raise PITJoinError("invalid_financial_statement_scope")
    if type(record.get("revision_no")) is not int or record["revision_no"] < 0:
        raise PITJoinError("invalid_filing_revision")
    return pub, observed, period_end


def join_asof_evidence(bundle, *, asof, decision_at_utc):
    """Join source contracts as of a *known* signal-day after-close instant.

    Reports unverified candidate presence, not numerical score eligibility.
    A receipt downloaded after the signal is NOT contemporaneous PIT evidence.
    """
    day = _day(asof)
    decision = _when(decision_at_utc)
    decision_kst = decision.astimezone(KST)
    if decision_kst.date() != day or decision_kst.time() < time(15, 30):
        raise PITJoinError("decision_must_be_on_signal_date_after_regular_close")
    if not isinstance(bundle, dict):
        raise PITJoinError("invalid_join_bundle")
    identity = {k: bundle.get(k) for k in ("code", "issuer_id", "market")}
    if (type(identity["code"]) is not str
            or not re.fullmatch("[0-9]{6}", identity["code"])
            or type(identity["issuer_id"]) is not str
            or not re.fullmatch("[0-9]{8}", identity["issuer_id"])
            or identity["market"] not in MARKETS):
        raise PITJoinError("invalid_join_identity")

    # Entire historical roster must be authenticated independently later.
    roster = bundle.get("roster")
    if not isinstance(roster, dict) or roster.get("asof") != asof:
        raise PITJoinError("missing_same_day_roster")
    seen_at = _when(roster.get("observed_at_utc"))
    members = roster.get("members")
    if (not isinstance(members, list) or not 0 < len(members) <= MAX_MEMBERS
            or any(type(x) is not str or not re.fullmatch("[0-9]{8}", x)
                   for x in members) or len(set(members)) != len(members)):
        raise PITJoinError("invalid_roster_members")
    if identity["issuer_id"] not in members:
        raise PITJoinError("security_not_in_historical_roster")
    roster_available = seen_at <= decision

    # Price/volume requires same-day and same corporate issuer, no post-close
    # reverse revisions. Distinguish asserted label from verified session.
    price = bundle.get("price")
    price_aligned = False
    price_late = False
    if price is not None:
        _identity(price, identity)
        if price.get("asof") != asof or price.get("session") != SESSION:
            raise PITJoinError("price_snapshot_wrong_session_or_date")
        _digest(price.get("raw_sha256"))
        observed = _when(price.get("observed_at_utc"))
        price_late = observed > decision
        price_aligned = not price_late

    flow = bundle.get("flow_ledger")
    flow_summary = None
    flow_late = False
    if flow is not None:
        _identity(flow, identity)
        if flow.get("asof") != asof:
            raise PITJoinError("flow_snapshot_wrong_date")
        receipt_at = _when(flow.get("observed_at_utc"))
        flow_late = receipt_at > decision
        # Let the standalone ledger validate units, numeric missingness, the
        # 20/60/120/250 windows and pension total taxonomy without promotion.
        from v3_ias_flow_ledger import audit_daily_flow, FlowLedgerError
        try:
            observed = audit_daily_flow(flow["ledger"], asof=asof)
        except (KeyError, FlowLedgerError, TypeError):
            raise PITJoinError("unusable_flow_ledger") from None
        if (observed["code"] != identity["code"]
                or observed["issuer_id"] != identity["issuer_id"]
                or observed["latest_observed_date"] != asof
                or flow["ledger"].get("market") != identity["market"]):
            raise PITJoinError("flow_ledger_different_issuer_or_stale")
        flow_summary = {
            "observed_session_rows": observed["observed_rows"],
            "fully_observed_windows": [
                n for n, x in observed["periods"].items()
                if all(v["data_complete"] for v in x["categories"].values())
            ],
            "available_at_decision": not flow_late,
        }

    # Historical filings: evaluate the revision actually observed before the
    # signal, never select the currently latest post-signal amendment.
    filings = bundle.get("filings", [])
    if not isinstance(filings, list) or len(filings) > MAX_FILINGS:
        raise PITJoinError("invalid_filing_collection")
    unique_receipts = set()
    unique_versions = set()
    eligible = {}
    ignored_after_decision = 0
    seen_scopes = set()
    for doc in filings:
        _identity(doc, identity)
        pub, observed, period_end = _receipt(doc, decision)
        receipt = doc["rcept_no"]
        if receipt in unique_receipts:
            raise PITJoinError("duplicate_filing_receipt")
        unique_receipts.add(receipt)
        group = (period_end, doc["scope"])
        version_key = (*group, doc["revision_no"])
        if version_key in unique_versions:
            raise PITJoinError("ambiguous_filing_revision")
        unique_versions.add(version_key)
        if pub > decision or observed > decision:
            ignored_after_decision += 1
            continue
        seen_scopes.add(doc["scope"])
        current = eligible.get(group)
        if current is None or (pub, doc["revision_no"], receipt) > (
                current[0], current[1], current[2]):
            eligible[group] = (pub, doc["revision_no"], receipt)
    # This scope counts eligible groups, not certified financial metrics.
    selected_scope = (
        "CFS" if "CFS" in seen_scopes else
        "OFS" if "OFS" in seen_scopes else None
    )
    usable_filing_periods = sorted(
        set(d.isoformat() for d, s in eligible if s == selected_scope)
    ) if selected_scope else []

    # Sector classification is date-dependent, not inferred from the current
    # company website / today's KRX classifications.
    sector = bundle.get("sector")
    sector_aligned = False
    sector_future_or_late = False
    if sector is not None:
        _identity(sector, identity)
        start = _day(sector.get("valid_from"))
        end = _day(sector["valid_to"]) if sector.get("valid_to") else None
        if end is not None and end <= start:
            raise PITJoinError("invalid_sector_membership_interval")
        if type(sector.get("sector_code")) is not str or not re.fullmatch(
                "[A-Za-z0-9._-]{2,30}", sector["sector_code"]):
            raise PITJoinError("invalid_sector_code")
        observed = _when(sector.get("observed_at_utc"))
        sector_future_or_late = (
            not (start <= day and (end is None or day < end))
            or observed > decision)
        sector_aligned = not sector_future_or_late

    return {
        "schema": SCHEMA, "asof": asof,
        "decision_at_utc": decision_at_utc,
        "code": identity["code"], "issuer_id": identity["issuer_id"],
        "market": identity["market"],
        "roster": {
            "same_day_identity_declared": True,
            "observed_no_later_than_decision": roster_available,
        },
        "price": {
            "record_present": price is not None,
            "same_day_observed_before_decision": price_aligned,
            "post_signal_retrieval_detected": price_late,
        },
        "flow": flow_summary or {
            "observed_session_rows": None,
            "fully_observed_windows": [],
            "available_at_decision": False,
        },
        "financials": {
            "chosen_scope_research_only": selected_scope,
            "eligible_filing_periods": usable_filing_periods,
            "future_or_post_signal_filings_ignored": ignored_after_decision,
            "historical_revision_chain_authenticated": False,
        },
        "sector": {
            "membership_date_matched_before_decision": sector_aligned,
            "post_signal_or_out_of_interval": sector_future_or_late,
        },
        "join_date_identity_consistent": True,
        "all_inputs_retrieved_by_signal": bool(
            roster_available and price_aligned and flow_summary
            and flow_summary["available_at_decision"]
            and selected_scope and sector_aligned),
        "source_provenance_independently_authenticated": False,
        "complete_historical_universe_verified": False,
        "market_price_volume_regular_session_verified": False,
        "financial_statement_scope_pit_verified": False,
        "financial_values_pit_verified": False,
        "flow_and_sector_corporate_actions_verified": False,
        "backtesting_authorized": False,
        "v3_six_pillar_confirmed_score": None,
        "v3_release_ready": False,
        "confirmed_buy": False,
    }
