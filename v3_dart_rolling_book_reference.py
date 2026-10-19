"""Rolling, current-listed OpenDART *reference* annual book/equity coverage.

No score, investor-flow, backtest, order, full OHLCV, or official KRX PBR.
Source-year accounts may be reused, but any ratio uses TODAY's listed market
capitalization and is explicitly non-PIT, approximate and non-authoritative.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import math
import re
from typing import Callable

from v22_full_universe_audit import audit_published_universe

SCHEMA="v3-dart-current-listed-annual-book-reference-0.1"
CODE=re.compile(r"\d{6}\Z")
RECEIPT=re.compile(r"\d{14}\Z")
MAX_PER_RUN=40
MAX_RECORDS=5000
FIN_FIELDS=("revenue","operating_income","operating_cashflow","liabilities",
            "equity","owners_equity","owners_profit","operating_margin_pct",
            "debt_to_equity_pct")
KNOWN_STATUS=("observed","unconfirmed","deferred")


def _num(v,*,positive=False):
    if type(v) not in (int,float) or not math.isfinite(v):
        return None
    if positive and v<=0:
        return None
    return v


def _day(v):
    if type(v) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",v):
        raise ValueError("invalid_market_day")
    try:
        parsed=date.fromisoformat(v)
    except ValueError:
        raise ValueError("invalid_market_day") from None
    if parsed.isoformat()!=v:
        raise ValueError("invalid_market_day")
    return v


def _source_record(item,day):
    """Trust nothing except date-visible receipt and finite named DERIVED values."""
    if type(item) is not dict or item.get("status") not in KNOWN_STATUS:
        raise ValueError("invalid_issuer_account_status")
    code=item.get("code")
    if type(code) is not str or not CODE.fullmatch(code):
        raise ValueError("invalid_issuer_account_code")
    reports=item.get("reports") or []
    if type(reports) is not list or len(reports)>3:
        raise ValueError("unexpected_report_count")
    usable=[]
    for report in reports:
        if not isinstance(report,dict):
            continue
        receipt=report.get("receipt_id")
        year=report.get("year")
        fs=report.get("fs_div")
        if (type(receipt) is not str or not RECEIPT.fullmatch(receipt)
            or receipt[:8]>day.replace("-","")
            or type(year) is not int or not 2018<=year<=int(day[:4])-1
            or fs not in ("CFS","OFS")
            or report.get("currency")!="KRW"
            or not isinstance(report.get("values"),dict)):
            continue
        values={}
        for name in FIN_FIELDS:
            val=report["values"].get(name)
            values[name]=_num(val)
        usable.append({"year":year,"fs_div":fs,"receipt_id":receipt,
                       "values":values,"currency":"KRW",
                       "revision_history_verified":False,
                       "historical_point_in_time_verified":False})
    # Store only latest dated annual report, not raw account rows.
    usable.sort(key=lambda x:(x["year"],x["fs_div"]=="CFS"),reverse=True)
    status="observed" if usable else item["status"] if item["status"]!="observed" else "unconfirmed"
    return {"code":code,"status":status,
            "annual":usable[0] if usable else None,
            "last_attempted_day":day}


def _valid_cache(cache,day):
    if cache is None:
        return {}
    if (type(cache) is not dict or cache.get("schema")!=SCHEMA
        or _day(cache.get("asof"))>day or not isinstance(cache.get("records"),list)
        or len(cache["records"])>MAX_RECORDS):
        raise ValueError("untrusted_prior_book_cache")
    found={}
    for r in cache["records"]:
        if type(r) is not dict or type(r.get("code")) is not str or not CODE.fullmatch(r["code"]) or r["code"] in found:
            raise ValueError("bad_prior_book_identity")
        checked=_day(r.get("last_attempted_day"))
        if checked>day:
            raise ValueError("future_prior_book_record")
        rec=_source_record({
            "code":r["code"],"status":r.get("status"),"reports":
            ([r["annual"]] if r.get("annual") else [])
        },day)
        rec["last_attempted_day"]=checked
        found[r["code"]]=rec
    return found


def _inputs(market,catalog):
    audit=audit_published_universe(market)
    day=audit["asof"]
    if (type(catalog) is not dict or catalog.get("schema")!="v22-current-listed-catalog-0.1"
        or catalog.get("date")!=day or catalog.get("listed_total")!=audit["market_listing_size"]
        or not isinstance(catalog.get("rows"),list)
        or len(catalog["rows"])!=audit["market_listing_size"]):
        raise ValueError("incomplete_current_listing_catalog")
    members={}
    valid_price={s["code"]:s for s in market["stocks"]}
    for row in catalog["rows"]:
        if type(row) is not dict or type(row.get("code")) is not str or not CODE.fullmatch(row["code"]) or row["code"] in members:
            raise ValueError("invalid_listing_code")
        code=row["code"]
        if row.get("market") not in ("KOSPI","KOSDAQ") or not isinstance(row.get("name"),str):
            raise ValueError("invalid_listing_market")
        price=valid_price.get(code)
        if (row.get("price_history_status")=="priced") != (price is not None):
            raise ValueError("price_catalog_not_coherent")
        cap=_num(row.get("reference_market_cap_eok"),positive=True)
        members[code]={"code":code,"market_cap_eok":cap,
                       "market":row["market"],"score":price.get("score") if price else None,
                       "coverage_pct":price.get("coverage_pct") if price else None,
                       "priced":price is not None,
                       "price_status":row.get("price_history_status")}
    if len(members)!=audit["market_listing_size"]:
        raise ValueError("incomplete_listed_members")
    return day,members


def plan(market,catalog,*,prior=None,official=None,limit=MAX_PER_RUN):
    """Pick unattempted currently listed names; first best-scored, then rest."""
    day,members=_inputs(market,catalog)
    if type(limit) is not int or not 1<=limit<=MAX_PER_RUN:
        raise ValueError("overlarge_dart_request")
    history=_valid_cache(prior,day)
    history={c:x for c,x in history.items() if c in members}  # Delisted = drop.
    if (isinstance(official,dict) and official.get("asof")==day
        and isinstance(official.get("dart"),dict)):
        for item in official["dart"].get("stocks",[]):
            if isinstance(item,dict) and item.get("code") in members and item["code"] not in history:
                try:
                    r=_source_record(item,day)
                    if r["status"]=="observed":
                        history[r["code"]]=r
                except ValueError:
                    continue
    # A failure is attempted, NOT a zero PBR. Retry only after fresh names.
    ordered=sorted(members.values(),key=lambda s:(
        s["code"] in history,not s["priced"],
        s["price_status"]=="price_source_or_validation_error",
        -(_num(s["score"]) or 0),-(_num(s["coverage_pct"]) or 0),s["code"]))
    targets=[x for x in ordered if x["code"] not in history][:limit]
    if not targets:
        # Once all currently listed symbols have been visited, bounded retry
        # for source-unavailable names, oldest previous attempt first.
        retry=[x for x in ordered if history.get(x["code"],{}).get("status")!="observed"]
        retry.sort(key=lambda x:(history[x["code"]]["last_attempted_day"],x["code"]))
        targets=retry[:limit]
    return day,members,history,targets


def merge(market,catalog, prior, results, *,official=None,limit=MAX_PER_RUN):
    day,members,history,targets=plan(market,catalog,prior=prior,official=official,limit=limit)
    if not isinstance(results,list) or len(results)!=len(targets):
        raise ValueError("dart_batch_results_incomplete")
    expected={x["code"] for x in targets}
    received=set()
    for row in results:
        account=_source_record(row,day)
        code=account["code"]
        if code not in expected or code in received:
            raise ValueError("dart_batch_identity_mismatch")
        received.add(code)
        history[code]=account
    observed=[]
    for code,record in sorted(history.items()):
        src=record.get("annual")
        if record["status"]!="observed" or src is None:
            continue
        vals=src["values"]
        book=vals["equity"] if src["fs_div"]=="OFS" else vals["owners_equity"]
        cap=members[code]["market_cap_eok"]
        pbr=round(cap*1e8/book,3) if _num(book,positive=True) is not None and cap is not None else None
        if pbr is not None and (not math.isfinite(pbr) or not 0<pbr<1000):
            pbr=None
        observed.append({"code":code,"pbr_simple_annual_reference":pbr,
                         "year":src["year"],"fs_div":src["fs_div"],
                         "receipt_id":src["receipt_id"],
                         "market_cap_reference_day":day,
                         "official_exchange_pbr_verified":False,
                         "financial_statement_pit_verified":False})
    reports=[x for x in observed if x["pbr_simple_annual_reference"] is not None]
    return {
        "schema":SCHEMA,"asof":day,"market_listed_total":len(members),
        "attempted_reference_count":len(history),"observed_annual_count":len(observed),
        "pbr_simple_reference_count":len(reports),
        "new_attempts_this_run":len(targets),
        "other_listed_without_book_observation":len(members)-len(observed),
        "official_exchange_pbr_verified":False,
        "score_weight_changed":False,"stock_buy_certified":False,
        "stock_flow_data_assumed":False,
        "historical_point_in_time_verified":False,
        "raw_dart_account_rows_published":False,
        "reference_notes":"Current listed market cap / latest date-visible annual separate equity or consolidated owners equity; NOT official KRX PBR or PIT",
        "records":[history[k] for k in sorted(history)],
        "observations":observed,
    }


def write(snapshot,catalog, *,output,prior=None,official=None,key=None,
          collector:Callable|None=None,limit=MAX_PER_RUN):
    day,members,history,targets=plan(snapshot,catalog,prior=prior,official=official,limit=limit)
    if collector is None:
        from official_data import dart_collect
        collector=dart_collect
    if targets and (type(key) is not str or not key.strip()):
        raise ValueError("missing_dart_credentials")
    results=[]
    for idx in range(0,len(targets),20):
        # Bounded reused official connector; 1 observed annual year only to
        # cover more CURRENT issuers before costly historical restatements.
        batch=collector(targets[idx:idx+20],day,key,years=1,budget_seconds=300)
        if not isinstance(batch,list) or len(batch)!=len(targets[idx:idx+20]):
            raise ValueError("dart_partial_batch_without_attribution")
        results.extend(batch)
    data=merge(snapshot,catalog,prior,results,official=official,limit=limit)
    path=Path(output)
    path.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(data,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":"))
    if len(encoded.encode("utf8"))>8_000_000:
        raise ValueError("derived_book_reference_size_limit")
    tmp=path.with_suffix(".tmp")
    tmp.write_text(encoded+"\n",encoding="utf8")
    tmp.replace(path)
    return {k:v for k,v in data.items() if k not in ("records","observations")}
