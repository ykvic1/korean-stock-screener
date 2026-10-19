"""Daily user-facing ranked-candidate quant evidence accounting.

This is a report on *whether* today's shortlisted names have historical
comparison evidence, not a simulated or predicted stock performance score.
No KRX raw OHLCV is copied, no BUY claims, and shortfalls remain visible.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
import re

from quant_priority_universe import prioritized_stocks

PERIODS=("초단기 1주","단기 2주","스윙 1개월","스윙 2개월",
         "중기 3개월","중기 6개월","장기 1년")
CODE=re.compile(r"\d{6}\Z")


class CoverageError(ValueError):
    pass


def num(x):
    return type(x) in (int,float) and math.isfinite(x)


def period_ready(p):
    """Real old-case distributions, not out-of-sample forecast validation."""
    return (
        isinstance(p,dict) and p.get("status")=="exploratory"
        and num(p.get("sample_n")) and p["sample_n"]>=40
        and num(p.get("symbol_n")) and p["symbol_n"]>=10
        and num(p.get("quarter_n")) and p["quarter_n"]>=12
        and all(num(p.get(k)) for k in ("mean_net_return_pct",
                                        "stop_first_pct",
                                        "max_adverse_p75_pct"))
    )


def audit_recommended_quant(snapshot:dict,recommendations:dict,
                            study:dict|None=None,limit:int=120)->dict:
    if not isinstance(snapshot,dict) or snapshot.get("status")!="success" or (
         snapshot.get("version")!="2.0") or not isinstance(snapshot.get("stocks"),list):
        raise CoverageError("bad_public_screen")
    asof=snapshot.get("date")
    try:
        if type(asof) is not str or date.fromisoformat(asof).isoformat()!=asof:
            raise ValueError()
    except ValueError:
        raise CoverageError("bad_screen_date") from None
    if (not isinstance(recommendations,dict)
        or recommendations.get("asof")!=asof
        or recommendations.get("research_source_matched") is not True
        or not isinstance(recommendations.get("dossiers"),list)):
        raise CoverageError("stale_or_unmatched_dossiers")
    current={x.get("code"):x for x in snapshot["stocks"]
             if isinstance(x,dict) and isinstance(x.get("code"),str)}
    priority=[r["code"] for r in prioritized_stocks(snapshot,recommendations,limit)]
    qvalid=(isinstance(study,dict) and study.get("asof")==asof
            and study.get("status")=="exploratory"
            and isinstance(study.get("stocks"),list))
    qbycode={}
    if qvalid:
        for item in study["stocks"]:
            if (isinstance(item,dict) and isinstance(item.get("code"),str)
                and CODE.fullmatch(item["code"]) and item["code"] not in qbycode):
                qbycode[item["code"]]=item
    seen=set()
    rows=[]
    for d in recommendations["dossiers"]:
        if not isinstance(d,dict) or not isinstance(d.get("code"),str):
            raise CoverageError("bad_dossier")
        code=d["code"]
        if not CODE.fullmatch(code) or code in seen or code not in current:
            raise CoverageError("unknown_or_duplicate_recommendation")
        if len(rows)>=20:
            raise CoverageError("too_many_recommended_records")
        seen.add(code)
        if code not in priority:
            # A candidate will be skipped by the limited fetch budget:
            # do not mark the program as 'coverage complete'.
            raise CoverageError("candidate_not_prioritized_for_study")
        q=qbycode.get(code)
        observed=(q is not None and q.get("status")=="exploratory"
                  and q.get("reference_price_date")==asof)
        if observed:
            good=[p for p in PERIODS if period_ready(q.get("periods",{}).get(p))]
            state="research_available" if good else "research_but_periods_insufficient"
        elif q and q.get("status")=="insufficient":
            good=[]
            state="history_insufficient"
        else:
            good=[]
            state="awaiting_fresh_study"
        usable_outofsample=False
        # Historical quant in this repo has not passed PIT / delisting gates.
        rows.append({
            "code":code,
            "name":d.get("name") if isinstance(d.get("name"),str) else code,
            "v2_observed_score":(
                d.get("v2_score",{}).get("normalized")
                if isinstance(d.get("v2_score"),dict) and
                num(d.get("v2_score",{}).get("normalized")) else None),
            "priority_in_next_study":True,
            "state":state,
            "source_data_date":asof,
            "available_historical_periods":good,
            "historical_period_count":len(good),
            "out_of_sample_prediction_validated":usable_outofsample,
            "buy_certified":False,
        })
    states={}
    for state in ("research_available","research_but_periods_insufficient",
                  "history_insufficient","awaiting_fresh_study"):
        states[state]=sum(x["state"]==state for x in rows)
    return {
        "schema":"v3-ranked-quant-coverage-v1",
        "asof":asof,
        "candidate_count":len(rows),
        "candidates_in_next_120_stock_study":len(rows),
        "study_source_asof":study.get("asof") if isinstance(study,dict) else None,
        "quant_study_same_date":bool(qvalid),
        "candidates_with_usable_historical_periods":states["research_available"],
        "candidate_status_counts":states,
        "historical_statistics_exploratory_only":True,
        "survivorship_bias_unresolved":True,
        "predicted_profit_or_buy_certified":False,
        "stocks":rows,
    }


def load(p:Path,optional=False):
    if optional and not p.is_file():
        return None
    if not p.is_file() or p.stat().st_size>85_000_000:
        raise CoverageError("input_file_unavailable_or_oversize")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,ValueError,UnicodeError):
        raise CoverageError("invalid_input_json") from None


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--screen",default="docs/data.json")
    p.add_argument("--recommendations",default="docs/recommendations.json")
    p.add_argument("--quant",default="docs/quant.json")
    p.add_argument("--output",default="docs/v3-quant-coverage.json")
    p.add_argument("--assert-study-candidates",action="store_true")
    args=p.parse_args(argv)
    try:
        result=audit_recommended_quant(load(Path(args.screen)),
            load(Path(args.recommendations)),load(Path(args.quant),optional=True))
        if args.assert_study_candidates and result["candidate_status_counts"]["awaiting_fresh_study"]:
            raise CoverageError("recommended_stocks_not_in_study_result")
        if args.assert_study_candidates and not result["quant_study_same_date"]:
            raise CoverageError("study_date_not_current")
        dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
        tmp=dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",
                       encoding="utf-8")
        tmp.replace(dest)
        print(json.dumps({k:result[k] for k in
            ("asof","candidate_count","quant_study_same_date",
             "candidates_with_usable_historical_periods","candidate_status_counts")},
            ensure_ascii=False))
        return 0
    except (CoverageError,ValueError,TypeError,KeyError,OSError):
        print('{"status":"blocked","reason":"candidate_quant_coverage_unverified"}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
