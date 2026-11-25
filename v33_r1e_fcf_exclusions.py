"""Reconstruct frozen R1e ranks and prepare matched FCF exclusion cohorts."""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from v3_forward_factor_validation import MARKETS, factor_value
from v3_operational_research_score import SPECS, VERSION, finite, rounded
from v3_score_observation_capture import SCORE_PATH


CANDIDATES = {
    "omit_fcf_persistence": ("financial", "cashflow_fcf_persistence"),
    "omit_fcf_yield": ("valuation", "fcf_yield"),
}
TOP_SIZES = (3, 10, 30, 100)


def score_parts(parts, row, omitted=None):
    """Preserve R1e's per-area rounding and frozen deductions."""
    area_totals = []
    for area, factors in parts.items():
        retained = [part for key, part in factors.items() if (area, key) != omitted]
        area_totals.append((
            round(sum(part[0] for part in retained), 6),
            round(sum(part[1] for part in retained), 6),
        ))
    available = round(sum(weight for _, weight in area_totals), 6)
    if not available:
        return None, None, available
    base = round(sum(points for points, _ in area_totals), 6)
    adjusted = base - row["capital_deduction"] - row["risk_deduction"]
    return rounded(max(0, min(100, adjusted))), base, available


def ranked_codes(scores):
    return sorted((code for code, (score, _, _) in scores.items() if score is not None),
                  key=lambda code: (-scores[code][0], -scores[code][2], code))


def prepare(snapshot):
    """Return cohort rankings only after the complete frozen baseline matches."""
    asof = snapshot["asof"]
    rows = snapshot["rows"]
    if (snapshot["version"] != VERSION or snapshot.get("format") != "compact-parts-v1"
            or date.fromisoformat(asof).isoformat() != asof
            or snapshot["listed_total"] != len(rows)):
        raise ValueError("invalid frozen R1e snapshot")

    scores = {}
    cohorts = {}
    for code, row in rows.items():
        if row["market"] not in MARKETS or any(
            not finite(row[field]) or row[field] < 0
            for field in ("capital_deduction", "risk_deduction")
        ):
            raise ValueError("invalid frozen R1e market or deduction")

        states = {}
        for area, factors in row["parts"].items():
            for key, part in factors.items():
                state = factor_value(part, SPECS[area][key], asof)
                if state == "missing" or date.fromisoformat(part[2]).isoformat() != part[2]:
                    raise ValueError("invalid frozen R1e factor")
                states[area, key] = state

        original = score_parts(row["parts"], row)
        if original != (row["score"], row["base"], row["available"]):
            raise ValueError("frozen R1e score does not reconstruct")
        scores[code] = original
        if original[0] is not None and all(
            states.get(factor) in ("high", "low") for factor in CANDIDATES.values()
        ):
            cohorts[code] = {
                name: score_parts(row["parts"], row, omitted=factor)
                for name, factor in CANDIDATES.items()
            }

    original_rank = ranked_codes(scores)
    ranks = {code: rank for rank, code in enumerate(original_rank, 1)}
    if any(row["rank"] != ranks.get(code) for code, row in rows.items()):
        raise ValueError("frozen R1e rank does not reconstruct")

    return {
        "baseline": [code for code in original_rank if code in cohorts],
        **{name: ranked_codes({code: variants[name] for code, variants in cohorts.items()})
           for name in CANDIDATES},
    }, original_rank


def audit_snapshot(path):
    raw = Path(path).read_bytes()
    snapshot = json.loads(raw)
    ranking, original = prepare(snapshot)
    baseline = ranking["baseline"]
    return {
        "schema": "v33-r1e-fcf-exclusion-cohorts-1",
        "asof": snapshot["asof"],
        "model_version": VERSION,
        "source_sha256": sha256(raw).hexdigest(),
        "listed_count": len(snapshot["rows"]),
        "baseline_ranks_reconstructed": len(original),
        "common_eligible_count": len(baseline),
        "common_eligible_by_market": {
            market: sum(snapshot["rows"][code]["market"] == market for code in baseline)
            for market in MARKETS
        },
        "excluded_ranked_count": len(original) - len(baseline),
        "variants": {
            name: {
                "excluded_factor": ".".join(factor),
                "top_overlap": {
                    n: len(set(baseline[:n]).intersection(ranking[name][:n]))
                    if len(baseline) >= n else None for n in TOP_SIZES
                },
            }
            for name, factor in CANDIDATES.items()
        },
        "performance_validated": False,
        "reweighting_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, nargs="?", default=SCORE_PATH)
    args = parser.parse_args()
    print(json.dumps(audit_snapshot(args.snapshot), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
