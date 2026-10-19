"""Probe 2016, 2021 and 2026 KRX access for the planned 10-year research.

Three selected noncontiguous historical days x KOSPI/KOSDAQ = 6 API calls.
No datastore, no raw rows, no raw hashes, no identifiers/prices in reports.
This checks whether provider access *reaches* 10 years, not completeness
of all intervening observations nor historic PIT/delistings/split data.
"""
from __future__ import annotations

import argparse
import json
import os

from v3_krx_history_collection import collect_krx_history, HistoryCollectionError

HISTORICAL_DAYS = ("2016-10-17", "2021-10-18", "2026-10-16")


class HistoricalReachError(ValueError):
    """Static safe reasons, never echo upstream values or credential."""


def research_historical_reach(*, live=False, key=None, client=None):
    if live is not True:
        return {
            "schema": "v3-krx-decade-reach-check-0.1",
            "status": "dry_run", "planned_market_calls_maximum": 6,
            "observed_dates": 0, "actual_api_calls": 0,
            "real_historical_access_observed": False,
            "private_raw_retained": False, "v3_release_ready": False,
        }
    if type(key) is not str or not key.strip():
        raise HistoricalReachError("krx_key_unavailable")
    try:
        report = collect_krx_history(
            list(HISTORICAL_DAYS), live=True, key=key, client=client,
            archive_path=None)
    except HistoryCollectionError:
        raise HistoricalReachError("provider_or_historical_shape_failure") from None
    except Exception:
        raise HistoricalReachError("unclassified_historical_probe_failure") from None
    if (report.get("observed_days") != len(HISTORICAL_DAYS)
            or len(report.get("days", [])) != len(HISTORICAL_DAYS)):
        raise HistoricalReachError("historic_provider_dates_incomplete")
    days = []
    for index, rawday in enumerate(report["days"]):
        if rawday.get("asof") != HISTORICAL_DAYS[index]:
            raise HistoricalReachError("historic_provider_date_mismatch")
        markets = {m.get("market"): m for m in rawday.get("markets", [])}
        if set(markets) != {"KOSPI", "KOSDAQ"}:
            raise HistoricalReachError("historic_provider_market_missing")
        item = {
            "day": HISTORICAL_DAYS[index],
            "market_source_row_count": {},
            "market_valid_numeric_count": {},
            "market_quarantined_count": {},
        }
        for market in ("KOSPI", "KOSDAQ"):
            m = markets[market]
            n = m["provider_rows_observed"]
            valid = m["observed_numeric_candidates"]
            quarantined = m["quarantined_unpriced_candidates"]
            if (type(n) is not int or type(valid) is not int
                    or type(quarantined) is not int or n < 1
                    or valid < 1 or quarantined < 0
                    or valid + quarantined > n):
                raise HistoricalReachError("historic_observation_count_invalid")
            item["market_source_row_count"][market] = n
            item["market_valid_numeric_count"][market] = valid
            item["market_quarantined_count"][market] = quarantined
        days.append(item)
    return {
        "schema": "v3-krx-decade-reach-check-0.1",
        "status": "real_historical_2016_and_2021_provider_rows_observed",
        "source": "KRX OpenAPI two-market daily stock endpoints",
        "observed_dates": len(days),
        "actual_api_calls": 2 * len(days),
        "day_summaries": days,
        "oldest_historical_api_date_observed": HISTORICAL_DAYS[0],
        "newest_historical_api_date_observed": HISTORICAL_DAYS[-1],
        "real_historical_access_observed": True,
        "all_intervening_trade_dates_archived": False,
        "official_historical_session_calendar_verified": False,
        "historic_stock_master_and_delistings_verified": False,
        "corporate_action_adjustments_verified": False,
        "private_raw_retained": False, "vendor_rows_published": False,
        "v3_release_ready": False, "confirmed_buy": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Three historical KRX candidate years, counts only")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = research_historical_reach(
            live=args.live, key=os.environ.get("KRX_API_KEY"))
        print(json.dumps(result, sort_keys=True))
        return 0
    except HistoricalReachError as exc:
        print(json.dumps({
            "status": "blocked", "reason": str(exc),
            "private_raw_retained": False, "v3_release_ready": False,
            "confirmed_buy": False,
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
