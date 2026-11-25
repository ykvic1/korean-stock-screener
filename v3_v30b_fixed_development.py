"""Fixed-date development gap ledger; zero score is not fabricated source data."""
from collections import Counter
from datetime import date
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path

from v3_operational_evidence_priority import category, SHORT_PRICE_HISTORY
from v3_v30b_release_gate import R1D_PART_LIMITS, r1d_public_reconciles

CUTOFF = "2026-11-20"
DATA_BRANCH = "v30b-development-data-20261120"


def component(point, maximum, reason):
    earned, observed = (point[0], point[1]) if point else (0, 0)
    if not all(type(n) in (int, float) and math.isfinite(n) for n in (earned, observed, maximum)):
        raise ValueError("invalid_component_score")
    if not 0 <= earned <= observed <= maximum + .000001:
        raise ValueError("invalid_component_weight")
    missing = round(max(0, maximum - observed), 6)
    return {"maximum": maximum, "earned": earned, "observed_weight": observed,
            "zero_fallback_weight": missing, "zero_fallback_points": 0,
            "reason": reason if missing > .00001 else None,
            "source_review_pending": missing > .00001 and reason != SHORT_PRICE_HISTORY}


def cashflow_terminal_reviews(report, gap, issuer_evidence):
    """Close attempted account gaps only; no assumption about missing raw CFO/CAPEX."""
    from v3_dart_cashflow_gap_audit import CODE_BY_CLASS
    if (gap.get("asof") != CUTOFF or issuer_evidence.get("asof") != CUTOFF
        or gap.get("source_eligible_unfetched_company_years") != 0
        or gap.get("listed_issuer_count") != report["listed_total"]
        or issuer_evidence.get("listed_total") != report["listed_total"]
        or not gap.get("source_cashflow_sha256")
        or gap["source_cashflow_sha256"] != issuer_evidence.get("source_cashflow_sha256")
        or issuer_evidence.get("source_receipt_scope_checked") is not True
        or issuer_evidence.get("fiscal_years") != [2023, 2024, 2025]):
        raise ValueError("unreconciled_fixed_cashflow_evidence")
    records = issuer_evidence["records"]
    allowed = set(CODE_BY_CLASS.values()) | {"N"}
    counts = Counter()
    for code, row in records.items():
        if code not in report["rows"] or len(row) != 4 or row[0] not in ("CFS", "OFS") or not set(row[1:]) <= allowed:
            raise ValueError("invalid_fixed_cashflow_issuer_record")
        counts.update(row[1:])
    if (any(counts[symbol] != gap["categories"][name] for name, symbol in CODE_BY_CLASS.items())
        or sum(counts[symbol] for symbol in CODE_BY_CLASS.values()) != gap["attempted_company_years"]
        or len(records) != gap["issuers_with_saved_cashflow_years"]):
        raise ValueError("fixed_cashflow_classification_count_mismatch")
    missing = {"X", "C", "B", "P", "I"}
    reviews = {}
    for code, row in records.items():
        for area, key, statuses in (
            ("financial", "cashflow_fcf_persistence", row[1:]),
            ("valuation", "fcf_yield", row[-1:])):
            # N denotes uncollected and U unmatched receipt. Neither is an
            # attempted missing-account conclusion. Keep such gaps pending.
            if not set(statuses) & missing or set(statuses) & {"N", "U"}:
                continue
            point = report["rows"][code]["parts"][area].get(key)
            maximum = R1D_PART_LIMITS[area][key]
            weight = round(maximum - (point[1] if point else 0), 6)
            if weight <= .00001:
                continue
            reviews[":".join((code, area, key))] = {
                "asof": CUTOFF, "missing_weight": weight,
                "status": "source_missing_after_attempt",
                "evidence": "fixed annual FCF account audit: " + row[0] + ":" + "".join(statuses)}
    return reviews


def price_error_terminal_reviews(report, catalog, trading, market, history_progress):
    """Close still-missing price factors after a documented fixed-day source error.

    A zero listing-volume reference is not official suspension proof. Preserve
    all recovered indicators and financial scores; only the residual invalid
    price/money component receives zero, never a fabricated price or volume.
    """
    if (catalog.get("date") != CUTOFF or trading.get("asof") != CUTOFF
        or market.get("date") != CUTOFF or history_progress.get("asof") != CUTOFF
        or trading.get("listed_total") != report["listed_total"]
        or market.get("examined_count") != report["listed_total"]
        or history_progress.get("listed_total") != report["listed_total"]
        or history_progress.get("pending_sessions") != 0
        or history_progress.get("requested_sessions") != history_progress.get("archived_sessions")
        or history_progress.get("requested_completed_weeks") != history_progress.get("archived_completed_weeks")):
        raise ValueError("fixed_price_source_attempt_not_reconciled")
    members = {r["code"]: r for r in catalog["rows"]}
    error_codes = {code for code, stage, reason in market["collection_errors"]
                   if reason == "price_zero_latest_volume"}
    statuses = trading["rows"]
    eligible = {code for code, member in members.items()
                if member.get("price_history_status") == "price_source_or_validation_error"
                and member.get("listing_zero_volume_reference") is True
                and code in error_codes and statuses.get(code, {}).get("state") == "zero_volume"
                and all(statuses[code].get(k) == member[k] for k in ("name","market"))}
    reviews = {}
    for code in sorted(eligible):
        for area in ("volume","technical","momentum"):
            for key, maximum in R1D_PART_LIMITS[area].items():
                point = report["rows"][code]["parts"][area].get(key)
                weight = round(maximum - (point[1] if point else 0), 6)
                if weight <= .00001:
                    continue
                reviews[":".join((code, area, key))] = {
                    "asof": CUTOFF, "missing_weight": weight,
                    "status": "source_error_after_attempt",
                    "evidence": "fixed market collection price_zero_latest_volume; matched listing zero-volume reference; regular close archive complete; residual unavailable"}
    return reviews


