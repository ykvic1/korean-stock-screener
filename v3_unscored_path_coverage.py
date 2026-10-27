"""Aggregate a published full-market daily snapshot's UN-SCORED price-path coverage.

No network/provider requests, no raw OHLCV export, and no extra 4+7 scoring.
The JSON is an audit of the CURRENT dated price scan only, not a backtest,
trade signal, official-KRX verification, or a 100-point V3 scorecard.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from v3_unscored_path_observations import SCHEMA, valid_observation


class PathCoverageError(ValueError):
    pass


def _count(value):
    return type(value) is int and value >= 0


def summarize(daily: dict) -> dict:
    if type(daily) is not dict or daily.get("status") != "success":
        raise PathCoverageError("invalid_published_daily_snapshot")
    asof = daily.get("date")
    try:
        if type(asof) is not str or date.fromisoformat(asof).isoformat() != asof:
            raise ValueError("invalid_date")
    except (ValueError, TypeError):
        raise PathCoverageError("invalid_daily_source_date") from None
    stocks = daily.get("stocks")
    source = daily.get("v3_unscored_path_source")
    if type(stocks) is not list or type(source) is not dict:
        raise PathCoverageError("missing_price_path_source")
    if (source.get("schema") != SCHEMA
            or source.get("additional_volume_or_momentum_points_authorized") != 0
            or type(source.get("additional_volume_or_momentum_points_authorized")) is not int
            or source.get("raw_ohlcv_exported") is not False
            or source.get("source_exchange_and_adjustments_independently_verified") is not False):
        raise PathCoverageError("unearned_price_path_source_claim")
    eligible, examined, candidates = (
        daily.get("eligible_count"), daily.get("examined_count"),
        daily.get("candidate_count"))
    if (not all(_count(n) for n in (eligible, examined, candidates))
            or not candidates <= examined <= eligible
            or candidates != len(stocks)):
        raise PathCoverageError("daily_scan_count_mismatch")
    if type(daily.get("history_scan_complete")) is not bool:
        raise PathCoverageError("missing_full_market_completion_status")
    observations = []
    symbols = set()
    for stock in stocks:
        if type(stock) is not dict or stock.get("price_date") != asof:
            raise PathCoverageError("mixed_market_session_observation")
        code = stock.get("code")
        if type(code) is not str or not code or code in symbols:
            raise PathCoverageError("invalid_or_duplicate_stock_code")
        symbols.add(code)
        observation = stock.get("v3_unscored_path_observations")
        if not valid_observation(observation, asof):
            raise PathCoverageError("unverified_or_forged_path_observation")
        if observation is not None:
            observations.append(observation)
    count = len(observations)
    if (source.get("observed_issuer_count") != count
            or type(source.get("observed_issuer_count")) is not int
            or source.get("unavailable_issuer_count") != candidates-count
            or type(source.get("unavailable_issuer_count")) is not int):
        raise PathCoverageError("source_coverage_receipt_mismatch")
    # All numbers describe counted bars from an unadjusted auxiliary price
    # feed. None grants new investment-score points, even when count is high.
    return {
        "schema": "v3-unscored-path-source-coverage-0.1",
        "asof": asof,
        "listed_eligible_issuers": eligible,
        "attempted_issuers": examined,
        "current_price_issuers": candidates,
        "market_scan_complete": daily["history_scan_complete"],
        "observed_121_session_paths": count,
        "unavailable_121_session_paths": candidates-count,
        "path_coverage_pct_of_current": round(100*count/candidates, 2) if candidates else None,
        "up_days20_majority_issuer_count": sum(o["up_close_days20"] >= 11 for o in observations),
        "positive_five_session_blocks_majority_count": sum(
            o["positive_nonoverlapping_five_interval_blocks60"] >= 7
            for o in observations),
        "recent60_outperformed_prior60_issuer_count": sum(
            o["recent60_close_change_pct"] > o["prior60_close_change_pct"]
            for o in observations),
        "issuers_with_complete_high_volume_up_spike": sum(
            o["complete_post_spike_events_evaluated"] > 0
            for o in observations),
        "issuers_with_post_spike_price_hold_and_volume_easing": sum(
            o["post_spike_price_hold_with_volume_easing_events"] > 0
            for o in observations),
        "additional_volume_or_momentum_points_authorized": 0,
        "raw_ohlcv_exported": False,
        "regular_session_independently_verified": False,
        "corporate_actions_adjusted_verified": False,
        "market_index_path_verified": False,
        "investment_grade_score_certified": False,
        "interpretation": "unscored_research_coverage_not_a_buy_signal",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", default="docs/data.json")
    parser.add_argument("--output", default="docs/v3-unscored-path-coverage.json")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    summary = summarize(json.loads(Path(args.daily).read_text(encoding="utf-8")))
    output = Path(args.output)
    if args.verify:
        if not output.exists() or json.loads(output.read_text(encoding="utf-8")) != summary:
            raise PathCoverageError("price_path_coverage_out_of_sync")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False,
                                     indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    print(json.dumps({"status": "verified" if args.verify else "written",
                      "asof": summary["asof"],
                      "observed_121_session_paths": summary["observed_121_session_paths"],
                      "new_points": 0}))


if __name__ == "__main__":
    main()
