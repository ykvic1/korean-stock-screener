"""Replay three existing financial/valuation factors on the fixed historical universe."""

import argparse
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, time
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
import sys

from v3_dart_bulk_history import SCHEMA as HISTORY_SCHEMA, _valid_receipt, research_parts
from v3_forward_horizon_readiness import _anniversary
from v3_historical_annual_receipt_recovery import PIN_DAY, PINNED_HASHES
from v3_historical_performance_pilot_audit import audit as audit_delisting_pilot
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS
from v3_historical_market_coverage import (
    ENTRY, HORIZONS, MARKETS, ROOT, SIGNAL, TAG, day, ledger_rows, read_panels, require, summarize, traded,
)
from v3_historical_market_share_actions import load_replay
from v3_operational_research_score import SPECS, VERSION, score_issuer

SCHEMA = "v3-historical-financial-factor-replay-v1"
FISCAL = (2021, 2022, 2023, 2024)
SCOPES = ("CFS", "OFS")
FIELDS = ("assets", "liabilities", "equity", "revenue", "operating_income")
PARTS = {
    "operating_growth_margin": ("financial", "operating_margin_3y_pct"),
    "balance_sheet_strength": ("financial", "liabilities_to_assets_pct"),
    "sales_to_cap": ("valuation", "sales_to_market_cap"),
}


def prior_receipt(receipt, year):
    # Receipt metadata has a date but no intraday availability timestamp.
    return _valid_receipt(receipt, year, SIGNAL) and receipt[:8] < SIGNAL.strftime("%Y%m%d")


def content(path):
    raw = path.read_bytes()
    return json.loads(raw), sha256(raw).hexdigest()


def operating_catalog(prices, signal_panel, expected):
    import pyarrow.parquet as pq

    with redirect_stdout(sys.stderr):
        from screener import market_snapshot

    market = pq.read_table(prices, columns=["Code", "Name", "Market", "Close", "Volume", "Amount", "Marcap"],
                           filters=[("Date", "=", datetime.combine(SIGNAL, time.min)),
                                    ("Market", "in", list(MARKETS))])
    selected = market_snapshot(market.to_pandas())
    require(len(selected) == expected
            and all(signal_panel[code][:2] == (row["market"], row["name"])
                    for code, row in selected.items()), "historical_financial_scope_mismatch")
    raw_caps = dict(zip(market.column("Code").to_pylist(), market.column("Marcap").to_pylist(), strict=True))
    rows = []
    for code, row in sorted(selected.items()):
        quote = signal_panel[code]
        shares, close, cap = quote[2], quote[6], raw_caps[code]
        verified_cap = (row["market_cap_eok"] if shares and close and cap == shares * close else None)
        rows.append({"code": code, "name": row["name"], "market": row["market"],
                     "reference_market_cap_eok": verified_cap})
    return {"date": SIGNAL.isoformat(), "listed_total": len(rows), "rows": rows}


def dated_reference(bulk, identity, current):
    history = {}
    for code, original in bulk["records"].items():
        if code not in identity or identity[code] != current.get(code):
            continue
        years = {}
        for year in FISCAL:
            scopes = original["years"].get(str(year), {})
            for scope in SCOPES:
                row = scopes.get(scope, {})
                if prior_receipt(row.get("receipt_id"), year):
                    values = {key: value for key, value in row["values"].items()
                              if key in FIELDS and type(value) is int}
                    if values:
                        years.setdefault(str(year), {})[scope] = {
                            "values": values, "receipt_id": row["receipt_id"],
                        }
        if years:
            history[code] = {"years": years}
    return history


def add_statement(rows, code, name, market, scope, receipt, year, values, identity):
    require(code in identity and identity[code] == (market, name)
            and scope in SCOPES and year in FISCAL[1:]
            and prior_receipt(receipt, 2024)
            and values.keys() <= set(FIELDS) and all(type(value) is int for value in values.values()),
            "unbound_historical_financial_statement")
    balance = [values.get(key) for key in FIELDS[:3]]
    require(all(value is None for value in balance) or (
        all(value is not None for value in balance)
        and balance[0] > 0 and balance[1] >= 0 and balance[0] == sum(balance[1:])
    ), "unreconciled_historical_statement_balance")
    record = rows.setdefault(code, {"years": {}})
    require(str(year) not in record["years"] and all(
        list(annual) == [scope] and annual[scope]["receipt_id"] == receipt
        for annual in record["years"].values()), "duplicate_or_mixed_annual_statement")
    record["years"][str(year)] = {scope: {"receipt_id": receipt, "values": values}}


