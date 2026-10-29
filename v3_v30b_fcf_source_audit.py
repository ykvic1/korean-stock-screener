"""Read-only three-source V3.0b FCF count/receipt reconciliation.

Reconciles the already published DART cashflow gap aggregate, FCF preview,
and current five-area observed-score summary. This proves ONLY internal
arithmetic and same-source fingerprints. It cannot authenticate the original
DART filings, their accounting scope, historical PIT, or investment scores.
No network, API key, raw company amounts, or output file is required.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 2_000_000
CATEGORIES = (
    "missing_cfo_and_capex", "missing_cfo_only",
    "missing_both_capex", "missing_ppe_only",
    "missing_intangible_only", "fcf_observed_receipt_unmatched",
    "fcf_observed_receipt_matched",
)
DEPTH_CLASSES = (
    "no_saved_cashflow_years", "saved_incomplete_years",
    "three_year_source_receipt_complete",
    "three_years_attempted_missing_accounts",
    "three_years_with_fcf_but_unmatched_receipts",
)
YEARS = ("2023", "2024", "2025")


class FCFReconciliationError(ValueError):
    """Fixed error codes; no issuer, filing or numeric source values."""


def _assert(ok: bool, code: str) -> None:
    if not ok:
        raise FCFReconciliationError(code)


def _count(v: Any) -> bool:
    return type(v) is int and v >= 0


def _date(v: Any) -> bool:
    if type(v) is not str or len(v) != 10:
        return False
    try:
        return date.fromisoformat(v).isoformat() == v
    except ValueError:
        return False


def _count_map(value: Any, keys: tuple[str, ...], code: str) -> dict:
    _assert(type(value) is dict and set(value) == set(keys)
            and all(_count(x) for x in value.values()), code)
    return value


def _points(points: Any, available: Any, maximum: int) -> bool:
    if type(available) is not int or available not in (0, maximum):
        return False
    if available == 0:
        return points is None
    return (type(points) in (int, float) and math.isfinite(points)
            and 0 <= points <= maximum)


def audit_fcf_sources(observed: Any, gap: Any, preview: Any) -> dict:
    """Check three aggregate sources without manufacturing missing CF values."""
    _assert(all(type(x) is dict for x in (observed, gap, preview)),
            "fcf_public_inputs_invalid")
    day = observed.get("asof")
    total = observed.get("listed_total")
    _assert(_date(day) and day == gap.get("asof") == preview.get("asof"),
            "fcf_source_day_mismatch")
    _assert(_count(total) and total > 0
            and gap.get("listed_issuer_count") == total
            and preview.get("listed_total") == total,
            "fcf_listed_universe_mismatch")
    fingerprint = gap.get("source_cashflow_sha256")
    _assert(type(fingerprint) is str
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None
            and preview.get("cashflow_snapshot_sha256") == fingerprint,
            "fcf_source_fingerprint_mismatch")
    _assert(gap.get("historical_pit_verified") is False
            and gap.get("trading_score_promoted") is False
            and gap.get("unmatched_receipt_not_certified") is True
            and gap.get("missing_ifrs_account_not_interpreted_as_zero") is True
            and gap.get("unattempted_years_not_interpreted_as_zero") is True
            and gap.get("raw_dart_account_rows_or_issuer_ids_written") is False
            and preview.get("hist_PIT_verified") is False
            and preview.get("raw_source_rows_stored") is False
            and preview.get("source_same_filing_scope_receipt_checked") is True
            and preview.get("joined_to_main_v3_ranking") is False
            and preview.get("buy_recommendations_authorized") is False
            and preview.get("six_pillar_confirmed_100pt") is None
            and observed.get("certified_100point_count") == 0
            and observed.get("missing_weight_is_not_zero") is True,
            "fcf_untrusted_scoring_or_provenance_claim")

    counts = _count_map(gap.get("categories"), CATEGORIES,
                        "fcf_gap_categories_invalid")
    by_year = gap.get("by_year")
    by_scope = gap.get("by_scope")
    _assert(type(by_year) is dict and set(by_year) == set(YEARS)
            and type(by_scope) is dict and set(by_scope) == {"CFS", "OFS"},
            "fcf_gap_partitions_invalid")
    for bucket in list(by_year.values()) + list(by_scope.values()):
        _count_map(bucket, CATEGORIES, "fcf_gap_partitions_invalid")
    for category in CATEGORIES:
        _assert(sum(by_year[y][category] for y in YEARS) == counts[category]
                and sum(by_scope[s][category] for s in ("CFS", "OFS"))
                == counts[category], "fcf_gap_partitions_do_not_reconcile")
    attempted = gap.get("attempted_company_years")
    observed_years = gap.get("observed_fcf_company_years")
    missing_years = gap.get("missing_fcf_company_years")
    matched = counts["fcf_observed_receipt_matched"]
    unmatched = counts["fcf_observed_receipt_unmatched"]
    _assert(all(_count(v) for v in (attempted, observed_years, missing_years))
            and attempted == sum(counts.values())
            and attempted == observed_years + missing_years
            and observed_years == matched + unmatched,
            "fcf_gap_totals_do_not_reconcile")
    _assert(all(_count(gap.get(key)) for key in (
                "source_eligible_unfetched_company_years",
                "source_eligible_unfetched_issuer_count",
                "issuers_with_saved_cashflow_years",
                "issuers_without_verified_latest_annual_receipt"))
            and gap["issuers_with_saved_cashflow_years"] <= total
            and gap["issuers_without_verified_latest_annual_receipt"] <= total,
            "fcf_gap_unfetched_contract_invalid")

    depth = _count_map(gap.get("three_year_issuer_depth"), DEPTH_CLASSES,
                       "fcf_issuer_depth_invalid")
    _assert(sum(depth.values()) == total
            and depth["no_saved_cashflow_years"] ==
                total - gap["issuers_with_saved_cashflow_years"],
            "fcf_issuer_depth_does_not_reconcile")

    rows = preview.get("rows")
    _assert(type(rows) is list and len(rows) <= total,
            "fcf_preview_rows_invalid")
    finance, valuation, seen = 0, 0, set()
    for item in rows:
        _assert(type(item) is dict and type(item.get("code")) is str
                and re.fullmatch(r"[0-9]{6}", item["code"]) is not None
                and item["code"] not in seen
                and item.get("asof") == day
                and item.get("market") in ("KOSPI", "KOSDAQ")
                and item.get("strict_receipt_scope_matched") is True
                and item.get("historical_PIT_certified") is False
                and item.get("scoring_certified") is False,
                "fcf_preview_identity_or_certification_invalid")
        seen.add(item["code"])
        fw = item.get("financial_fcf_assets_available_max")
        vw = item.get("valuation_fcf_yield_available_max")
        _assert(_points(item.get("financial_fcf_assets_candidate_points"), fw, 4)
                and _points(item.get("valuation_fcf_yield_candidate_points"), vw, 5),
                "fcf_preview_points_or_observation_invalid")
        finance += int(fw == 4)
        valuation += int(vw == 5)
    _assert(finance == preview.get("financial_fcf4_research_candidate_issuer_count")
            and valuation == preview.get("valuation_fcf5_research_candidate_issuer_count")
            and len(rows) == preview.get("real_latest_fcf_yield_raw_issuer_count")
            and preview.get("real_fcf_assets_three_year_raw_issuer_count") >= finance,
            "fcf_preview_counts_do_not_reconcile")
    _assert(len(rows) <= by_year["2025"]["fcf_observed_receipt_matched"]
            and finance <= depth["three_year_source_receipt_complete"],
            "fcf_derived_coverage_exceeds_original_receipts")
    _assert(observed.get("fcf_observed_issuer_counts") ==
            {"financial4": finance, "valuation5": valuation},
            "fcf_current_scorecard_count_mismatch")
    components = observed.get("financial_valuation_component_observed_issuers")
    unobserved = observed.get("financial_valuation_component_unobserved_issuers")
    _assert(type(components) is dict and type(unobserved) is dict
            and components.get("financial_fcf_assets_3y") == finance
            and components.get("valuation_fcf_yield_latest") == valuation
            and unobserved.get("financial_fcf_assets_3y") == total - finance
            and unobserved.get("valuation_fcf_yield_latest") == total - valuation,
            "fcf_subfactor_observation_mismatch")

    missing_types = {k: counts[k] for k in CATEGORIES
                     if k not in ("fcf_observed_receipt_matched",
                                  "fcf_observed_receipt_unmatched")}
    return {
        "schema": "v3-v30b-fcf-three-source-internal-reconciliation-0.1",
        "asof": day,
        "listed_total": total,
        "cashflow_snapshot_reconciled": True,
        "attempted_company_years": attempted,
        "fcf_observed_company_years": observed_years,
        "fcf_missing_account_company_years": missing_years,
        "missing_account_reason_counts": missing_types,
        "source_eligible_unfetched_company_years":
            gap["source_eligible_unfetched_company_years"],
        "financial_fcf4_observed_issuers": finance,
        "valuation_fcf5_observed_issuers": valuation,
        "three_year_source_receipt_complete_issuers":
            depth["three_year_source_receipt_complete"],
        "independent_dart_receipt_authentication": False,
        "historical_pit_or_corporate_action_certified": False,
        "approved_final_100point_scoring": False,
        "official_issuer_score": None,
        "raw_issuer_or_filing_rows_in_output": False,
        "provider_calls": 0,
        "read_only": True,
    }


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_BYTES:
            raise FCFReconciliationError("fcf_input_exceeds_limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict:
            raise FCFReconciliationError("fcf_input_invalid")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise FCFReconciliationError("fcf_input_unavailable") from None


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path,
        default=ROOT / "docs/v3-current-observed-all-listed-summary.json")
    parser.add_argument("--gap", type=Path,
        default=ROOT / "docs/v3-dart-cashflow-gap-summary.json")
    parser.add_argument("--preview", type=Path,
        default=ROOT / "docs/v3-dart-fcf-research-preview.json")
    parser.add_argument("--assert-unverified", action="store_true")
    args = parser.parse_args(argv)
    report = audit_fcf_sources(_read(args.observed), _read(args.gap),
                               _read(args.preview))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if args.assert_unverified and (
        report["approved_final_100point_scoring"]
        or report["independent_dart_receipt_authentication"]
        or report["official_issuer_score"] is not None):
        raise SystemExit(3)
    return report


if __name__ == "__main__":
    main()
