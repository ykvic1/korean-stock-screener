"""Reapply the live listing filter to the fixed historical research anchor."""

import argparse
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, time
from hashlib import sha256
from inspect import getsource
import json
from pathlib import Path
import re
import sys

from v3_historical_market_coverage import END, ENTRY, HORIZONS, ROOT, SIGNAL, TAG
from v3_historical_market_coverage import ledger_rows, read_panels, require, summarize, traded
from v3_historical_market_share_actions import load_replay


def market_identity(rows):
    return {item["code"]: (item["market"], item["name"]) for item in rows}


def market_count(codes, source):
    return dict(sorted(Counter(source[code][0] for code in codes).items()))


def count_valuations(stable, adjusted, delisting_prices, cohorts):
    """Use a verified action-adjusted value when both routes price the same issue."""
    delisted = {str(h): set() for h in HORIZONS}
    for row in delisting_prices:
        if row["value_per_entry_share_krw"]:
            delisted[row["horizon_months"]].add(row["code"])
    result = {}
    for h in map(str, HORIZONS):
        base = stable[h]
        revised = {row["code"] for row in adjusted[h]["rows"]}
        require(len(revised) == adjusted[h]["count"], "duplicate_historical_adjusted_valuation")
        require(not (base | revised) & delisted[h], "historical_scope_delisting_overlap")
        result[h] = {
            key: {
                "raw_continuous_only": len((base - revised) & cohort),
                "action_adjusted": len(revised & cohort),
                "overlap_superseded": len(base & revised & cohort),
                "non_delisted_unique": len((base | revised) & cohort),
                "delisting_settlement": len(delisted[h] & cohort),
                "includes_unlisted_entitlement": sum(
                    row["code"] in cohort and row["valuation_includes_unlisted_entitlement"]
                    for row in adjusted[h]["rows"]),
            }
            for key, cohort in cohorts.items()
        }
    return result


def build(paths, current_catalog):
    import pandas as pd
    import pyarrow.parquet as pq

    # The screener imports PyKRX, which can emit an optional login notice.
    with redirect_stdout(sys.stderr):
        from screener import market_snapshot

    panels = read_panels(paths)
    historical = pq.read_table(paths[0], filters=[("Date", "=", datetime.combine(SIGNAL, time.min))],
                              columns=["Code", "Name", "Market", "Dept", "Close", "Volume", "Amount", "Marcap"])
    operating = market_identity(market_snapshot(historical.to_pandas()).values())
    current = market_identity(market_snapshot(pd.read_csv(paths[2], dtype={"Code": str})).values())
    catalog_raw = current_catalog.read_bytes()
    catalog = json.loads(catalog_raw)
    published = market_identity(catalog["rows"])
    require(catalog["date"] == END.isoformat() and current == published
            and len(current) == len(catalog["rows"]) == catalog["listed_total"],
            "operating_filter_current_control_mismatch")
    universe = {code: row[:2] for code, row in panels[SIGNAL].items()}
    require(operating.items() <= universe.items(), "operating_filter_historical_identity_mismatch")

    excluded = universe.keys() - operating.keys()
    spac = {row["Code"] for row in historical.to_pylist() if row["Dept"] == "SPAC(소속부없음)"}
    require(spac <= excluded, "historical_spac_department_in_operating_scope")
    preferred = {code for code in excluded - spac if code[-1] != "0"
                 and re.search(r"(?:[1-9][0-9]*)?우(?:[A-Z]|\(전환\))?$", universe[code][1])}
    roster = ledger_rows(ROOT, "delistings")
    delisted = {r["code"] for r in roster if r["anchor_membership"] == "present"}
    prices = ledger_rows(ROOT, "price-replay")
    stable = {str(h): set() for h in HORIZONS}
    raw_report = summarize(panels, roster, ledger_rows(ROOT, "delisting-trade-observations"),
                           prices, stable_codes=stable)
    adjusted = load_replay(panels)
    instrument_file = ROOT / "ops" / f"v3-performance-classified-funds-{TAG}.json"
    fund_raw = instrument_file.read_bytes()
    funds = json.loads(fund_raw)
    fund_codes = {r["code"] for r in funds}
    require(len(fund_codes) == len(funds)
            and all(operating.get(r["code"]) == ("KOSPI", r["name"]) for r in funds),
            "historical_scope_fund_identity_mismatch")
    cohorts = {"all_observed_securities": universe.keys(), "reproduced_operating_scope": operating.keys(),
               "identified_funds_in_operating_scope": fund_codes}
    return {
        "signal_session": SIGNAL.isoformat(),
        "current_control": {"session": END.isoformat(), "matched_count": len(current),
                            "published_catalog_sha256": sha256(catalog_raw).hexdigest()},
        "price_source_sha256": raw_report["source_sha256"],
        "fund_register_sha256": sha256(fund_raw).hexdigest(),
        "operating_selector_sha256": sha256(getsource(market_snapshot).encode("utf-8")).hexdigest(),
        "action_register_sha256": adjusted["register_sha256"],
        "scope_counts": {key: {"listed": len(codes), "by_market": market_count(codes, universe),
                               "later_delisted": len(codes & delisted),
                               "next_open_observed": sum(traded(panels[ENTRY].get(c)) for c in codes)}
                         for key, codes in cohorts.items()},
        "source_spac_department_excluded_count": len(spac),
        "preferred_name_and_class_excluded_count": len(preferred),
        "other_rows_excluded_by_operating_filter": len(excluded - spac - preferred),
        "identified_funds_by_type": dict(sorted(Counter(r["kind"] for r in funds).items())),
        "horizons": count_valuations(stable, adjusted["horizons"], prices, cohorts),
        "historical_security_types_independently_certified": False,
        "historical_adjusted_prices_independently_certified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path, metavar="SOURCE")
    parser.add_argument("control_catalog", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    report = build(args.sources, args.control_catalog)
    if args.verify:
        require(report == json.loads(args.verify.read_text(encoding="utf-8")), "historical_scope_report_mismatch")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