def money_terminal_reviews(report, catalog, money, receipt, raw_money, existing):
    """After successful exact-cutoff replay, close residual unusable money windows.

    Never discard a usable source component not incorporated by the scorer.
    """
    from hashlib import sha1
    from v3_actual_money_contract import usable_records, usable_component_records, closed_week_end
    from v3_dart_bulk_history import _stable_digest
    from v3_actual_money_structure import COMPONENT_MAX
    digest = sha1(b"blob " + str(len(raw_money)).encode() + bytes([0]) + raw_money).hexdigest()
    if (receipt.get("schema") != "v30b-fixed-money-replay-receipt-1"
        or receipt.get("asof") != CUTOFF or receipt.get("listed") != report["listed_total"]
        or receipt.get("workflow_conclusion") != "success"
        or receipt.get("money_git_blob") != digest
        or receipt.get("component_weight_losses") != 0 or receipt.get("provider_calls") != 0
        or money.get("asof") != CUTOFF or catalog.get("date") != CUTOFF
        or money.get("source_catalog_sha256") != _stable_digest(catalog)
        or money.get("listed_total") != report["listed_total"]
        or money.get("daily_cohorts_inspected") != 96 or money.get("calendar_weeks_inspected") != 240
        or money.get("daily_asof", "9999") > CUTOFF or money.get("weekly_session_end", "9999") > CUTOFF
        or money.get("calendar_week_end") != closed_week_end(CUTOFF)):
        raise ValueError("fixed_money_replay_receipt_mismatch")
    records = usable_records(money, catalog)
    components = usable_component_records(money, catalog)
    if (len(records) != money["eligible_issuers"] or len(components) != money["component_eligible_issuers"]
        or not records or not components):
        raise ValueError("fixed_money_source_contract_mismatch")
    reviews = {}
    limits = {"long_horizon_growth":6, **COMPONENT_MAX}
    for code, row in report["rows"].items():
        rec = records.get(code, {})
        obs = components.get(code, {}).get("component_observations", {})
        old_structure = rec.get("r1c_structure", {})
        for key, maximum in limits.items():
            label = ":".join((code, "volume", key))
            if label in existing:
                continue
            point = row["parts"]["volume"].get(key)
            observed = point[1] if point else 0
            weight = round(maximum - observed, 6)
            if weight <= .00001:
                continue
            if key == "long_horizon_growth":
                usable = 6 if rec or obs.get("growth6") is not None else 0
            else:
                pair = obs.get("components", {}).get(key)
                usable = max(pair[1] if pair else 0,
                             maximum if old_structure.get("components", {}).get(key) is not None else 0)
            if usable > observed + .00001:
                continue
            reviews[label] = {"asof":CUTOFF, "missing_weight":weight,
                "status":"source_missing_after_attempt",
                "evidence":"successful fixed money archive replay; component window/baseline/directional evidence unavailable; run " + str(receipt["workflow_run_id"])}
    return reviews


