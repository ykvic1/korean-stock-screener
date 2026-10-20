"""Bounded four-year OpenDART annual evidence enrichment; NEVER a V3 buy score.

Preserves the existing rolling 1-year collector's 8x40 weekday schedule and
does not fetch anything until explicitly invoked (manual CI workflow).
Only observed numbers and 4-fiscal-year scope-consistent completeness are
reported. FCF needs CAPEX, net debt needs cash/debt; both remain unavailable.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import math
import re

from v3_dart_rolling_book_reference import _inputs

SCHEMA = "v3-four-year-dart-financial-evidence-0.1"
MAX_COMPANIES = 4
FIELDS = ("revenue", "operating_income", "operating_cashflow",
          "liabilities", "equity", "owners_equity", "owners_profit")
CODE = re.compile(r"[0-9]{6}\Z")
RECEIPT = re.compile(r"[0-9]{14}\Z")


class FourYearEvidenceError(ValueError):
    """Fixed error labels; no private accounts, keys or source payloads."""


def _day(raw):
    if type(raw) is not str:
        raise FourYearEvidenceError("invalid_evidence_day")
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise FourYearEvidenceError("invalid_evidence_day") from None
    if d.isoformat() != raw:
        raise FourYearEvidenceError("invalid_evidence_day")
    return d


def _number(x):
    if x is None:
        return None
    if type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e19:
        raise FourYearEvidenceError("invalid_observed_annual_amount")
    return float(x)


def _record(code, reports, asof):
    if type(code) is not str or not CODE.fullmatch(code):
        raise FourYearEvidenceError("invalid_fouryear_code")
    if not isinstance(reports, list) or len(reports) > 4:
        raise FourYearEvidenceError("unexpected_annual_report_count")
    valid, years, scopes = [], set(), set()
    cutoff = _day(asof)
    for item in reports:
        if not isinstance(item, dict):
            raise FourYearEvidenceError("invalid_annual_report")
        year, scope, receipt = item.get("year"), item.get("fs_div"), item.get("receipt_id")
        if type(year) is not int or not 2015 <= year < cutoff.year:
            raise FourYearEvidenceError("invalid_annual_year")
        if year in years:
            raise FourYearEvidenceError("repeated_annual_year")
        years.add(year)
        if scope not in ("CFS", "OFS"):
            raise FourYearEvidenceError("invalid_annual_scope")
        scopes.add(scope)
        if type(receipt) is not str or not RECEIPT.fullmatch(receipt):
            raise FourYearEvidenceError("missing_annual_receipt")
        try:
            received = date.fromisoformat(receipt[:4]+"-"+receipt[4:6]+"-"+receipt[6:8])
        except ValueError:
            raise FourYearEvidenceError("invalid_annual_receipt_day") from None
        if received > cutoff or received <= date(year, 12, 31):
            raise FourYearEvidenceError("lookahead_or_pre_yearend_receipt")
        if item.get("currency") != "KRW" or not isinstance(item.get("values"), dict):
            raise FourYearEvidenceError("invalid_currency_or_values")
        values = {k: _number(item["values"].get(k)) for k in FIELDS}
        valid.append({"year":year,"fs_div":scope,"receipt_id":receipt,
                      "values":values, "currency":"KRW"})
    valid.sort(key=lambda r:r["year"])
    is_four = (len(valid)==4 and len(scopes)==1 and
               [r["year"] for r in valid] ==
               list(range(valid[0]["year"],valid[0]["year"]+4)))
    cagr, margin = None, None
    if is_four:
        revenues=[r["values"]["revenue"] for r in valid]
        if all(v is not None and v > 0 for v in revenues):
            cagr=round(100*((revenues[-1]/revenues[0])**(1/3)-1),6)
        margins=[]
        for r in valid[-3:]:
            v=r["values"]
            margins.append(100*v["operating_income"]/v["revenue"]
                           if v["operating_income"] is not None and
                              v["revenue"] is not None and v["revenue"]>0 else None)
        if all(x is not None for x in margins):
            margin=round(sum(margins)/3,6)
    # Four successive filings are NOT the same thing as four consecutive
    # years with usable income, cash flow and balance-sheet numbers.
    revenue_op_observed = is_four and all(
        r["values"]["revenue"] is not None and r["values"]["revenue"] > 0
        and r["values"]["operating_income"] is not None
        for r in valid
    )
    cashflow_3y_observed = is_four and all(
        r["values"]["operating_cashflow"] is not None
        for r in valid[-3:]
    )
    latest_balance_observed = is_four and all(
        valid[-1]["values"][name] is not None
        for name in ("liabilities", "equity", "owners_equity")
    )
    core_observed = (revenue_op_observed and cashflow_3y_observed
                     and latest_balance_observed)
    return {
        "code":code,
        "four_year_income_values_observed":revenue_op_observed,
        "last_three_years_operating_cashflow_observed":cashflow_3y_observed,
        "latest_year_balance_values_observed":latest_balance_observed,
        "core_financial_observed_for_research":core_observed,
        "status":"four_consecutive_years_observed" if is_four
                 else "insufficient_consecutive_single_scope_years",
        "reports":valid,
        "observed_fiscal_years":[r["year"] for r in valid],
        "same_scope_four_years":is_four,
        "accounting_scope":next(iter(scopes)) if len(scopes)==1 else None,
        "annual_revenue_cagr3_reference_pct":cagr,
        "three_year_operating_margin_mean_reference_pct":margin,
        "fcf_assets_reference_pct":None,
        "net_debt_equity_reference_pct":None,
        "valuation_official_pbr":None,
        "four_year_financial_pit_authenticated":False,
        "historical_revisions_authenticated":False,
        "financial15_confirmed_points":None,
        "valuation15_confirmed_points":None,
        "v3_full100_investable_score":None,
        "buy_certified":False,
    }


def plan(market, catalog, annual_book, *, prior=None, limit=MAX_COMPANIES):
    day, members = _inputs(market, catalog)
    if type(limit) is not int or not 1 <= limit <= MAX_COMPANIES:
        raise FourYearEvidenceError("out_of_budget_fouryear_pilot")
    if (not isinstance(annual_book, dict)
            or annual_book.get("asof") != day
            or annual_book.get("market_listed_total") != len(members)
            or annual_book.get("historical_point_in_time_verified") is not False
            or annual_book.get("official_exchange_pbr_verified") is not False
            or not isinstance(annual_book.get("records"), list)):
        raise FourYearEvidenceError("incompatible_annual_book_census")
    observed={}
    for row in annual_book["records"]:
        if (not isinstance(row,dict) or type(row.get("code")) is not str
                or row["code"] not in members or row["code"] in observed):
            raise FourYearEvidenceError("untrusted_annual_book_identity")
        if row.get("status")=="observed" and isinstance(row.get("annual"),dict):
            observed[row["code"]]=row
    existing={}
    if prior is not None:
        if (not isinstance(prior,dict) or prior.get("schema")!=SCHEMA
                or _day(prior.get("asof"))>_day(day)
                or not isinstance(prior.get("records"),list)
                or len(prior["records"])>len(members)):
            raise FourYearEvidenceError("invalid_previous_fouryear_snapshot")
        for record in prior["records"]:
            if (not isinstance(record,dict) or record.get("code") in existing
                    or record.get("code") not in members):
                raise FourYearEvidenceError("invalid_cached_fouryear_code")
            existing[record["code"]]=_record(record["code"],record.get("reports"),day)
    price={x["code"]:x for x in market["stocks"]}
    candidates=sorted(
        (c for c in observed if c in price and
         not existing.get(c,{}).get("core_financial_observed_for_research",False)),
        key=lambda c:(-price[c].get("score",0), c)
    )
    selected=candidates[:limit]
    return day, members, existing, selected


def merge(market,catalog,annual_book,results, *,prior=None,limit=MAX_COMPANIES):
    day, members, existing, requested=plan(
        market,catalog,annual_book,prior=prior,limit=limit)
    if not isinstance(results,list) or len(results)!=len(requested):
        raise FourYearEvidenceError("incomplete_pilot_batch")
    seen=set()
    for row in results:
        if not isinstance(row,dict) or row.get("code") not in requested:
            raise FourYearEvidenceError("unexpected_fouryear_issuer")
        code=row["code"]
        if code in seen:
            raise FourYearEvidenceError("duplicate_pilot_issuer")
        seen.add(code)
        # Explicitly incomplete responses become missing evidence, not 0 score.
        reports=row.get("reports") or []
        existing[code]=_record(code,reports,day)
    output=[existing[c] for c in sorted(existing)]
    return {
        "schema":SCHEMA,"asof":day,"listed_total":len(members),
        "pilot_issuer_count":len(output),"requested_this_run":len(requested),
        "four_consecutive_years_observed_count":sum(
            r["same_scope_four_years"] for r in output),
        "four_year_income_values_observed_count":sum(
            r["four_year_income_values_observed"] for r in output),
        "last_three_years_operating_cashflow_observed_count":sum(
            r["last_three_years_operating_cashflow_observed"] for r in output),
        "latest_year_balance_values_observed_count":sum(
            r["latest_year_balance_values_observed"] for r in output),
        "four_year_core_financial_research_observed_count":sum(
            r["core_financial_observed_for_research"] for r in output),
        "four_years_all_financial_pillars_certified":False,
        "unverified_financial_pit_receipts":True,
        "raw_account_rows_stored":False,
        "source_api_calls_in_merge":0,
        "confirmed_six_pillar_ranks":None,
        "full_v3_ready":False,
        "records":output,
    }


def write(market,catalog,annual_book,*,output,prior=None,key=None,
          collector=None,limit=MAX_COMPANIES):
    day,members,cached,selected=plan(
        market,catalog,annual_book,prior=prior,limit=limit)
    if collector is None:
        from official_data import dart_collect
        collector=dart_collect
    if selected and (type(key) is not str or not key.strip()):
        raise FourYearEvidenceError("missing_manual_pilot_key")
    targets=[{"code":code,"market_cap_eok":members[code]["market_cap_eok"]}
             for code in selected]
    replies=collector(targets,day,key,years=4,budget_seconds=420) if targets else []
    if not isinstance(replies,list):
        raise FourYearEvidenceError("unexpected_collector_response")
    report=merge(market,catalog,annual_book,replies,prior=prior,limit=limit)
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(report,ensure_ascii=False,allow_nan=False,
                       separators=(",",":"),sort_keys=True)
    if len(payload.encode("utf8"))>8_000_000:
        raise FourYearEvidenceError("oversized_fouryear_reference")
    tmp=output.with_suffix(".tmp")
    tmp.write_text(payload+"\n",encoding="utf8")
    tmp.replace(output)
    return {k:v for k,v in report.items() if k!="records"}
