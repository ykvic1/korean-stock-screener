"""Verify deliberately unpriced historical holder-date gaps without backfills.

For each of 17 original securities and all 244 eligible trading sessions,
partition holder positions into (a) previously authenticated original/successor
daily CLOSE observations and (b) unpriced legal/market gaps, classified using
the fixed share exchange, cash settlement and final-trade disclosures.

These disclosures are frozen historical research assertions, NOT a newly
certified point-in-time register, exchange effective date, adjusted total
return, or a trading recommendation. Never fill a quote during a legal gap.
Private issuer IDs and quotes are discarded before publication.
"""
from collections import Counter, defaultdict
from datetime import date

from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import ROOT, day, ledger_rows, traded
from v3_historical_official_holder_terminal import require
from v3_historical_official_holder_daily_path import valid_summary as valid_daily

SCHEMA = "v3-historical-official-holder-unpriced-gap-boundaries-v1"
CAUSES = (
    "exchange_waiting_for_effect",
    "exchange_effective_before_share_trading",
    "cash_waiting_for_effect",
    "cash_effective_before_payment",
    "cash_entitlement_no_market_shares",
    "post_liquidation_trade_no_mark",
    "unresolved_distribution_no_mark",
)
EVIDENCE = (
    "old_security_absent_in_both_sources",
    "old_security_untraded_in_both_sources",
    "original_trade_after_frozen_last_trade",
    "one_source_missing",
    "old_security_identity_mismatch",
    "old_security_listed_shares_mismatch",
    "old_security_trade_status_mismatch",
)
PROVEN_EVIDENCE = set(EVIDENCE[:2])


def gap_plan(p, root=ROOT):
    """Classify all dates without a tradeable holder mark; no invented OHLC."""
    require(type(p) is dict and len(p["days"]) == 244
            and len(p["reference"]) == 17 and p["days"] == sorted(set(p["days"]))
            and sum(map(len, p["by_day"].values())) == 3461,
            "historical_holder_gap_plan_scope_changed")
    original = set(p["reference"])
    trades = {r["code"]: r for r in ledger_rows(root, "delisting-trade-observations")
              if r["code"] in original}
    settlements = {r["code"]: r for r in ledger_rows(root, "delisting-settlement")
                   if r["code"] in original}
    require(original == trades.keys() == settlements.keys(),
            "historical_holder_gap_roster_mismatch")
    planned_trading = {
        (origin, session)
        for session, rows in p["by_day"].items()
        for origin, symbol, units in rows
    }
    require(len(planned_trading) == 3461
            and all(session in p["days"] and origin in original
                    for origin, session in planned_trading),
            "historical_gap_duplicate_or_invalid_trading_plan")
    gap_by_day = defaultdict(list)
    transitions = Counter()
    for origin in sorted(original):
        t, s = trades[origin], settlements[origin]
        last = day(t["last_positive_volume_candidate"])
        treatment = s["treatment"]
        effective = day(s["event_on"]) if s["event_on"] else None
        listing = (day(s["settlement_or_share_availability_on"])
                   if s["settlement_or_share_availability_on"] else None)
        if treatment == "stock_exchange":
            require(effective and listing and last < effective <= listing
                    and s["successor_code"],
                    "invalid_stock_exchange_effective_gap_dates")
        elif treatment == "cash_exchange":
            require(effective and listing and last < effective <= listing
                    and not s["successor_code"],
                    "invalid_cash_claim_effective_gap_dates")
        elif treatment == "liquidation_trading":
            require(not effective and not listing and
                    day(s["liquidation_last_on"]) == last,
                    "invalid_liquidation_trade_termination")
        elif treatment == "distribution_unresolved":
            require(not effective and not listing and not s["successor_code"],
                    "invalid_unresolved_distribution_boundary")
        else:
            require(False, "unknown_historical_holder_gap_treatment")
        for session in p["days"]:
            if (origin, session) in planned_trading:
                continue
            require(session > last, "unpriced_day_before_frozen_last_trade")
            if treatment == "stock_exchange":
                require(session < listing,
                        "missing_holder_mark_after_successor_share_listing")
                cause = ("exchange_waiting_for_effect" if session < effective
                         else "exchange_effective_before_share_trading")
            elif treatment == "cash_exchange":
                if session < effective:
                    cause = "cash_waiting_for_effect"
                elif session < listing:
                    cause = "cash_effective_before_payment"
                else:
                    cause = "cash_entitlement_no_market_shares"
            elif treatment == "liquidation_trading":
                cause = "post_liquidation_trade_no_mark"
            else:
                cause = "unresolved_distribution_no_mark"
            transitions[cause] += 1
            gap_by_day[session].append((origin, cause))
    gap_total = sum(map(len, gap_by_day.values()))
    require(gap_total == 17 * len(p["days"]) - len(planned_trading) == 687
            and len(transitions) >= 5
            and set(transitions) <= set(CAUSES),
            "historical_gap_calendar_partition_changed")
    return {
        "sessions": p["days"],
        "by_day": dict(gap_by_day),
        "causes": dict(transitions),
        "total": gap_total,
        "traded_or_suspended_holder_rows": len(planned_trading),
        "eligible_holder_day_cells": len(p["days"]) * len(original),
    }


