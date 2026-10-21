"""Observed CF/CAPEX -> research-only FCF/assets and FCF yield peer candidates.

Nothing here promotes 6-pillar scores: historic filing PIT, corporate
actions, accounting revisions and other areas are independently missing.
The same issuer/year/scope and receipt MUST match existing annual BS/IS.
Zero FCF or zero CAPEX is not substituted for missing company accounts.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from v3_dart_cashflow_enrichment import (
    SCHEMA as CASH_SCHEMA, verify_state, fingerprint,
    asof_members, YEARS,
)
from v3_dart_bulk_history import _midrank, write_json

SCHEMA="v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1"

class FCFPreviewError(ValueError):
    """Fixed, non-sensitive source-evidence failure labels."""


def real_features(catalog,bulk,cash):
    asof=catalog["date"]
    caps={v["code"]:v.get("reference_market_cap_eok")
          for v in catalog["rows"]}
    out={}
    for code,item in cash["records"].items():
        major=bulk["records"].get(code,{})
        scope=item["scope"]
        source_major=major.get("years",{})
        three=[]
        all_three=True
        for y in YEARS:
            year=str(y)
            record=item["years"].get(year)
            stmt=source_major.get(year,{}).get(scope,{})
            if not isinstance(record,dict) or not isinstance(stmt,dict):
                all_three=False
                continue
            if (record.get("annual_receipt_id") is None or
                record.get("major_annual_receipt_matched") is not True or
                record["annual_receipt_id"]!=stmt.get("receipt_id")):
                all_three=False
                continue
            fcf=record.get("free_cashflow_krw")
            asset=stmt.get("values",{}).get("assets")
            if (type(fcf) is not int or type(asset) is not int or asset<=0):
                all_three=False
                continue
            three.append(100*fcf/asset)
        ratio=round(sum(three)/3,6) if all_three and len(three)==3 else None
        latest=item["years"].get(str(YEARS[-1]),{})
        major_latest=source_major.get(str(YEARS[-1]),{}).get(scope,{})
        matching=(latest.get("major_annual_receipt_matched") is True and
                  latest.get("annual_receipt_id") is not None and
                  latest.get("annual_receipt_id")==major_latest.get("receipt_id"))
        last_fcf=latest.get("free_cashflow_krw") if matching else None
        cap=caps.get(code)
        val=round(100*last_fcf/(cap*1e8),6) if (
            type(last_fcf) is int and type(cap) in (int,float)
            and math.isfinite(cap) and 0<cap<1e12) else None
        if ratio is not None and (not math.isfinite(ratio) or abs(ratio)>1e6):
            ratio=None
        if val is not None and (not math.isfinite(val) or abs(val)>1e6):
            val=None
        if ratio is not None or val is not None:
            out[code]={
                "financial_fcf_assets_3y_pct":ratio,
                "valuation_fcf_yield_pct":val,
            }
    return out


def score_preview(market,catalog,major,cash,minimum_peers=30):
    # Strictly same source-date company universe; old-source cashflow rows
    # are rechecked receipt-by-receipt against the current major archive.
    if type(minimum_peers) is not int or minimum_peers<8:
        raise FCFPreviewError("invalid_fcf_peer_minimum")
    asof,members=asof_members(market,catalog,major)
    verify_state(cash,market,catalog,major,strict_fingerprint=False)
    if cash.get("source_fiscal_years")!=list(YEARS):
        raise FCFPreviewError("wrong_fcf_source_year_window")
    values=real_features(catalog,major,cash)
    peaks={}
    for m in ("KOSPI","KOSDAQ"):
        peaks[m]={
            field:sorted(v[field] for c,v in values.items()
                          if members[c]["market"]==m and v[field] is not None)
            for field in ("financial_fcf_assets_3y_pct","valuation_fcf_yield_pct")
        }
    points=[]
    financial=valuation=raw_fin=raw_val=0
    for code,metrics in sorted(values.items()):
        m=members[code]["market"]
        fin=metrics["financial_fcf_assets_3y_pct"]
        val=metrics["valuation_fcf_yield_pct"]
        raw_fin+=int(fin is not None)
        raw_val+=int(val is not None)
        fin_peers=peaks[m]["financial_fcf_assets_3y_pct"]
        val_peers=peaks[m]["valuation_fcf_yield_pct"]
        fin_pt=round(4*_midrank(fin,fin_peers,1),6) if (
            fin is not None and len(fin_peers)>=minimum_peers) else None
        val_pt=round(5*_midrank(val,val_peers,1),6) if (
            val is not None and len(val_peers)>=minimum_peers) else None
        financial+=int(fin_pt is not None)
        valuation+=int(val_pt is not None)
        points.append({
            "code":code,"market":m,"asof":asof,
            "raw_observed_financial_fcf_assets_3y_pct":fin,
            "raw_observed_valuation_fcf_yield_pct":val,
            "financial_fcf_assets_candidate_points":fin_pt,
            "financial_fcf_assets_available_max":4 if fin_pt is not None else 0,
            "valuation_fcf_yield_candidate_points":val_pt,
            "valuation_fcf_yield_available_max":5 if val_pt is not None else 0,
            "financial_fcf_peer_count":len(fin_peers),
            "valuation_fcf_peer_count":len(val_peers),
            "strict_receipt_scope_matched":True,
            "historical_PIT_certified":False,
            "scoring_certified":False,
        })
    return {
        "schema":SCHEMA,"asof":asof,"listed_total":len(members),
        "cashflow_snapshot_sha256":fingerprint(cash),
        "major_history_sha256":fingerprint(major),
        "market_catalog_sha256":fingerprint(catalog),
        "minimum_same_market_peers":minimum_peers,
        "real_fcf_assets_three_year_raw_issuer_count":raw_fin,
        "real_latest_fcf_yield_raw_issuer_count":raw_val,
        "financial_fcf4_research_candidate_issuer_count":financial,
        "valuation_fcf5_research_candidate_issuer_count":valuation,
        "financial_fcf_weight_available_max":4,
        "valuation_fcf_weight_available_max":5,
        "source_same_filing_scope_receipt_checked":True,
        "raw_source_rows_stored":False,
        "hist_PIT_verified":False,
        "four_year_financial15_complete":False,
        "valuation15_complete":False,
        "six_pillar_confirmed_100pt":None,
        "joined_to_main_v3_ranking":False,
        "buy_recommendations_authorized":False,
        "notes":"No historical prices/PIT, full-CF source-only FCF; no CAPEX zeros or issuer substitution",
        "rows":points,
    }


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market",default="docs/data.json")
    ap.add_argument("--catalog",default="docs/v22-market-catalog.json")
    ap.add_argument("--major",default="docs/v3-dart-bulk-history.json")
    ap.add_argument("--cashflow",default="docs/v3-dart-cashflow-reference.json")
    ap.add_argument("--out",default="docs/v3-dart-fcf-research-preview.json")
    ap.add_argument("--mode",choices=("write","verify"),default="write")
    args=ap.parse_args(argv)
    def read(p):
        return json.loads(Path(p).read_text(encoding="utf8"))
    out=score_preview(read(args.market),read(args.catalog),
                      read(args.major),read(args.cashflow))
    path=Path(args.out)
    if args.mode=="write":
        write_json(path,out)
    elif not path.is_file() or read(path)!=out:
        raise FCFPreviewError("cashflow_research_preview_stale_after_rebase")
    print(json.dumps({
        "status":"source_observed_unverified_fcf_research",
        "fcf_assets_3y_raw":out["real_fcf_assets_three_year_raw_issuer_count"],
        "fcf_yield_2025_raw":out["real_latest_fcf_yield_raw_issuer_count"],
        "financial4_peer_scored":out["financial_fcf4_research_candidate_issuer_count"],
        "valuation5_peer_scored":out["valuation_fcf5_research_candidate_issuer_count"],
        "full_six_pillar":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
