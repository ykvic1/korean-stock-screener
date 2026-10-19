"""Current-listed KOSPI/KOSDAQ catalog for hands-on v3 research beta.

This is a derived *current listing* index, not a delisted historical universe,
not KRX licensed vendor raw, and not a verified v3 scoring feed.

Use existing all-market listing identities and *published* per-stock v2.2
observations; keep all listed symbols searchable even when price history is
unavailable. PBR and flow are useful reference evidence when observed, never
required or fabricated. Do not copy raw OHLCV.
"""
from __future__ import annotations
from collections import Counter
import json
import math
from pathlib import Path
import re
from v22_full_universe_audit import audit_published_universe

class ActiveCatalogError(ValueError):
    pass

def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)

def _amount(value):
    return round(float(value),2) if _finite(value) and value>=0 else None

def _six_code(value):
    return type(value) is str and re.fullmatch(r"\d{6}",value) is not None

def build_active_catalog(snapshot:dict, listing:dict)->dict:
    audit=audit_published_universe(snapshot)
    if not isinstance(listing,dict) or len(listing)!=audit["market_listing_size"]:
        raise ActiveCatalogError("catalog_listing_count_mismatch")
    priced={r["code"]:r for r in snapshot["stocks"]}
    price_errors={e[0] for e in snapshot["collection_errors"] if e[1]=="가격이력"}
    insufficient={e[0] for e in snapshot["history_exclusions"]}
    known=set(priced)|price_errors|insufficient
    listing_codes=set(listing)
    if any(not _six_code(code) for code in listing_codes):
        raise ActiveCatalogError("catalog_invalid_code")
    if not known<=listing_codes:
        raise ActiveCatalogError("catalog_missing_known_code")
    unknown=listing_codes-known
    if len(unknown)!=audit["unattempted_batch_count"]:
        raise ActiveCatalogError("catalog_unmatched_missing_part_count")
    if len(set(priced)&price_errors|set(priced)&insufficient|price_errors&insufficient):
        raise ActiveCatalogError("catalog_duplicate_price_outcome")
    rows=[]
    status_counts=Counter()
    pbr_observed=0;flow_observed=0
    for code in sorted(listing_codes):
        item=listing[code]
        if not isinstance(item,dict) or item.get("code")!=code:
            raise ActiveCatalogError("catalog_listing_identity_mismatch")
        name=item.get("name")
        market=item.get("market")
        if not isinstance(name,str) or not name.strip() or len(name)>120 or market not in ("KOSPI","KOSDAQ"):
            raise ActiveCatalogError("catalog_listing_identity_invalid")
        existing=priced.get(code)
        if existing and (existing["market"]!=market or existing.get("name")!=name):
            raise ActiveCatalogError("catalog_derived_identity_mismatch")
        if existing:
            status="priced"
        elif code in price_errors:
            status="price_source_or_validation_error"
        elif code in insufficient:
            status="insufficient_history"
        else:
            status="unattempted_batch"
        status_counts[status]+=1
        # The front end uses the independently published current-price analysis
        # only, never the unvalidated listing's standalone quote.
        raw_pbr=existing.get("pbr") if existing else item.get("pbr")
        pbr=round(float(raw_pbr),3) if _finite(raw_pbr) and 0<raw_pbr<100 else None
        if pbr is not None: pbr_observed+=1
        flow=None
        if existing and existing.get("flows_confirmed") is True:
            foreign=existing.get("foreign_shares")
            inst=existing.get("inst_shares")
            asof=existing.get("flow_end_date")
            observations=existing.get("flow_observations")
            if (type(foreign) is int and type(inst) is int and
                    type(observations) is int and observations>0 and
                    type(asof) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}",asof) and
                    asof<=snapshot["date"]):
                flow={"foreign_20d_shares_observed":foreign,
                      "institution_20d_shares_observed":inst,
                      "observations":observations,
                      "start":existing.get("flow_start_date"),
                      "end":asof,
                      "source":existing.get("flow_source") if
                          isinstance(existing.get("flow_source"),str) else "v2_source_unidentified",
                      "independently_verified":False}
                flow_observed+=1
        rows.append({
            "code":code, "name":name, "market":market,
            "sector": existing.get("sector") if existing and existing.get("sector") else item.get("sector"),
            "price_history_status":status,
            "published_price":existing["price"] if existing else None,
            "reference_market_cap_eok":_amount(item.get("market_cap_eok")),
            "reference_trading_value_eok":_amount(item.get("trading_eok")),
            "pbr_observed":pbr,
            "pbr_source": (item.get("valuation_source") or "v2_observed_unknown_provider") if pbr is not None else None,
            "flow_reference":flow,
            "fundamental_revenue":None,
            "fundamental_operating_profit":None,
            "v3_confirmed_score":None,
        })
    if (status_counts["priced"]!=audit["priced_current_session_count"] or
            sum(status_counts.values())!=audit["market_listing_size"] or
            flow_observed!=audit["flow_confirmed_count"] or
            pbr_observed < audit["pbr_observed_count"]):
        raise ActiveCatalogError("catalog_outcome_or_evidence_counts_mismatch")
    return {
        "schema":"v22-current-listed-catalog-0.1",
        "date":snapshot["date"],
        "scope":"currently_listed_KOSPI_KOSDAQ_ordinary_shares_only",
        "listed_total":len(rows),
        "priced_count":status_counts["priced"],
        "status_counts":{k:status_counts[k] for k in
                         ("priced","price_source_or_validation_error",
                          "insufficient_history","unattempted_batch")},
        "pbr_observed_count":pbr_observed,
        "flow_reference_count":flow_observed,
        "data_level":"reference_observations_not_independent_facts",
        "historical_delisted_stocks_included":False,
        "historical_backtest_has_survivorship_bias":True,
        "official_v3_six_pillar_score_ready":False,
        "rows":rows,
    }

def write_active_catalog(snapshot:dict, listing:dict, path:Path)->dict:
    result=build_active_catalog(snapshot,listing)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    payload=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    if len(payload.encode("utf-8"))>8_000_000:
        raise ActiveCatalogError("catalog_result_too_large")
    tmp.write_text(payload+"\n",encoding="utf-8")
    tmp.replace(path)
    return {k:v for k,v in result.items() if k not in ("rows",)}
