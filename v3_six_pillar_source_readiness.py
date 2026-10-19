"""Offline source-readiness audit for the user-approved six-pillar 100.

This does not compute company returns, ranks or investment scores. It reports
what the *published* price-screen and rolling DART files actually contain.
Not a source-of-truth certificate, stock selector, or substitute for PIT data.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import math
import re

from v3_score_contract import PILLAR_POINTS

CODE = re.compile(r"[0-9]{6}\Z")
ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
MAX_MARKET_BYTES = 80_000_000
MAX_OTHER_BYTES = 30_000_000
PILLAR_KEYS = ("ias", "financial", "valuation", "technical", "industry",
               "momentum")


class ReadinessError(ValueError):
    """Sanitized source-quality errors with no raw issuer/company data."""


def _day(value):
    if type(value) is not str or not ISO.fullmatch(value):
        raise ReadinessError("invalid_source_day")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ReadinessError("invalid_source_day") from None


def _count(value, limit=20000):
    if type(value) is not int or not 0 <= value <= limit:
        raise ReadinessError("invalid_source_count")
    return value


def _filesafe_load(path, size_limit):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > size_limit:
        raise ReadinessError("missing_or_oversized_local_source")
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (ValueError, UnicodeError, OSError):
        raise ReadinessError("invalid_local_json_source") from None
    if not isinstance(data, dict):
        raise ReadinessError("invalid_local_json_object")
    return data


def _unique_codes(records, expected=None):
    if not isinstance(records, list) or len(records) > 20000:
        raise ReadinessError("invalid_issuer_rows")
    seen = set()
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("code"), str):
            raise ReadinessError("invalid_issuer_row")
        code = row["code"]
        if not CODE.fullmatch(code) or code in seen:
            raise ReadinessError("invalid_or_duplicate_issuer_code")
        seen.add(code)
    if expected is not None and len(records) != expected:
        raise ReadinessError("issuer_count_disagrees_with_rows")
    return seen


def _valid_technical_candidate(item, asof):
    candidate = item.get("v3_pattern_integrated")
    if not isinstance(candidate, dict) or (
        candidate.get("schema") != "v3-pattern-within-six-pillars-research-0.4"
        or candidate.get("asof") != asof
    ):
        return False
    score = candidate.get("technical_research_candidate")
    return (type(score) in (float, int) and math.isfinite(score)
            and 0 <= score <= PILLAR_POINTS["technical"])


def _recent_complete_week(item, market_day):
    if not isinstance(item, dict) or (
        item.get("timeframe") != "weekly"
        or item.get("completed_week_only") is not True
        or item.get("status") != "heuristic_unverified"
        or item.get("scoring_allowed") is not False
    ):
        return False
    try:
        day = _day(item.get("asof"))
    except ReadinessError:
        return False
    return day.weekday() == 4 and 0 <= (market_day-day).days <= 13


def _market_source(market):
    if market.get("status") != "success":
        raise ReadinessError("market_run_not_successful")
    day = _day(market.get("date"))
    total = _count(market.get("candidate_count"))
    rows = market.get("stocks")
    codes = _unique_codes(rows, total)
    observed_flow = _count(market.get("flow_confirmed_count"), total)
    if observed_flow != sum(x.get("flows_confirmed") is True for x in rows):
        raise ReadinessError("published_flow_count_mismatch")
    technical = sum(_valid_technical_candidate(x, day.isoformat())
                    for x in rows)
    paired_relative_strength = sum(
        type(x.get("relative20")) in (float, int)
        and type(x.get("relative60")) in (float, int)
        and math.isfinite(x["relative20"]) and math.isfinite(x["relative60"])
        for x in rows
    )
    daily_observed = sum(
        isinstance(x.get("pattern_research"), dict)
        and x["pattern_research"].get("asof") == day.isoformat()
        and x["pattern_research"].get("timeframe") == "daily"
        and x["pattern_research"].get("status") == "heuristic_unverified"
        and x["pattern_research"].get("scoring_allowed") is False
        for x in rows
    )
    completed_week_observed = sum(
        _recent_complete_week(x.get("pattern_weekly_research"), day)
        for x in rows
    )
    ma120_observed = sum(
        type(x.get("ma120w_gap")) in (int, float)
        and math.isfinite(x["ma120w_gap"])
        for x in rows
    )
    structural_stop_observed = sum(
        type(x.get("price")) in (int, float)
        and type(x.get("stop_line")) in (int, float)
        and type(x.get("stop_pct")) in (int, float)
        and all(math.isfinite(x[k]) for k in ("price", "stop_line", "stop_pct"))
        and 0 < x["stop_line"] < x["price"]
        and x["stop_pct"] < 0
        for x in rows
    )
    return day, codes, {
        "priced_screened_issuer_count": total,
        "v2_legacy_flow_confirmed_count_not_v3_ias_certified": observed_flow,
        "research_technical25_observed_issuer_count_not_trade_certified": technical,
        "current_snapshot_daily_pattern_observation_count": daily_observed,
        "current_snapshot_completed_week_pattern_observation_count": completed_week_observed,
        "legacy_120week_ma_reference_count_not_official": ma120_observed,
        "structural_stop_reference_count_not_executable": structural_stop_observed,
        "v2_relative_strength_pair_count_not_official_v3_momentum": paired_relative_strength,
        "reported_full_six_pillar_sort_count":
            len(market.get("six_pillar_research_sort") or []),
    }


def audit(market, catalog, book):
    if not all(isinstance(x, dict) for x in (market, catalog, book)):
        raise ReadinessError("invalid_source_object")
    market_day, screened, price_counts = _market_source(market)
    listed = _count(catalog.get("listed_total"))
    all_codes = _unique_codes(catalog.get("rows"), listed)
    if not screened.issubset(all_codes):
        raise ReadinessError("screened_issuer_not_in_market_master")
    catalog_day = _day(catalog.get("asof") or catalog.get("date"))
    book_day = _day(book.get("asof"))
    if catalog_day != market_day:
        raise ReadinessError("market_catalog_source_date_mismatch")
    sector_label_count = sum(
        isinstance(r.get("sector"), str) and bool(r["sector"].strip())
        for r in catalog["rows"]
    )
    if book_day > market_day:
        raise ReadinessError("future_dart_reference_source")
    attempts = _count(book.get("attempted_reference_count"), listed)
    annuals = _count(book.get("observed_annual_count"), attempts)
    pbr = _count(book.get("pbr_simple_reference_count"), annuals)
    if _count(book.get("market_listed_total")) != listed:
        raise ReadinessError("dart_listed_census_mismatch")
    if book.get("historical_point_in_time_verified") is not False or (
        book.get("official_exchange_pbr_verified") is not False):
        raise ReadinessError("unexpected_dart_certification_status")
    # Relative field pairs, book references and daily pattern heuristics are
    # observations; none of them certify a complete scoring pillar.
    blockers = {
        "ias": "v2_flow_observation_not_verified_20_60_120_250_day_PIT_IAS",
        "financial": "one_year_dart_book_not_four_year_cfs_ofs_original_receipts",
        "valuation": "simple_annual_PB_not_official_exchange_PBR_or_earnings_FCF_yields",
        "technical": "heuristic_price_patterns_not_independently_approved_live_score",
        "industry": "issuer_sector_membership_and_full_cohort_PIT_unverified",
        "momentum": "historical_market_relative_returns_not_independently_PIT_verified",
    }
    if dict(PILLAR_POINTS) != {
        "ias": 20, "financial": 15, "valuation": 15,
        "technical": 25, "industry": 15, "momentum": 10,
    }:
        raise ReadinessError("unexpected_scoring_contract")
    return {
        "schema": "v3-six-pillar-source-readiness-audit-0.1",
        "market_asof": market_day.isoformat(),
        "catalog_asof": catalog_day.isoformat(),
        "dart_reference_asof": book_day.isoformat(),
        "source_day_lag_calendar_days": (market_day - book_day).days,
        "contract_weights": dict(PILLAR_POINTS),
        "observed_counts": {
            "current_market_listed": listed,
            **price_counts,
            "listed_sector_label_count_not_PIT_membership": sector_label_count,
            "dart_issuer_attempts": attempts,
            "dart_annual_issuer_observed_not_3y": annuals,
            "dart_simple_pb_references_not_official_pbr": pbr,
            "unattempted_listed_dart_issuers": listed-attempts,
        },
        "pillars": {
            k: {"maximum_points": PILLAR_POINTS[k],
                "confirmed_for_investment": False,
                "blocker": blockers[k]}
            for k in PILLAR_KEYS
        },
        "full_six_pillar_investment_rank_ready": False,
        "confirmed_buy_candidates": None,
        "investment_decision_changed": False,
        "user_interface_changed": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", default="docs/data.json")
    ap.add_argument("--catalog", default="docs/v22-market-catalog.json")
    ap.add_argument("--book", default="docs/v3-dart-book-reference.json")
    ap.add_argument("--output", help="Private artifact path; never publish as a stock rank")
    a = ap.parse_args(argv)
    report = audit(_filesafe_load(a.market, MAX_MARKET_BYTES),
                   _filesafe_load(a.catalog, MAX_OTHER_BYTES),
                   _filesafe_load(a.book, MAX_OTHER_BYTES))
    printed = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(printed + "\n", encoding="utf-8")
    print(printed)


if __name__ == "__main__":
    main()
