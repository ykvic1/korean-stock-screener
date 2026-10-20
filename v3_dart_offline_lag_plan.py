"""Read-only offline four-year DART cohort viability with snapshot-date safety.

A 2026-10-20 market and 2026-10-19 DART annual snapshot are not a same-day
census. This AUDIT skips only the dependent cohort plan, records lag, and
leaves the strict live four-year OpenDART plan() unchanged. Missing is NOT zero.
"""
from __future__ import annotations

from datetime import date
from v3_dart_four_year_reference import plan
from v3_dart_rolling_book_reference import _inputs

SCHEMA="v3-dart-four-year-offline-candidate-plan-0.2"


class OfflineDARTAuditError(ValueError):
    pass


def _day(raw):
    if type(raw) is not str:
        raise OfflineDARTAuditError("invalid_audit_source_date")
    try:
        day=date.fromisoformat(raw)
    except ValueError:
        raise OfflineDARTAuditError("invalid_audit_source_date") from None
    if day.isoformat()!=raw:
        raise OfflineDARTAuditError("invalid_audit_source_date")
    return day


def report(market,catalog,book,*,prior=None,limit=2):
    market_day,members=_inputs(market,catalog)  # fail closed on malformed market
    if not isinstance(book,dict) or not isinstance(book.get("records"),list):
        raise OfflineDARTAuditError("invalid_annual_source_snapshot")
    book_day=_day(book.get("asof"))
    current=_day(market_day)
    if book_day>current:
        raise OfflineDARTAuditError("future_annual_book_must_not_be_used")
    if prior is not None:
        if not isinstance(prior,dict) or not isinstance(prior.get("records"),list):
            raise OfflineDARTAuditError("invalid_prior_fouryear_audit_snapshot")
        prior_day=_day(prior.get("asof"))
        if prior_day>current:
            raise OfflineDARTAuditError("future_fouryear_source_not_allowed")
    output={
        "schema":SCHEMA,
        "asof":market_day,
        "listed_issuers":len(members),
        "annual_book_source_asof":book_day.isoformat(),
        "annual_book_lag_days":(current-book_day).days,
        "prior_fouryear_source_asof":prior.get("asof") if prior else None,
        "previously_piloted_issuers_unreconciled":len(prior["records"]) if prior else 0,
        "next_manual_pilot_cohort_count":None,
        "candidate_plan_status":"blocked_not_same_day_census",
        "reason":"annual_book_and_market_asof_differ",
        "api_calls":0,
        "auto_live_collection_enabled":False,
        "current_source_and_historical_pit_certified":False,
        "full_financial15_verified":False,
        "unverified_values_never_zero_filled":True,
    }
    if book_day!=current:
        # Do not falsely treat an older issuer book as if it belongs to today's
        # current listed universe. Other market/annual age auditors still report
        # independent data-quality breaches; nothing is papered over.
        return output
    day,listed,cache,chosen=plan(market,catalog,book,prior=prior,limit=limit)
    output.update({
        "candidate_plan_status":"ready_same_day_sources_unverified_fundamentals",
        "reason":None,
        "previously_piloted_issuers_unreconciled":len(cache),
        "next_manual_pilot_cohort_count":len(chosen),
        "core_financial_research_issuers_unverified":sum(
            bool(x.get("core_financial_observed_for_research"))
            for x in cache.values()),
    })
    return output