def build(report, comparison, catalog, frozen_catalog, legacy_priority=None, reviews=None):
    if not (report["asof"] == comparison["asof"] == catalog["date"] == frozen_catalog["date"] == CUTOFF):
        raise ValueError("development_cutoff_mismatch")
    def identities(c):
        return {r["code"]: (r["name"], r["market"]) for r in c["rows"]}
    if identities(catalog) != identities(frozen_catalog) or len(identities(catalog)) != 2592:
        raise ValueError("development_roster_changed")
    if r1d_public_reconciles(report, comparison, catalog) is None:
        raise ValueError("development_scores_not_reconciled")
    members = {r["code"]: r for r in catalog["rows"]}
    queried = set()
    if legacy_priority and legacy_priority.get("asof") == CUTOFF:
        queried = set(legacy_priority.get("fcf_three_year_previously_queried_missing_account_codes", []))
    reviews = reviews or {}
    if not isinstance(reviews, dict):
        raise ValueError('invalid_gap_reviews')
    used_reviews = set()
    reasons = Counter()
    dispositions = Counter()
    pending_issuers = 0
    full = 0
    rows = {}
    for code, row in report["rows"].items():
        parts = {}
        for area, limits in R1D_PART_LIMITS.items():
            parts[area] = {}
            for key, maximum in limits.items():
                point = row["parts"][area].get(key)
                if point and date.fromisoformat(point[2]) > date.fromisoformat(CUTOFF):
                    raise ValueError("post_cutoff_component")
                reason = category(area, key, members[code].get("price_history_status"), code in queried)
                part = component(point, maximum, reason)
                review_key = ":".join((code, area, key))
                if review_key in reviews:
                    review = reviews[review_key]
                    if (not part["source_review_pending"] or review.get("asof") != CUTOFF
                        or review.get("missing_weight") != part["zero_fallback_weight"]
                        or review.get("status") not in ("source_missing_after_attempt", "source_error_after_attempt", "insufficient_history_confirmed")
                        or not isinstance(review.get("evidence"), str) or not review["evidence"].strip()):
                        raise ValueError("invalid_or_stale_gap_review")
                    part["source_review_pending"] = False
                    part["review"] = review
                    used_reviews.add(review_key)
                parts[area][key] = part
                if part["reason"]:
                    reasons[part["reason"]] += 1
                    dispositions["pending" if part["source_review_pending"] else "terminal_zero"] += 1
        flat = [p for ps in parts.values() for p in ps.values()]
        observed = round(sum(p["observed_weight"] for p in flat), 6)
        missing = round(sum(p["zero_fallback_weight"] for p in flat), 6)
        earned = round(sum(p["earned"] for p in flat), 6)
        if abs(observed - row["available"]) > .00002 or abs(observed + missing - 100) > .00002:
            raise ValueError("fixed_weight_mismatch")
        if row["base"] is not None and abs(earned - row["base"]) > .00002:
            raise ValueError("fixed_earned_points_mismatch")
        pending = any(p["source_review_pending"] for p in flat)
        pending_issuers += pending
        full += missing <= .00001
        rows[code] = {"name": row["name"], "market": row["market"], "maximum": 100,
                      "observed_weight": observed, "zero_fallback_weight": missing,
                      "base": earned, "score": row["score"] if row["score"] is not None else 0,
                      "rank": row["rank"], "source_review_pending": pending, "parts": parts}
    if used_reviews != set(reviews):
        raise ValueError("unmatched_gap_review")
    return {"schema": "v30b-fixed-development-gap-ledger-1", "asof": CUTOFF,
            "source_digest": report["source_digest"], "listed_total": len(rows),
            "all_100_point_budgets_accounted": True, "full_observed_issuers": full,
            "pending_source_review_issuers": pending_issuers,
            "gap_reason_counts": dict(reasons), "gap_disposition_counts": dict(dispositions),
            "missing_inputs_are_not_fabricated": True,
            "missing_component_earned_points": 0, "fixed_data_review_complete": pending_issuers == 0,
            "daily_ingestion_separate": True, "provider_calls": 0, "rows": rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs", type=Path, required=True)
    p.add_argument("--frozen-catalog", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--reviews", type=Path)
    a = p.parse_args()
    def read(name):
        return json.loads((a.docs / name).read_text())
    report = read("v3-operational-research-scores.json")
    for name, digest in report["source_hashes"].items():
        if sha256((a.docs / name).read_bytes()).hexdigest() != digest:
            raise ValueError("fixed_source_digest_mismatch")
    legacy = a.docs / "v3-near-100-evidence-priority.json"
    reviews = cashflow_terminal_reviews(report, read("v3-dart-cashflow-gap-summary.json"),
                                       read("v3-dart-cashflow-issuer-evidence.json"))
    reviews.update(price_error_terminal_reviews(report, read("v22-market-catalog.json"),
        read("v3-trading-status.json"), read("data.json"), read("v3-regular-close-history-progress.json")))
    money_receipt = a.docs.parent / "ops" / "v30b-fixed-money-replay-receipt.json"
    if money_receipt.is_file():
        raw_money = (a.docs / "v3-actual-money-participation.json").read_bytes()
        reviews.update(money_terminal_reviews(report, read("v22-market-catalog.json"),
            json.loads(raw_money), json.loads(money_receipt.read_text()), raw_money, reviews))
    if a.reviews and a.reviews.is_file():
        manual = json.loads(a.reviews.read_text())
        if set(manual) & set(reviews):
            raise ValueError("duplicate_gap_disposition")
        reviews.update(manual)
    result = build(report, read("v3-operational-research-comparison.json"),
                   read("v22-market-catalog.json"), json.loads(a.frozen_catalog.read_text()),
                   json.loads(legacy.read_text()) if legacy.is_file() else None,
                   reviews)
    a.out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k:v for k,v in result.items() if k != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
