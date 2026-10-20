"""Seven-day rotating V3 research SOURCE COVERAGE and provisional point replay.

Reads only existing, already-published data. No provider calls, fabricated V3
pillar points, old V2.2 score ranking, raw accounts, prices, or buy advice.
At ~2497 ordinary issuers, seven daily cohorts are 356/357 each. A successful
inspection counts as processed even if the issuer has NO usable numeric V3
factor, but missing remains null and explicit in its coverage record.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path
import re

from v3_score_contract import PILLAR_POINTS

SCHEMA = "v3-seven-day-research-source-rotation-0.1"
REPORT_SCHEMA = "v3-seven-day-research-coverage-0.1"
EPOCH = date(2026, 10, 20)  # first intended Tuesday evening local session
GROUPS = 7
CODE = re.compile(r"[0-9]{6}\Z")
TECH_PARTS = {
    "daily_pattern": 7, "weekly_pattern": 8, "daily_rsi": 2,
    "weekly_rsi": 3, "weekly_ma120": 3, "volume_confirmation": 2,
}
PILLARS = ("ias", "technical", "financial", "valuation", "industry", "momentum")
assert sum(TECH_PARTS.values()) == PILLAR_POINTS["technical"] == 25
assert sum(PILLAR_POINTS.values()) == 100


class RotationError(ValueError):
    """Fixed labels; never echo raw third-party data or provider identifiers."""


def day(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise RotationError("invalid_source_day")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise RotationError("invalid_source_day") from None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def codes_from_catalog(catalog):
    if type(catalog) is not dict or catalog.get("schema") != "v22-current-listed-catalog-0.1":
        raise RotationError("invalid_full_market_catalog")
    rows = catalog.get("rows")
    if type(rows) is not list or not 1 <= len(rows) <= 6000:
        raise RotationError("invalid_current_listed_roster")
    if catalog.get("listed_total") != len(rows):
        raise RotationError("incomplete_current_listed_roster")
    mapping = {}
    for row in rows:
        if type(row) is not dict or type(row.get("code")) is not str:
            raise RotationError("invalid_listed_identity")
        code = row["code"]
        if not CODE.fullmatch(code) or code in mapping or row.get("market") not in ("KOSPI", "KOSDAQ"):
            raise RotationError("duplicate_or_unlisted_code")
        mapping[code] = row
    return mapping


def source_tables(market, catalog, book, four=None):
    members = codes_from_catalog(catalog)
    asof = day(market.get("date")).isoformat()
    if (day(catalog.get("date")).isoformat() != asof or
        market.get("status") != "success" or not isinstance(market.get("stocks"), list)):
        raise RotationError("market_catalog_date_or_status_mismatch")
    prices = {}
    for stock in market["stocks"]:
        c = stock.get("code") if type(stock) is dict else None
        if type(c) is not str or c not in members or c in prices:
            raise RotationError("invalid_priced_market_identity")
        prices[c] = stock
    if not prices or len(prices) > len(members):
        raise RotationError("no_valid_priced_market_snapshot")
    for c, row in members.items():
        if ((row.get("price_history_status") == "priced") != (c in prices)):
            raise RotationError("catalog_market_price_disagreement")
    if (type(book) is not dict or
        book.get("schema") != "v3-dart-current-listed-annual-book-reference-0.1" or
        book.get("market_listed_total") != len(members) or
        day(book.get("asof")) > day(asof) or
        book.get("historical_point_in_time_verified") is not False or
        book.get("official_exchange_pbr_verified") is not False):
        raise RotationError("untrusted_single_year_annual_reference")
    annual = {}
    for row in book.get("records", []):
        c = row.get("code") if type(row) is dict else None
        if c not in members or c in annual:
            raise RotationError("duplicate_or_unlisted_dart_source")
        annual[c] = row
    four_source = {}
    if four is not None:
        if (four.get("schema") != "v3-four-year-dart-financial-evidence-0.1" or
            four.get("listed_total") != len(members) or
            day(four.get("asof")) > day(asof) or
            four.get("full_v3_ready") is not False or
            four.get("four_years_all_financial_pillars_certified") is not False):
            raise RotationError("untrusted_four_year_reference")
        for row in four.get("records", []):
            c = row.get("code") if type(row) is dict else None
            if c not in members or c in four_source:
                raise RotationError("duplicate_or_unlisted_four_year_source")
            four_source[c] = row
    return asof, members, prices, annual, four_source


def existing_evidence_adapter(row, market_asof):
    """Rebuild research adapter only from existing dated inputs, with no API.

    Old published market files predate the v3-pattern adapter even though
    individual daily/weekly research observations and volume inputs exist.
    Never bypass an existing but malformed adapter, and never infer a score
    from legacy V2.2 points.
    """
    if not isinstance(row, dict) or row.get("price_date") != market_asof:
        return None, False
    z = row.get("v3_pattern_integrated")
    if z is not None:
        return z, False
    if not (isinstance(row.get("pattern_research"), dict) or
            isinstance(row.get("pattern_weekly_research"), dict)):
        return None, False
    try:
        from v3_pattern_composite_score import evaluate
        reconstructed = evaluate(row)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, False
    if reconstructed.get("asof") != market_asof:
        return None, False
    return reconstructed, True


def technical_points(row, market_asof):
    if row is None:
        return None, 0, "price_not_observed"
    z, _ = existing_evidence_adapter(row, market_asof)
    if not isinstance(z, dict) or z.get("schema") != "v3-pattern-within-six-pillars-research-0.4":
        return None, 0, "no_current_v3_technical_adapter"
    if (row.get("price_date") != market_asof or z.get("asof") != market_asof or
        z.get("technical_maximum") != 25 or z.get("buy_certified") is not False or
        z.get("v3_release_ready") is not False or z.get("investable_score") is not None):
        return None, 0, "technical_source_date_or_release_contract_invalid"
    parts = z.get("technical_parts")
    if not isinstance(parts, dict):
        full = z.get("technical_research_candidate")
        if number(full) and 0 <= full <= 25:
            return round(full, 4), 25, "research_unverified"
        return None, 0, "technical_subparts_not_available"
    weights = z.get("technical_weights")
    if weights is not None and any(weights.get(k) != v for k, v in TECH_PARTS.items()):
        return None, 0, "technical_weight_contract_mismatch"
    points, available = 0.0, 0
    for key, maximum in TECH_PARTS.items():
        if key not in parts:
            return None, 0, "technical_subparts_incomplete"
        val = parts[key]
        if val is None:
            continue
        if not number(val) or not 0 <= val <= maximum:
            return None, 0, "invalid_technical_part"
        points += val
        available += maximum
    if z.get("technical_observed_maximum", available) != available:
        return None, 0, "technical_available_weight_mismatch"
    full = z.get("technical_research_candidate")
    if full is not None and (
        not number(full) or available != 25 or abs(full - points) > .01):
        return None, 0, "technical_total_inconsistent"
    return (round(points, 4), available, "research_unverified") if available else (
        None, 0, "technical_factors_unavailable")


def inspect_issuer(code, *, market_asof, catalog_row, price_row, annual_row, four_row, inspected_on):
    tech, weight, note = technical_points(price_row, market_asof)
    research, backfilled = existing_evidence_adapter(price_row, market_asof)
    # Publish only small verified-contract research subparts, NOT original OHLCV
    # or original DART statements. A new market date invalidates this cache.
    reconstructed = ({
        "schema": research["schema"], "asof": research["asof"],
        "technical_maximum": research["technical_maximum"],
        "technical_weights": research["technical_weights"],
        "technical_parts": research["technical_parts"],
        "technical_observed_maximum": research["technical_observed_maximum"],
        "technical_research_candidate": research["technical_research_candidate"],
        "buy_certified": False, "v3_release_ready": False,
        "investable_score": None,
    } if backfilled and weight > 0 and isinstance(research, dict) else None)
    annual = annual_row.get("annual") if isinstance(annual_row, dict) else None
    receipt_valid = (isinstance(annual, dict) and
                     isinstance(annual.get("receipt_id"), str) and
                     re.fullmatch(r"[0-9]{14}", annual["receipt_id"]) is not None and
                     annual["receipt_id"][:8] <= market_asof.replace("-", "") and
                     annual.get("fs_div") in ("CFS", "OFS"))
    core = bool(isinstance(four_row, dict) and
                four_row.get("current_core_financial_research_observed") is True and
                four_row.get("four_year_financial_pit_authenticated") is False)
    relative = (isinstance(price_row, dict) and
                number(price_row.get("price20")) and number(price_row.get("price60")))
    sector = isinstance(catalog_row.get("sector"), str) and bool(catalog_row["sector"].strip())
    flow_ref = bool(isinstance(price_row, dict) and price_row.get("flows_confirmed") is True)
    # These flags are source READINESS, not V3 weighted research scores.
    reference = {
        "ias": "legacy_flow_reference_only" if flow_ref else "verified_ias_not_available",
        "technical": note,
        "financial": "four_year_core_reference_not_PIT" if core else (
            "annual_statement_reference_only" if receipt_valid else "four_year_financial_missing"),
        "valuation": "simple_book_reference_only" if receipt_valid else "official_PBR_missing",
        "industry": "sector_label_only" if sector else "sector_cohort_not_available",
        "momentum": "raw_20_60_relative_reference_only" if relative else "benchmark_momentum_missing",
    }
    return {
        "code": code, "last_recalculated_kst": inspected_on,
        "market_source_asof": market_asof,
        "research_score_points": tech, "available_weight": weight,
        "technical_rebuilt_from_saved_source": reconstructed is not None,
        "technical_research_backfill": reconstructed,
        "six_pillar_research_score_confirmed": False,
        "source_reference_status": reference,
        "source_observed": {
            "technical_parts": weight > 0,
            "annual_receipt": receipt_valid,
            "current_four_year_core": core,
            "simple_pb_unverified": receipt_valid,
            "sector_label": sector,
            "price_20_60_relative_pair": relative,
            "legacy_flow_not_ias": flow_ref,
        },
        "no_data_construed_as_zero": True,
    }


def cycle_for(when):
    offset = (when - EPOCH).days
    if offset < 0:
        raise RotationError("rotation_not_started")
    start = EPOCH + timedelta(days=(offset // GROUPS) * GROUPS)
    return start.isoformat(), offset % GROUPS


def recalculate(market, catalog, book, *, when, prior=None, four=None):
    when = day(when)
    if when < EPOCH:
        raise RotationError("rotation_not_started")
    asof, members, prices, annual, four_source = source_tables(market, catalog, book, four)
    age = (when - day(asof)).days
    if age < 0 or age > 9:
        raise RotationError("future_or_stale_market_for_recalculation")
    cycle, position = cycle_for(when)
    roster = sorted(members)
    prior_records = {}
    if prior is not None:
        if (type(prior) is not dict or prior.get("schema") != SCHEMA or
            not isinstance(prior.get("records"), dict) or
            day(prior.get("run_kst")) > when or
            day(prior.get("market_asof")) > day(asof)):
            raise RotationError("untrusted_previous_rotation")
        prior_records = prior["records"]
    # Preserve each issuer's prior research date; prior-cycle results are NOT
    # counted as updated this week. A changed listing is safely reconciled.
    records = {c: prior_records[c] for c in roster if c in prior_records}
    # New universe members that would have been assigned an already-passed
    # cohort are caught up instead of silently omitted until the next cycle.
    targets = [c for i, c in enumerate(roster)
               if i % GROUPS <= position and records.get(c, {}).get("cycle") != cycle]
    for c in targets:
        record = inspect_issuer(c, market_asof=asof,
                               catalog_row=members[c], price_row=prices.get(c),
                               annual_row=annual.get(c), four_row=four_source.get(c),
                               inspected_on=when.isoformat())
        record["cycle"] = cycle
        records[c] = record
    current = [r for r in records.values() if r.get("cycle") == cycle]
    updated = len(current)
    if updated > len(roster):
        raise RotationError("weekly_processed_exceeds_roster")
    breakdown = {key: sum(bool(row["source_observed"].get(key)) for row in current)
                 for key in ("technical_parts", "annual_receipt", "current_four_year_core",
                             "simple_pb_unverified", "sector_label",
                             "price_20_60_relative_pair", "legacy_flow_not_ias")}
    points = sum(r.get("research_score_points") is not None for r in current)
    # Aggregate only fixed diagnostic codes, never raw market rows/API data.
    # This distinguishes no-data from an accidentally strict source-date
    # gate, so a legitimate research signal is not silently discarded.
    technical_blockers = {}
    market_asof_mismatch = 0
    valid_adapter_same_stock_date = 0
    for rec in current:
        reason = rec["source_reference_status"]["technical"]
        technical_blockers[reason] = technical_blockers.get(reason, 0) + 1
        price = prices.get(rec["code"])
        if price is not None and price.get("price_date") != asof:
            market_asof_mismatch += 1
        z, _ = existing_evidence_adapter(price, asof)
        if isinstance(z, dict) and price and z.get("asof") == price.get("price_date"):
            valid_adapter_same_stock_date += 1
    backfilled_count = sum(r.get("technical_rebuilt_from_saved_source") is True
                           for r in current)
    groups = [sum(i % GROUPS == k for i in range(len(roster))) for k in range(GROUPS)]
    result = {
        "schema": SCHEMA, "cycle_start": cycle, "run_kst": when.isoformat(),
        "market_asof": asof, "listed_total": len(roster), "cohort_counts": groups,
        "cohort_index_today": position, "processed_today": len(targets),
        "processed_this_cycle": updated, "unprocessed_this_cycle": len(roster) - updated,
        "source_reference_counts_this_cycle": breakdown,
        "numeric_technical_candidates_this_cycle": points,
        "technical_unverified_reason_counts": technical_blockers,
        "individual_price_date_vs_market_mismatch_count": market_asof_mismatch,
        "technical_adapter_same_stock_date_count": valid_adapter_same_stock_date,
        "technical_backfilled_candidate_count_this_cycle": backfilled_count,
        "records": records, "source_collection_calls": 0,
        "legacy_v22_score_used": False, "new_provider_calls_made": False,
        "v3_live_full_six_pillar_score_claimed": False,
    }
    report = {
        "schema": REPORT_SCHEMA, "cycle_start": cycle, "run_kst": when.isoformat(),
        "market_asof": asof, "listed_total": len(roster), "cohort_counts": groups,
        "cohort_index_today": position, "processed_today": len(targets),
        "processed_this_cycle": updated, "unprocessed_this_cycle": len(roster)-updated,
        "source_reference_counts_this_cycle": breakdown,
        "numeric_technical_candidates_this_cycle": points,
        "technical_unverified_reason_counts": technical_blockers,
        "individual_price_date_vs_market_mismatch_count": market_asof_mismatch,
        "technical_adapter_same_stock_date_count": valid_adapter_same_stock_date,
        "technical_backfilled_candidate_count_this_cycle": backfilled_count,
        "verified_full_six_pillar_issuer_count": 0,
        "all_issuers_updated_this_cycle": updated == len(roster),
        "calendar_age_days": age,
        "price_market_reused_as_input_no_new_market_provider_calls": True,
        "source_collection_calls": 0,
        "missing_pillars_not_filled_with_zero": True,
        "limitations": [
            "Daily rotation replays existing published V3 research, not new KRX or DART requests",
            "Four-year financial, verified institution flows and PIT industry/momentum need new lawful sources",
            "Reference flags are not investment scores and cannot be added to the 100-point sum",
        ],
    }
    return result, report


def read_json(path, required=True):
    p = Path(path)
    if not p.is_file():
        if required:
            raise RotationError("missing_required_snapshot")
        return None
    if p.stat().st_size > 80_000_000:
        raise RotationError("oversized_source_snapshot")
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                              sort_keys=True, allow_nan=False) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--market", default="docs/data.json")
    a.add_argument("--catalog", default="docs/v22-market-catalog.json")
    a.add_argument("--annual", default="docs/v3-dart-book-reference.json")
    a.add_argument("--four", default="docs/v3-dart-four-year-reference.json")
    a.add_argument("--state", default="docs/v3-weekly-rotation.json")
    a.add_argument("--report", default="docs/v3-weekly-coverage.json")
    a.add_argument("--when", required=True, help="Current Asia/Seoul calendar day")
    a.add_argument("--mode", choices=("audit", "publish", "verify"), default="audit")
    args = a.parse_args(argv)
    market = read_json(args.market)
    catalog = read_json(args.catalog)
    book = read_json(args.annual)
    four = read_json(args.four, required=False)
    prior = read_json(args.state, required=False)
    if args.mode == "verify":
        if prior is None or prior.get("market_asof") != market.get("date"):
            raise RotationError("post_rebase_market_date_changed")
        if prior.get("run_kst") != args.when or prior.get("schema") != SCHEMA:
            raise RotationError("post_rebase_rotation_run_changed")
        source_tables(market, catalog, book, four)
        report = read_json(args.report)
        if (report.get("schema") != REPORT_SCHEMA or
            report.get("processed_this_cycle") != prior.get("processed_this_cycle")):
            raise RotationError("post_rebase_coverage_mismatch")
        print("V3 rotation source day and published count preserved after rebase")
        return
    state, coverage = recalculate(market, catalog, book, when=args.when,
                                  prior=prior, four=four)
    if args.mode == "publish":
        write_json(args.state, state)
        write_json(args.report, coverage)
    else:
        write_json("reports/v3-weekly-rotation-audit.json", coverage)
    print(json.dumps({k: v for k, v in coverage.items()
                      if k not in ("limitations",)}, ensure_ascii=False,
                     sort_keys=True))


if __name__ == "__main__":
    main()
