"""Frozen V3.0b operational release, independent of daily data and certification."""
import argparse
import json
from pathlib import Path
import subprocess

CUTOFF = "2026-11-20"
COMMIT = "6608c3c091c3b7252c926975a87e9af650e41e8a"
BANDS = ("100", "95_to_under_100", "90_to_under_95", "80_to_under_90",
         "70_to_under_80", "60_to_under_70", "under_60")
WEIGHTS = {"volume":25, "technical":30, "financial":12.5, "valuation":12.5, "momentum":20}
AUDIT_CHECKS = ("score_parts_totals_ranks_and_roster", "same_market_day",
                "every_operational_csv_cell", "priced_issuer_identity",
                "collection_reason_schema", "all_unpriced_issuers_explained",
                "examined_entire_roster", "zero_volume_display_retained",
                "pages_attachment_and_details")


def evaluate(scope, commit, audit, comparison, closeout, gap, receipts, krx):
    """No current-day or full-coverage inputs; missing observations remain missing."""
    accepted = scope.get("operational_development_acceptance", {})
    coverage = comparison.get("operational_coverage", {})
    dist = audit.get("available_weight_distribution", {})
    total = coverage.get("listed_total")
    ranked = coverage.get("ranked_count")
    distribution_valid = (type(total) is int and total > 0 and set(dist) == set(BANDS)
        and all(type(n) is int and n >= 0 for n in dist.values())
        and sum(dist.values()) == total and type(ranked) is int and 0 <= ranked <= total)
    checks = {
        "frozen_commit_and_cutoff": (commit == COMMIT == accepted.get("deployed_commit")
            and scope.get("development_cutoff_kst") == accepted.get("baseline_asof") == CUTOFF
            and scope.get("advance_development_cutoff_on_new_market_data") is False
            and scope.get("post_cutoff_ingestion") == "scheduled_operations"),
        "frozen_publication_replayed": (audit.get("asof") == comparison.get("asof") == CUTOFF
            and audit.get("publication_coherent") is True
            and all(audit.get("checks", {}).get(k) is True for k in AUDIT_CHECKS)
            and audit.get("unexplained_unpriced_count") == 0
            and audit.get("pages", {}).get("status") == "coherent"),
        "frozen_coverage_reconciled": (distribution_valid and audit.get("listed_total") == total
            == accepted.get("listed_total") and ranked == accepted.get("ranked_count")
            and coverage.get("available_100_count") == dist.get("100")
            and coverage.get("available_95_count") == sum(dist.get(k, 0) for k in BANDS[:2])
            and coverage.get("available_90_count") == sum(dist.get(k, 0) for k in BANDS[:3])
            and coverage.get("budgets") == WEIGHTS),
        "initial_collection_closed_with_missing": (
            closeout.get("schema") == "v3-initial-data-collection-closeout-1"
            and closeout.get("asof") == "2026-11-16"
            and closeout.get("initial_data_collection_complete") is True
            and closeout.get("missing_data_excluded_from_completion") is True
            and closeout.get("all_listed_100_weight_required") is False
            and closeout.get("missing_values_filled_or_weights_normalized") is False
            and closeout.get("daily_refresh_continues") is True
            and all(closeout.get("checks", {}).get(k) is True for k in
                ("publication_coherent", "available_fcf_sources_attempted",
                 "historical_receipt_collection_complete", "fixed_krx_window_extracted"))),
        "frozen_eligible_collection_attempted": (gap.get("asof") == CUTOFF
            and gap.get("listed_issuer_count") == total
            and gap.get("source_eligible_unfetched_company_years") == 0
            and gap.get("missing_ifrs_account_not_interpreted_as_zero") is True
            and receipts.get("filing_months_checkpointed") == receipts.get("filing_months_total") == 120
            and receipts.get("filing_months_remaining") == 0
            and krx.get("five_session_cohorts_completed") == krx.get("five_session_cohorts_target") == 490
            and krx.get("cross_cohort_bridges_completed") == krx.get("cross_cohort_bridges_required_for_completed_source") == 489
            and krx.get("source_history_coverage_ratio") == 1),
        "no_unearned_certification": (coverage.get("missing_is_not_zero") is True
            and all(d.get("investment_score_certified") is False
                and d.get("performance_validated") is False for d in (coverage, audit, closeout))
            and scope.get("wave_validation_required") is False
            and scope.get("forward_performance_required_for_release") is False
            and audit.get("provider_calls") == coverage.get("provider_calls") == 0),
    }
    ready = all(checks.values())
    return {"schema":"v30b-frozen-operational-release-1", "asof":CUTOFF,
        "baseline_commit":COMMIT, "checks":checks,
        "operational_release_ready":ready, "development_complete":ready,
        "daily_data_refresh_continues":True, "daily_data_changes_reopen_development":False,
        "all_listed_100_weight_required":False,
        "investment_score_certified":False, "performance_validated":False,
        "historical_source_certification_required_for_operational_release":False,
        "listed_total":total, "ranked_count":ranked,
        "available_weight_distribution":dist if distribution_valid else None,
        "historical_research_followup":scope.get("remaining_fixed_snapshot_verification", []),
        "provider_calls":0, "blockers":[k for k,v in checks.items() if not v]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--scope", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    baseline = a.baseline.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=baseline, text=True).strip()
    if commit != COMMIT or subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=baseline, text=True).strip():
        raise SystemExit("wrong_or_modified_frozen_baseline")
    raw = subprocess.check_output(["python", "-B", "v3_operational_publication_audit.py"],
                                  cwd=baseline, text=True)
    read = lambda name: json.loads((baseline / "docs" / name).read_text())
    result = evaluate(json.loads(a.scope.read_text()), commit, json.loads(raw),
        read("v3-operational-research-comparison.json"),
        read("v3-data-collection-closeout.json"), read("v3-dart-cashflow-gap-summary.json"),
        read("v3-dart-historical-receipt-progress.json"), read("v3-krx-derived-backfill-progress.json"))
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    a.out.write_text(payload)
    print(payload, end="")
    if not result["operational_release_ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
