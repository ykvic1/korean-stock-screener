"""Read-only V3.0b release audit for the *five scored area* contract.

Public summaries measure availability, not independently authenticated market
facts. This module never turns a self-reported flag, a full observed weight, a
completed historical extraction window, or a caller's proof into a trade score.
It crosschecks already-published aggregate JSON and issuer CSV; no provider calls or writes.
The older six-pillar v3_release_gate remains unchanged for compatibility.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from decimal import Decimal, InvalidOperation
from io import StringIO
import re
from datetime import date
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MAX_INPUT_BYTES = 2_000_000
MAX_CSV_BYTES = 4_000_000
AREA_WEIGHTS = {
    "volume": 20, "technical": 25, "financial": 20,
    "valuation": 20, "momentum": 15,
}
EXPECTED_SCORE_STATE = "provisional_observed_only_not_investment_grade"


class V30bInputError(ValueError):
    """Only fixed codes are emitted; never include file contents or issuer IDs."""


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _check(ok: bool, issues: list[str], code: str) -> None:
    if not ok:
        issues.append(code)


def _histogram(value: Any, maximum: int, total: int,
               issues: list[str], code: str) -> dict[int, int] | None:
    if not isinstance(value, dict) or not value:
        issues.append(code)
        return None
    bins: dict[int, int] = {}
    for raw_weight, count in value.items():
        if (not isinstance(raw_weight, str) or not raw_weight.isdecimal()
                or str(int(raw_weight)) != raw_weight or not _is_count(count)):
            issues.append(code)
            return None
        weight = int(raw_weight)
        if weight > maximum:
            issues.append(code)
            return None
        bins[weight] = count
    _check(sum(bins.values()) == total, issues, code)
    return bins


def _valid_iso_day(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False



# Independent replay of the already-public issuer CSV against aggregate JSON.
# This is publication coherence, NEVER KRX price, DART scope or PIT proof.
CSV_COLUMNS = (
    "종목코드", "종목명", "시장", "가격자료기준일",
    "거래량_OBV_잠정점수", "거래량_OBV_산출배점",
    "기술_잠정점수", "기술_산출배점",
    "재무_잠정점수", "밸류에이션_잠정점수",
    "모멘텀_잠정점수", "모멘텀_산출배점",
    "관측잠정합계_100점중", "산출가능배점_100점중",
    "미확보배점_100점중", "정식투자점수", "투자등급",
    "재무_산출배점", "가치_산출배점",
    "FCF자산3년_잠정점수", "FCF수익률_잠정점수",
)
CSV_AREA_FIELDS = {
    "volume": ("거래량_OBV_산출배점", "거래량_OBV_잠정점수"),
    "technical": ("기술_산출배점", "기술_잠정점수"),
    "financial": ("재무_산출배점", "재무_잠정점수"),
    "valuation": ("가치_산출배점", "밸류에이션_잠정점수"),
    "momentum": ("모멘텀_산출배점", "모멘텀_잠정점수"),
}


def _csv_integer(raw: Any, maximum: int) -> int:
    if raw == "":
        return 0
    if (not isinstance(raw, str) or not re.fullmatch(r"0|[1-9][0-9]{0,2}", raw)
            or int(raw) > maximum):
        raise ValueError("invalid_weight")
    return int(raw)


def _csv_points(raw: Any, maximum: int) -> Decimal | None:
    if raw == "":
        return None
    if not isinstance(raw, str) or len(raw) > 60:
        raise ValueError("invalid_points")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise ValueError("invalid_points") from None
    if not number.is_finite() or number < 0 or number > maximum:
        raise ValueError("invalid_points")
    return number


def issuer_csv_reconciles(observed: Any, scorecard: Any,
                          csv_text: Any) -> bool:
    """Recompute all 2,497 issuer weights/histograms; return only a boolean.

    Names, tickers and amounts are intentionally never returned to the report.
    A matching CSV cannot independently certify source data or trading scores.
    """
    if (not isinstance(observed, dict) or not isinstance(scorecard, dict)
            or type(csv_text) is not str
            or len(csv_text.encode("utf-8")) > MAX_CSV_BYTES):
        return False
    try:
        reader = csv.DictReader(StringIO(csv_text.lstrip("\ufeff"), newline=""),
                                strict=True)
        if reader.fieldnames != list(CSV_COLUMNS):
            return False
        area_hist = {key: Counter() for key in AREA_WEIGHTS}
        total_hist, market_hist = Counter(), {"KOSPI": Counter(), "KOSDAQ": Counter()}
        complete, partial, missing = {}, {}, {}
        seen = set()
        overlaps = 0
        for row in reader:
            if (None in row or any(v is None for v in row.values())
                    or not re.fullmatch(r"[0-9]{6}", row["종목코드"])):
                return False
            code = row["종목코드"]
            market = row["시장"]
            if (code in seen or market not in market_hist
                    or row["가격자료기준일"] != observed.get("asof")
                    or not row["종목명"] or len(row["종목명"]) > 120
                    or row["정식투자점수"] != ""
                    or row["투자등급"] != "미검증 연구용"):
                return False
            seen.add(code)
            weights = {}
            earned = Decimal(0)
            for area, (weight_key, points_key) in CSV_AREA_FIELDS.items():
                weight = _csv_integer(row[weight_key], AREA_WEIGHTS[area])
                points = _csv_points(row[points_key], weight)
                if (weight == 0) != (points is None):
                    return False
                weights[area] = weight
                area_hist[area][weight] += 1
                if points is not None:
                    earned += points
            available = sum(weights.values())
            if (_csv_integer(row["산출가능배점_100점중"], 100) != available
                    or _csv_integer(row["미확보배점_100점중"], 100)
                    != 100 - available):
                return False
            reported = _csv_points(row["관측잠정합계_100점중"], 100)
            if available == 0:
                if reported is not None:
                    return False
            elif reported is None or abs(reported - earned) > Decimal("0.001"):
                return False
            total_hist[available] += 1
            market_hist[market][available] += 1
            overlaps += all(weight > 0 for weight in weights.values())
            if len(seen) > observed.get("listed_total", 0):
                return False
        if len(seen) != observed.get("listed_total"):
            return False
        for area, bins in area_hist.items():
            maximum = AREA_WEIGHTS[area]
            complete[area] = bins[maximum]
            missing[area] = bins[0]
            partial[area] = sum(count for w, count in bins.items()
                                if 0 < w < maximum)
        return (
            dict(total_hist) == {
                int(k): v for k, v in observed["available_weight_distribution"].items()}
            and {market: dict(bins) for market, bins in market_hist.items()} == {
                market: {int(k): v for k, v in hist.items()}
                for market, hist in observed["available_weight_distribution_by_market"].items()}
            and {area: {str(k): v for k, v in hist.items()}
                 for area, hist in area_hist.items()} ==
                observed["area_available_weight_distribution"]
            and complete == observed["complete_area_issuer_counts"]
            and partial == observed["partial_area_issuer_counts"]
            and missing == observed["unacquired_area_issuer_counts"]
            and {area: complete[area] + partial[area] for area in AREA_WEIGHTS}
                == observed["observed_issuer_counts"]
            and observed["five_area_observed_overlap_issuers"] == overlaps
            and observed["observed_any_factor_issuers"]
                == len(seen) - total_hist[0]
            and observed["provisional_complete_100point_observation_count"]
                == total_hist[100]
            and scorecard["provisional_numeric_issuer_counts"]
                == observed["observed_issuer_counts"]
        )
    except (ValueError, KeyError, TypeError, AttributeError, csv.Error):
        return False



def issuer_catalog_reconciles(observed: Any, catalog: Any, csv_text: Any) -> bool:
    """Independently match every published issuer to the saved market census.

    Histogram equality alone cannot detect a substituted stock. This check
    compares the exact six-digit code, Korean name, market and source date;
    it returns only a boolean and does not certify the KRX feed as official.
    """
    if (not isinstance(observed, dict) or not isinstance(catalog, dict)
            or type(csv_text) is not str
            or len(csv_text.encode("utf-8")) > MAX_CSV_BYTES):
        return False
    try:
        count = observed["listed_total"]
        rows = catalog["rows"]
        if (catalog.get("schema") != "v22-current-listed-catalog-0.1"
                or catalog.get("date") != observed.get("asof")
                or catalog.get("scope")
                != "currently_listed_KOSPI_KOSDAQ_ordinary_shares_only"
                or type(count) is not int or count <= 0
                or catalog.get("listed_total") != count
                or type(rows) is not list or len(rows) != count):
            return False
        census = {}
        for item in rows:
            if type(item) is not dict:
                return False
            code, name, market = (item.get("code"), item.get("name"),
                                  item.get("market"))
            if (type(code) is not str
                    or not re.fullmatch(r"[0-9]{6}", code)
                    or type(name) is not str or not name or len(name) > 120
                    or market not in ("KOSPI", "KOSDAQ") or code in census):
                return False
            census[code] = (name, market)
        reader = csv.DictReader(StringIO(csv_text.lstrip("\ufeff"), newline=""),
                                strict=True)
        if reader.fieldnames != list(CSV_COLUMNS):
            return False
        seen = set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                return False
            code = row["종목코드"]
            if (code in seen or code not in census
                    or (row["종목명"], row["시장"]) != census[code]
                    or row["가격자료기준일"] != observed["asof"]):
                return False
            seen.add(code)
        return len(seen) == len(census)
    except (ValueError, KeyError, TypeError, AttributeError, csv.Error):
        return False


def assess_v30b(observed: Any, scorecard: Any, history: Any,
                 *, proof: Any = None,
                 observed_csv_text: Any = None,
                 catalog: Any = None) -> dict:
    """Measure validated coverage and fail closed on independent certification.

    The optional proof argument is intentionally ignored: unverified assertions
    can NEVER enable an independently verified V3.0b score.
    """
    _ = proof
    o = observed if isinstance(observed, dict) else {}
    s = scorecard if isinstance(scorecard, dict) else {}
    h = history if isinstance(history, dict) else {}
    issues: list[str] = []
    _check(all(isinstance(x, dict) for x in (observed, scorecard, history)),
           issues, "missing_or_invalid_public_summary")
    total = o.get("listed_total")
    _check(_is_count(total) and total > 0 and total == s.get("listed_total"),
           issues, "listed_universe_mismatch")
    safe_total = total if _is_count(total) and total > 0 else 0
    asof = o.get("asof")
    _check(_valid_iso_day(asof) and asof == s.get("asof"),
           issues, "public_snapshot_asof_mismatch")

    # V3.0b approved model: five scored areas, industry informational only.
    _check(o.get("area_budgets") == AREA_WEIGHTS and s.get("weights") == AREA_WEIGHTS
           and sum(AREA_WEIGHTS.values()) == 100
           and s.get("industry_excluded_from_score_budget") is True
           and s.get("sector_label_is_information_only") is True
           and s.get("volume20_replaces_unavailable_investor_IAS20") is True,
           issues, "five_area_model_mismatch")
    _check(s.get("score_state") == EXPECTED_SCORE_STATE
           and s.get("missing_data_never_zero_filled") is True
           and o.get("missing_weight_is_not_zero") is True
           and o.get("unacquired_weight_is_not_a_zero_score") is True
           and o.get("not_an_investment_ranking") is True,
           issues, "observed_only_semantics_mismatch")

    dist = _histogram(o.get("available_weight_distribution"), 100,
                      safe_total, issues, "invalid_total_weight_distribution")
    areas = o.get("area_available_weight_distribution")
    complete = o.get("complete_area_issuer_counts")
    partial = o.get("partial_area_issuer_counts")
    missing = o.get("unacquired_area_issuer_counts")
    observed_counts = o.get("observed_issuer_counts")
    score_counts = s.get("provisional_numeric_issuer_counts")
    _check(all(isinstance(v, dict) and set(v) == set(AREA_WEIGHTS)
               for v in (areas, complete, partial, missing,
                         observed_counts, score_counts)),
           issues, "invalid_area_observation_schema")

    validated_area_counts: dict[str, int] = {}
    if isinstance(areas, dict):
        for area, maximum in AREA_WEIGHTS.items():
            bins = _histogram(areas.get(area), maximum, safe_total, issues,
                              "invalid_area_histogram")
            if bins is None:
                continue
            fully_observed = bins.get(maximum, 0)
            partly_observed = sum(count for weight, count in bins.items()
                                  if 0 < weight < maximum)
            never_observed = bins.get(0, 0)
            if not all(isinstance(x, dict) for x in
                       (complete, partial, missing, observed_counts, score_counts)):
                continue
            _check(fully_observed == complete.get(area)
                   and partly_observed == partial.get(area)
                   and never_observed == missing.get(area)
                   and fully_observed + partly_observed == observed_counts.get(area)
                   and observed_counts.get(area) == score_counts.get(area),
                   issues, "area_count_or_source_mismatch")
            validated_area_counts[area] = fully_observed
    if dist is not None:
        fully_observed = dist.get(100, 0)
        _check(fully_observed == o.get("provisional_complete_100point_observation_count"),
               issues, "observed_100point_overlap_mismatch")
        _check(dist.get(0, 0) == s.get("no_numeric_factor_issuer_count"),
               issues, "unobserved_issuer_count_mismatch")
        _check(safe_total - dist.get(0, 0) == o.get("observed_any_factor_issuers")
               and o.get("observed_any_factor_issuers") ==
               s.get("at_least_one_numeric_factor_count"),
               issues, "any_factor_issuer_count_mismatch")
        market = o.get("available_weight_distribution_by_market")
        if not isinstance(market, dict) or not market:
            issues.append("invalid_market_coverage_distribution")
        else:
            combined: Counter[int] = Counter()
            for subhist in market.values():
                # A market may contain fewer issuers; validate its own bins.
                if not isinstance(subhist, dict):
                    issues.append("invalid_market_coverage_distribution")
                    continue
                market_size = sum(v for v in subhist.values() if _is_count(v))
                part = _histogram(subhist, 100, market_size, issues,
                                  "invalid_market_coverage_distribution")
                if part is not None:
                    combined.update(part)
            _check(dict(combined) == {k: v for k, v in dist.items() if v != 0}
                   or dict(combined) == dist,
                   issues, "market_total_coverage_mismatch")

    csv_reconciled = (issuer_csv_reconciles(o, s, observed_csv_text)
                      if observed_csv_text is not None else None)
    if csv_reconciled is False:
        issues.append("public_issuer_csv_aggregate_mismatch")

    catalog_reconciled = (issuer_catalog_reconciles(o, catalog, observed_csv_text)
                          if catalog is not None and observed_csv_text is not None
                          else None)
    if catalog_reconciled is False:
        issues.append("public_issuer_catalog_membership_mismatch")

    # The old backfill's 490/490 extraction window is *not* adjusted-price or
    # delisted-universe verification, and must never be presented as such.
    cohorts = h.get("five_session_cohorts_completed")
    target = h.get("five_session_cohorts_target")
    bridges = h.get("cross_cohort_bridges_completed")
    required_bridges = h.get("cross_cohort_bridges_required_for_completed_source")
    window_complete = (
        _is_count(cohorts) and _is_count(target) and target > 0
        and cohorts == target and _is_count(bridges)
        and _is_count(required_bridges) and bridges == required_bridges
        and h.get("source_history_coverage_ratio") == 1
    )
    _check(window_complete, issues, "historical_source_window_incomplete")
    _check(o.get("certified_100point_count") == 0
           and s.get("full_six_pillar_certified_count") == 0,
           issues, "unsupported_public_certification_claim")

    snapshot_valid = not issues
    full = dist.get(100, 0) if snapshot_valid and dist is not None else None
    counts = validated_area_counts if snapshot_valid else None
    missing_count = safe_total - full if snapshot_valid and full is not None else None
    gates = {
        "public_summary_internal_consistency": snapshot_valid,
        "published_issuer_csv_coherence": snapshot_valid and csv_reconciled is True,
        "published_issuer_catalog_membership": (
            snapshot_valid and catalog_reconciled is True),
        "five_area_observation_all_listed": snapshot_valid and full == safe_total,
        "fixed_window_krx_extraction": snapshot_valid and window_complete,
        # No existing public aggregate JSON can independently establish these.
        "independent_adjusted_price_and_corporate_actions": False,
        "historical_delisted_pit_universe": False,
        "authenticated_point_in_time_source_chain": False,
        "investment_grade_scoring": False,
    }
    blockers = set(issues)
    if snapshot_valid and missing_count:
        blockers.add("all_listed_observation_incomplete")
    blockers.update({
        "independent_adjusted_price_and_corporate_actions_unverified",
        "historical_delisted_pit_universe_unverified",
        "authenticated_source_chain_unverified",
        "investment_grade_scoring_not_authorized",
    })
    return {
        "schema": "v3-v30b-five-area-readiness-audit-0.1",
        "asof": asof if snapshot_valid else None,
        "public_summary_valid": snapshot_valid,
        "public_issuer_csv_reconciled": csv_reconciled,
        "public_issuer_catalog_reconciled": catalog_reconciled,
        "listed_total": safe_total if snapshot_valid else None,
        "observed_all_five_areas_100weight_count": full,
        "missing_all_five_area_observation_count": missing_count,
        "complete_area_issuer_counts": counts,
        "area_completion_gaps": (
            {k: safe_total - v for k, v in counts.items()} if counts else None),
        "historical_fixed_window_extracted": snapshot_valid and window_complete,
        "historical_window_is_pit_or_adjusted_certified": False,
        "industry_in_100point_score": False,
        "missing_factor_as_zero": False,
        "gates": gates,
        "blocker_codes": sorted(blockers),
        "v30b_release_ready": False,
        "confirmed_scoring_authorized": False,
        "investment_grade_certified_count": 0,
        "certified_score": None,
        "certified_rank": None,
        "external_provider_calls": 0,
        "read_only": True,
    }


def _read_public_json(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise V30bInputError("public_summary_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise V30bInputError("invalid_public_summary")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise V30bInputError("public_summary_unreadable") from None


def _read_public_csv(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_CSV_BYTES:
            raise V30bInputError("public_issuer_csv_too_large")
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise V30bInputError("public_issuer_csv_unreadable") from None


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--observed", type=Path,
                   default=ROOT / "docs/v3-current-observed-all-listed-summary.json")
    p.add_argument("--scorecard", type=Path,
                   default=ROOT / "docs/v3-all-listed-scorecard-summary.json")
    p.add_argument("--history", type=Path,
                   default=ROOT / "docs/v3-krx-derived-backfill-progress.json")
    p.add_argument("--observed-csv", type=Path,
                   default=ROOT / "docs/v3-current-observed-all-listed.csv")
    p.add_argument("--catalog", type=Path,
                   default=ROOT / "docs/v22-market-catalog.json")
    p.add_argument("--assert-blocked", action="store_true",
                   help="also fail if release/scoring unexpectedly becomes authorized")
    a = p.parse_args(argv)
    report = assess_v30b(_read_public_json(a.observed),
                          _read_public_json(a.scorecard),
                          _read_public_json(a.history),
                          observed_csv_text=_read_public_csv(a.observed_csv),
                          catalog=_read_public_json(a.catalog))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if not report["public_summary_valid"]:
        raise SystemExit(2)
    if a.assert_blocked and (report["v30b_release_ready"]
                             or report["confirmed_scoring_authorized"]
                             or report["certified_score"] is not None):
        raise SystemExit(3)
    return report


if __name__ == "__main__":
    main()