def restored_statements(report, receipts, identity):
    require(report["schema"] == "v3-recovered-prior-annual-primary-statements-v1"
            and report["signal_market_date"] == SIGNAL.isoformat()
            and report["point_in_time_scores_verified"] is False
            and report["restored_primary_statement_count"] == len(report["records"]) == 51,
            "invalid_restored_annual_ledger")
    rows = {}
    for code, record in report["records"].items():
        official = receipts["records"][code]
        receipt, proof = record["receipt"], record["receipt_verification_source"]
        require(record["name"] == official["name"]
                and record["fiscal_month"] == "2024-12"
                and record["latest_retained_receipt"] == official["retained_later_receipt"]
                and record["scope"] in official["retained_scopes"]
                and day(record["public_body_displayed_on"]) < SIGNAL
                and record["values_krw"] == record["comparative_values_krw"]["2024"]
                and re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"]),
                "restored_annual_source_identity_mismatch")
        require((proof == "OPENDART_LIST_AND_ORIGINAL_ZIP_PLUS_DART_PUBLIC_PRIMARY_STATEMENT"
                 and official["status"] == "prior_document_zip_verified"
                 and official["selected_receipt"] == receipt)
                or (proof == "DART_PUBLIC_VIEWER_MUTUAL_BODY_HISTORY"
                    and record["opendart_listing_hold"] == official["status"]
                    and all(re.fullmatch(r"[0-9a-f]{64}", record[field])
                            for field in ("public_earlier_page_sha256", "public_later_page_sha256"))),
                "restored_annual_source_proof_mismatch")
        for year, values in record["comparative_values_krw"].items():
            add_statement(rows, code, record["name"], official["market"], record["scope"],
                          receipt, int(year), values, identity)
    return rows


def delisted_statements(identity):
    coverage = {row["code"]: row for row in ledger_rows(ROOT, "delisted-financial-coverage")}
    rows = {}
    candidates = ledger_rows(ROOT, "delisted-annual-statements")
    candidates.extend(row for row in ledger_rows(ROOT, "financial-statements")
                      if row["filing_period_end"] == "2024-12-31")
    for row in candidates:
        code = row["code"]
        source = coverage[code]
        require(source["review_status"] in ("reviewed_fy2024", "reviewed_prior_pilot")
                and source["selected_fy2024_receipt"] == row["dart_receipt"]
                and re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]), "delisted_annual_proof_mismatch")
        period = day(row["statement_period_end"])
        require((period.month, period.day) == (12, 31), "non_annual_delisted_statement")
        add_statement(rows, code, row["name"], source["market"], row["scope"],
                      row["dart_receipt"], period.year,
                      {key: int(row[key + "_krw"]) for key in FIELDS if row[key + "_krw"]}, identity)
    require(all(len(record["years"]) == int(coverage[code]["annual_statement_rows"])
                for code, record in rows.items()), "incomplete_delisted_annual_input_coverage")
    return rows


def calculate_parts(catalog, inputs):
    history = {"schema": HISTORY_SCHEMA, "asof": catalog["date"], "fiscal_years": FISCAL,
               "listed_total": catalog["listed_total"], "records": inputs,
               "four_year_income_observed_count": 0}
    empty_book = {"asof": catalog["date"], "market_listed_total": catalog["listed_total"], "records": []}
    rows = research_parts(history, catalog, empty_book)["rows"]
    result = {}
    for row in rows:
        components = {"financial": {}, "valuation": {}}
        for area, key in PARTS.values():
            part = row["unverified_components"][key]
            components[area][key] = {"points": part["candidate_points"], "available": part["max_points"],
                                     "asof": catalog["date"]}
        parts, _ = score_issuer(None, {"components": components}, day=catalog["date"])
        result[row["code"]] = {
            factor: {"input": row["unverified_components"][key]["observed_value"],
                     "same_market_peers": row["unverified_components"][key]["peer_count"],
                     **parts[area][factor]}
            for factor, (area, key) in PARTS.items() if parts[area][factor] is not None
        }
    return result


