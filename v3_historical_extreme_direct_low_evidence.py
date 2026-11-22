"""Read-only audit of independently compared frozen downside LOW sessions.

Consumes only anonymous official KRX comparison aggregates from completed
authenticated Actions jobs. Never calls a provider or reveals issuer rows.
Direct low evidence applies ONLY to the newly run extrema dates, not old
market-wide aggregate sessions or the full adjusted return path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from v3_historical_downside_official_ohlc_evidence import (
    EVIDENCE as BASELINE_PATH,
    evidence_sessions as baseline_evidence_sessions,
    verified_market_session,
)
from v3_historical_pilot_official_prices import (
    BATCHES, downside_extreme_trading_symbols, exact_traded_extreme_symbols,
)
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "ops/v3-historical-extreme-direct-low-20261122.json"
SCHEMA = "v3-historical-extreme-direct-low-corroboration-v1"
REPORT_SCHEMA = "v3-historical-pilot-official-prices-v1"
NEW_BATCHES = ("downside_followup", "downside_tail_1", "downside_tail_2",
               "downside_tail_3", "downside_tail_4", "downside_tail_5")
SOURCE_RUNS = {"downside_followup": 45012847985, **{
    f"downside_tail_{i}": 45012986989 for i in range(1, 6)}}
SHA = re.compile(r"[a-f0-9]{64}\Z")


class DirectLowEvidenceError(ValueError):
    """Bounded static failure reason codes only."""


def require(ok: bool, reason: str):
    if not ok:
        raise DirectLowEvidenceError(reason)


def build(report: dict, baseline: dict) -> dict:
    historical_days, _, _, _ = baseline_evidence_sessions(baseline)
    targets = downside_extreme_trading_symbols()
    new_date_keys = {day.isoformat() for day in targets} - set(historical_days)
    require(len(historical_days) == 14 and len(targets) == 35
            and len(new_date_keys) == 27,
            "invalid_frozen_extreme_date_partition")
    require(type(report) is dict and report.get("schema") == SCHEMA,
            "invalid_direct_low_evidence_schema")
    require(report.get("historical_adjusted_path_certified") is False
            and report.get("point_in_time_scores_verified") is False
            and report.get("regular_only_volume_verified") is False
            and report.get("performance_validated") is False
            and report.get("v30b_release_ready") is False,
            "unfounded_direct_low_certification")
    batches = report.get("batches")
    require(type(batches) is list and len(batches) == len(NEW_BATCHES)
            and {b.get("batch") for b in batches if type(b) is dict}
                == set(NEW_BATCHES),
            "incomplete_direct_low_batch_roster")
    compared = {}
    actual_symbols = 0
    total_market = total_traded = total_zero = 0
    for evidence in batches:
        name = evidence["batch"]
        run_id, job_id, artifact_id = (
            evidence.get("run_id"), evidence.get("job_id"),
            evidence.get("artifact_id"))
        require(type(run_id) is int and run_id == SOURCE_RUNS[name]
                and type(job_id) is int and job_id > 0
                and type(artifact_id) is int and artifact_id > 0,
                "invalid_direct_low_job_provenance")
        result = evidence.get("result")
        require(type(result) is dict and result.get("schema") == REPORT_SCHEMA
                and result.get("batch") == name
                and result.get("public_panel_source_sha256") == list(SOURCE_DIGESTS)
                and result.get("failed_sessions") == {}
                and result.get("market_wide_ohlc_and_membership_matched") is True
                and result.get("turnover_numeric_comparison_complete") is True
                and result.get("turnover_all_values_matched") is True
                and result.get("actual_extreme_trading_symbol_ohlc_matched") is True
                and result.get("extreme_issuer_identity_pit_certified") is False
                and result.get("adjusted_price_history_verified") is False
                and result.get("volume_session_equivalence_verified") is False
                and result.get("performance_validated") is False,
                "invalid_official_direct_low_result")
        requested = [day.isoformat() for day in BATCHES[name]]
        sessions = result.get("sessions")
        require(result.get("requested_sessions") == requested
                and type(sessions) is dict and set(sessions) == set(requested),
                "missing_or_repeated_extreme_date")
        sum_symbols = 0
        for day_key, item in sessions.items():
            require(day_key in new_date_keys and day_key not in compared
                    and type(item) is dict, "duplicate_or_baseline_official_date")
            source = item.get("official_source_sha256")
            require(type(source) is dict and set(source) == {"KOSPI", "KOSDAQ"}
                    and all(type(x) is str and SHA.fullmatch(x)
                            for x in source.values()),
                    "missing_official_market_source_fingerprint")
            scope, exact, zero = verified_market_session(item.get("market_wide"))
            traded = item.get("actual_extreme_trading_symbols")
            expected = len(targets[next(d for d in targets
                                        if d.isoformat() == day_key)])
            require(type(traded) is dict
                    and traded.get("scope") == expected
                    and exact_traded_extreme_symbols(traded),
                    "specific_extreme_security_low_not_verified")
            turnover = item.get("turnover")
            require(type(turnover) is dict and turnover.get("scope") == scope
                    and turnover.get("identity_mismatch") == 0
                    and turnover.get("numeric_equivalence_is_not_session_certification") is True,
                    "inconsistent_official_trading_money_comparison")
            for field in ("volume", "amount"):
                counter = turnover.get("fields", {}).get(field, {})
                require(counter.get("compared") == scope
                        and counter.get("matched") == scope
                        and counter.get("mismatched") == 0
                        and counter.get("public_unavailable") == 0
                        and counter.get("official_unavailable") == 0,
                        "incomplete_official_trading_money_comparison")
            total_market += scope
            total_traded += exact
            total_zero += zero
            sum_symbols += expected
            compared[day_key] = expected
        require(result.get("actual_extreme_target_sessions_expected")
                    == len(requested)
                and result.get("actual_extreme_target_sessions_compared")
                    == len(requested)
                and result.get("actual_extreme_symbol_days_in_batch")
                    == sum_symbols
                and result.get("actual_extreme_symbol_days_exact_ohlc")
                    == sum_symbols,
                "incomplete_official_extreme_symbol_batch")
        actual_symbols += sum_symbols
    require(set(compared) == new_date_keys, "missing_direct_low_extreme_date")
    return {
        "schema": "v3-historical-extreme-direct-low-coverage-v1",
        "saved_historical_marketwide_dates": len(historical_days),
        "new_direct_low_compared_dates": len(compared),
        "all_frozen_extreme_dates_with_official_marketwide_ohlc":
            len(targets),
        "direct_low_trading_symbol_date_pairs": actual_symbols,
        "new_marketwide_issuer_date_pairs": total_market,
        "new_marketwide_exact_traded_ohlc": total_traded,
        "new_marketwide_untraded_on_both": total_zero,
        "frozen_85_horizons_whose_extreme_day_has_direct_low_check":
            44,
        "prior_41_horizons_marketwide_day_only_not_directly_authenticated":
            41,
        "new_provider_calls_by_this_readonly_validation": 0,
        "historical_adjusted_path_certified": False,
        "point_in_time_scores_verified": False,
        "regular_only_volume_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args(argv)
    try:
        saved = json.loads(args.evidence.read_text(encoding="utf-8"))
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        result = build(saved, baseline)
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(1, "official_extreme_direct_low_evidence_invalid\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
