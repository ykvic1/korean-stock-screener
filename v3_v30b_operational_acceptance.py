"""V3.0b development completion, distinct from historical investment certification."""
import argparse
import json
from pathlib import Path
from hashlib import sha256

from v3_operational_publication_audit import audit


def evaluate(ledger, publication):
    checks = {
        "fixed_date_and_roster": ledger.get("asof") == "2026-11-20" and ledger.get("listed_total") == 2592,
        "fixed_budget_accounted": ledger.get("all_100_point_budgets_accounted") is True,
        "fixed_gap_review_complete": ledger.get("fixed_data_review_complete") is True
            and ledger.get("pending_source_review_issuers") == 0
            and ledger.get("gap_disposition_counts", {}).get("pending", 0) == 0,
        "no_fabricated_source_inputs": ledger.get("missing_inputs_are_not_fabricated") is True
            and ledger.get("missing_component_earned_points") == 0,
        "separate_daily_ingestion": ledger.get("daily_ingestion_separate") is True,
        "post_cutoff_operations": publication.get("asof", "") >= "2026-11-23",
        "current_publication_coherent": publication.get("publication_coherent") is True
            and bool(publication.get("checks")) and all(publication["checks"].values()),
        "all_current_issuers_displayed": publication.get("pages", {}).get("status") == "coherent"
            and publication.get("pages", {}).get("detail_rows_checked") == publication.get("listed_total"),
    }
    return {"schema":"v30b-operational-development-acceptance-1",
            "operational_development_complete":all(checks.values()),"checks":checks,
            "development_asof":ledger.get("asof"),"development_listed":ledger.get("listed_total"),
            "development_observed_100":ledger.get("full_observed_issuers"),
            "pending_issuers":ledger.get("pending_source_review_issuers"),
            "terminal_zero_components":ledger.get("gap_disposition_counts",{}).get("terminal_zero",0),
            "operations_asof":publication.get("asof"),"operations_listed":publication.get("listed_total"),
            "operations_available_weight_distribution":publication.get("available_weight_distribution"),
            "investment_score_certified":False,"performance_validated":False,
            "historical_certification_separate":True,"wave_required":False,
            "provider_calls":0}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger",type=Path,required=True)
    p.add_argument("--docs",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    raw=a.ledger.read_bytes()
    result=evaluate(json.loads(raw),audit(a.docs))
    result["fixed_ledger_sha256"]=sha256(raw).hexdigest()
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False))
    if not result["operational_development_complete"]:
        raise SystemExit(2)


if __name__=="__main__":
    main()