def join_outcomes(panels):
    audit_delisting_pilot()
    roster = ledger_rows(ROOT, "delistings")
    prices = ledger_rows(ROOT, "price-replay")
    downside = {(row["code"], row["horizon_months"]): row
                for row in ledger_rows(ROOT, "downside-horizons")}
    stable = {str(period): {} for period in HORIZONS}
    summarize(panels, roster, ledger_rows(ROOT, "delisting-trade-observations"), prices,
              stable_outcomes=stable)
    adjusted = load_replay(panels)
    for period, bucket in adjusted["horizons"].items():
        for code in adjusted["unpriced_paid_rights_codes_by_horizon"][period]:
            stable[period].pop(code, None)
        for row in bucket["rows"]:
            code = row["code"]
            # A reviewed transition supersedes a raw continuous mark, e.g. ex-rights.
            stable[period][code] = {**row, "basis": "reviewed_share_action"}
    for row in prices:
        code, period = row["code"], row["horizon_months"]
        path = downside[code, period]
        require(code not in stable[period] and row["valuation_session"] == path["valuation_session"],
                "overlapping_or_misaligned_delisting_outcomes")
        stable[period][code] = {
            "valuation_session": row["valuation_session"], "basis": "delisting_case_replay",
            "method": row["method"], "price_change_pct": float(row["price_change_pct"])
            if row["price_change_pct"] else None,
            "observed_adverse_excursion_pct": float(path["maximum_adverse_excursion_observed_pct"]),
            "unpriced_sessions": int(path["unpriced_sessions"]),
        }
    return stable, adjusted["register_sha256"]


def summarize_outcomes(codes, outcomes, entry):
    summary = {}
    for period in map(str, HORIZONS):
        points = [outcomes[period][code] for code in codes if code in outcomes[period]]
        priced = [row for row in points if row["price_change_pct"] is not None]
        adverse = [point["observed_adverse_excursion_pct"] for point in points
                   if point["observed_adverse_excursion_pct"] is not None]
        summary[period] = {
            "cohort_size": len(codes),
            "entry_open_observed": sum(traded(entry.get(code)) for code in codes),
            "priced": len(priced), "adverse_path_observed": len(adverse),
            "priced_path_with_unpriced_sessions": sum(row["unpriced_sessions"] > 0 for row in priced),
            "priced_by_basis": dict(Counter(row.get("basis", "continuous_raw_quote") for row in priced)),
            "median_price_change_pct": round(median(row["price_change_pct"] for row in priced), 4) if priced else None,
            "median_observed_adverse_excursion_pct": round(median(adverse), 4) if adverse else None,
        }
    return summary


def public_price_paths(catalog, panels, outcomes, summary):
    """Project the fixed historical cohort onto a small, price-only Pages feed."""
    sessions = sorted(session for session in panels if session >= ENTRY)
    valuations = {str(h): next(s for s in sessions if s >= _anniversary(SIGNAL, h)).isoformat()
                  for h in HORIZONS}
    rows = {}
    for member in catalog["rows"]:
        code = member["code"]
        paths = {}
        for period, bucket in outcomes.items():
            result = bucket.get(code)
            if result is None:
                continue
            require(result["valuation_session"] == valuations[period], "public_price_path_date_mismatch")
            paths[period] = [result["price_change_pct"], result["observed_adverse_excursion_pct"],
                             result["unpriced_sessions"], result.get("basis"),
                             bool(result.get("valuation_includes_unlisted_entitlement"))]
        rows[code] = {
            "name": member["name"], "market": member["market"],
            "entry_observed": traded(panels[ENTRY].get(code)), "paths": paths,
        }
    return {
        "schema": "v3-historical-price-paths-1",
        "signal_session": SIGNAL.isoformat(), "entry_session": ENTRY.isoformat(),
        "valuations": valuations, "cohort_size": summary["12"]["cohort_size"],
        "priced": {period: summary[period]["priced"] for period in valuations},
        "path_columns": ["price_pct", "downside_pct", "unpriced_sessions", "basis", "unlisted_entitlement"],
        "rows": rows, "performance_validated": False,
    }


