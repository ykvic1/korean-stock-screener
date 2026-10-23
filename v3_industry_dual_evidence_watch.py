"""Cross-check already PUBLIC aggregate DART earnings breadth and V2.2 relative price research.

Only current DART company industry-code/market groups are compared. This
does NOT verify historical KRX sector membership, adjusted prices, market
index history or any part of the approved investment-grade industry 15pts.
No individual issuer, raw KRX records, annual profits or API calls.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from io import StringIO
import json
import math
from pathlib import Path

from v3_dart_bulk_history import write_json

SCHEMA = "v3-current-company-industry-dual-evidence-watch-v1"
EARNINGS_SCHEMA = "v3-industry-saved-annual-earnings-missingness-0.3-breadth-research"
PRICES_SCHEMA = "v3-current-company-industry-relative-strength-observed-v1"
MIN_GROUP = 10
MIN_COVERAGE = 0.75
MIN_PROFIT_LOWER_BOUND = 0.50


class CrossSourceError(ValueError):
    """Fixed error categories; no issuer-specific values in logs."""


def _ratio(x):
    return type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1


def _number(x):
    return type(x) in (int, float) and math.isfinite(x) and -100 <= x <= 1000


def _count(x):
    return type(x) is int and x >= 0


def _group_key(row, field):
    if type(row) is not dict:
        raise CrossSourceError("invalid_group_record")
    market, prefix = row.get("market"), row.get(field)
    if (market not in ("KOSPI", "KOSDAQ") or type(prefix) is not str
            or len(prefix) != 2 or not prefix.isascii() or not prefix.isdigit()):
        raise CrossSourceError("invalid_current_industry_group_key")
    return market, prefix


def build(earnings, prices):
    if type(earnings) is not dict or type(prices) is not dict:
        raise CrossSourceError("invalid_aggregate_sources")
    asof = earnings.get("asof")
    try:
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError()
    except (ValueError, TypeError):
        raise CrossSourceError("invalid_aggregate_asof") from None
    source_e = earnings.get("source_sha256")
    source_p = prices.get("source_sha256")
    if (earnings.get("schema") != EARNINGS_SCHEMA
        or prices.get("schema") != PRICES_SCHEMA
        or prices.get("asof") != asof
        or not _count(earnings.get("listed_total"))
        or earnings["listed_total"] != prices.get("listed_total")
        or earnings.get("current_company_industry_codes_observed")
           != earnings["listed_total"]
        or not isinstance(source_e, dict) or not isinstance(source_p, dict)
        or source_e.get("market_catalog") != source_p.get("catalog")
        or source_e.get("industry_current_company_codes")
           != source_p.get("current_dart_company_industry_codes")
        or not all(type(s) is str and len(s) == 64
                   and all(c in "0123456789abcdef" for c in s)
                   for s in (source_e.get("market_catalog"),
                             source_e.get("industry_current_company_codes")))
        or earnings.get("actual_industry_15_points_authorized") is not False
        or earnings.get("historical_sector_profit_breadth_certified") is not False
        or earnings.get("official_krx_sector_membership_pit_verified") is not False
        or earnings.get("source_calls") != 0
        or prices.get("official_industry_15_points_authorized") is not False
        or prices.get("historical_krx_sector_membership_pit_verified") is not False
        or prices.get("independently_verified_benchmark_session_or_adjusted_prices") is not False
        or prices.get("investment_grade_100point_scores_authorized") is not False
        or prices.get("new_provider_calls") != 0):
        raise CrossSourceError("stale_mismatched_or_promoted_aggregate_source")

    eg, pg = earnings.get("groups"), prices.get("groups")
    if (type(eg) is not list or type(pg) is not list
        or not _count(earnings.get("group_count"))
        or len(eg) != earnings["group_count"]
        or len(pg) != prices.get("groups_total")
        or len(eg) != len(pg)):
        raise CrossSourceError("incomplete_aggregate_group_roster")
    groups_e = {}
    for row in eg:
        key = _group_key(row, "industry_code_prefix_2")
        n, verified, strict, missing = (
            row.get("classified"), row.get("two_year_operating_profit_verified_issuers"),
            row.get("compared"), row.get("missing"))
        if (key in groups_e or not all(_count(z) for z in (n, verified, strict, missing))
            or n < 1 or n != strict + missing or not strict <= verified <= n
            or row.get("source_is_current_dart_company_industry_not_krx_sector") is not True
            or not _ratio(row.get("source_coverage_ratio"))
            or abs(row["source_coverage_ratio"] - strict / n) > .000002
            or row.get("verified_profit_only_gap_count") != verified - strict):
            raise CrossSourceError("inconsistent_earnings_group")
        count = row.get("two_year_operating_profit_improving_issuers")
        lower = row.get("current_code_improving_breadth_lower_bound")
        upper = row.get("current_code_improving_breadth_upper_bound")
        if verified < 3:
            if count is not None or lower is not None or upper is not None:
                raise CrossSourceError("small_earnings_sample_exposed")
        elif (not _count(count) or count > verified
              or not _ratio(lower) or not _ratio(upper)
              or abs(lower - count / n) > .000002
              or abs(upper - (count + n - verified) / n) > .000002):
            raise CrossSourceError("unreconciled_earnings_breadth_bounds")
        groups_e[key] = row

    result = []
    price_pairs = profit_pairs = joint = price_watch = 0
    seen = set()
    for row in pg:
        key = _group_key(row, "current_dart_industry_prefix_2")
        if key in seen or key not in groups_e:
            raise CrossSourceError("unmatched_or_duplicate_price_group")
        seen.add(key)
        e = groups_e[key]
        n, priced, both = (
            row.get("current_listed_issuers"), row.get("same_day_priced_issuers"),
            row.get("complete_relative20_60_issuer_count"))
        if (not all(_count(z) for z in (n, priced, both))
            or n < 1 or n != e["classified"] or not both <= priced <= n
            or row.get("missing_relative20_60_issuer_count") != n - both
            or row.get("current_company_codes_not_historical_krx_sector") is not True
            or row.get("market_benchmark_dates_independently_verified") is not False
            or row.get("prices_corporate_action_adjusted_verified") is not False
            or row.get("official_industry_15_points") is not None
            or not _ratio(row.get("observed_20_60_coverage"))
            or abs(row["observed_20_60_coverage"] - both/n) > .000002):
            raise CrossSourceError("inconsistent_relative_price_group")
        relative20 = row.get("median_market_relative_20pct_sample")
        relative60 = row.get("median_market_relative_60pct_sample")
        up = row.get("positive_relative20_issuers_sample")
        lower_price = row.get("positive_relative20_breadth_all_lower_bound")
        upper_price = row.get("positive_relative20_breadth_all_upper_bound")
        if both < 3:
            if any(v is not None for v in (
                    relative20, relative60, up, lower_price, upper_price)):
                raise CrossSourceError("small_price_sample_exposed")
        elif (not _number(relative20) or not _number(relative60)
              or not _count(up) or up > both
              or not _ratio(lower_price) or not _ratio(upper_price)
              or abs(lower_price - up/n) > .000002
              or abs(upper_price - (up + n - both)/n) > .000002):
            raise CrossSourceError("unreconciled_price_breadth_bounds")
        genuine_price_watch = (both >= MIN_GROUP and both/n >= MIN_COVERAGE
                               and relative20 is not None and relative60 is not None
                               and 0 < relative20 < 15 and relative60 <= 0
                               and up * 2 > both)
        if ((row.get("early_rotation_research_state") == "early_relative_recovery_watch")
            != genuine_price_watch):
            raise CrossSourceError("inconsistent_upstream_early_recovery_state")
        verified = e["two_year_operating_profit_verified_issuers"]
        earnings_ok = (n >= MIN_GROUP and verified/n >= MIN_COVERAGE
                       and e["current_code_improving_breadth_lower_bound"] is not None
                       and e["current_code_improving_breadth_lower_bound"] >= MIN_PROFIT_LOWER_BOUND)
        status = ("dual_evidence_watch" if genuine_price_watch and earnings_ok
                  else "price_only_watch" if genuine_price_watch
                  else "earnings_only_watch" if earnings_ok
                  else "not_dual_evidence_watch")
        joint += int(status == "dual_evidence_watch")
        price_watch += int(genuine_price_watch)
        price_pairs += both
        profit_pairs += verified
        result.append({
            "market": key[0], "current_dart_industry_prefix_2": key[1],
            "current_listed_issuers": n,
            "verified_relative20_60_sample_issuers": both,
            "verified_two_year_operating_profit_issuers": verified,
            "price_pair_coverage": round(both/n, 6),
            "profit_pair_coverage": round(verified/n, 6),
            "market_relative20_median_sample_pct": relative20,
            "market_relative60_median_sample_pct": relative60,
            "profit_improving_breadth_all_lower_bound": e.get(
                "current_code_improving_breadth_lower_bound"),
            "observed_state": status,
            "industry15_points": None,
            "investment_buy_signal": False,
            "historical_sector_PIT_verified": False,
        })
    if (seen != set(groups_e)
        or sum(r["current_listed_issuers"] for r in result) != earnings["listed_total"]
        or profit_pairs != earnings.get("two_year_operating_profit_verified_any_revenue")
        or price_pairs != prices.get("paired_20_60_observations")
        or price_watch != prices.get("groups_early_recovery_watch")):
        raise CrossSourceError("cross_source_aggregate_totals_disagree")
    result.sort(key=lambda x: (
        x["observed_state"] != "dual_evidence_watch", x["market"],
        x["current_dart_industry_prefix_2"]))
    return {
        "schema": SCHEMA, "asof": asof, "listed_total": earnings["listed_total"],
        "current_company_code_market_groups": len(result),
        "price_relative20_60_verified_samples": price_pairs,
        "two_year_profit_verified_samples": profit_pairs,
        "price_recovery_watch_groups": price_watch,
        "dual_evidence_watch_groups": joint,
        "minimum_group_issuer_count": MIN_GROUP,
        "minimum_each_source_coverage": MIN_COVERAGE,
        "minimum_profit_breadth_lower_bound": MIN_PROFIT_LOWER_BOUND,
        "historical_krx_sector_pit_verified": False,
        "official_industry15_points_authorized": False,
        "investable_stock_screener_score_authorized": False,
        "external_provider_calls": 0,
        "source_sha256": {
            "catalog": source_e["market_catalog"],
            "current_dart_company_industry": source_e["industry_current_company_codes"],
        },
        "note": "The group joins observed FY2024-25 DART operating-profit comparisons "
                "and approximate relative20/60 price breadth from the same 2026 current "
                "DART company-code group. No historical KRX sector PIT, adjusted prices "
                "or issuer-level investment-grade industry factor is verified. Candidate "
                "states are observations, NOT industry scores, sector indices or buy calls.",
        "groups": result,
    }


def to_csv(report):
    if (report.get("schema") != SCHEMA
        or report.get("official_industry15_points_authorized") is not False
        or report.get("historical_krx_sector_pit_verified") is not False
        or report.get("investable_stock_screener_score_authorized") is not False
        or report.get("external_provider_calls") != 0):
        raise CrossSourceError("unsafe_public_industry_report")
    buf = StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["시장", "현재DART업종코드앞2자리", "현재상장기업수",
                "20_60일_가격관측기업수", "2개년_영업이익검증기업수",
                "상대강도표본확보율_%", "영업이익표본확보율_%",
                "20일시장대비중앙값_%", "60일시장대비중앙값_%",
                "전체현재구성_영업이익개선율하한_%", "이중실측_연구상태",
                "자료기준일", "과거KRX업종구성_PIT", "산업15점"])
    states = {
        "dual_evidence_watch": "가격·실적 함께 회복 관찰",
        "price_only_watch": "가격 회복만 관찰",
        "earnings_only_watch": "실적 확산만 관찰",
        "not_dual_evidence_watch": "이중조건 비해당 또는 미확보",
    }
    seen = set()
    for row in report.get("groups", []):
        key = _group_key(row, "current_dart_industry_prefix_2")
        if (key in seen or row.get("observed_state") not in states
            or row.get("industry15_points") is not None
            or row.get("investment_buy_signal") is not False
            or row.get("historical_sector_PIT_verified") is not False):
            raise CrossSourceError("unsafe_public_industry_group")
        seen.add(key)
        def fmt(x):
            return "" if x is None else f"{x:.2f}"
        w.writerow([*key, row["current_listed_issuers"],
                    row["verified_relative20_60_sample_issuers"],
                    row["verified_two_year_operating_profit_issuers"],
                    fmt(100*row["price_pair_coverage"]),
                    fmt(100*row["profit_pair_coverage"]),
                    fmt(row["market_relative20_median_sample_pct"]),
                    fmt(row["market_relative60_median_sample_pct"]),
                    ("" if row["profit_improving_breadth_all_lower_bound"] is None
                     else fmt(100*row["profit_improving_breadth_all_lower_bound"])),
                    states[row["observed_state"]], report["asof"], "미확인", ""])
    if (len(seen) != report.get("current_company_code_market_groups")
        or len(seen) != len(report.get("groups", []))):
        raise CrossSourceError("unreconciled_export_groups")
    return "\ufeff" + buf.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="docs")
    parser.add_argument("--mode", choices=("write", "verify"), default="verify")
    args = parser.parse_args(argv)
    root = Path(args.docs)
    earnings = json.loads((root/"v3-industry-saved-profit-gap-summary.json").read_text("utf8"))
    prices = json.loads((root/"v3-industry-observed-relative-strength.json").read_text("utf8"))
    report = build(earnings, prices)
    rendered = to_csv(report)
    dest = root/"v3-industry-dual-evidence-watch.json"
    sheet = root/"v3-industry-dual-evidence-watch.csv"
    if args.mode == "write":
        write_json(dest, report)
        sheet.write_text(rendered, encoding="utf8")
    elif (not dest.exists() or not sheet.exists()
          or json.loads(dest.read_text("utf8")) != report
          or sheet.read_text("utf8") != rendered):
        raise CrossSourceError("saved_cross_source_publication_drift")
    print(json.dumps({"state": "cross_source_research_only",
                      "asof": report["asof"],
                      "groups": report["current_company_code_market_groups"],
                      "dual_evidence_watch": report["dual_evidence_watch_groups"],
                      "industry15_certified": 0, "provider_calls": 0},
                     sort_keys=True))


if __name__ == "__main__":
    main()
