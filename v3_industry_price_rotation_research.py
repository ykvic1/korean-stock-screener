"""Current DART company-code industry x saved close-date relative strength.

Zero provider calls, no raw OHLCV publication. These are CURRENT company
industry prefixes and V2.2 approximate stock-vs-index 20/60 session relative
returns, not historical exchange sector membership or certified factor scores.
Missing, stale, partial, malformed or extremely small samples stay unknown.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import date
from io import StringIO
import json
import math
from pathlib import Path
from statistics import median

from v3_dart_rolling_book_reference import _inputs
from v3_dart_industry_codes import prior_records
from v3_dart_bulk_history import _stable_digest, write_json

SCHEMA = "v3-current-company-industry-relative-strength-observed-v1"
MIN_DISCLOSE = 3
MIN_SIGNAL = 10
MIN_SIGNAL_COVERAGE = 0.75
MAX_RELATIVE_PCT = 1000


class IndustryPriceError(ValueError):
    """Do not expose provider observations in error text."""


def source_ready(market, catalog, industry):
    """Return False on genuinely unfinished daily source synchronisation."""
    if any(type(x) is not dict for x in (market, catalog, industry)):
        return False
    asof = catalog.get("date")
    try:
        day = date.fromisoformat(asof)
    except (TypeError, ValueError):
        return False
    if (day.isoformat() != asof or market.get("date") != asof
        or industry.get("asof") != asof
        or type(catalog.get("listed_total")) is not int
        or not 1 <= catalog["listed_total"] <= 6000
        or industry.get("listed_total") != catalog["listed_total"]
        or industry.get("observed_industry_codes") != catalog["listed_total"]
        or industry.get("missing_industry_codes") != 0
        or industry.get("not_yet_attempted") != 0
        or type(industry.get("records")) is not dict
        or len(industry["records"]) != catalog["listed_total"]):
        return False
    return True


def valid_relative(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and -100 <= value <= MAX_RELATIVE_PCT)


def build(market, catalog, industry):
    """All CURRENT listed issuers form the group denominator, including unpriced."""
    if not source_ready(market, catalog, industry):
        raise IndustryPriceError("industry_market_source_date_or_completion_mismatch")
    try:
        asof, members = _inputs(market, catalog)
        classified = prior_records(industry, asof, members)
    except (ValueError, KeyError, TypeError) as exc:
        raise IndustryPriceError("invalid_saved_market_or_industry_source") from None
    if (len(classified) != len(members) or len(members) != catalog["listed_total"]
        or industry.get("official_krx_sector_membership_verified") is not False
        or industry.get("past_constituent_membership_pit_verified") is not False
        or industry.get("industry_5433_points_authorized") is not False
        or industry.get("investable_v3_score_certified") is not False):
        raise IndustryPriceError("uncertified_or_incomplete_industry_source")

    available = market.get("index_available")
    if (type(available) is not list
        or not all(x in ("KOSPI", "KOSDAQ") for x in available)
        or len(set(available)) != len(available)):
        raise IndustryPriceError("untrusted_market_benchmark_availability")
    priced = market.get("stocks")
    if type(priced) is not list:
        raise IndustryPriceError("invalid_market_price_rows")
    observations = {}
    for row in priced:
        code = row.get("code") if type(row) is dict else None
        if (code not in members or code in observations
            or row.get("market") != members[code]["market"]
            or row.get("price_date") != asof):
            raise IndustryPriceError("undated_duplicate_or_wrong_market_price")
        observations[code] = row

    groups = defaultdict(lambda: {"members": 0, "priced": 0,
                                  "rel20": [], "rel60": [], "positive20": 0})
    for code, issuer in members.items():
        classification = classified[code]
        sector = classification.get("industry_code")
        mkt = issuer["market"]
        if (classification.get("status") != "observed"
            or classification.get("market") != mkt
            or type(sector) is not str or len(sector) < 2
            or not sector[:2].isascii() or not sector[:2].isdigit()):
            raise IndustryPriceError("current_company_industry_identity_invalid")
        g = groups[(mkt, sector[:2])]
        g["members"] += 1
        row = observations.get(code)
        if row is None:
            continue
        g["priced"] += 1
        if mkt not in available or not (
            valid_relative(row.get("relative20")) and
            valid_relative(row.get("relative60"))):
            continue
        a, b = float(row["relative20"]), float(row["relative60"])
        g["rel20"].append(a)
        g["rel60"].append(b)
        g["positive20"] += int(a > 0)

    results = []
    total_members = total_priced = total_observed = 0
    candidate_count = 0
    for (mkt, prefix), g in sorted(groups.items()):
        n, priced_count, observed = (g["members"], g["priced"],
                                     len(g["rel20"]))
        if len(g["rel60"]) != observed or not 0 <= observed <= priced_count <= n:
            raise IndustryPriceError("unreconciled_industry_relative_coverage")
        total_members += n
        total_priced += priced_count
        total_observed += observed
        enough_for_display = observed >= MIN_DISCLOSE
        robust = (observed >= MIN_SIGNAL
                  and observed / n >= MIN_SIGNAL_COVERAGE)
        relative20 = round(median(g["rel20"]), 4) if enough_for_display else None
        relative60 = round(median(g["rel60"]), 4) if enough_for_display else None
        sample_breadth = (round(g["positive20"] / observed, 6)
                          if enough_for_display else None)
        lower = (round(g["positive20"] / n, 6)
                 if enough_for_display else None)
        upper = (round((g["positive20"] + n - observed) / n, 6)
                 if enough_for_display else None)
        if not robust:
            state = "insufficient_sample"
        elif (relative20 > 0 and relative60 <= 0
              and g["positive20"] * 2 > observed and relative20 < 15):
            state = "early_relative_recovery_watch"
            candidate_count += 1
        elif relative20 >= 15:
            state = "relative20_extension_caution"
        else:
            state = "no_early_recovery_pattern_observed"
        results.append({
            "market": mkt, "current_dart_industry_prefix_2": prefix,
            "current_listed_issuers": n,
            "same_day_priced_issuers": priced_count,
            "complete_relative20_60_issuer_count": observed,
            "missing_relative20_60_issuer_count": n - observed,
            "observed_20_60_coverage": round(observed / n, 6),
            "median_market_relative_20pct_sample": relative20,
            "median_market_relative_60pct_sample": relative60,
            "positive_relative20_issuers_sample": (
                g["positive20"] if enough_for_display else None),
            "positive_relative20_breadth_observed_only": sample_breadth,
            "positive_relative20_breadth_all_lower_bound": lower,
            "positive_relative20_breadth_all_upper_bound": upper,
            "early_rotation_research_state": state,
            "current_company_codes_not_historical_krx_sector": True,
            "market_benchmark_dates_independently_verified": False,
            "prices_corporate_action_adjusted_verified": False,
            "official_industry_15_points": None,
        })
    if total_members != len(members) or total_priced != len(observations):
        raise IndustryPriceError("industry_price_group_totals_mismatch")
    return {
        "schema": SCHEMA, "asof": asof,
        "listed_total": len(members),
        "price_observation_total": len(observations),
        "paired_20_60_observations": total_observed,
        "groups_total": len(results),
        "groups_early_recovery_watch": candidate_count,
        "eligible_market_indices": sorted(available),
        "group_minimum_disclosure": MIN_DISCLOSE,
        "group_minimum_signal_issuer_count": MIN_SIGNAL,
        "group_minimum_signal_coverage": MIN_SIGNAL_COVERAGE,
        "relative20_and_relative60_same_day_market_reference": True,
        "independently_verified_benchmark_session_or_adjusted_prices": False,
        "historical_krx_sector_membership_pit_verified": False,
        "official_industry_15_points_authorized": False,
        "investment_grade_100point_scores_authorized": False,
        "new_provider_calls": 0,
        "source_sha256": {
            "market": _stable_digest(market),
            "catalog": _stable_digest(catalog),
            "current_dart_company_industry_codes": _stable_digest(industry),
        },
        "note": "Relative20/60 are saved V2.2 stock-vs-index approximations. "
                "60 sessions include the latest 20. Groups use CURRENT OpenDART "
                "company-code prefix, not historical PIT KRX sectors. "
                "Observed medians and positive breadth cannot be treated as "
                "verified official sector returns; missing-company bounds and "
                "sample thresholds guard false confidence. No 15pt sector scores.",
        "groups": results,
    }


def to_csv(report):
    """Safe aggregate-only table; do not publish constituent price returns."""
    if (report.get("schema") != SCHEMA
        or report.get("official_industry_15_points_authorized") is not False
        or report.get("investment_grade_100point_scores_authorized") is not False
        or report.get("historical_krx_sector_membership_pit_verified") is not False
        or report.get("new_provider_calls") != 0):
        raise IndustryPriceError("research_report_not_safe_for_public_export")
    out = StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([
        "시장", "현재DART업종코드앞2자리", "현재상장기업수",
        "동일시장일_가격보유기업수", "20_60일상대강도모두확보기업수",
        "20_60일상대강도미확보기업수", "상대강도쌍_실측확보율_%",
        "20일_시장대비상대강도_표본중앙값_%", "60일_시장대비상대강도_표본중앙값_%",
        "20일_시장대비강세기업수_표본3이상", "20일_표본내강세기업비율_%",
        "현재분류전체_강세기업비율하한_%", "현재분류전체_강세기업비율상한_%",
        "초기순환매_관찰상태", "시장자료기준일",
        "과거KRX업종구성_PIT", "시장지수날짜별독립검증", "정식산업15점",
    ])
    labels = {
        "insufficient_sample": "표본·확보율 부족",
        "early_relative_recovery_watch": "상대강도 회복 관찰 후보",
        "relative20_extension_caution": "20일 상대강도 과열 주의",
        "no_early_recovery_pattern_observed": "초기 회복 조건 비해당",
    }
    def rate(x):
        return "" if x is None else f"{x * 100:.2f}"
    def num(x):
        return "" if x is None else f"{x:.2f}"
    seen = set()
    for row in report.get("groups", []):
        key = (row.get("market"), row.get("current_dart_industry_prefix_2"))
        if (key in seen or key[0] not in ("KOSPI", "KOSDAQ")
            or type(key[1]) is not str or len(key[1]) != 2
            or not key[1].isdigit()
            or row.get("early_rotation_research_state") not in labels
            or row.get("official_industry_15_points") is not None):
            raise IndustryPriceError("invalid_group_for_public_export")
        seen.add(key)
        writer.writerow([
            *key, row["current_listed_issuers"],
            row["same_day_priced_issuers"],
            row["complete_relative20_60_issuer_count"],
            row["missing_relative20_60_issuer_count"],
            rate(row["observed_20_60_coverage"]),
            num(row["median_market_relative_20pct_sample"]),
            num(row["median_market_relative_60pct_sample"]),
            ("" if row["positive_relative20_issuers_sample"] is None else
             row["positive_relative20_issuers_sample"]),
            rate(row["positive_relative20_breadth_observed_only"]),
            rate(row["positive_relative20_breadth_all_lower_bound"]),
            rate(row["positive_relative20_breadth_all_upper_bound"]),
            labels[row["early_rotation_research_state"]], report["asof"],
            "미확인", "미검증", "",
        ])
    if len(seen) != report.get("groups_total"):
        raise IndustryPriceError("incomplete_industry_group_export")
    return "\ufeff" + out.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="docs")
    parser.add_argument("--mode", choices=("write", "verify", "write-if-ready",
                                          "verify-if-ready"), default="write-if-ready")
    args = parser.parse_args(argv)
    root = Path(args.docs)
    def read(name):
        return json.loads((root / name).read_text(encoding="utf8"))
    market = read("data.json")
    catalog = read("v22-market-catalog.json")
    industry = read("v3-dart-industry-classification.json")
    if not source_ready(market, catalog, industry) and args.mode.endswith("-if-ready"):
        print(json.dumps({"status": "deferred_unaligned_current_company_price_source",
                          "market_asof": catalog.get("date"),
                          "industry_asof": industry.get("asof"),
                          "provider_calls": 0}, sort_keys=True))
        return
    report = build(market, catalog, industry)
    rendered = to_csv(report)
    summary = root / "v3-industry-observed-relative-strength.json"
    sheet = root / "v3-industry-observed-relative-strength.csv"
    if args.mode.startswith("write"):
        write_json(summary, report)
        sheet.write_text(rendered, encoding="utf8")
    else:
        if (not summary.is_file() or not sheet.is_file()
            or read(summary.name) != report
            or sheet.read_text(encoding="utf8") != rendered):
            raise IndustryPriceError("published_relative_strength_source_drift")
    print(json.dumps({"status": "saved_industry_price_relative_research",
                      "asof": report["asof"], "listed": report["listed_total"],
                      "paired": report["paired_20_60_observations"],
                      "groups": report["groups_total"],
                      "early_relative_recovery_watch": report["groups_early_recovery_watch"],
                      "industry_score_issued": 0, "provider_calls": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
