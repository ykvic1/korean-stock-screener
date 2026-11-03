"""Aggregate-only coverage of private actual-money disjoint-period research.

Read at most 96 immutable historical cohorts, no KRX/provider calls, no
per-issuer results, prices or monetary amounts in public output. The frozen
archive endpoint is explicit; this is NOT current-session score evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from v3_krx_money_backfill import CHECKPOINT, _path, _cohort_at_recent_first, history_candidates
from v3_krx_money_public_progress import collect as progress, PRIVATE_REPO, MoneyProgressError
from v3_krx_money_5day import validate_money_blob
from v3_krx_money_horizon_research import observe, DAILY
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA = "v3-public-private-money-disjoint-horizon-coverage-0.1"
MAX_COHORTS = 2 * max(DAILY) // 5
OUTPUT = "v3-krx-money-horizon-coverage.json"


class MoneyCoverageError(ValueError):
    """Fixed error codes only; no private source content in diagnostics."""


def collect(storage, dates, digest):
    if storage.repository != PRIVATE_REPO:
        raise MoneyCoverageError("unexpected_money_coverage_repository")
    storage.verify_private()
    before, version = storage.get_content(CHECKPOINT)
    coverage = progress(storage, dates, digest)
    count = min(coverage["five_session_cohorts_completed"], MAX_COHORTS)
    records = []
    for index in range(count):
        window = _cohort_at_recent_first(dates, index)
        blob, _ = storage.get_content(_path(window))
        try:
            cohort = validate_money_blob(blob, window[0], window[-1])
        except Exception:
            raise MoneyCoverageError("missing_or_invalid_money_coverage_cohort") from None
        records.append({"index":index,"cohort":cohort})
    endpoint = records[0]["cohort"]["window_end"] if records else None
    population = (records[0]["cohort"]["derived_issuer_money_factors"]
                  if records else {})
    horizons = {str(h):{
        "latest_window_complete_issuer_market_pairs":0,
        "disjoint_comparison_complete_issuer_market_pairs":0,
        "zero_baseline_issuer_market_pairs":0,
        "positive_baseline_comparable_issuer_market_pairs":0,
        "higher_average_amount_issuer_market_pairs":0,
        "every_recent_cohort_above_previous_average_issuer_market_pairs":0,
    } for h in DAILY}
    for factor in population.values():
        try:
            result = observe(records,market=factor["market"],
                             code=factor["issuer_code"],asof=endpoint)
        except Exception:
            raise MoneyCoverageError("invalid_money_coverage_issuer_evidence") from None
        for h in DAILY:
            window = result["daily_research"][str(h)]
            if window is None:
                continue
            row = horizons[str(h)]
            row["latest_window_complete_issuer_market_pairs"] += 1
            row["disjoint_comparison_complete_issuer_market_pairs"] += int(window["disjoint_baseline_complete"])
            row["zero_baseline_issuer_market_pairs"] += int(window["disjoint_baseline_zero_amount"])
            ratio = window["versus_previous_disjoint_window_ratio"]
            if ratio is not None:
                row["positive_baseline_comparable_issuer_market_pairs"] += 1
                row["higher_average_amount_issuer_market_pairs"] += int(
                    window["amount_increased_vs_disjoint_baseline"])
                row["every_recent_cohort_above_previous_average_issuer_market_pairs"] += int(
                    window["recent_cohorts_above_previous_average_ratio"] == 1)
    after, after_version = storage.get_content(CHECKPOINT)
    if before != after or version != after_version:
        raise MoneyCoverageError("money_coverage_checkpoint_changed_during_read")
    return {
        "schema":SCHEMA,
        "historical_archive_endpoint":endpoint,
        "population":"complete_issuer_market_pairs_in_latest_historical_money_cohort",
        "population_issuer_market_pairs":len(population),
        "historical_cohorts_inspected":count,
        "historical_cohorts_read_limit":MAX_COHORTS,
        "daily_disjoint_period_coverage":horizons,
        "calendar_weekly_verified":False,
        "regular_session_only_verified":False,
        "corporate_actions_and_issuer_pit_verified":False,
        "current_market_session_alignment_verified":False,
        "exact_daily_money_obv_verified":False,
        "exact_single_day_spike_concentration_verified":False,
        "private_checkpoint_stable_during_read":True,
        "original_bars_or_per_issuer_amounts_published":False,
        "new_krx_api_calls":0,
        "investment_points":None,
        "scoring_allowed":False,
        "existing_issuer_scores_unchanged":True,
        "note":"Historical, quantized five-session amount comparisons only; "
               "not current liquidity, exact calendar weeks, identified buying "
               "or validated investment-score/return evidence.",
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs",default="docs")
    parser.add_argument("--mode",choices=("verify","write"),default="verify")
    args=parser.parse_args(argv)
    repo=os.getenv("V3_PRIVATE_ARCHIVE_REPO","")
    token=os.getenv("V3_PRIVATE_ARCHIVE_TOKEN","")
    if repo != PRIVATE_REPO or len(token) < 20:
        raise MoneyCoverageError("private_money_coverage_secrets_missing")
    dates,digest=history_candidates()
    report=collect(PrivateGitStorage(repo,token),dates,digest)
    target=Path(args.docs)/OUTPUT
    if args.mode=="write":
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf8")
    else:
        try:
            prior=json.loads(target.read_text(encoding="utf8"))
        except Exception:
            raise MoneyCoverageError("published_money_coverage_unavailable") from None
        if prior != report:
            raise MoneyCoverageError("published_money_coverage_changed")
    print(json.dumps({"status":"verified_historical_money_horizon_coverage",
                      "cohorts_inspected":report["historical_cohorts_inspected"],
                      "new_krx_api_calls":0,"scoring_allowed":False},sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status":"blocked",
                          "category":str(error) if isinstance(error,(MoneyCoverageError,MoneyProgressError))
                          else "untrusted_private_money_horizon_error",
                          "source_payload_logged":False},sort_keys=True))
        raise SystemExit(1)
