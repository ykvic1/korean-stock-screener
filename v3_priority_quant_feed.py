"""Small derived historical quant feed for the displayed recommended stocks only.

Build from actually fetched source-day price research. Does not copy raw OHLCV,
reuse other dates, synthesize incomplete values, or alter the main 120-stock
research and price-screen data.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from v3_ranked_quant_coverage import (CoverageError,audit_recommended_quant,
                                       PERIODS,num,load)


def build_priority_feed(market:dict,recommendations:dict,quant:dict)->dict:
    audit=audit_recommended_quant(market,recommendations,quant)
    if not audit["quant_study_same_date"]:
        raise CoverageError("stale_priority_study")
    if audit["candidate_status_counts"]["awaiting_fresh_study"]:
        raise CoverageError("missing_featured_symbol_in_live_study")
    if (not isinstance(quant.get("requested_symbols"),int) or
        quant["requested_symbols"]<20 or quant["requested_symbols"]>120 or
        not isinstance(quant.get("available_symbols"),int) or
        quant["available_symbols"]<20):
        raise CoverageError("insufficient_live_peer_cohort")
    if (quant.get("bias_audit",{}).get("prediction_validated") is True):
        raise CoverageError("unexpected_future_prediction_claim")
    qmap={q["code"]:q for q in quant["stocks"]
          if isinstance(q,dict) and isinstance(q.get("code"),str)}
    results=[]
    for c in audit["stocks"]:
        q=qmap[c["code"]]
        if q.get("status")=="exploratory":
            if q.get("reference_price_date")!=audit["asof"]:
                raise CoverageError("research_price_not_asof")
            studies={}
            for period in PERIODS:
                p=q.get("periods",{}).get(period,{})
                if not isinstance(p,dict):
                    continue
                # Only observed descriptive statistics with measured samples.
                numeric={}
                for name in ("sample_n","symbol_n","quarter_n",
                             "mean_net_return_pct","stop_first_pct",
                             "max_adverse_p75_pct","target_first_pct","up_pct"):
                    v=p.get(name)
                    numeric[name]=v if num(v) else None
                studies[period]={
                    "status":p.get("status") if p.get("status") in
                              ("exploratory","insufficient") else "insufficient",
                    **numeric,
                    "chronological_holdout":{
                        "test_mean_net_return_pct":None,
                    },
                }
        else:
            studies={}
        results.append({
            "code":c["code"],
            "name":c["name"],
            "status":q.get("status") if q.get("status") in
                     ("exploratory","insufficient") else "insufficient",
            "reference_price_date":audit["asof"],
            "periods":studies,
            "peer_cohort_requested":quant["requested_symbols"],
            "peer_cohort_valid":quant["available_symbols"],
        })
    return {
        "schema":"v3-targeted-live-shortlist-quant-v1",
        "asof":audit["asof"],
        "status":"exploratory",
        "peer_cohort_requested":quant["requested_symbols"],
        "peer_cohort_valid":quant["available_symbols"],
        "candidate_count":len(results),
        "historical_statistics_exploratory_only":True,
        "survivorship_bias_unresolved":True,
        "verified_predictive_accuracy":False,
        "raw_ohlcv_copied":False,
        "stocks":results,
    }


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--market",default="docs/data.json")
    p.add_argument("--recommendations",default="docs/recommendations.json")
    p.add_argument("--quant",default="docs/quant.json")
    p.add_argument("--output",default="docs/v3-priority-quant.json")
    a=p.parse_args(argv)
    try:
        out=build_priority_feed(load(Path(a.market)),
                                load(Path(a.recommendations)),load(Path(a.quant)))
        dest=Path(a.output)
        dest.parent.mkdir(parents=True,exist_ok=True)
        tmp=dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+"\n",
                       encoding="utf-8")
        tmp.replace(dest)
        print(json.dumps({
            "asof":out["asof"],"candidate_count":out["candidate_count"],
            "peer_cohort_valid":out["peer_cohort_valid"],
            "source_type":"derived_historical_quant_no_raw_OHLCV",
        },ensure_ascii=False))
        return 0
    except (CoverageError,ValueError,TypeError,KeyError,OSError):
        print('{"status":"blocked","reason":"invalid_targeted_quant_evidence"}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
