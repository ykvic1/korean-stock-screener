"""V3.3 cross-sectional overlap of saved R1e factor contributions."""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from v3_factor_overlap_audit import MIN_PAIRS, _spearman
from v3_forward_factor_validation import MARKETS, factor_value
from v3_operational_research_score import SPECS, VERSION
from v3_score_observation_capture import SCORE_PATH


# Fixed hypotheses, before inspecting outcomes.
PAIRS = (
    ("volume.weekly_structure_increment", "technical.weekly_pattern"),
    ("technical.daily_pattern", "technical.independent_breakout_confirmation"),
    ("technical.long_ma_position", "momentum.existing_momentum"),
    ("momentum.daily_ma_slope_turn", "momentum.weekly_ma_slope_turn"),
    ("financial.cashflow_fcf_persistence", "valuation.fcf_yield"),
)
OVERLAP_THRESHOLD = 0.85


def complete_points(row, factor, asof):
    area, key = factor.split(".")
    value = row.get("parts", {}).get(area, {}).get(key)
    if factor_value(value, SPECS[area][key], asof) in ("high", "low"):
        return value[0]
    return None


def summarize(snapshot):
    asof = snapshot["asof"]
    if (snapshot["version"] != VERSION or snapshot.get("format") != "compact-parts-v1"
            or date.fromisoformat(asof).isoformat() != asof):
        raise ValueError("expected a dated active R1e score snapshot")

    markets = {market: [] for market in MARKETS}
    for row in snapshot["rows"].values():
        if row["rank"] is not None:
            markets[row["market"]].append(row)

    results = {}
    for market, rows in markets.items():
        pairs = []
        for left, right in PAIRS:
            observed = []
            for row in rows:
                x = complete_points(row, left, asof)
                y = complete_points(row, right, asof)
                if x is not None and y is not None:
                    observed.append((x, y))
            n = len(observed)
            rho = _spearman(observed) if n >= MIN_PAIRS else None
            pairs.append({
                "left": left,
                "right": right,
                "complete_pair_count": n,
                "pair_coverage_pct": round(100 * n / len(rows), 2) if rows else None,
                "spearman_rho": round(rho, 6) if rho is not None else None,
                "status": ("insufficient_pairs" if n < MIN_PAIRS else
                           "constant_factor" if rho is None else "observed"),
                "overlap_warning": rho is not None and abs(rho) >= OVERLAP_THRESHOLD,
            })
        results[market] = {"ranked_count": len(rows), "pairs": pairs}

    return {
        "schema": "v33-r1e-factor-overlap-1",
        "asof": asof,
        "model_version": VERSION,
        "minimum_complete_pairs": MIN_PAIRS,
        "overlap_warning_absolute_rho": OVERLAP_THRESHOLD,
        "markets": results,
        "performance_validated": False,
        "reweighting_authorized": False,
    }


def audit_snapshot(path):
    raw = Path(path).read_bytes()
    return {**summarize(json.loads(raw)), "source_sha256": sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, nargs="?", default=SCORE_PATH)
    args = parser.parse_args()
    print(json.dumps(audit_snapshot(args.snapshot), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
