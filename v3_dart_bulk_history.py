"""Bulk past-year DART major accounts for EVERY currently listed stock.

DART fnlttMultiAcnt accepts up to 100 company IDs/year. Four 100-company
calls cover four FYs; missing CF/CAPEX MUST remain unknown, not zero. Public
derived annual reference only, no source account rows, keys, or market OHLCV.
Not a historical-PIT or 100-point investment-grade score.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import json
import math
import re
import time

from v3_dart_rolling_book_reference import _inputs

SCHEMA = "v3-dart-multi-company-four-year-major-account-0.1"
SCORE_SCHEMA = "v3-dart-multi-year-research-parts-0.1"
MAX_CORPS = 100
MAX_GROUPS = 3
YEARS_BACK = 4
MAX_SOURCE_FISCAL_CALLS = 8
MAX_SOURCE_RUN_SECONDS = 1200
LABELS = {
    ("IS","매출액"): "revenue",
    ("IS","영업수익"): "revenue",
    ("IS","수익(매출액)"): "revenue",
    ("IS","매출액(수익)"): "revenue",
    ("IS","영업이익"): "operating_income",
    ("IS","영업이익(손실)"): "operating_income",
    ("BS","자산총계"): "assets",
    ("BS","부채총계"): "liabilities",
    ("BS","자본총계"): "equity",
}
FIELDS = ("revenue","operating_income","assets","liabilities","equity")
CODE = re.compile(r"[0-9]{6}\Z")
CORP = re.compile(r"[0-9]{8}\Z")
RECEIPT = re.compile(r"[0-9]{14}\Z")

class BulkHistoryError(ValueError):
    """Fixed, non-provider, non-sensitive exceptions."""


def _day(raw):
    if type(raw) is not str:
        raise BulkHistoryError("invalid_bulk_asof")
    try:
        d=date.fromisoformat(raw)
    except ValueError:
        raise BulkHistoryError("invalid_bulk_asof") from None
    if d.isoformat()!=raw:
        raise BulkHistoryError("invalid_bulk_asof")
    return d


def _amount(raw):
    if type(raw) is not str or len(raw)>32:
        return None
    value=raw.strip()
    if not re.fullmatch(r"-?(?:0|[1-9]\d{0,17}|[1-9]\d{0,2}(?:,\d{3}){1,5})",value):
        return None
    amount=int(value.replace(",",""))
    return amount if abs(amount)<=10**18 else None


def _valid_receipt(raw, year, cutoff):
    if type(raw) is not str or not RECEIPT.fullmatch(raw):
        return False
    try:
        d=date.fromisoformat(raw[:4]+"-"+raw[4:6]+"-"+raw[6:8])
    except ValueError:
        return False
    return date(year,12,31)<d<=cutoff


def _empty_year():
    return {}


def _normalize_year(rows, year, asof, requested, corp_to_stock,
                    diagnostics=None):
    """Keep independently valid official records; quarantine unsafe rows.

    Official multi-company API may omit a company, return stale disclosures
    or return extra/unexpected rows. One bad issuer must not invalidate good
    issuers. A batch with zero or overwhelmingly few trusted rows still aborts.
    Only counts and fixed diagnostic labels are logged, never provider rows.
    """
    cutoff=_day(asof)
    if type(rows) is not list or len(rows)>120_000:
        raise BulkHistoryError("invalid_bulk_dart_row_count")
    data={code:{} for code in requested}
    observed={}
    tainted=set()
    counts={}
    trusted=0
    def reject(label):
        counts[label]=counts.get(label,0)+1
    for row in rows:
        if type(row) is not dict:
            reject("malformed_row")
            continue
        code=row.get("stock_code")
        corp=row.get("corp_code")
        if type(code) is not str or code not in data:
            reject("unrequested_or_blank_stock_code")
            continue
        if corp is not None and (type(corp) is not str or
                not CORP.fullmatch(corp) or corp_to_stock.get(corp)!=code):
            reject("corp_code_mismatch")
            continue
        if str(row.get("bsns_year"))!=str(year):
            reject("fiscal_year_mismatch")
            continue
        if row.get("reprt_code")!="11011":
            reject("annual_report_code_mismatch")
            continue
        scope=row.get("fs_div")
        if scope not in ("CFS","OFS"):
            reject("unknown_accounting_scope")
            continue
        if row.get("currency")!="KRW":
            reject("unconfirmed_krw_currency")
            continue
        if not _valid_receipt(row.get("rcept_no"),year,cutoff):
            reject("invalid_or_future_receipt")
            continue
        key=(code,scope)
        if key in tainted:
            reject("scope_quarantined_due_to_mixed_receipt")
            continue
        receipt=row["rcept_no"]
        if key in observed and observed[key]!=receipt:
            # Never combine two different report revisions under one scope:
            # drop all of its accounts, but preserve independent issuers.
            tainted.add(key)
            data[code].pop(scope,None)
            reject("mixed_receipts_same_company_scope")
            continue
        observed[key]=receipt
        trusted+=1
        acct=LABELS.get((row.get("sj_div"),row.get("account_nm")))
        if acct is None:
            continue
        value=_amount(row.get("thstrm_amount"))
        doc=data[code].setdefault(scope,{"receipt_id":receipt,
                                         "values":{},"ambiguous":[]})
        if acct in doc["values"]:
            doc["ambiguous"].append(acct)
            doc["values"][acct]=None
        else:
            doc["values"][acct]=value
    if diagnostics is not None:
        if type(diagnostics) is not dict:
            raise BulkHistoryError("invalid_bulk_diagnostic_target")
        diagnostics.update({
            "returned_row_count":len(rows),
            "trusted_row_count":trusted,
            "quarantined_row_count":sum(counts.values()),
            "quarantine_reasons":dict(sorted(counts.items())),
            "quarantined_company_scope_count":len(tainted),
        })
    if rows and (trusted==0 or trusted*20<len(rows)):
        raise BulkHistoryError("dart_bulk_fatal_no_trustworthy_source_rows")
    for code in requested:
        for scope,ref in list(data[code].items()):
            for key in FIELDS:
                ref["values"].setdefault(key,None)
            if (ref["values"]["assets"] is not None and
                ref["values"]["liabilities"] is not None and
                ref["values"]["equity"] is not None and
                abs(ref["values"]["assets"]-ref["values"]["liabilities"]-
                    ref["values"]["equity"])>
                max(1,abs(ref["values"]["assets"])*0.01)):
                ref["values"]["assets"]=None
                ref["values"]["liabilities"]=None
                ref["values"]["equity"]=None
            ref["ambiguous"]=sorted(set(ref["ambiguous"]))
    return data

def _validate_prior(prior, asof, members):
    if prior is None:
        return {}
    if (type(prior) is not dict or prior.get("schema")!=SCHEMA
            or _day(prior.get("asof"))>_day(asof)
            or type(prior.get("records")) is not dict):
        raise BulkHistoryError("untrusted_bulk_previous_snapshot")
    keep={}
    for code,row in prior["records"].items():
        if (code not in members or type(row) is not dict or
            type(row.get("years")) is not dict or
            not all(str(y).isdigit() and 2015<=int(y)<_day(asof).year
                    for y in row["years"])):
            continue  # delisted, or unusable old year isolated
        keep[code]=row
    return keep


def plan(market,catalog,*,prior=None,groups=MAX_GROUPS):
    asof,members=_inputs(market,catalog)
    if type(groups) is not int or not 1<=groups<=MAX_GROUPS:
        raise BulkHistoryError("invalid_bulk_max_groups")
    retained=_validate_prior(prior,asof,members)
    fiscal=list(range(_day(asof).year-YEARS_BACK,_day(asof).year))
    eligible=[]
    for code in sorted(members):
        years=retained.get(code,{}).get("years",{})
        if any(str(y) not in years for y in fiscal):
            eligible.append(code)
    selected=eligible[:groups*MAX_CORPS]
    cohorts=[selected[i:i+MAX_CORPS] for i in range(0,len(selected),MAX_CORPS)]
    return asof,members,retained,fiscal,cohorts


def _mapping(blob, requested_codes):
    # DART's full current+historic corporation master grows independently of
    # today's ~2,497 KRX listed names. Do not confuse a growing XML source
    # file with a valid stock market census. Still cap ZIP and XML memory.
    if type(blob) is not bytes or not blob:
        raise BulkHistoryError("dart_company_master_not_zip_bytes")
    if len(blob)>30_000_000:
        raise BulkHistoryError("dart_company_master_zip_over_30mb")
    try:
        with ZipFile(BytesIO(blob)) as z:
            if "CORPCODE.xml" not in z.namelist():
                raise BulkHistoryError("dart_company_master_missing_xml")
            with z.open("CORPCODE.xml") as fd:
                body=fd.read(80_000_001)
        if len(body)>80_000_000:
            raise BulkHistoryError("dart_company_master_xml_over_80mb")
        xml=ET.fromstring(body)
    except BulkHistoryError:
        raise
    except Exception:
        raise BulkHistoryError("dart_company_master_not_parseable_zip_xml") from None
    mapped={}
    for item in xml.findall("list"):
        code=item.findtext("stock_code")
        corp=item.findtext("corp_code")
        if code in requested_codes:
            if not CORP.fullmatch(corp or ""):
                raise BulkHistoryError("dart_company_master_bad_corp_identity")
            if code in mapped and mapped[code]!=corp:
                raise BulkHistoryError("dart_company_master_duplicate_stock_identity")
            mapped[code]=corp
    if not mapped:
        raise BulkHistoryError("dart_company_master_no_listed_matches")
    return mapped


def fetch_group(session,key,asof,years,code_cohort,corp_map,*,
                prior_years=None,on_year=None,max_requests=None,
                deadline=None):
    """Query ≤25 actual issuers per annual request; checkpoint every batch.

    The official 100-company API allows *at most* 100 names, but repeated
    real-world 100-name large filings had dropped the HTTP connection.
    Smaller groups bound response volume and preserve independently valid
    company-years without replaying previously saved years.
    """
    prior_years=prior_years or {}
    found={c:dict(prior_years.get(c,{})) for c in code_cohort}
    available=[c for c in code_cohort if c in corp_map]
    requests_made=0
    for year in years:
        needed=[c for c in available if str(year) not in found[c]]
        for offset in range(0,len(needed),25):
            if max_requests is not None and requests_made>=max_requests:
                raise BulkHistoryError("dart_bulk_safe_source_call_budget_reached")
            if deadline is not None and time.monotonic()>=deadline:
                raise BulkHistoryError("dart_bulk_safe_elapsed_time_budget_reached")
            batch=needed[offset:offset+25]
            lookup={corp_map[c]:c for c in batch}
            try:
                response=session.get(
                    "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json",
                    params={"crtfc_key":key,"corp_code":",".join(lookup),
                            "bsns_year":str(year),"reprt_code":"11011"},
                    timeout=55)
                requests_made+=1
                response.raise_for_status()
                payload=response.json()
            except Exception as exc:
                kind=type(exc).__name__
                reason=("dart_bulk_http_timeout"
                        if kind in ("ReadTimeout","ConnectTimeout","Timeout")
                        else "dart_bulk_http_connection_failed"
                        if kind=="ConnectionError" else "dart_bulk_http_status_failed"
                        if kind=="HTTPError" else "dart_bulk_invalid_json"
                        if kind=="JSONDecodeError" else "dart_bulk_transport_unavailable")
                raise BulkHistoryError(reason) from None
            if not isinstance(payload,dict):
                raise BulkHistoryError("dart_bulk_unexpected_response")
            status=payload.get("status")
            if status=="013":
                rows=[]
            elif status=="000":
                rows=payload.get("list")
                if not isinstance(rows,list) or not rows:
                    raise BulkHistoryError("dart_bulk_empty_success_response")
            else:
                # A status such as 020 (quota) must pause the run.
                raise BulkHistoryError("dart_bulk_provider_stop_or_schema")
            diagnostics={}
            try:
                extracted=_normalize_year(
                    rows,year,asof,set(batch),lookup,diagnostics=diagnostics)
            finally:
                print(json.dumps({
                    "dart_bulk_fiscal_year":year,
                    "batch_requested_companies":len(batch),
                    "source_validation":diagnostics,
                    "raw_dart_rows_logged":False,
                },sort_keys=True),flush=True)
            for code in batch:
                found[code][str(year)]=extracted.get(code,{})
            if on_year:
                on_year({c:{str(year):found[c][str(year)]}
                         for c in batch},year)
        # Keep 'no corporation mapping' distinct from real observed accounts;
        # annual observations are never invented, and unmatched firms remain
        # an empty researched source for any year in this completed cohort.
        for code in code_cohort:
            if code not in available:
                found[code].setdefault(str(year),{})
    return found,requests_made


def combine(market,catalog,prior,results,*,groups=MAX_GROUPS):
    asof,members,retained,fiscal,cohorts=plan(
        market,catalog,prior=prior,groups=groups)
    expected=set(code for cohort in cohorts for code in cohort)
    if type(results) is not dict or set(results)!=expected:
        raise BulkHistoryError("partial_or_unexpected_bulk_cohort")
    for code,years in results.items():
        if type(years) is not dict or set(years)!=set(map(str,fiscal)):
            raise BulkHistoryError("missing_bulk_fiscal_year")
        for year,scope in years.items():
            if type(scope) is not dict or not set(scope)<= {"CFS","OFS"}:
                raise BulkHistoryError("bad_bulk_scope")
        previous=retained.get(code,{"years":{}})
        new_years=dict(previous["years"])
        new_years.update(years)
        retained[code]={"years":new_years}
    completed=sum(all(str(y) in rec["years"] for y in fiscal)
                  for rec in retained.values())
    four_income=0
    for code in retained:
        if _best_income_window(retained[code],fiscal) is not None:
            four_income+=1
    return {
       "schema":SCHEMA,"asof":asof,"listed_total":len(members),
       "fiscal_years":fiscal,
       "attempted_company_count":completed,
       "four_year_income_observed_count":four_income,
       "unattempted_company_count":len(members)-completed,
       "last_run_new_companies":len(expected),
       "historical_PIT_source_certified":False,
       "four_year_complete_financial15_certified":False,
       "cfo_capex_and_net_debt_from_bulk_api_available":False,
       "source":"Official OpenDART multi-company BS/IS plus already observed annual book",
       "raw_source_rows_published":False,
       "records":{code:retained[code] for code in sorted(retained)}
    }


def verified_current_annual_book_year(market,catalog,annual_book,prior):
    """Reuse only same-asof, real published DART FY receipts and exact accounts.

    This is already collected official derived evidence, not a fabricated
    multi-company statement or proof of historic PIT. It contains only fields
    truly observed in the annual book; notably, assets remain unknown.
    """
    asof,members=_inputs(market,catalog)
    if (type(annual_book) is not dict or annual_book.get("asof")!=asof or
            annual_book.get("market_listed_total")!=len(members) or
            type(annual_book.get("records")) is not list):
        raise BulkHistoryError("incompatible_observed_annual_seed_asof")
    current_year=_day(asof).year-1
    retained=_validate_prior(prior,asof,members)
    seeded={}
    seen=set()
    for row in annual_book["records"]:
        if type(row) is not dict:
            raise BulkHistoryError("invalid_observed_annual_seed_row")
        code=row.get("code")
        if code not in members or row.get("status")!="observed":
            continue
        if code in seen:
            raise BulkHistoryError("duplicate_observed_annual_seed_issuer")
        seen.add(code)
        annual=row.get("annual")
        if type(annual) is not dict:
            continue
        scope=annual.get("fs_div")
        v=annual.get("values")
        if (annual.get("year")!=current_year or scope not in ("CFS","OFS") or
                annual.get("currency")!="KRW" or type(v) is not dict or
                not _valid_receipt(annual.get("receipt_id"),current_year,_day(asof))):
            continue
        revenue=v.get("revenue")
        income=v.get("operating_income")
        if (type(revenue) is not int or not 0<revenue<=10**18 or
                type(income) is not int or abs(income)>10**18):
            continue
        if str(current_year) in retained.get(code,{}).get("years",{}):
            continue
        values={"revenue":revenue,"operating_income":income,"assets":None,
                "liabilities":None,"equity":None}
        for key in ("liabilities","equity"):
            entry=v.get(key)
            if type(entry) is int and abs(entry)<=10**18:
                values[key]=entry
        seeded[code]={str(current_year):{scope:{
            "receipt_id":annual["receipt_id"],
            "values":values,"ambiguous":[],
            "source_origin":"previously_observed_dart_annual_book",
        }}}
    return seeded


def partial_year_checkpoint(market,catalog,prior,updates):
    """Persist source-confirmed company/years independently of four-year rank."""
    asof,members=_inputs(market,catalog)
    retained=_validate_prior(prior,asof,members)
    fiscal=list(range(_day(asof).year-YEARS_BACK,_day(asof).year))
    expected={str(y) for y in fiscal}
    if type(updates) is not dict or not updates or not set(updates)<=set(members):
        raise BulkHistoryError("invalid_partial_fiscal_issuer")
    for code,years in updates.items():
        if (type(years) is not dict or not years or
            not set(years)<=expected or any(
                type(scopes) is not dict or not set(scopes)<={"CFS","OFS"}
                for scopes in years.values())):
            raise BulkHistoryError("invalid_partial_fiscal_year")
        value=dict(retained.get(code,{"years":{}})["years"])
        for year,scopes in years.items():
            if year in value and value[year]!=scopes:
                raise BulkHistoryError("conflicting_saved_year_evidence")
            value[year]=scopes
        retained[code]={"years":value}
    completed=sum(all(str(y) in r["years"] for y in fiscal)
                  for r in retained.values())
    income=sum(_best_income_window(r,fiscal) is not None
               for r in retained.values())
    return {
        "schema":SCHEMA,"asof":asof,"listed_total":len(members),
        "fiscal_years":fiscal,
        "attempted_company_count":completed,
        "four_year_income_observed_count":income,
        "unattempted_company_count":len(members)-completed,
        "last_run_new_companies":len(updates),
        "completed_source_company_years":sum(len(v["years"]) for v in retained.values()),
        "observed_nonempty_company_years":sum(
            sum(bool(v) for v in row["years"].values())
            for row in retained.values()),
        "historical_PIT_source_certified":False,
        "four_year_complete_financial15_certified":False,
        "cfo_capex_and_net_debt_from_bulk_api_available":False,
        "source":"Official OpenDART multi-company BS/IS plus already observed annual book",
        "raw_source_rows_published":False,
        "records":{code:retained[code] for code in sorted(retained)},
    }


def _best_income_window(record, fiscal):
    if not record or any(str(y) not in record["years"] for y in fiscal):
        return None
    years=record["years"]
    for scope in ("CFS","OFS"):
        vals=[years[str(y)].get(scope,{}).get("values",{}) for y in fiscal]
        if all(type(v.get("revenue")) is int and v["revenue"]>0 and
               type(v.get("operating_income")) is int for v in vals):
            cagr=100*((vals[-1]["revenue"]/vals[0]["revenue"])**(1/3)-1)
            margin=sum(100*v["operating_income"]/v["revenue"] for v in vals[-3:])/3
            if all(math.isfinite(x) and abs(x)<1e7 for x in (cagr,margin)):
                return {"accounting_scope":scope,
                        "revenue_cagr_3y_pct":round(cagr,6),
                        "operating_margin_3y_pct":round(margin,6)}
    return None


def _midrank(value, ordered, direction):
    left=bisect_left(ordered,value);right=bisect_right(ordered,value)
    pct=(left+right)/(2*len(ordered))
    return pct if direction>0 else 1-pct


def research_parts(data,catalog,book,*,minimum_peers=30):
    """Produce observed 4+4 financial and 5+5 valuation research points.

    Only observed, date-linked inputs with >=30 same-market peers contribute.
    Missing FCF, flow, PIT or other factors stay unavailable, never zero.
    """
    if (type(data) is not dict or data.get("schema")!=SCHEMA or
        type(minimum_peers) is not int or minimum_peers<8 or
        catalog.get("date")!=data.get("asof") or
        book.get("asof")!=data.get("asof") or
        catalog.get("listed_total")!=data.get("listed_total") or
        book.get("market_listed_total")!=data.get("listed_total")):
        raise BulkHistoryError("incompatible_bulk_scoring_asof")
    fiscal=data["fiscal_years"]
    market={row["code"]:row["market"] for row in catalog["rows"]}
    selected={}
    for code,record in data["records"].items():
        if code in market:
            selected[code]=_best_income_window(record,fiscal)
    book_annual={r["code"]:r for r in book["records"]}
    pbr={}
    earnings_yield={}
    cap_by_code={r["code"]:r.get("reference_market_cap_eok")
                 for r in catalog["rows"]}
    for o in book.get("observations",[]):
        code=o.get("code");v=o.get("pbr_simple_annual_reference")
        a=book_annual.get(code,{}).get("annual",{})
        if (code in market and type(v) in (int,float) and math.isfinite(v)
            and 0<v<1000 and o.get("market_cap_reference_day")==data["asof"]
            and o.get("receipt_id")==a.get("receipt_id") and
            o.get("fs_div")==a.get("fs_div") and o.get("year")==a.get("year")
            and o.get("official_exchange_pbr_verified") is False
            and code not in pbr):
            pbr[code]=float(v)
            profit=a.get("values",{}).get("owners_profit")
            cap=cap_by_code.get(code)
            if (type(profit) in (int,float) and math.isfinite(profit)
                and type(cap) in (int,float) and math.isfinite(cap)
                and 0<cap<1e12 and abs(profit)<1e19
                and a.get("year")==fiscal[-1]
                and a.get("currency")=="KRW"):
                earnings_yield[code]=round(100*profit/(cap*1e8),6)
    series={}
    for m in ("KOSPI","KOSDAQ"):
        members=[c for c in market if market[c]==m]
        series[m]={k:sorted(
            (pbr.get(c) if k=="pbr" else earnings_yield.get(c) if
             k=="earnings_yield_pct" else (selected.get(c) or {}).get(k))
            for c in members if
            (pbr.get(c) if k=="pbr" else earnings_yield.get(c) if
             k=="earnings_yield_pct" else (selected.get(c) or {}).get(k)) is not None
        ) for k in ("revenue_cagr_3y_pct","operating_margin_3y_pct",
                    "pbr","earnings_yield_pct")}
    output=[]
    count_fin=0;count_pb=0;count_earnings=0
    for code in sorted(market):
        src=selected.get(code) or {}
        benchmark=series[market[code]]
        pts={};available=0
        for metric,weight,direction in (("revenue_cagr_3y_pct",4,1),
                                        ("operating_margin_3y_pct",4,1),
                                        ("pbr",5,-1),
                                        ("earnings_yield_pct",5,1)):
            v=(pbr.get(code) if metric=="pbr" else
               earnings_yield.get(code) if metric=="earnings_yield_pct"
               else src.get(metric))
            peer=benchmark[metric]
            earned=round(weight*_midrank(v,peer,direction),6) if (
                v is not None and len(peer)>=minimum_peers) else None
            pts[metric]={"observed_value":v,"candidate_points":earned,
                         "max_points":weight if earned is not None else 0,
                         "peer_count":len(peer)}
            available+=weight if earned is not None else 0
        if pts["revenue_cagr_3y_pct"]["candidate_points"] is not None:
            count_fin+=1
        if pts["pbr"]["candidate_points"] is not None:
            count_pb+=1
        if pts["earnings_yield_pct"]["candidate_points"] is not None:
            count_earnings+=1
        if available:
            output.append({
                "code":code,"market":market[code],
                "asof":data["asof"],
                "source_fiscal_years":fiscal,
                "unverified_components":pts,
                "financial_candidate_points":(
                    round(sum(pts[k]["candidate_points"] or 0 for k in (
                        "revenue_cagr_3y_pct","operating_margin_3y_pct")),6)
                    if any(pts[k]["candidate_points"] is not None for k in (
                        "revenue_cagr_3y_pct","operating_margin_3y_pct")) else None),
                "financial_available_max":sum(pts[k]["max_points"] for k in (
                    "revenue_cagr_3y_pct","operating_margin_3y_pct")),
                "valuation_candidate_points":(
                    round(sum(pts[k]["candidate_points"] or 0 for k in
                              ("pbr","earnings_yield_pct")),6)
                    if any(pts[k]["candidate_points"] is not None
                           for k in ("pbr","earnings_yield_pct")) else None),
                "valuation_available_max":sum(pts[k]["max_points"] for k in
                                            ("pbr","earnings_yield_pct")),
                "combined_provisional_points":round(
                    sum(z["candidate_points"] or 0 for z in pts.values()),6),
                "combined_available_max":available,
                "confirmed_v3_score":None,
                "scoring_certified":False,
            })
    return {
        "schema":SCORE_SCHEMA,"asof":data["asof"],
        "listed_total":len(market),
        "four_year_income_observed":data["four_year_income_observed_count"],
        "companies_with_computable_4y_income_scores":count_fin,
        "companies_with_computable_pb_scores":count_pb,
        "companies_with_computable_earnings_yield_scores":count_earnings,
        "model_financial_full_points":15,"model_valuation_full_points":15,
        "financial_partial_max_possible":8,
        "valuation_partial_max_possible":10,
        "other_four_pillar_points_claimed":0,
        "coverage_peer_minimum":minimum_peers,
        "historical_PIT_certified":False,
        "fully_verified_six_pillar_count":0,
        "all_scores_provisional_unverified":True,
        "rows":output,
    }


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,
                       separators=(",",":"),allow_nan=False)
    if len(encoded.encode("utf8"))>10_000_000:
        raise BulkHistoryError("bulk_derived_archive_too_large")
    tmp=path.with_suffix(".tmp")
    tmp.write_text(encoded+"\n",encoding="utf8")
    tmp.replace(path)


def collect(market,catalog,prior,*,key,session,groups=MAX_GROUPS,
            on_progress=None,annual_book=None):
    # Enrich from ALREADY VERIFIED 2025 DART annual filings before any
    # additional source request. Independently persist this observed evidence.
    if annual_book is not None:
        reuse=verified_current_annual_book_year(
            market,catalog,annual_book,prior)
        if reuse:
            prior=partial_year_checkpoint(market,catalog,prior,reuse)
            if on_progress:
                on_progress(prior,_day(prior["asof"]).year-1)
    asof,members,retained,years,cohorts=plan(
        market,catalog,prior=prior,groups=groups)
    if not cohorts:
        return combine(market,catalog,prior,{},groups=groups),0
    if type(key) is not str or len(key)<20:
        raise BulkHistoryError("missing_dart_bulk_key")
    requested={code for group in cohorts for code in group}
    try:
        resp=session.get("https://opendart.fss.or.kr/api/corpCode.xml",
                         params={"crtfc_key":key},timeout=55)
        resp.raise_for_status()
        mapping=_mapping(resp.content,requested)
    except BulkHistoryError:
        raise
    except Exception:
        raise BulkHistoryError("dart_company_mapping_failure") from None
    complete={}
    calls=1
    deadline=time.monotonic()+MAX_SOURCE_RUN_SECONDS
    checkpoint=prior
    def save_year(updates,year):
        nonlocal checkpoint
        if on_progress:
            checkpoint=partial_year_checkpoint(
                market,catalog,checkpoint,updates)
            on_progress(checkpoint,year)
    for cohort in cohorts:
        row,n=fetch_group(
            session,key,asof,years,cohort,mapping,
            prior_years={c:retained.get(c,{}).get("years",{}) for c in cohort},
            on_year=save_year if on_progress else None,
            max_requests=MAX_SOURCE_FISCAL_CALLS-(calls-1),
            deadline=deadline)
        complete.update(row)
        calls+=n
    return combine(market,catalog,prior,complete,groups=groups),calls


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market",default="docs/data.json")
    p.add_argument("--catalog",default="docs/v22-market-catalog.json")
    p.add_argument("--annual",default="docs/v3-dart-book-reference.json")
    p.add_argument("--state",default="docs/v3-dart-bulk-history.json")
    p.add_argument("--scores",default="docs/v3-dart-bulk-provisional.json")
    p.add_argument("--groups",type=int,default=3)
    p.add_argument("--mode",choices=("dry-run","live","verify"),default="dry-run")
    args=p.parse_args(argv)
    market=json.loads(Path(args.market).read_text(encoding="utf8"))
    catalog=json.loads(Path(args.catalog).read_text(encoding="utf8"))
    annual=json.loads(Path(args.annual).read_text(encoding="utf8"))
    state=Path(args.state)
    prior=json.loads(state.read_text(encoding="utf8")) if state.is_file() else None
    asof,members,_,years,cohorts=plan(market,catalog,prior=prior,groups=args.groups)
    if args.mode=="verify":
        if (prior is None or prior["asof"]!=asof or
            prior["listed_total"]!=len(members) or
            prior["fiscal_years"]!=years):
            raise BulkHistoryError("rebase_bulk_market_changed")
        score=json.loads(Path(args.scores).read_text(encoding="utf8"))
        if (score.get("asof")!=asof or score.get("listed_total")!=len(members)):
            raise BulkHistoryError("rebase_bulk_scoring_invalid")
        # Recalculate all provisional parts from the post-rebase DART book.
        if research_parts(prior,catalog,annual)!=score:
            raise BulkHistoryError("rebase_bulk_source_or_scores_changed")
        print("bulk_post_rebase_provenance_verified")
        return
    if args.mode=="dry-run":
        print(json.dumps({"asof":asof,"listed":len(members),
             "unprocessed_today":sum(map(len,cohorts)),
             "next_groups":len(cohorts),"years":years,
             "API_calls":0,"saved_data":False}))
        return
    import os, requests
    key=os.getenv("DART_API_KEY","")
    checkpoints=[]
    def save_verified_year(archive,year):
        research=research_parts(archive,catalog,annual)
        write_json(args.state,archive)
        write_json(args.scores,research)
        checkpoints.append(year)
    try:
        archive,calls=collect(
            market,catalog,prior,key=key,session=requests.Session(),
            groups=args.groups,on_progress=save_verified_year,
            annual_book=annual)
    except BulkHistoryError as err:
        if not checkpoints:
            raise
        # A later-year source outage must NOT discard independently valid
        # historical years already collected. Resume them on the next job.
        print(json.dumps({
            "status":"partial_real_dart_fiscal_history_saved",
            "fiscal_year_checkpoints_written":len(checkpoints),
            "last_saved_fiscal_year":checkpoints[-1],
            "subsequent_source_failure":str(err),
            "raw_api_data_logged":False,
            "full_v3_ready":False,
        },sort_keys=True),flush=True)
        return
    research=research_parts(archive,catalog,annual)
    write_json(args.state,archive)
    write_json(args.scores,research)
    print(json.dumps({"asof":asof,"listed":len(members),
        "completed":archive["attempted_company_count"],
        "complete_income_4y":archive["four_year_income_observed_count"],
        "financial_partial_scored":research["companies_with_computable_4y_income_scores"],
        "pbr_partial_scored":research["companies_with_computable_pb_scores"],
        "earnings_yield_partial_scored":research["companies_with_computable_earnings_yield_scores"],
        "source_calls":calls,"full_v3_ready":False},sort_keys=True))


if __name__=="__main__":
    main()
