"""Derived-only DART FCF missingness audit, grounded in saved actual annual CF.

This produces aggregate company-year counts, never 0-valued replacements for
unseen CFO/PPE/intangible CAPEX. It does not fetch from any provider, repair
unobserved source accounts, certify historical PIT, or promote trading scores.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v3_dart_bulk_history import write_json
from v3_dart_cashflow_enrichment import (
    YEARS, CashflowError, asof_members, fingerprint, matched_scope,
    source_receipt_years, verify_state,
)

SCHEMA = "v3-annual-dart-fcf-missingness-source-audit-0.1"
CLASSES = (
    "missing_cfo_and_capex",
    "missing_cfo_only",
    "missing_both_capex",
    "missing_ppe_only",
    "missing_intangible_only",
    "fcf_observed_receipt_unmatched",
    "fcf_observed_receipt_matched",
)


class GapAuditError(ValueError):
    """Fixed text only; never include company codes, filings or numbers."""


def _classify(observation):
    cfo = observation["operating_cashflow_krw"]
    ppe = observation["ppe_capex_krw"]
    intangible = observation["intangible_capex_krw"]
    if cfo is None:
        return ("missing_cfo_and_capex" if ppe is None or intangible is None
                else "missing_cfo_only")
    if ppe is None and intangible is None:
        return "missing_both_capex"
    if ppe is None:
        return "missing_ppe_only"
    if intangible is None:
        return "missing_intangible_only"
    return ("fcf_observed_receipt_matched"
            if observation["major_annual_receipt_matched"] is True
            else "fcf_observed_receipt_unmatched")


def _counts():
    return {category: 0 for category in CLASSES}


def audit_cashflow_gaps(state, market, catalog, major):
    """Read only fully validated saved company-year observations.

    Unattempted firms and missing annual years are not counted as a zero or
    as a failed account. Categories partition observed attempts exactly.
    """
    try:
        verify_state(state, market, catalog, major)
        asof, members = asof_members(market, catalog, major)
    except (CashflowError, ValueError, TypeError):
        raise GapAuditError("invalid_saved_cashflow_source") from None
    overall = _counts()
    by_year = {str(year): _counts() for year in YEARS}
    by_scope = {"CFS": _counts(), "OFS": _counts()}
    for company in state["records"].values():
        scope = company["scope"]
        for year, observation in company["years"].items():
            category = _classify(observation)
            overall[category] += 1
            by_year[year][category] += 1
            by_scope[scope][category] += 1
    metrics = state["metrics"]
    attempted = sum(overall.values())
    missing = sum(overall[name] for name in CLASSES[:5])
    observed = sum(overall[name] for name in CLASSES[5:])
    if (attempted != metrics["attempted_company_years"]
        or observed != metrics["fcf_company_years"]
        or missing + observed != attempted
        or overall["fcf_observed_receipt_matched"]
        != metrics["receipt_matched_fcf_company_years"]
        or sum(sum(counts.values()) for counts in by_year.values()) != attempted
        or sum(sum(counts.values()) for counts in by_scope.values()) != attempted):
        raise GapAuditError("cashflow_gap_partition_mismatch")
    # Classify each current listed issuer once. This distinguishes absence of
    # a fetched fiscal year from a fetched statement with missing IFRS accounts.
    # It never infers that a missing capital-expenditure line is zero.
    issuer_depth = {
        "no_saved_cashflow_years": 0,
        "saved_incomplete_years": 0,
        "three_years_attempted_missing_accounts": 0,
        "three_years_with_fcf_but_unmatched_receipts": 0,
        "three_year_source_receipt_complete": 0,
    }
    pending_issuers = 0
    pending_years = 0
    missing_latest_receipt_issuers = 0
    for code in members:
        saved = state["records"].get(code)
        if saved is None:
            issuer_depth["no_saved_cashflow_years"] += 1
        else:
            years = saved["years"]
            if any(str(y) not in years for y in YEARS):
                issuer_depth["saved_incomplete_years"] += 1
            elif any(years[str(y)]["free_cashflow_krw"] is None
                     for y in YEARS):
                issuer_depth["three_years_attempted_missing_accounts"] += 1
            elif any(years[str(y)]["major_annual_receipt_matched"] is not True
                     for y in YEARS):
                issuer_depth["three_years_with_fcf_but_unmatched_receipts"] += 1
            else:
                issuer_depth["three_year_source_receipt_complete"] += 1

        source = major["records"].get(code)
        preferred = saved["scope"] if saved is not None else None
        scope = matched_scope(source, asof=asof, preferred_scope=preferred)
        if scope is None:
            missing_latest_receipt_issuers += 1
            continue
        if saved is not None and saved["scope"] != scope:
            # A different statement scope cannot be silently joined.
            continue
        supported_years = source_receipt_years(source, scope, asof=asof)
        observed_years = saved["years"] if saved is not None else {}
        outstanding = sum(str(y) not in observed_years for y in supported_years)
        if outstanding:
            pending_issuers += 1
            pending_years += outstanding
    if (sum(issuer_depth.values()) != len(members)
        or issuer_depth["three_year_source_receipt_complete"] !=
           metrics["three_year_fcf_receipt_matched_issuers"]
        or issuer_depth["no_saved_cashflow_years"] !=
           len(members) - len(state["records"])
        or pending_years > len(members) * len(YEARS) - attempted
        or pending_issuers > len(members)):
        raise GapAuditError("cashflow_issuer_gap_partition_mismatch")
    return {
        "schema": SCHEMA, "asof": asof,
        "source_cashflow_sha256": fingerprint(state),
        "listed_issuer_count": state["listed_total"],
        "issuers_with_saved_cashflow_years": len(state["records"]),
        "attempted_company_years": attempted,
        "observed_fcf_company_years": observed,
        "missing_fcf_company_years": missing,
        "categories": overall, "by_year": by_year, "by_scope": by_scope,
        "three_year_issuer_depth": issuer_depth,
        "source_eligible_unfetched_issuer_count": pending_issuers,
        "source_eligible_unfetched_company_years": pending_years,
        "issuers_without_verified_latest_annual_receipt": missing_latest_receipt_issuers,
        "unattempted_years_not_interpreted_as_zero": True,
        "missing_ifrs_account_not_interpreted_as_zero": True,
        "unmatched_receipt_not_certified": True,
        "historical_pit_verified": False,
        "provider_api_calls": 0,
        "raw_dart_account_rows_or_issuer_ids_written": False,
        "trading_score_promoted": False,
        "note": "Source-observed attempts only. Missing IFRS CFO or either CAPEX component remains unscored.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="docs/data.json")
    parser.add_argument("--catalog", default="docs/v22-market-catalog.json")
    parser.add_argument("--major", default="docs/v3-dart-bulk-history.json")
    parser.add_argument("--cashflow", default="docs/v3-dart-cashflow-reference.json")
    parser.add_argument("--out", default="docs/v3-dart-cashflow-gap-summary.json")
    parser.add_argument("--mode", choices=("write", "verify"), default="write")
    args = parser.parse_args(argv)

    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))

    report = audit_cashflow_gaps(
        read(args.cashflow), read(args.market), read(args.catalog),
        read(args.major))
    output = Path(args.out)
    if args.mode == "write":
        write_json(output, report)
    elif not output.is_file() or read(output) != report:
        raise GapAuditError("saved_cashflow_gap_report_changed")
    print(json.dumps({
        "status": "verified_saved_cashflow_missingness_only",
        "observed_fcf_company_years": report["observed_fcf_company_years"],
        "missing_fcf_company_years": report["missing_fcf_company_years"],
        "provider_api_calls": 0, "trading_score_promoted": False,
        "source_eligible_unfetched_company_years":
            report["source_eligible_unfetched_company_years"],
        "issuer_depth": report["three_year_issuer_depth"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
