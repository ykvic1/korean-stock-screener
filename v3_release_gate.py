"""Independent, read-only v3 release readiness audit for Korean stock screener.

No scores, predictions or rankings are created here. Existing unverified v2.2
and OpenDART outputs must never be promoted by changing a version label.
Only explicit, independently supported proof can close a release blocker.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_SESSION = "KRX_REGULAR_0900_1530_ASIA_SEOUL"
BASELINE_COUNT = 53
MAX_BYTES = 12_000_000
GATES = (
    "consistent_snapshots",
    "fixed_53_price_history",
    "regular_session_ohlcv",
    "independently_reconciled_history",
    "point_in_time_universe",
    "verified_investor_flows",
    "pit_and_scope_safe_financials",
    "sector_pit_membership",
    "score_model_verified",
    "persistent_incremental_store",
    "v3_end_to_end_verified",
)


def _result(passed: bool, reasons: list[str]) -> dict:
    return {"passed": passed, "reasons": reasons if not passed else []}


def assess_v3(reliability: Any, quant: Any, daily: Any,
              baseline: Any, *, proof: Any = None) -> dict:
    """All missing/contradictory evidence fails closed, including proof claims.

    A claim in a config file is not independent evidence. Reserved 'proof'
    keys can be used in future only after separate, authenticated validators
    create those contracts. Until then, external proof never promotes a gate.
    """
    r = reliability if isinstance(reliability, dict) else {}
    q = quant if isinstance(quant, dict) else {}
    d = daily if isinstance(daily, dict) else {}
    b = baseline if isinstance(baseline, dict) else {}
    # Explicitly ignore asserted external 'proof' until independently signed
    # source and validation contracts are implemented.
    _ = proof

    gates = {}
    asofs = [r.get("asof"), q.get("asof"), d.get("date")]
    dates_match = (all(isinstance(t, str) and len(t) == 10 for t in asofs)
                   and len(set(asofs)) == 1)
    r_stocks = r.get("stocks")
    q_stocks = q.get("stocks")
    d_stocks = d.get("stocks")
    sample_lists = (isinstance(r_stocks, list), isinstance(q_stocks, list),
                    isinstance(d_stocks, list))
    snapshot_consistent = (
        dates_match and sample_lists[0] and sample_lists[1] and sample_lists[2]
        and type(r.get("requested")) is int
        and len(r_stocks) == r["requested"]
        and type(q.get("requested_symbols")) is int
        and len(q_stocks) == q["requested_symbols"]
        and type(d.get("candidate_count")) is int
        and len(d_stocks) == d["candidate_count"]
        and q.get("requested_symbols") == r.get("requested"))
    gates["consistent_snapshots"] = _result(bool(snapshot_consistent), [
        "Daily, reliability and quant dates, requested counts or stock arrays disagree"
    ])

    pinned = b.get("stocks") if isinstance(b.get("stocks"), list) else None
    base = r.get("baseline_cohort") if isinstance(r.get("baseline_cohort"), dict) else {}
    pinned_codes = ([s.get("code") for s in pinned if isinstance(s, dict)]
                    if pinned is not None else [])
    base_codes = ([s.get("code") for s in base.get("stocks", [])
                   if isinstance(s, dict)]
                  if isinstance(base.get("stocks"), list) else [])
    baseline_identity = (
        len(pinned_codes) == BASELINE_COUNT
        and len(set(pinned_codes)) == BASELINE_COUNT
        and all(isinstance(s, str) and len(s) == 6 and s.isdigit()
                for s in pinned_codes)
        and base.get("requested") == BASELINE_COUNT
        and len(base_codes) == BASELINE_COUNT
        and set(base_codes) == set(pinned_codes))
    fixed_53_ok = (
        baseline_identity
        and base.get("full_history_verified") == BASELINE_COUNT
        and base.get("numeric_segment_usable") == BASELINE_COUNT)
    gates["fixed_53_price_history"] = _result(bool(fixed_53_ok), [
        "Original fixed 53 symbols must all have full, audited OHLCV histories"
    ])

    r_requested = r.get("requested")
    regular_session = (
        snapshot_consistent
        and type(r_requested) is int and r_requested > 0
        and r.get("regular_session_verified") == r_requested
        and all(isinstance(stock, dict)
                and isinstance(stock.get("session_validation"), dict)
                and stock["session_validation"].get("price_session") == REQUIRED_SESSION
                and stock["session_validation"].get("volume_session") == REQUIRED_SESSION
                and stock["session_validation"].get("final") is True
                and stock["session_validation"].get("official_close_status") == "matched"
                and stock["session_validation"].get("history_session_verified") is True
                and stock["session_validation"].get("confirmed_scoring_allowed") is True
                and isinstance(stock["session_validation"].get("official_source_sha256"), str)
                and len(stock["session_validation"]["official_source_sha256"]) == 64
                for stock in r_stocks))
    # Even unanimous internal labels cannot replace an independently checked
    # official regular-session *volume* provider contract.
    gates["regular_session_ohlcv"] = _result(False, [
        "Certified provider-level KRX regular-only volume contract missing"
        if regular_session else
        "KRX regular-session prices AND volume and indicator history not verified"
    ])

    independent = (
        baseline_identity
        and base.get("independently_matched_recent") == BASELINE_COUNT
        and type(r_requested) is int and r_requested > 0
        and r.get("independently_matched") == r_requested)
    gates["independently_reconciled_history"] = _result(bool(independent), [
        "Independent price/volume comparisons and corporate actions missing for full cohort"
    ])

    # The remaining contracts are not present in v2.2 artifacts. Do not
    # infer completeness from a user-provided flag or the DART pilot.
    gates["point_in_time_universe"] = _result(False, [
        "Date-specific all-listed KOSPI/KOSDAQ ordinary-share master is not verified"
    ])
    flow_count = d.get("flow_confirmed_count", 0)
    gates["verified_investor_flows"] = _result(False, [
        "Certified 20/60/120/250-session institutional/foreign flows and provenance missing"
        if not isinstance(flow_count, int) or flow_count > 0 else
        "Current investor-flow confirmed count is zero; certified flows missing"
    ])
    gates["pit_and_scope_safe_financials"] = _result(False, [
        "OpenDART 2025 research is not approved for historical PIT, statement scope or scoring"
    ])
    gates["sector_pit_membership"] = _result(False, [
        "Historical sector membership and independently verified relative returns missing"
    ])
    gates["score_model_verified"] = _result(False, [
        "IAS component normalization and EA thresholds not frozen/validated; no invented 100-point rank"
    ])
    gates["persistent_incremental_store"] = _result(False, [
        "Atomic immutable incremental 5-year stock universe storage not verified"
    ])
    gates["v3_end_to_end_verified"] = _result(False, [
        "v3-specific live mobile, Excel and operational release verification not performed"
    ])
    all_passed = all(gates[name]["passed"] for name in GATES)
    return {
        "schema": "v3-release-readiness-0.1",
        "asof": asofs[0] if dates_match else None,
        "phase": "research_only",
        "v3_release_ready": bool(all_passed),
        "v3_confirmed_score": None,
        "v3_confirmed_rank": None,
        "v3_data_scoring_authorized": False,
        "manual_approval_for_release_needed": True,
        "gates": gates,
        "passed_count": sum(bool(gates[k]["passed"]) for k in GATES),
        "total_gates": len(GATES),
        "blocking_gate_ids": [key for key in GATES if not gates[key]["passed"]],
        "measured": {
            "daily_flow_confirmed": d.get("flow_confirmed_count"),
            "regular_session_verified": r.get("regular_session_verified"),
            "independent_matched": r.get("independently_matched"),
            "fixed_53_numeric_usable": base.get("numeric_segment_usable"),
            "fixed_53_full_history_verified": base.get("full_history_verified"),
            "fixed_53_independent_matched": base.get("independently_matched_recent"),
        },
    }


def _load(path: Path) -> Any:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("Missing, empty or oversized v3 input")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="v3 offline evidence gate; read-only; fail closed")
    parser.add_argument("--reliability", default="docs/reliability.json")
    parser.add_argument("--quant", default="docs/quant_v22.json")
    parser.add_argument("--daily", default="docs/data.json")
    parser.add_argument("--baseline", default="config/v22_baseline_53.json")
    parser.add_argument("--output", help="optional private JSON report; NOT docs/Pages")
    parser.add_argument("--assert-blocked", action="store_true",
                        help="CI checks v3 is not falsely marked ready")
    args = parser.parse_args(argv)
    try:
        report = assess_v3(_load(Path(args.reliability)),
                           _load(Path(args.quant)),
                           _load(Path(args.daily)),
                           _load(Path(args.baseline)))
    except (OSError, ValueError, UnicodeError):
        parser.error("Evidence cannot be verified; v3 remains blocked")
    if args.output:
        out = Path(args.output)
        if (out.is_absolute() or ".." in out.parts or "docs" in out.parts
                or out.suffix != ".json"):
            parser.error("Output must be private relative JSON outside docs")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(json.dumps({
        "v3_release_ready": report["v3_release_ready"],
        "passed": report["passed_count"],
        "total": report["total_gates"],
        "blocking_gate_ids": report["blocking_gate_ids"],
        "scoring_authorized": False,
    }, ensure_ascii=False))
    if args.assert_blocked and (
            report["v3_release_ready"] is not False
            or report["v3_confirmed_score"] is not None
            or report["v3_data_scoring_authorized"] is not False):
        raise SystemExit("Unexpected v3 release/scoring approval")
    return report


if __name__ == "__main__":
    main()
