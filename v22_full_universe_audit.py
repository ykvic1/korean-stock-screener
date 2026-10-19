"""Read-only accounting audit of the PUBLIC v2.2 full-market price snapshot.

Check that *every attempted stock* belongs to exactly one disjoint
outcome: current usable price, price fetch/validation failure, or
insufficient history. Entirely unattempted batch slots are counted
separately. Unverified official provenance/PIT is NEVER declared true.

The output contains aggregate counts only. It does not duplicate stock-level
prices, vendor OHLCV, company details, key materials or private portfolios.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
import re
from typing import Any

CODE = re.compile(r"[0-9]{6}\Z")
MARKETS = ("KOSPI", "KOSDAQ")
REASONS = (
    "successfully_priced", "price_collection_or_validation_error",
    "insufficient_price_history", "not_attempted", "unreconciled",
)
MAX_SYMBOLS = 20_000


class UniverseAccountingError(ValueError):
    """Safe fixed-code failure reason; user must not mistake count for 0."""


def _int(data: dict, name: str, *, allow_null: bool = False):
    val = data.get(name)
    if allow_null and val is None:
        return None
    if type(val) is not int or not 0 <= val <= MAX_SYMBOLS:
        raise UniverseAccountingError("invalid_" + name)
    return val


def _code(x: Any) -> str:
    if type(x) is not str or CODE.fullmatch(x) is None:
        raise UniverseAccountingError("invalid_stock_identity")
    return x


def _day(x: Any) -> str:
    if type(x) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
        raise UniverseAccountingError("invalid_snapshot_date")
    try:
        parsed = date.fromisoformat(x)
    except ValueError:
        raise UniverseAccountingError("invalid_snapshot_date") from None
    if parsed.isoformat() != x:
        raise UniverseAccountingError("invalid_snapshot_date")
    return x


def _failure_group(value: Any, *, price: bool):
    if not isinstance(value, list) or len(value) != (3 if price else 2):
        raise UniverseAccountingError("invalid_failure_row_contract")
    return _code(value[0])


def audit_published_universe(snapshot: dict) -> dict:
    if (type(snapshot) is not dict or snapshot.get("version") != "2.0"
            or snapshot.get("status") != "success"):
        raise UniverseAccountingError("not_successful_v2_market_snapshot")
    day = _day(snapshot.get("date"))
    eligible = _int(snapshot, "eligible_count")
    attempted = _int(snapshot, "examined_count")
    deferred = _int(snapshot, "history_deferred_count")
    priced = _int(snapshot, "candidate_count")
    usable = _int(snapshot, "history_usable_count", allow_null=True)
    error_count = _int(snapshot, "error_count")
    flow_count = _int(snapshot, "flow_confirmed_count")
    fundamental_count = _int(snapshot, "fundamental_count")
    if min(eligible,attempted,priced)==0:
        raise UniverseAccountingError("empty_market_snapshot")
    stocks = snapshot.get("stocks")
    errors = snapshot.get("collection_errors")
    excluded = snapshot.get("history_exclusions")
    if not all(type(x) is list for x in (stocks, errors, excluded)):
        raise UniverseAccountingError("invalid_stocks_or_errors")
    if (len(stocks)!=priced or len(errors)!=error_count
            or (usable is not None and usable != priced)):
        raise UniverseAccountingError("published_count_mismatch")
    if eligible!=attempted+deferred or priced>attempted:
        raise UniverseAccountingError("universe_count_mismatch")
    is_complete=snapshot.get("history_scan_complete")
    if type(is_complete) is not bool or is_complete!=(deferred==0):
        raise UniverseAccountingError("wrong_market_coverage_flag")
    successful=set()
    market_counts=Counter()
    for stock in stocks:
        if type(stock) is not dict:
            raise UniverseAccountingError("invalid_priced_stock_row")
        code=_code(stock.get("code"))
        if code in successful:
            raise UniverseAccountingError("duplicate_successful_stock")
        successful.add(code)
        market=stock.get("market")
        if market not in MARKETS:
            raise UniverseAccountingError("invalid_stock_market")
        market_counts[market]+=1
        price=stock.get("price")
        if (type(price) not in (float,int) or not math.isfinite(price)
                or price<=0):
            raise UniverseAccountingError("invalid_stock_price")
        if stock.get("price_date")!=day:
            raise UniverseAccountingError("mismatched_price_asof")
        if type(stock.get("flows_confirmed")) is not bool:
            raise UniverseAccountingError("missing_flow_provenance_marker")
    if sum(s.get("flows_confirmed") is True for s in stocks)!=flow_count:
        raise UniverseAccountingError("flow_count_mismatch")
    if sum(s.get("pbr") is not None for s in stocks)!=fundamental_count:
        raise UniverseAccountingError("fundamental_count_mismatch")
    failed=set()
    flow_unavailable=set()
    error_types=Counter()
    for row in errors:
        code=_failure_group(row,price=True)
        stage=row[1]
        if stage=="가격이력":
            if code in successful or code in failed:
                raise UniverseAccountingError("duplicate_price_outcome")
            failed.add(code)
            cause=str(row[2])
            # Aggregate a stable finite type, NEVER copy untrusted supplier URL.
            cause=(cause if re.fullmatch(r"[A-Za-z_]{1,64}",cause)
                   else "Other_or_stale")
            error_types[cause]+=1
        elif stage=="기관·외국인 수급":
            if code not in successful:
                raise UniverseAccountingError("orphan_flow_failure")
            flow_unavailable.add(code)
        else:
            raise UniverseAccountingError("unknown_failure_stage")
    insufficient=set()
    for row in excluded:
        code=_failure_group(row,price=False)
        if code in successful or code in failed or code in insufficient:
            raise UniverseAccountingError("duplicate_excluded_stock")
        insufficient.add(code)
    if len(successful)+len(failed)+len(insufficient)!=attempted:
        raise UniverseAccountingError("attempted_stock_accounting_mismatch")
    if len(successful)+len(failed)+len(insufficient)+deferred!=eligible:
        raise UniverseAccountingError("market_universe_accounting_mismatch")
    unpriced=len(failed)+len(insufficient)+deferred
    return {
        "schema":"v22-full-market-accounting-0.1",
        "asof":day,
        "status":("census_accounted_with_unresolved_quality"
                  if unpriced or flow_count<priced else
                  "census_accounted_with_unverified_external_sources"),
        "market_listing_size":eligible,
        "attempted_stock_count":attempted,
        "complete_batch_census":is_complete,
        "priced_current_session_count":priced,
        "price_coverage_pct":round(100*priced/eligible,2),
        "not_priced_total":unpriced,
        "price_failure_count":len(failed),
        "insufficient_price_history_count":len(insufficient),
        "unattempted_batch_count":deferred,
        "price_issue_types":dict(sorted(error_types.items())),
        "priced_by_market":{m:market_counts[m] for m in MARKETS},
        "flow_confirmed_count":flow_count,
        "flow_unavailable_in_errors_count":len(flow_unavailable),
        "pbr_observed_count":fundamental_count,
        "all_attempted_stock_codes_reconciled_once":True,
        "independent_historical_prices_verified":False,
        "full_5_to_10_year_history_verified":False,
        "all_stock_corporate_actions_verified":False,
        "official_regular_only_volume_verified":False,
        "historical_issuer_delisting_pit_verified":False,
        "foreign_institution_250_day_history_verified":False,
        "v3_full_score_release_ready":False,
        "confirmed_stock_recommendations":0,
        "private_or_vendor_raw_copied":False,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Fail-closed all-stock v2.2 price/coverage account audit")
    p.add_argument("--daily",required=True,help="Existing public v2.2 docs/data.json")
    p.add_argument("--output",help="Aggregate-only json (not raw stock prices)")
    a=p.parse_args(argv)
    try:
        f=Path(a.daily)
        if not f.is_file() or f.stat().st_size>80_000_000:
            raise UniverseAccountingError("snapshot_unavailable_or_oversize")
        d=json.loads(f.read_text(encoding="utf-8"))
        result=audit_published_universe(d)
        if a.output:
            out=Path(a.output)
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({
            "status":result["status"],"asof":result["asof"],
            "eligible":result["market_listing_size"],
            "priced":result["priced_current_session_count"],
            "failures":result["not_priced_total"],
            "flow_confirmed":result["flow_confirmed_count"],
            "v3_full_score_release_ready":False,
        },ensure_ascii=False,sort_keys=True))
        return 0
    except (UniverseAccountingError,ValueError,TypeError,KeyError,OSError):
        print('{"status":"blocked","reason":"invalid_or_unaccounted_public_market_snapshot"}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
