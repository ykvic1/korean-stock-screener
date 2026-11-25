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
            "gap_reason_counts": dict(reasons), "missing_inputs_are_not_fabricated": True,
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
    result = build(report, read("v3-operational-research-comparison.json"),
                   read("v22-market-catalog.json"), json.loads(a.frozen_catalog.read_text()),
                   json.loads(legacy.read_text()) if legacy.is_file() else None,
                   json.loads(a.reviews.read_text()) if a.reviews and a.reviews.is_file() else None)
    a.out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k:v for k,v in result.items() if k != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
