"""Continuous, offline V3 source quality watch. No provider access or stock picks.

Runs after successful scheduled full-market batches AND independently every
weekday morning KST so failed or skipped publisher runs cannot hide staleness.
Never mutates docs/, underlying scores, existing portfolio, or API quotas.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from v3_six_pillar_source_readiness import (
    ReadinessError, _day, _filesafe_load, audit,
    MAX_MARKET_BYTES, MAX_OTHER_BYTES,
)
from v3_dart_four_year_reference import SCHEMA as FOUR_SCHEMA
from v3_dart_four_year_reference import _record as verify_four_annual_record

VERSION = "v3-continuous-market-quality-watch-0.1"
MIN_PUBLISHED_COVERAGE = 0.80
CUTOVER_OBSERVATION_DAY = date(2026, 10, 20)
KST = ZoneInfo("Asia/Seoul")


def verify_four_year_optional(four: dict | None, catalog: dict,
                              market_day: date) -> dict:
    """Count revalidated source observations, never extrapolate 15-point scores."""
    absent = {"status": "not_published", "four_year_observed": 0,
              "four_year_core_financial_research_observed": 0,
              "four_year_income_values_observed": 0,
              "last_three_years_operating_cashflow_observed": 0,
              "latest_year_balance_values_observed": 0,
              "pilot_issuer_count": 0, "source_date": None}
    if four is None:
        return absent
    if not isinstance(four, dict) or four.get("schema") != FOUR_SCHEMA:
        raise ReadinessError("invalid_four_year_reference_schema")
    refday = _day(four.get("asof"))
    if refday > market_day:
        raise ReadinessError("future_four_year_reference")
    if four.get("listed_total") != catalog.get("listed_total"):
        raise ReadinessError("four_year_market_census_mismatch")
    for name, expected in (
        ("full_v3_ready", False),
        ("four_years_all_financial_pillars_certified", False),
        ("raw_account_rows_stored", False),
    ):
        if four.get(name) is not expected:
            raise ReadinessError("unearned_four_year_score_certification")
    if four.get("confirmed_six_pillar_ranks") is not None:
        raise ReadinessError("unearned_four_year_investment_ranks")
    records = four.get("records")
    if not isinstance(records, list) or len(records) > catalog["listed_total"]:
        raise ReadinessError("invalid_four_year_issuer_rows")
    listed = {x["code"] for x in catalog["rows"]}
    unique = set()
    observed = 0
    income_observed = 0
    cashflow_observed = 0
    balance_observed = 0
    core_observed = 0
    for row in records:
        if not isinstance(row, dict) or type(row.get("code")) is not str:
            raise ReadinessError("invalid_four_year_issuer_row")
        code = row["code"]
        if code not in listed or code in unique:
            raise ReadinessError("invalid_or_duplicate_four_year_issuer")
        unique.add(code)
        # Re-check source receipt years, dates, CFS/OFS consistency and
        # observed statement values. Refuse hand-filled completed flags.
        try:
            rebuilt = verify_four_annual_record(code, row.get("reports"), str(market_day))
        except (ValueError, TypeError, KeyError):
            raise ReadinessError("invalid_four_year_annual_evidence") from None
        if (row.get("same_scope_four_years") is not rebuilt["same_scope_four_years"]
                or row.get("observed_fiscal_years") != rebuilt["observed_fiscal_years"]
                or row.get("annual_revenue_cagr3_reference_pct") !=
                    rebuilt["annual_revenue_cagr3_reference_pct"]
                or row.get("three_year_operating_margin_mean_reference_pct") !=
                    rebuilt["three_year_operating_margin_mean_reference_pct"]):
            raise ReadinessError("unverified_four_year_evidence_flag")
        for flag in ("four_year_income_values_observed",
                     "last_three_years_operating_cashflow_observed",
                     "latest_year_balance_values_observed",
                     "core_financial_observed_for_research"):
            if row.get(flag) is not rebuilt[flag]:
                raise ReadinessError("forged_four_year_account_coverage")
        for noncertified in ("financial15_confirmed_points",
                             "valuation15_confirmed_points",
                             "v3_full100_investable_score"):
            if row.get(noncertified) is not None or row.get("buy_certified") is not False:
                raise ReadinessError("unearned_four_year_issuer_score")
        observed += int(rebuilt["same_scope_four_years"])
        income_observed += int(rebuilt["four_year_income_values_observed"])
        cashflow_observed += int(
            rebuilt["last_three_years_operating_cashflow_observed"])
        balance_observed += int(rebuilt["latest_year_balance_values_observed"])
        core_observed += int(rebuilt["core_financial_observed_for_research"])
    if (four.get("pilot_issuer_count") != len(records)
            or four.get("four_consecutive_years_observed_count") != observed
            or four.get("four_year_income_values_observed_count") != income_observed
            or four.get("last_three_years_operating_cashflow_observed_count") != cashflow_observed
            or four.get("latest_year_balance_values_observed_count") != balance_observed
            or four.get("four_year_core_financial_research_observed_count") != core_observed):
        raise ReadinessError("four_year_aggregated_count_mismatch")
    return {"status": "research_only", "four_year_observed": observed,
            "four_year_core_financial_research_observed": core_observed,
            "four_year_income_values_observed": income_observed,
            "last_three_years_operating_cashflow_observed": cashflow_observed,
            "latest_year_balance_values_observed": balance_observed,
            "pilot_issuer_count": len(records), "source_date": refday.isoformat(),
            "source_day_lag_calendar_days": (market_day-refday).days}


def assess_sources(market: dict, catalog: dict, book: dict,
                   four: dict | None = None, *,
                   today: date | None = None) -> dict:
    """Produce date-labelled decision-neutral monitoring metrics.

    No evidence is invented. A long holiday is a warning, not a false claim
    of exchange open status. Hard fail only on severe stale/coverage/data
    violations and when a newly published batch silently loses V3 hook.
    """
    if today is None:
        today = datetime.now(KST).date()
    if type(today) is not date:
        raise ReadinessError("invalid_observer_clock")
    report = audit(market, catalog, book)
    day = _day(report["market_asof"])
    lag = (today - day).days
    if lag < 0:
        raise ReadinessError("future_market_snapshot_relative_to_kst")
    counts = report["observed_counts"]
    listed = counts["current_market_listed"]
    screened = counts["priced_screened_issuer_count"]
    coverage = round(screened / listed * 100, 2) if listed else 0.0
    four_status = verify_four_year_optional(four, catalog, day)
    issues = []
    warnings = []
    if lag >= 14:
        issues.append("published_snapshot_at_least_14_calendar_days_old")
    elif lag >= 4:
        warnings.append("market_snapshot_older_than_four_days_check_calendar_or_batch")
    if listed >= 100 and screened / listed < MIN_PUBLISHED_COVERAGE:
        issues.append("price_screened_coverage_below_80_percent")
    if day >= CUTOVER_OBSERVATION_DAY and screened >= 100:
        if counts["research_technical25_observed_issuer_count_not_trade_certified"] == 0:
            issues.append("new_market_snapshot_missing_all_technical25_observations")
        if counts["current_snapshot_daily_pattern_observation_count"] == 0:
            warnings.append("current_market_daily_pattern_research_absent")
        if counts["current_snapshot_completed_week_pattern_observation_count"] == 0:
            warnings.append("completed_weekly_pattern_research_absent")
    if counts["v2_legacy_flow_confirmed_count_not_v3_ias_certified"] == 0:
        warnings.append("verified_ias20_investor_flow_not_available")
    if four_status["status"] == "not_published":
        warnings.append("four_year_annual_source_not_yet_published")
    if counts["reported_full_six_pillar_sort_count"]:
        # A research shortlist is not a certified 100-point portfolio rank.
        warnings.append("research_sort_is_not_certified_six_pillar_investment_rank")
    return {
        "schema": VERSION,
        "status": "critical" if issues else "attention" if warnings else "pass",
        "market_asof": day.isoformat(),
        "checked_on_kst": today.isoformat(),
        "market_data_calendar_age_days": lag,
        "market_trading_day_verified": False,
        "market_listed_count": listed,
        "market_priced_count": screened,
        "market_coverage_pct": coverage,
        "daily_pattern_observed_count":
            counts["current_snapshot_daily_pattern_observation_count"],
        "completed_weekly_pattern_observed_count":
            counts["current_snapshot_completed_week_pattern_observation_count"],
        "technical25_research_observed_count":
            counts["research_technical25_observed_issuer_count_not_trade_certified"],
        "v2_legacy_flow_observed_not_ias20":
            counts["v2_legacy_flow_confirmed_count_not_v3_ias_certified"],
        "dart_one_year_observed_count":
            counts["dart_annual_issuer_observed_not_3y"],
        "dart_book_source_day_lag": report["source_day_lag_calendar_days"],
        "dart_four_year": four_status,
        "contract_weights": report["contract_weights"],
        "full_investment_rank_certified": False,
        "full_investment_rank_count": None,
        "trade_recommendation": None,
        "source_quality_issues": issues,
        "source_quality_warnings": warnings,
        "published_data_modified": False,
    }


def markdown_summary(result: dict) -> str:
    def cell(value):
        return str(value).replace("|", " ").replace("\n", " ")
    metrics = [
        ("진단", result.get("status", "critical")),
        ("시장 자료 기준일", result.get("market_asof", "미확인")),
        ("검증 실행일(KST)", result.get("checked_on_kst", "미확인")),
        ("자료 경과 달력일", result.get("market_data_calendar_age_days", "미확인")),
        ("가격 분석 종목", result.get("market_priced_count", "미확인")),
        ("가격 분석 확보율(%)", result.get("market_coverage_pct", "미확인")),
        ("일봉 패턴 원천 관측", result.get("daily_pattern_observed_count", "미확인")),
        ("완성 주봉 원천 관측", result.get("completed_weekly_pattern_observed_count", "미확인")),
        ("기술적 25점 연구 관측", result.get("technical25_research_observed_count", "미확인")),
        ("1개년 DART 참고", result.get("dart_one_year_observed_count", "미확인")),
        ("4개년 DART 공시 연속 관측", result.get("dart_four_year", {}).get("four_year_observed", "미확인")),
        ("4개년 핵심 재무수치 관측", result.get("dart_four_year", {}).get("four_year_core_financial_research_observed", "미확인")),
        ("정식 6영역 투자순위", "미인증"),
    ]
    table = "\n".join("| "+cell(k)+" | "+cell(v)+" |" for k,v in metrics)
    flags = result.get("source_quality_issues", [])+result.get("source_quality_warnings", [])
    return ("## V3 데이터 지속 품질감사 (읽기 전용)\n\n"
            "| 항목 | 검증 결과 |\n|---|---|\n"+table+"\n\n"
            +"검사·경고: "+(", ".join(cell(x) for x in flags) if flags else "없음")
            +"\n\n주의: 지연은 휴장일 여부 미확인 상태의 달력일입니다."
             " 투자점수, 공식 수급·밸류, 실시간 매수 신호가 아닙니다.\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="docs/data.json")
    parser.add_argument("--catalog", default="docs/v22-market-catalog.json")
    parser.add_argument("--book", default="docs/v3-dart-book-reference.json")
    parser.add_argument("--four-year", default="docs/v3-dart-four-year-reference.json")
    parser.add_argument("--output", default="reports/v3-continuous-quality-watch.json")
    parser.add_argument("--summary", help="GitHub Actions $GITHUB_STEP_SUMMARY file")
    a = parser.parse_args(argv)
    try:
        market = _filesafe_load(a.market, MAX_MARKET_BYTES)
        catalog = _filesafe_load(a.catalog, MAX_OTHER_BYTES)
        book = _filesafe_load(a.book, MAX_OTHER_BYTES)
        four = (_filesafe_load(a.four_year, MAX_OTHER_BYTES)
                if Path(a.four_year).is_file() else None)
        result = assess_sources(market, catalog, book, four)
    except (ReadinessError, ValueError) as exc:
        # Never echo raw company payloads, keys or source values into logs.
        reason = (str(exc) if isinstance(exc, ReadinessError)
                  else "invalid_source_evidence")
        result = {
            "schema": VERSION, "status": "critical",
            "checked_on_kst": datetime.now(KST).date().isoformat(),
            "full_investment_rank_certified": False,
            "trade_recommendation": None,
            "source_quality_issues": [reason],
            "source_quality_warnings": [],
            "published_data_modified": False,
        }
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2,
                         allow_nan=False)+"\n"
    destination = Path(a.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    summary = markdown_summary(result)
    if a.summary:
        with Path(a.summary).open("a", encoding="utf-8") as stream:
            stream.write(summary+"\n")
    print(summary)
    return 2 if result["status"] == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