def build(paths, financial_path, current_path, *, price_paths_out=None):
    bulk, bulk_sha = content(financial_path)
    current, current_sha = content(current_path)
    require((bulk_sha, current_sha) == PINNED_HASHES[1:] and bulk["asof"] == current["date"] == PIN_DAY,
            "untrusted_historical_financial_reference")
    panels = read_panels(paths)
    anchor = panels[SIGNAL]
    scope, scope_sha = content(ROOT / "ops" / f"v3-performance-operating-scope-{TAG}.json")
    funds, funds_sha = content(ROOT / "ops" / f"v3-performance-classified-funds-{TAG}.json")
    catalog = operating_catalog(paths[0], anchor, scope["scope_counts"]["reproduced_operating_scope"]["listed"])
    members = {row["code"]: (row["market"], row["name"]) for row in catalog["rows"]}
    require(len({row["code"] for row in funds}) == len(funds)
            and all(members.get(row["code"]) == ("KOSPI", row["name"]) for row in funds),
            "historical_financial_fund_identity")
    identity = {code: row[:2] for code, row in anchor.items()}
    restored, restored_sha = content(ROOT / "ops" / f"v3-performance-recovered-annual-statements-{TAG}.json")
    receipts, receipts_sha = content(ROOT / "ops" / f"v3-performance-annual-receipt-recovery-{TAG}.json")
    require(restored["receipt_ledger_sha256"] == receipts_sha, "restored_annual_receipt_digest_mismatch")
    verified = restored_statements(restored, receipts, identity)
    delisted = delisted_statements(identity)
    require(not verified.keys() & delisted.keys(), "overlapping_historical_filing_sources")
    reference = dated_reference(bulk, identity, {row["code"]: (row["market"], row["name"]) for row in current["rows"]})
    cohort = {row["code"] for row in catalog["rows"]}
    sources = {key: set(data) & cohort for key, data in (
        ("dated_bulk_reference", reference), ("restored_original_statements", verified),
        ("reviewed_later_delisted_statements", delisted))}
    sources["dated_bulk_reference"] -= verified.keys() | delisted.keys()
    parts = calculate_parts(catalog, {**reference, **verified, **delisted})
    full = {code for code, values in parts.items() if set(values) == set(PARTS)}
    outcomes, action_digests = join_outcomes(panels)
    groups = {"complete_three_parts": full, **sources}
    detailed = sources["restored_original_statements"] | sources["reviewed_later_delisted_statements"]
    records = {}
    for code in sorted(detailed):
        observed = parts.get(code, {})
        records[code] = {
            "name": identity[code][1], "market": identity[code][0], "parts": observed,
            "observed_points": round(sum(v["points"] for v in observed.values()), 6) if observed else None,
            "available_points": sum(v["available"] for v in observed.values()),
            "price_paths": {period: rows[code] for period, rows in outcomes.items() if code in rows},
        }
    outcome_coverage = {key: summarize_outcomes(codes, outcomes, panels[ENTRY])
                        for key, codes in groups.items() if key != "dated_bulk_reference"}
    result = {
        "schema": SCHEMA, "score_model": VERSION, "signal_session": SIGNAL.isoformat(),
        "entry_session": ENTRY.isoformat(), "historical_scope_count": len(cohort),
        "existing_operating_scope_identified_funds": len(funds),
        "signal_market_cap_reconciled_count": sum(r["reference_market_cap_eok"] is not None for r in catalog["rows"]),
        "source_sha256": {"restored_statements": restored_sha, "receipts": receipts_sha,
                          "bulk_financial_reference": bulk_sha, "reference_catalog": current_sha,
                          "operating_scope": scope_sha, "classified_funds": funds_sha,
                          "price_inputs": list(SOURCE_DIGESTS), **action_digests,
                          **{name: sha256((ROOT / "ops" / f"v3-performance-{name}-{TAG}.csv")
                                          .read_bytes()).hexdigest() for name in (
                                              "delistings", "delisting-trade-observations",
                                              "delisted-financial-coverage", "delisted-annual-statements",
                                              "financial-statements", "price-replay", "downside-horizons")}},
        "financial_input_counts": {key: len(codes) for key, codes in sources.items()},
        "parts_replayed_counts": {key: dict(Counter(factor for code in codes for factor in parts.get(code, {})))
                                  for key, codes in {"operating_scope": cohort, **sources}.items()},
        "part_weights": {name: SPECS[area][name] for name, (area, _) in PARTS.items()},
        "complete_three_part_issuers": len(full),
        "outcome_coverage": outcome_coverage,
        "rows": records,
        "receipt_dated_bulk_values_independently_version_verified": False,
        "historical_security_types_independently_certified": False,
        "cash_dividend_and_trading_cost_net_return_verified": False,
        "intraday_market_path_independently_verified": False,
        "point_in_time_scores_verified": False, "performance_validated": False,
        "provider_calls": 0,
    }
    if price_paths_out is not None:
        public = public_price_paths(catalog, panels, outcomes, outcome_coverage["complete_three_parts"])
        price_paths_out.write_text(json.dumps(public, ensure_ascii=False, sort_keys=True,
                                             allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    parser.add_argument("financial", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--price-paths-out", type=Path, help="write the Pages research view JSON")
    args = parser.parse_args()
    print(json.dumps(build(args.sources, args.financial, args.catalog, price_paths_out=args.price_paths_out),
                     ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