def observe_gap(public, official, session, targets):
    """One original issuer's *non-trade status*, never its quote price."""
    answers = []
    for origin, cause in targets:
        require(cause in CAUSES, "unknown_historical_gap_cause")
        src, krx = public.get(origin), official.get(origin)
        if src is None and krx is None:
            evidence = "old_security_absent_in_both_sources"
        elif src is None or krx is None:
            evidence = "one_source_missing"
        elif (src[0] != krx[0] or not normalized_name(src[1])
              or normalized_name(src[1]) != normalized_name(krx[1])):
            evidence = "old_security_identity_mismatch"
        elif (type(src[2]) not in (int, float) or type(krx[3]) is not int
              or src[2] != krx[3]):
            evidence = "old_security_listed_shares_mismatch"
        elif traded(src) or krx[5] is True:
            evidence = "original_trade_after_frozen_last_trade"
        elif krx[5] is not False:
            evidence = "old_security_trade_status_mismatch"
        else:
            evidence = "old_security_untraded_in_both_sources"
        answers.append((origin, session, cause, evidence))
    return answers


def summarize(plan, observations, daily):
    """Public anonymous cause/proof counts; reject all fabricated OHLC."""
    require(type(plan) is dict and type(observations) is list
            and plan["total"] == 687 and plan["eligible_holder_day_cells"] == 4148
            and plan["traded_or_suspended_holder_rows"] == 3461,
            "incomplete_historical_gap_boundary_report")
    causes = Counter(dict.fromkeys(CAUSES, 0))
    sources = Counter(dict.fromkeys(EVIDENCE, 0))
    seen = set()
    for origin, session, cause, evidence in observations:
        require((origin, session) not in seen
                and session in plan["by_day"]
                and (origin, cause) in plan["by_day"][session]
                and cause in causes and evidence in sources,
                "invalid_or_duplicate_original_gap_observation")
        seen.add((origin, session))
        causes[cause] += 1
        sources[evidence] += 1
    require(sum(causes.values()) <= plan["total"],
            "extra_historical_gap_observation")
    missing = plan["total"] - len(seen)
    require(missing >= 0, "extra_historical_gap_observation")
    verified = sum(sources[k] for k in PROVEN_EVIDENCE)
    daily_pass = valid_daily(daily)
    success = (
        missing == 0
        and all(causes[k] == plan["causes"].get(k, 0) for k in CAUSES)
        and verified == plan["total"] and daily_pass
        and plan["traded_or_suspended_holder_rows"]
            + plan["total"] == plan["eligible_holder_day_cells"]
    )
    return {
        "schema": SCHEMA,
        "historical_original_issuers": 17,
        "market_sessions_after_entry": 244,
        "eligible_holder_day_cells": plan["eligible_holder_day_cells"],
        "quoted_or_halted_holder_date_cells": plan["traded_or_suspended_holder_rows"],
        "unpriced_corporate_gap_holder_date_cells": plan["total"],
        "verified_no_original_trade_after_last_trade": verified,
        "missing_historical_gap_date_observations": missing,
        "gap_causes": dict(causes),
        "official_nontrade_evidence": dict(sources),
        "all_corporate_action_gaps_accounted_for": success,
        "new_krx_market_api_requests": 0,
        "new_private_archive_reads": 0,
        "legal_effective_and_listing_dates_newly_authenticated": False,
        "future_entitlement_mark_to_market_verified": False,
        "continuous_adjusted_intraday_low_verified": False,
        "first_intraday_reference_touch_proven": False,
        "regular_only_volume_verified": False,
        "historical_pit_score_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def valid_summary(report):
    if type(report) is not dict or report.get("schema") != SCHEMA:
        return False
    causes, evidence = report.get("gap_causes"), report.get("official_nontrade_evidence")
    if (type(causes) is not dict or set(causes) != set(CAUSES)
            or type(evidence) is not dict or set(evidence) != set(EVIDENCE)
            or any(type(n) is not int or n < 0
                   for n in list(causes.values()) + list(evidence.values()))
            or report.get("historical_original_issuers") != 17
            or report.get("market_sessions_after_entry") != 244
            or report.get("eligible_holder_day_cells") != 4148
            or report.get("quoted_or_halted_holder_date_cells") != 3461
            or report.get("unpriced_corporate_gap_holder_date_cells") != 687
            or report.get("verified_no_original_trade_after_last_trade") != 687
            or report.get("missing_historical_gap_date_observations") != 0
            or sum(causes.values()) != 687 or sum(evidence.values()) != 687
            or any(evidence[k] != 0 for k in set(EVIDENCE) - PROVEN_EVIDENCE)
            or report.get("all_corporate_action_gaps_accounted_for") is not True
            or any(report.get(k) != 0 for k in (
                "new_krx_market_api_requests", "new_private_archive_reads"))
            or any(report.get(k) is not False for k in (
                "legal_effective_and_listing_dates_newly_authenticated",
                "future_entitlement_mark_to_market_verified",
                "continuous_adjusted_intraday_low_verified",
                "first_intraday_reference_touch_proven",
                "regular_only_volume_verified",
                "historical_pit_score_verified",
                "performance_validated", "v30b_release_ready"))):
        return False
    return True
