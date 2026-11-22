"""Audit only the saved official market-wide OHLC evidence for frozen downside extrema.

The source is a pinned PUBLIC AGGREGATE report from prior authenticated KRX
comparisons, never original vendor records. Zero KRX requests. Market-wide
exact traded OHLC does not prove adjusted holding-unit paths, PIT scores,
session-only traded volume, or independent issuer-level intraday LOW inspection.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import re

from v3_historical_pilot_daily_replay import SOURCE_DIGESTS
from v3_historical_pilot_official_prices import BATCHES

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "ops/v3-historical-turnover-corroboration-20261117.json"
EXTREMES = ROOT / "ops/v3-performance-downside-horizons-20251113.csv"
SCHEMA = "v3-historical-downside-official-ohlc-evidence-v1"
SOURCE_SCHEMA = "v3-historical-turnover-corroboration-20261117-v1"
HORIZONS = ("1", "2", "3", "6", "12")
SHA = re.compile(r"[a-f0-9]{64}\Z")


class HistoricalExtremeEvidenceError(ValueError):
    """Only bounded fixed reason codes, never underlying market records."""


def require(condition, reason):
    if not condition:
        raise HistoricalExtremeEvidenceError(reason)


def session_day(value):
    require(type(value) is str and len(value) == 10, "invalid_extreme_evidence_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise HistoricalExtremeEvidenceError("invalid_extreme_evidence_date") from None
    require(parsed.isoformat() == value, "invalid_extreme_evidence_date")
    return parsed


def verified_market_session(raw):
    """A saved aggregate may attest every TRADED candle, not halted lows."""
    require(type(raw) is dict, "invalid_saved_official_market_scope")
    required = ("scope", "exact_ohlc", "positive_trade_on_both",
                "no_positive_trade_on_either", "absent_from_official",
                "absent_from_public", "identity_mismatch",
                "listed_share_mismatch", "listed_shares_unavailable",
                "positive_trade_on_public_only", "positive_trade_on_official_only")
    require(all(type(raw.get(k)) is int and raw[k] >= 0 for k in required),
            "invalid_saved_official_market_scope")
    require(raw["scope"] >= 1000 and raw["exact_ohlc"] > 0
            and raw["scope"] == raw["exact_ohlc"] + raw["no_positive_trade_on_either"]
            and raw["exact_ohlc"] == raw["positive_trade_on_both"]
            and all(raw[k] == 0 for k in required[4:]),
            "historical_marketwide_official_ohlc_not_exact")
    diff = raw.get("different_price_by_field")
    require(type(diff) is dict and set(diff) == {"open", "high", "low", "close"}
            and all(type(n) is int and n == 0 for n in diff.values()),
            "historical_marketwide_official_ohlc_not_exact")
    return raw["scope"], raw["exact_ohlc"], raw["no_positive_trade_on_either"]


def evidence_sessions(report):
    require(type(report) is dict and report.get("schema") == SOURCE_SCHEMA,
            "invalid_historical_official_report")
    require(report.get("source_scope", {}).get("regular_0900_1530_volume_verified") is False
            and report["source_scope"].get("regular_0900_1530_turnover_verified") is False
            and report["source_scope"].get("adjusted_path_certified") is False
            and report["source_scope"].get("point_in_time_scores_verified") is False
            and report["source_scope"].get("performance_validated") is False,
            "historical_official_coverage_overclaimed")
    batches = report.get("batches")
    require(type(batches) is list and len(batches) >= 1,
            "missing_historical_official_batches")
    seen_names, selected = set(), {}
    scope_total = candle_total = zero_total = 0
    for batch in batches:
        require(type(batch) is dict and type(batch.get("batch")) is str
                and batch["batch"] in BATCHES
                and batch["batch"] not in seen_names,
                "reused_or_unknown_official_batch")
        seen_names.add(batch["batch"])
        requested = batch.get("requested_sessions")
        require(requested == [d.isoformat() for d in BATCHES[batch["batch"]]]
                and 1 <= len(requested) <= 5
                and type(batch.get("job_id")) is int and batch["job_id"] > 0
                and batch.get("schema") == "v3-historical-pilot-official-prices-v1"
                and batch.get("failed_sessions") == {}
                and batch.get("public_panel_source_sha256") == list(SOURCE_DIGESTS)
                and batch.get("performance_validated") is False
                and batch.get("adjusted_price_history_verified") is False
                and batch.get("volume_session_equivalence_verified") is False,
                "invalid_historical_official_batch")
        sessions = batch.get("sessions")
        require(type(sessions) is dict and set(sessions) == set(requested),
                "partial_historical_official_session_evidence")
        for on, data in sessions.items():
            session_day(on)
            require(on not in selected and type(data) is dict,
                    "duplicate_official_ohlc_session")
            hashes = data.get("official_source_sha256")
            require(type(hashes) is dict and set(hashes) == {"KOSPI", "KOSDAQ"}
                    and all(type(v) is str and SHA.fullmatch(v)
                            for v in hashes.values()),
                    "missing_historical_official_source_fingerprint")
            count, exact, untraded = verified_market_session(data.get("market_wide"))
            selected[on] = True
            scope_total += count
            candle_total += exact
            zero_total += untraded
    summary = report.get("summary")
    require(type(summary) is dict
            and summary.get("sessions") == len(selected)
            and summary.get("rows") == scope_total
            and summary.get("volume_matched") == scope_total
            and summary.get("amount_matched") == scope_total
            and summary.get("both_zero") == zero_total
            and summary.get("volume_mismatched") == 0
            and summary.get("amount_mismatched") == 0
            and summary.get("unavailable") == 0
            and summary.get("identity_mismatch") == 0,
            "historical_official_aggregate_inconsistent")
    return selected, scope_total, candle_total, zero_total


def build(report, rows):
    sessions, scope, exact, untraded = evidence_sessions(report)
    require(type(rows) is list and len(rows) == 85
            and all(type(row) is dict for row in rows),
            "invalid_frozen_downside_extreme_roster")
    seen, per_horizon = set(), {h: {"with_saved_official_ohlc": 0,
                                   "without_saved_official_ohlc": 0}
                                for h in HORIZONS}
    covered_days, all_days = set(), set()
    for row in rows:
        code, horizon, extreme_on = (row.get("code"), row.get("horizon_months"),
                                     row.get("adverse_extreme_on"))
        require(type(code) is str and re.fullmatch(r"[0-9A-Z]{6}", code)
                and horizon in HORIZONS and (code, horizon) not in seen,
                "duplicate_or_invalid_frozen_extreme_horizon")
        seen.add((code, horizon))
        day = session_day(extreme_on)
        valuation = session_day(row.get("valuation_session"))
        require(date(2025, 11, 14) <= day <= valuation <= date(2026, 11, 13)
                and type(row.get("observed_trade_bars")) is str
                and row["observed_trade_bars"].isascii()
                and row["observed_trade_bars"].isdecimal()
                and int(row["observed_trade_bars"]) > 0,
                "frozen_downside_extreme_outside_observed_path")
        all_days.add(extreme_on)
        if extreme_on in sessions:
            covered_days.add(extreme_on)
            per_horizon[horizon]["with_saved_official_ohlc"] += 1
        else:
            per_horizon[horizon]["without_saved_official_ohlc"] += 1
    issuers = {code for code, _ in seen}
    require(len(issuers) == 17 and len(seen) == 85
            and all({h for code, h in seen if code == issuer} == set(HORIZONS)
                    for issuer in issuers),
            "incomplete_frozen_downside_horizon_roster")
    with_evidence = sum(x["with_saved_official_ohlc"] for x in per_horizon.values())
    return {
        "schema": SCHEMA,
        "evidence_scope": "pinned_saved_marketwide_official_ohlc_aggregate_only",
        "saved_comparison_sessions": len(sessions),
        "saved_comparison_market_rows": scope,
        "saved_comparison_traded_ohlc_exact": exact,
        "saved_comparison_no_positive_trade": untraded,
        "frozen_downside_horizon_rows": len(rows),
        "frozen_downside_unique_extreme_dates": len(all_days),
        "horizon_rows_with_saved_official_ohlc_day": with_evidence,
        "horizon_rows_without_saved_official_ohlc_day": len(rows) - with_evidence,
        "extreme_dates_with_saved_official_ohlc": len(covered_days),
        "extreme_dates_without_saved_official_ohlc": len(all_days - covered_days),
        "official_ohlc_extreme_dates": sorted(covered_days),
        "not_documented_in_this_saved_ohlc_report_dates": sorted(all_days - covered_days),
        "by_horizon": per_horizon,
        "new_provider_calls": 0,
        "target_issuer_extreme_low_directly_authenticated": False,
        "intraday_extreme_entire_holding_path_verified": False,
        "historical_adjusted_prices_verified": False,
        "regular_only_volume_verified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=EVIDENCE)
    parser.add_argument("--extremes", type=Path, default=EXTREMES)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.source_report.read_text(encoding="utf-8"))
        with args.extremes.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        result = build(report, rows)
    except (OSError, ValueError, KeyError, TypeError, HistoricalExtremeEvidenceError) as exc:
        parser.exit(1, "historical_saved_official_ohlc_coverage_invalid\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
