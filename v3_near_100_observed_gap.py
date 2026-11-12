"""Find the nearest *actually observed* 100-weight research candidates.

This is a read-only evidence backlog planner, never a score publisher. It
compares the source-replayed current close CSV with independently published
DART annual and CF coverage. Missing account != zero, and a cached prior
factor never becomes a new same-day observation.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re

SCHEMA = "v3-observed-near100-source-gap-priority-0.1"
VOLUME_CONTRACT = "v3-volume-ma20-60-intersection-vs-ma240-6pt-0.1"
AREAS = {
    "volume": ("거래량_OBV_산출배점", 20),
    "technical": ("기술_산출배점", 25),
    "financial": ("재무_산출배점", 20),
    "valuation": ("가치_산출배점", 20),
    "momentum": ("모멘텀_산출배점", 15),
}
FCF_MISSING_ACCOUNT = {"B", "C", "I", "P", "X"}
EVIDENCE_FLAGS = FCF_MISSING_ACCOUNT | {"V", "U"}
CODE = re.compile(r"[0-9A-Z]{6}\Z")


class GapAuditError(ValueError):
    """Do not reveal arbitrary rows or external API responses."""


def _day(value):
    try:
        d = date.fromisoformat(value)
        if d.isoformat() == value:
            return value
    except (ValueError, TypeError):
        pass
    raise GapAuditError("untrusted_source_date")


def _weight(raw, maximum):
    if raw == "":
        return 0
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise GapAuditError("invalid_observed_weight")
    n = int(raw)
    if not 0 < n <= maximum:
        raise GapAuditError("invalid_observed_weight")
    return n


def _read_rows(text):
    if not isinstance(text, str) or len(text) > 6_000_000:
        raise GapAuditError("missing_or_oversized_observed_csv")
    reader = csv.DictReader(text.lstrip("\ufeff").splitlines())
    needed = {"종목코드", "시장", "가격자료기준일",
              "산출가능배점_100점중", "미확보배점_100점중",
              "FCF자산3년_잠정점수", "FCF수익률_잠정점수",
              "정식투자점수"}
    needed.update(field for field, _ in AREAS.values())
    if reader.fieldnames is None or not needed <= set(reader.fieldnames):
        raise GapAuditError("missing_observed_csv_contract")
    return list(reader)


def _factor_gaps(row):
    available = {}
    for area, (column, maximum) in AREAS.items():
        available[area] = _weight(row.get(column), maximum)
    return available


def generate(csv_text, summary, financial, annual, cf_evidence):
    """Audit real per-issuer source gaps; return only explainable priorities.

    No assumption that re-querying missing IFRS CAPEX will fill the source.
    A 95-point name's absent ownership profit becomes a candidate for an
    EXTRA, exact-annual-receipt official source probe, not 5 invented points.
    """
    if any(type(item) is not dict for item in
           (summary, financial, annual, cf_evidence)):
        raise GapAuditError("untrusted_coverage_source")
    day = _day(summary.get("asof"))
    if (any(doc.get("asof") != day for doc in
            (financial, annual, cf_evidence))
        or summary.get("schema") !=
           "v3-current-day-all-listed-observed-plus-dart-fcf-0.3"
        or summary.get("certified_100point_count") != 0
        or summary.get("not_an_investment_ranking") is not True
        or financial.get("historical_PIT_certified") is not False
        or financial.get("all_scores_provisional_unverified") is not True
        or annual.get("historical_point_in_time_verified") is not False
        or cf_evidence.get("historical_pit_verified") is not False
        or cf_evidence.get("trading_score_promoted") is not False):
        raise GapAuditError("untrusted_source_claim_or_asof")
    rows = _read_rows(csv_text)
    total = summary.get("listed_total")
    if type(total) is not int or total != len(rows) or not 1 <= total <= 6000:
        raise GapAuditError("incomplete_observed_market_census")
    p_rows = financial.get("rows")
    b_rows = annual.get("records")
    e_rows = cf_evidence.get("records")
    if not isinstance(p_rows, list) or not isinstance(b_rows, list) or not isinstance(e_rows, dict):
        raise GapAuditError("missing_dart_issuer_components")
    if len(p_rows) > 6000 or len(b_rows) > 6000 or len(e_rows) > 6000:
        raise GapAuditError("oversized_dart_issuer_components")
    p = {r.get("code"): r for r in p_rows}
    b = {r.get("code"): r for r in b_rows}
    if len(p) != len(p_rows) or len(b) != len(b_rows):
        raise GapAuditError("duplicate_fiscal_issuer")
    distribution = Counter()
    by_market = {"KOSPI": Counter(), "KOSDAQ": Counter()}
    gaps = {str(n): Counter() for n in (4, 5, 9, 12)}
    owner_profit_targets = []
    fcf_missing_account_targets = []
    fcf_not_yet_collected = []
    financial_other_targets = []
    seen = set()
    for row in rows:
        code = row["종목코드"]
        market = row["시장"]
        if (type(code) is not str or not CODE.fullmatch(code)
            or code in seen or market not in by_market
            or row.get("가격자료기준일") != day
            or row.get("정식투자점수") != ""
            or row.get("투자등급") != "미검증 연구용"):
            raise GapAuditError("invalid_or_duplicate_observed_issuer")
        seen.add(code)
        weights = _factor_gaps(row)
        amount = sum(weights.values())
        if (row.get("산출가능배점_100점중") != (str(amount) if amount else "")
            or row.get("미확보배점_100점중") != str(100 - amount)):
            raise GapAuditError("csv_observed_weights_disagree")
        distribution[amount] += 1
        by_market[market][amount] += 1
        gap = 100 - amount
        if gap not in (4, 5, 9, 12):
            continue
        incomplete = {k: maximum - weights[k]
                      for k, (_, maximum) in AREAS.items()
                      if weights[k] < maximum}
        shape = "+".join(f"{k}:{n}" for k, n in incomplete.items())
        gaps[str(gap)][shape] += 1
        fiscal = p.get(code) or {}
        components = fiscal.get("unverified_components") or {}
        if code in p and (fiscal.get("market") != market
                          or fiscal.get("asof") != day
                          or fiscal.get("scoring_certified") is not False):
            raise GapAuditError("fiscal_identity_or_claim_mismatch")
        # Exactly 95/100: valuation's only missing 5 points are the annual
        # owners' profit / earnings-yield subfactor. Confirm both source
        # tables really have NULL before issuing any re-query suggestion.
        if incomplete == {"valuation": 5} and row["FCF수익률_잠정점수"] != "":
            profit = components.get("earnings_yield_pct")
            book = b.get(code) or {}
            annual_row = book.get("annual") or {}
            values = annual_row.get("values") or {}
            if (type(profit) is dict
                and profit.get("candidate_points") is None
                and profit.get("max_points") == 0
                and book.get("status") == "observed"
                and annual_row.get("year") == 2025
                and annual_row.get("fs_div") in ("CFS", "OFS")
                and annual_row.get("currency") == "KRW"
                and values.get("owners_profit") is None):
                owner_profit_targets.append(code)
                gaps["5"]["verified_annual_profit_absent"] += 1
        # 96/100: when financial FCF 4 is missing, classify already
        # attempted-but-absent IFRS account separately from unfetched years.
        if incomplete == {"financial": 4}:
            if row["FCF자산3년_잠정점수"] == "":
                source = e_rows.get(code)
                if (type(source) is list and len(source) == 4
                    and source[0] in ("CFS", "OFS")
                    and all(z in EVIDENCE_FLAGS for z in source[1:])):
                    if any(z in FCF_MISSING_ACCOUNT for z in source[1:]):
                        fcf_missing_account_targets.append(code)
                        gaps["4"]["previously_queried_missing_ifrs_accounts"] += 1
                    elif "U" in source[1:]:
                        fcf_not_yet_collected.append(code)
                        gaps["4"]["unqueried_cashflow_years"] += 1
                    else:
                        gaps["4"]["other_fcf_source_or_asset_constraint"] += 1
                else:
                    gaps["4"]["unavailable_fcf_source_evidence"] += 1
            else:
                missing = [
                    name for name, item in components.items()
                    if name in ("revenue_cagr_3y_pct",
                                "operating_margin_3y_pct",
                                "liabilities_to_assets_pct",
                                "operating_roa_3y_pct")
                    and type(item) is dict
                    and item.get("candidate_points") is None
                ]
                financial_other_targets.append(code)
                gaps["4"]["other_financial_subfactor_missing"] += 1
    if distribution[100] != summary.get("provisional_complete_100point_observation_count"):
        raise GapAuditError("reported_100point_count_not_reproduced")
    recorded = {str(k):v for k,v in sorted(distribution.items())}
    if recorded != summary.get("available_weight_distribution"):
        raise GapAuditError("reported_weight_distribution_not_reproduced")
    # Never promote missing source cells or an old stale last-observed factor.
    return {
        "schema": SCHEMA, "asof": day, "listed_total": total,
        "volume_subfactor_contract":VOLUME_CONTRACT,
        "provisional_observed_100_weight_issuers": distribution[100],
        "provisional_100_weight_pct": round(100 * distribution[100] / total, 2),
        "complete_by_market": {
            k: by_market[k][100] for k in by_market},
        "near_complete_issuer_counts": {
            str(n): distribution[100 - n] for n in (4, 5, 9, 12)},
        "near_complete_source_gap_shapes": {
            k: dict(sorted(value.items())) for k, value in gaps.items()},
        "official_2025_owners_profit_missing_priority_codes":
            sorted(owner_profit_targets),
        "official_2025_owners_profit_missing_priority_count":
            len(owner_profit_targets),
        "fcf_three_year_previously_queried_missing_account_codes":
            sorted(fcf_missing_account_targets),
        "fcf_three_year_previously_queried_missing_account_count":
            len(fcf_missing_account_targets),
        "fcf_three_year_unfetched_priority_codes":sorted(fcf_not_yet_collected),
        "fcf_three_year_unfetched_priority_count":len(fcf_not_yet_collected),
        "other_financial_four_weight_gap_count":len(financial_other_targets),
        "missing_capex_is_not_zero":True,
        "no_points_added":True,
        "official_trade_score_certified":False,
        "issuer_last_observed_scores_unchanged":True,
        "action_note": (
            "Official FY2025 owners-profit missing: independently verify same "
            "issuer/year/scope/receipt before considering earnings-yield 5; "
            "previously queried missing IFRS CAPEX must not be guessed or "
            "blindly re-requested. Historical dated scores are separate "
            "from same-close 100-weight evidence counts."
        ),
    }


def retain_original_priority_cohort(original, observed, supplement):
    """Freeze an immutable SAME-DATE official-source acquisition queue.

    The user-facing 100-weight count belongs to the freshly replayed
    SCORECARD. This separate document is only a bounded missing-earnings
    collection plan: once actual owners-profit arrives, rewriting its
    issuer queue would invalidate the source receipt's selection anchor.
    A new market close always creates a new priority plan instead.
    """
    if type(supplement) is not dict or not supplement:
        return observed, False
    if (supplement.get("market_asof")!=observed.get("asof")
        or supplement.get("schema")!="v3-official-fy2025-owner-profit-source-0.1"
        or supplement.get("investment_grade_score_certified") is not False
        or supplement.get("historical_pit_verified") is not False
        or supplement.get("account_value_used_in_scores") is not False
        or type(supplement.get("records")) is not dict
        or not supplement["records"]):
        return observed, False
    if type(original) is not dict or original.get("asof")!=observed.get("asof"):
        return observed, False
    # A new same-day market census may expand 2,501 -> 2,590 after an
    # official FY2025 receipt-anchored source has already been collected.
    # Preserve that original immutable acquisition queue, even when the
    # new 2,590-stock SCORECARD is recalculated independently below.
    # Only a new close date or no existing official supplement may create
    # a fresh acquisition queue; never relabel the old receipt selection.
    if original.get("listed_total") != observed.get("listed_total"):
        if not (type(original.get("listed_total")) is int
            and type(observed.get("listed_total")) is int
            and 0 < original["listed_total"] < observed["listed_total"] <= 6000):
            raise GapAuditError("untrusted_frozen_profit_priority_cohort")
    names=original.get("official_2025_owners_profit_missing_priority_codes")
    if (original.get("schema")!=SCHEMA
        or original.get("no_points_added") is not True
        or original.get("official_trade_score_certified") is not False
        or original.get("missing_capex_is_not_zero") is not True
        or type(names) is not list or names!=sorted(set(names))
        or len(names)!=original.get(
            "official_2025_owners_profit_missing_priority_count")
        or not set(supplement["records"]).issubset(names)
        or type(original.get("provisional_observed_100_weight_issuers")) is not int
        or (observed["provisional_observed_100_weight_issuers"] <
            original["provisional_observed_100_weight_issuers"]
            # First transition from the old OBV-rise six-point source may
            # reduce observed coverage on the same day. Freeze the DART
            # acquisition queue, not the obsolete full-coverage score count.
            and not (original.get("volume_subfactor_contract") is None
                     and observed.get("volume_subfactor_contract")==VOLUME_CONTRACT))):
        raise GapAuditError("untrusted_frozen_profit_priority_cohort")
    return original, True


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs", default="docs")
    p.add_argument("--mode", choices=("write", "verify"), default="verify")
    a = p.parse_args(argv)
    docs = Path(a.docs)
    def read(name):
        return json.loads((docs / name).read_text(encoding="utf8"))
    csv_text = (docs / "v3-current-observed-all-listed.csv").read_text(
        encoding="utf8")
    summary = read("v3-current-observed-all-listed-summary.json")
    financial = read("v3-dart-bulk-provisional.json")
    annual = read("v3-dart-book-reference.json")
    cf_evidence = read("v3-dart-cashflow-issuer-evidence.json")
    # Cashflow and annual sources update asynchronously after each closing
    # price rollover. A known older source day does NOT block fresh prices,
    # and an old gap report always retains its own original source date.
    if any(x.get("asof") != summary.get("asof") for x in (
            financial, annual, cf_evidence)):
        print(json.dumps({"status":"waiting_same_day_source_priority_audit",
                          "asof":summary.get("asof"),
                          "provider_api_calls":0,
                          "issuer_scores_changed":False},sort_keys=True))
        return
    observed = generate(csv_text, summary, financial, annual, cf_evidence)
    dest = docs / "v3-near-100-evidence-priority.json"
    prior = read(dest.name) if dest.is_file() else None
    source_path = docs / "v3-dart-owner-profit-supplement.json"
    supplement = read(source_path.name) if source_path.is_file() else None
    report, frozen = retain_original_priority_cohort(
        prior, observed, supplement)
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n"
    if a.mode == "write":
        dest.write_text(canonical, encoding="utf8")
    elif not dest.is_file() or dest.read_text(encoding="utf8") != canonical:
        raise GapAuditError("near_100_priority_differs_from_saved_sources")
    print(json.dumps({
        "asof":report["asof"],"100weight":report["provisional_observed_100_weight_issuers"],
        "near_complete":report["near_complete_issuer_counts"],
        "official_missing_profit_targets":
            report["official_2025_owners_profit_missing_priority_count"],
        "fcf_missing_ifrs_account_not_zero":
            report["fcf_three_year_previously_queried_missing_account_count"],
        "no_points_added":True,
        "cohort_frozen_during_same_date_official_source_recovery":frozen,
        "current_observed_100weight":
            observed["provisional_observed_100_weight_issuers"],
        "priority_baseline_100weight":
            report["provisional_observed_100_weight_issuers"],
        },sort_keys=True))


if __name__ == "__main__":
    main()
