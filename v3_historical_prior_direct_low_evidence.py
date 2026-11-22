"""Validate the 8 previously marketwide-only downside dates at exact-security LOW.

Zero provider calls. This strictly checks saved official KRX anonymous aggregates
from the one-shot authenticated run, cross-references original 14-day source
fingerprints, and combines with the already verified 27 NEW direct-LOW dates.
It does NOT certify a continuous share-adjusted price path or realized returns.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from v3_historical_downside_official_ohlc_evidence import (
    EVIDENCE as BASELINE_FILE, evidence_sessions, verified_market_session,
)
from v3_historical_extreme_direct_low_evidence import (
    EVIDENCE as LATER_FILE, build as validate_later,
)
from v3_historical_pilot_official_prices import (
    downside_extreme_trading_symbols, exact_traded_extreme_symbols,
)
from v3_historical_market_coverage import ledger_rows
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "ops/v3-historical-prior-extreme-direct-low-20261122.json"
SCHEMA = "v3-historical-prior-extreme-direct-low-corroboration-v1"
REPORT_SCHEMA = "v3-historical-pilot-official-prices-v1"
PROVENANCE = {
    "prior_direct_0": (45018562773, 137196149127, 14751754413,
                       "0348c0f95b018af84ee452a5210a9b97bdbceae4b4e05da8f724c274a5af88ac"),
    "prior_direct_1": (45018562773, 137196148969, 14751714575,
                       "ad5d3c44e5f92e48ff43feaf088c24c07e325e335a38fe24c3db988be0d70697"),
}
SHA = re.compile(r"[0-9a-f]{64}\Z")


class PriorDirectLowError(ValueError):
    """Fixed aggregate-only failure code; never print issuer data."""


def require(condition, reason):
    if not condition:
        raise PriorDirectLowError(reason)


def _all_market_sources(later):
    seen = set()
    for batch in later["batches"]:
        for row in batch["result"]["sessions"].values():
            digests = row["official_source_sha256"]
            for fingerprint in digests.values():
                require(type(fingerprint) is str and SHA.fullmatch(fingerprint)
                        and fingerprint not in seen,
                        "reused_later_official_source_fingerprint")
                seen.add(fingerprint)
    return seen


def build(evidence: dict, baseline: dict, later: dict) -> dict:
    baseline_days, _, _, _ = evidence_sessions(baseline)
    later_counts = validate_later(later, baseline)
    targets = downside_extreme_trading_symbols()
    old_days = sorted(day.isoformat() for day in targets
                      if day.isoformat() in baseline_days)
    remaining_days = {day.isoformat() for day in targets} - set(old_days)
    require(len(old_days) == 8 and len(remaining_days) == 27
            and later_counts["new_direct_low_compared_dates"] == 27
            and later_counts["direct_low_trading_symbol_date_pairs"] == 33
            and later_counts["frozen_85_horizons_whose_extreme_day_has_direct_low_check"] == 44,
            "unexpected_old_new_extreme_partition")
    require(type(evidence) is dict and evidence.get("schema") == SCHEMA
            and evidence.get("recorded_on_kst") == "2026-11-22"
            and evidence.get("source_scope")
                == "previously_aggregated_eight_extreme_dates_exact_security_ohlc"
            and evidence.get("baseline_report_path")
                == "ops/v3-historical-turnover-corroboration-20261122.json"
            and evidence.get("later_direct_report_path")
                == "ops/v3-historical-extreme-direct-low-20261122.json"
            and evidence.get("official_direct_low_run_url")
                == "https://github.com/ykvic1/korean-stock-screener/actions/runs/45018562773"
            and evidence.get("prior_date_original_source_digests_unchanged") is True
            and evidence.get("new_provider_queries_at_readonly_replay") == 0
            and all(evidence.get(name) is False for name in (
                "regular_only_volume_verified", "historical_adjusted_path_certified",
                "point_in_time_scores_verified", "performance_validated",
                "v30b_release_ready")),
            "invalid_prior_low_evidence_or_certification")
    batches = evidence.get("batches")
    require(type(batches) is list and len(batches) == 2
            and {item.get("batch") for item in batches if type(item) is dict}
                == set(PROVENANCE),
            "missing_or_duplicate_prior_direct_low_batch")
    original = {day: row for batch in baseline["batches"]
                for day, row in batch["sessions"].items()}
    all_hashes = _all_market_sources(later)
    observed_dates = set()
    direct_symbols = market_rows = exact_candles = untraded = 0
    old_horizons = ledger_rows(ROOT, "downside-horizons")
    require(len(old_horizons) == 85, "invalid_frozen_85_horizon_roster")
    for batch in batches:
        name = batch["batch"]
        require((batch.get("run_id"), batch.get("job_id"),
                 batch.get("artifact_id"), batch.get("artifact_zip_sha256"))
                == PROVENANCE[name], "untrusted_prior_low_run_artifact")
        result = batch.get("result")
        expected = old_days[:5] if name == "prior_direct_0" else old_days[5:]
        require(type(result) is dict and result.get("schema") == REPORT_SCHEMA
                and result.get("batch") == name
                and result.get("requested_sessions") == expected
                and result.get("public_panel_source_sha256") == list(SOURCE_DIGESTS)
                and result.get("failed_sessions") == {}
                and result.get("actual_extreme_target_sessions_expected") == len(expected)
                and result.get("actual_extreme_target_sessions_compared") == len(expected)
                and result.get("actual_extreme_trading_symbol_ohlc_matched") is True
                and result.get("market_wide_ohlc_and_membership_matched") is True
                and result.get("turnover_numeric_comparison_complete") is True
                and result.get("turnover_all_values_matched") is True
                and result.get("extreme_issuer_identity_pit_certified") is False
                and result.get("adjusted_price_history_verified") is False
                and result.get("volume_session_equivalence_verified") is False
                and result.get("performance_validated") is False,
                "partial_or_rewritten_prior_direct_low_result")
        sessions = result.get("sessions")
        require(type(sessions) is dict and set(sessions) == set(expected),
                "missing_or_replayed_prior_direct_low_date")
        symbols_in_batch = 0
        for day, item in sessions.items():
            require(day not in observed_dates and day in original
                    and type(item) is dict,
                    "duplicated_or_unrecognized_prior_extreme_date")
            observed_dates.add(day)
            original_item = original[day]
            digest = item.get("official_source_sha256")
            require(type(digest) is dict and set(digest) == {"KOSPI", "KOSDAQ"}
                    and digest == original_item.get("official_source_sha256"),
                    "previously_pinned_krx_source_changed")
            for finger in digest.values():
                require(type(finger) is str and SHA.fullmatch(finger)
                        and finger not in all_hashes,
                        "reused_or_invalid_prior_krx_source_fingerprint")
                all_hashes.add(finger)
            scope, exact, zero = verified_market_session(item.get("market_wide"))
            old_market = original_item["market_wide"]
            comparable = (
                "scope", "exact_ohlc", "positive_trade_on_both",
                "no_positive_trade_on_either", "absent_from_official",
                "absent_from_public", "identity_mismatch", "listed_share_mismatch",
                "listed_shares_unavailable", "positive_trade_on_public_only",
                "positive_trade_on_official_only", "different_price_by_field",
            )
            require(all(item["market_wide"].get(key) == old_market.get(key)
                        for key in comparable),
                    "historical_official_marketwide_count_drift")
            extreme = item.get("actual_extreme_trading_symbols")
            expected_symbols = len(targets[next(d for d in targets
                                                if d.isoformat() == day)])
            require(type(extreme) is dict and extreme.get("scope") == expected_symbols
                    and exact_traded_extreme_symbols(extreme),
                    "actual_origin_successor_low_missing_or_different")
            turnover = item.get("turnover")
            require(type(turnover) is dict and turnover.get("scope") == scope
                    and turnover.get("identity_mismatch") == 0
                    and turnover.get("numeric_equivalence_is_not_session_certification") is True,
                    "invalid_prior_official_turnover_scope")
            for kind in ("volume", "amount"):
                field = turnover.get("fields", {}).get(kind, {})
                require(type(field) is dict
                        and field.get("compared") == scope
                        and field.get("matched") == scope
                        and field.get("mismatched") == 0
                        and field.get("public_unavailable") == 0
                        and field.get("official_unavailable") == 0,
                        "unmatched_prior_official_turnover")
            require(turnover == original_item.get("turnover"),
                    "previously_pinned_krx_turnover_changed")
            symbols_in_batch += expected_symbols
            market_rows += scope
            exact_candles += exact
            untraded += zero
        require(result.get("actual_extreme_symbol_days_in_batch") == symbols_in_batch
                and result.get("actual_extreme_symbol_days_exact_ohlc") == symbols_in_batch,
                "incomplete_prior_direct_security_batch_totals")
        direct_symbols += symbols_in_batch
    prior_horizon_rows = sum(row["adverse_extreme_on"] in observed_dates
                             for row in old_horizons)
    require(observed_dates == set(old_days) and prior_horizon_rows == 41
            and direct_symbols == 20 and direct_symbols + 33 == 53
            and len(observed_dates | remaining_days) == 35,
            "prior_low_coverage_not_complete")
    return {
        "schema": "v3-historical-all-extreme-direct-low-coverage-v1",
        "prior_marketwide_only_dates_now_exact_security_checked": len(old_days),
        "previously_exact_security_checked_dates": len(remaining_days),
        "all_35_extreme_dates_direct_security_checked": 35,
        "prior_direct_trading_security_date_pairs": direct_symbols,
        "earlier_direct_trading_security_date_pairs": 33,
        "all_extreme_trading_security_date_pairs": direct_symbols + 33,
        "prior_8_marketwide_issuer_date_pairs": market_rows,
        "prior_8_marketwide_exact_traded_ohlc": exact_candles,
        "prior_8_marketwide_untraded_on_both": untraded,
        "all_85_horizon_extreme_dates_directly_checked": prior_horizon_rows + 44,
        "provider_requests_by_readonly_audit": 0,
        "regular_only_volume_verified": False,
        "historical_adjusted_path_certified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = p.parse_args(argv)
    try:
        saved = json.loads(args.evidence.read_text(encoding="utf-8"))
        baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        later = json.loads(LATER_FILE.read_text(encoding="utf-8"))
        result = build(saved, baseline, later)
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        p.exit(1, "historical_all_extreme_direct_low_evidence_invalid\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
