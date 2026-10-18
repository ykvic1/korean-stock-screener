"""Daily full-universe research queue with explicit NO-BUY gate.

The search universe is never capped at 20. Assess every actually analysed
common share; after downside/data screens select <=20 research dossiers.
"Selected for deeper analysis" is NOT a purchase recommendation. Only verified
forward return plus financials, industry and independently verified institutional
flows could pass the (currently unfulfilled) confirmed-buy gate.

No API calls, hidden background work or fabrication; pure Python stdlib.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

MAX_DISPLAY=20
PERIODS={"1m":"스윙 1개월","3m":"중기 3개월",
         "6m":"중기 6개월","12m":"장기 1년"}
SERIOUS_FLAGS=("손절폭 과대","단기 급등","장기 추세 취약",
               "추격 주의","거래대금 부족","급격한 거래량 집중")


class RecommendationError(ValueError):
    pass


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def observed_score(row,weights):
    """Only observed factor weights form the denominator. Null != 0."""
    if not isinstance(weights,dict) or not isinstance(row.get("components"),dict):
        return None
    if len(weights)<1 or len(weights)>30 or not all(
        finite(w) and w>0 for w in weights.values()
    ) or abs(sum(weights.values())-100)>1e-5:
        return None
    components=row["components"]
    observed=available=0
    missing=[]
    for label,weight in weights.items():
        if label not in components:
            return None
        score=components[label]
        if score is None:
            missing.append({"factor":label,"weight":weight})
        elif not finite(score) or score<0 or score>weight:
            return None
        else:
            observed+=score
            available+=weight
    if not available:
        return None
    normalized=math.floor((observed/available*100+1e-12)*10)/10
    return {
        "normalized":normalized,
        "earned":round(observed,2),"available":round(available,2),
        "coverage_pct":round(available,2),"missing":missing,
        "basis":"v2_observed_only_not_v3"
    }


def stop_assessment(row):
    price=row.get("price")
    stop=row.get("stop_line")
    percent=row.get("stop_pct")
    if not (finite(price) and price>0 and finite(stop) and 0<stop<price
            and finite(percent) and percent<0):
        return {"status":"unverified","stop_pct":None,"stop_price":None}
    observed=100*(stop/price-1)
    if abs(observed-percent)>1:
        return {"status":"inconsistent","stop_pct":None,"stop_price":stop}
    flags=row.get("risk_flags") or []
    if not isinstance(flags,list):
        flags=[]
    warnings=[f for f in flags if isinstance(f,str) and any(
        t in f for t in SERIOUS_FLAGS)]
    thin=finite(row.get("trading_eok")) and row["trading_eok"]<2
    distance=abs(percent)
    # Match the PR #60 site definition: stop-distance tier depends ONLY on
    # the validated distance to structural invalidation. Chase, liquidity,
    # and long-term trend are separate research cautions, not a stop tier.
    risk=("high" if distance<3 or distance>15
          else "low" if distance<=8 else "medium")
    chase=any(any(t in flag for t in
                  ("단기 급등","추격 주의","급격한 거래량 집중"))
              for flag in warnings)
    weak_trend=any("장기 추세 취약" in flag for flag in warnings)
    non_stop=[]
    if chase:
        non_stop.append("급등·추격 주의")
    if thin:
        non_stop.append("거래대금 부족 · 환금성 주의")
    if weak_trend:
        non_stop.append("장기 추세 취약")
    return {"status":"observed","risk":risk,"stop_pct":percent,
            "stop_price":stop,"warning_flags":warnings,
            "non_stop_warning_flags":non_stop}


def published_quant(quant,day):
    if not isinstance(quant,dict) or quant.get("asof")!=day:
        return {}
    stocks=quant.get("stocks")
    if not isinstance(stocks,list):
        return {}
    return {x["code"]:x for x in stocks
            if isinstance(x,dict) and isinstance(x.get("code"),str)}


def quant_periods(study):
    result={}
    periods=study.get("periods",{}) if isinstance(study,dict) else {}
    for key,name in PERIODS.items():
        p=periods.get(name,{})
        enough=(isinstance(p,dict) and p.get("status")=="exploratory"
                and finite(p.get("sample_n")) and p["sample_n"]>=40
                and finite(p.get("symbol_n")) and p["symbol_n"]>=10
                and finite(p.get("quarter_n")) and p["quarter_n"]>=12
                and finite(p.get("mean_net_return_pct"))
                and finite(p.get("stop_first_pct"))
                and finite(p.get("max_adverse_p75_pct")))
        result[key]={
            "evidence":"historical_exploratory" if enough else "missing_or_insufficient",
            "mean_historical_net_pct":p["mean_net_return_pct"] if enough else None,
            "historical_stop_first_pct":p["stop_first_pct"] if enough else None,
            "historical_adverse_p75_pct":p["max_adverse_p75_pct"] if enough else None,
            "historical_sample_n":int(p["sample_n"]) if enough else None,
            "forward_validated":False
        }
    return result


def approach(row,historical):
    one,three,six=historical["1m"],historical["3m"],historical["6m"]
    has=lambda p:p["evidence"]=="historical_exploratory"
    if (has(one) and has(three) and
            one["mean_historical_net_pct"]>0 and three["mean_historical_net_pct"]<=0):
        return "1m","과거 유사조건에서 1개월 우위, 3개월 부진",True
    if (has(three) and has(six) and
            three["mean_historical_net_pct"]>0 and six["mean_historical_net_pct"]<=0):
        return "3m","과거 유사조건에서 3개월 우위, 6개월 부진",True
    phase=row.get("phase")
    weak=row.get("ma120w_gap") is not None and finite(row.get("ma120w_gap")) and row["ma120w_gap"] < -15
    if weak and phase=="초기 돌파 확인 중":
        return "1m","일봉 초기 돌파지만 주봉 장기추세 약세",True
    if phase=="초기 돌파 확인 중":
        return "1m","일봉 초기 돌파 가설",False
    if phase=="상승 후 수렴":
        return "3m","일봉 상승 후 수렴 가설",False
    if phase=="장기 추세 전환 관찰" and not weak:
        return "6m","주봉 장기 전환 관찰, 실적·업황 검증 필요",False
    return "unknown","기간 판단 근거 부족",bool(weak)


def forward_return(row,day):
    """Accept only an independently validated future model, never old 52w high."""
    r=row.get("independently_validated_return_forecast")
    if not isinstance(r,dict) or r.get("asof")!=day or r.get("validated") is not True:
        return None
    # A documented forecast must carry a valid independent calibration id.
    # No current v2 source supplies this structured field.
    if not (isinstance(r.get("calibration_id"),str) and r["calibration_id"].strip()
            and r.get("out_of_sample") is True and finite(r.get("expected_gain_pct"))
            and r["expected_gain_pct"]>0):
        return None
    return r["expected_gain_pct"]


def build_dossier(row,score,stop,history,day):
    horizon,horizon_basis,long_caution=approach(row,history)
    risk=stop.get("risk","unknown")
    gain=forward_return(row,day)
    loss=abs(stop["stop_pct"]) if stop["status"]=="observed" else None
    rr=round(gain/loss,2) if gain is not None and loss is not None and loss>0 else None
    # Any company data has to be independently verified; v2 does not supply
    # 3-5y PIT statements or industry cycle proof.
    financial_verified=(row.get("financial_confirmed") is True and
        isinstance(row.get("financial_evidence_id"),str) and
        bool(row["financial_evidence_id"].strip()))
    flow_verified=(row.get("flows_confirmed") is True and
        row.get("flow_session_status")=="regular_session_verified" and
        row.get("flow_cross_validation")=="independent_matched")
    industry_verified=(row.get("industry_cycle_confirmed") is True and
        isinstance(row.get("industry_evidence_id"),str) and
        bool(row["industry_evidence_id"].strip()))
    valuation_verified=(row.get("valuation_confirmed") is True and
        isinstance(row.get("valuation_evidence_id"),str) and
        bool(row["valuation_evidence_id"].strip()))
    gaps=[]
    for evidence,present in (
        ("최근 3~5년 재무·현금흐름",financial_verified),
        ("외국인·기관·연기금 수급 교차검증",flow_verified),
        ("산업 사이클·업황",industry_verified),
        ("실적 근거의 기업가치",valuation_verified),
        ("독립 검증된 미래 기대수익",gain is not None)
    ):
        if not present:gaps.append(evidence)
    reliable=(financial_verified and flow_verified and industry_verified and
        valuation_verified and gain is not None and loss is not None and rr>=1.8
        and risk in ("low","medium")
        and not stop.get("non_stop_warning_flags"))
    if reliable:
        status="verified_recommendation"
        strategy="조건부 분할매수 검토"
    elif risk=="high" or stop["status"]!="observed":
        status="risk_or_invalid_stop"
        strategy="신규 매수 관망, 구조적 손절 참고선 재검증"
    elif stop.get("non_stop_warning_flags"):
        status="separate_caution_research_only"
        strategy="손절폭과 별개로 추격·환금성·장기추세 확인 전 관망"
    else:
        status="needs_deeper_evidence"
        strategy="추가 검증 전 관망"
    signals=[str(x) for x in (row.get("signals") or []) if isinstance(x,str)][:5]
    patterns=[str(x) for x in (row.get("patterns") or []) if isinstance(x,str)][:5]
    phases=str(row.get("phase") or "현재 위치 미확인")
    result={
        "code":row["code"],"name":row["name"],"asof":day,
        "market":row.get("market"),"price":row.get("price"),
        "v2_score":score,
        "qualification":status,"trade_action":strategy,
        "approach_horizon":horizon,"approach_basis":horizon_basis,
        "long_holding_caution":long_caution,
        "technical":{
            "phase":phases,"signals":signals,"patterns":patterns,
            "obv_rising":row.get("obv_rising") is True,
            "one_day_volume_ratio":row.get("one_day_volume_ratio"),
            "ma120w_gap_pct":row.get("ma120w_gap"),
            "price20_pct":row.get("price20"),
            "price60_pct":row.get("price60")
        },
        "downside":{
            "risk_level":risk,"stop":stop,
            "structural_invalidation_price":stop.get("stop_price"),
            "hypothesis":"손절선 이탈 시 일봉/주봉 파동과 투자 가설 재점검"
        },
        "flows":{"validated":flow_verified,
                 "comment":"독립 검증된 거래주체 수급" if flow_verified
                 else "기관·외국인·연기금의 중장기 매집 미확인"},
        "financials":{"validated":financial_verified,
                      "comment":"3~5년 재무자료 확인됨" if financial_verified else
                      "매출·영업이익·현금흐름·부채 3~5년 검증 필요"},
        "industry":{"validated":industry_verified,
                    "comment":"산업 사이클 자료 검증됨" if industry_verified else
                    "산업 성장성·경기 사이클·공급과잉 해소 미검증"},
        "valuation":{"validated":valuation_verified,"pbr":row.get("pbr"),
                     "comment":"실적 기반 가치 검증" if valuation_verified else
                     "PBR만으로 저평가 판단 불가, 밸류 미검증"},
        "historical_quant":history,
        "return_assessment":{
            "expected_gain_pct":gain,
            "expected_return_independently_validated":gain is not None,
            "loss_reference_pct":stop.get("stop_pct"),
            "expected_reward_risk":rr,
            "past_52week_high_used_as_target":False,
            "caution":"과거 유사조건 수익은 미래 기대수익률이 아님"
        },
        "scenarios":{
            "up":{"conditions":"거래량 지속·기술적 돌파 안착·실적/수급 개선 확인",
                  "future_return_pct":None},
            "neutral":{"conditions":"횡보·거래량 감소 또는 핵심 자료 미확인",
                       "future_return_pct":None},
            "down":{"conditions":"구조적 지지선 이탈·수급 악화·실적/업황 악화",
                    "invalidation_price":stop.get("stop_price"),
                    "future_return_pct":None}
        },
        "evidence_gaps":gaps,
        "confirmed_buy":reliable,
        "prediction_probabilities":None,
        "elliott_primary":None,"elliott_alternative":None,
        "analyst_attention":"수급·실적·주봉 대안 파동 검증 후 매매 재판단"
    }
    # Exclude presentation-only changed status from fingerprint.
    material=json.dumps(result,ensure_ascii=False,sort_keys=True,
                        separators=(",",":"),allow_nan=False)
    result["evidence_fingerprint"]=hashlib.sha256(
        material.encode("utf-8")).hexdigest()[:16]
    return result


def calculate(snapshot,previous=None,quant=None):
    if not isinstance(snapshot,dict) or snapshot.get("status")!="success":
        raise RecommendationError("valid_market_snapshot_required")
    day=snapshot.get("date")
    if not isinstance(day,str) or len(day)!=10:
        raise RecommendationError("invalid_market_asof")
    universe=snapshot.get("stocks")
    if not isinstance(universe,list) or not universe:
        raise RecommendationError("no_valid_stock_rows")
    weights=snapshot.get("weights")
    qmap=published_quant(quant,day)
    assessed=[]
    checked=set()
    score_ready=0
    risk_pass=0
    for row in universe:
        if not isinstance(row,dict) or not isinstance(row.get("code"),str):
            raise RecommendationError("malformed_stock_record")
        if row["code"] in checked:
            raise RecommendationError("duplicate_stock_record")
        checked.add(row["code"])
        grade=observed_score(row,weights)
        if grade is None:
            continue
        score_ready+=1
        stop=stop_assessment(row)
        if stop["status"]=="observed" and stop["risk"]!="high":
            risk_pass+=1
        study=qmap.get(row["code"])
        history=quant_periods(study)
        dossier=build_dossier(row,grade,stop,history,day)
        assessed.append(dossier)
    # Review candidates must satisfy a basic score/data/downside gate.
    # Even if NONE does, do not fill 20 with weaker stocks.
    qualified=[d for d in assessed if
      d["v2_score"]["normalized"]>=70 and
      d["v2_score"]["coverage_pct"]>=40 and
      d["downside"]["risk_level"] in ("low","medium") and
      d["approach_horizon"]!="unknown"]
    # A confirmed expected-R/R > 1.8 is primary ONLY when validated,
    # otherwise research candidates are ordered by score/coverage and risk.
    qualified.sort(key=lambda d:(
        -int(d["confirmed_buy"]),
        -(d["return_assessment"]["expected_reward_risk"] or -1),
        -d["v2_score"]["normalized"],
        -d["v2_score"]["coverage_pct"],
        abs(d["downside"]["stop"]["stop_pct"] or -999),
        d["code"]
    ))
    chosen=qualified[:MAX_DISPLAY]
    old=previous.get("dossiers",[]) if isinstance(previous,dict) else []
    oldmap={d["code"]:d for d in old
            if isinstance(d,dict) and isinstance(d.get("code"),str)}
    selected_codes=set()
    changed={"entered":[],"updated":[],"unchanged":[],"exited":[]}
    for i,d in enumerate(chosen,1):
        d["rank"]=i
        code=d["code"]
        selected_codes.add(code)
        prior=oldmap.get(code)
        if prior is None:
            change="entered"
        elif prior.get("evidence_fingerprint")!=d["evidence_fingerprint"]:
            change="updated"
        else:
            change="unchanged"
        d["daily_change"]=change
        changed[change].append(code)
    changed["exited"]=[code for code in oldmap if code not in selected_codes]
    verified=sum(d["confirmed_buy"] for d in chosen)
    return {
        "schema":"recommendations-v1-research",
        "asof":day,"market_scan_complete":bool(snapshot.get("history_scan_complete")),
        "market_eligible":snapshot.get("eligible_count"),
        "market_examined":snapshot.get("examined_count"),
        "market_price_valid":len(universe),
        "screened_count":len(universe),
        "score_ready_count":score_ready,
        "downside_screen_pass_count":risk_pass,
        "research_eligible_count":len(qualified),
        "research_dossier_count":len(chosen),
        "confirmed_recommendation_count":verified,
        "max_display":MAX_DISPLAY,
        "recommendations_are_not_forced":True,
        "selection_basis":"all_actual_valid_symbols_before_display_limit",
        "verified_expected_rr_primary":True,
        "missing_validation_must_not_invent_expected_return":True,
        "research_source_date":quant.get("asof") if isinstance(quant,dict) else None,
        "research_source_matched":bool(qmap),
        "daily_changes":changed,
        "dossiers":chosen
    }


def history_update(previous_history,new):
    hist=previous_history if isinstance(previous_history,list) else []
    stamp={"date":new["asof"],"screened":new["screened_count"],
           "selected":[d["code"] for d in new["dossiers"]],
           "confirmed_count":new["confirmed_recommendation_count"],
           "entered":new["daily_changes"]["entered"],
           "exited":new["daily_changes"]["exited"],
           "updated":new["daily_changes"]["updated"]}
    hist=[h for h in hist if isinstance(h,dict) and h.get("date")!=new["asof"]]
    hist.append(stamp)
    hist.sort(key=lambda h:h.get("date",""),reverse=True)
    return hist[:90]


def read_optional(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (ValueError,OSError):
        return None


def publish(snapshot,docs):
    docs=Path(docs)
    if not docs.exists():
        docs.mkdir(parents=True)
    current=docs/"recommendations.json"
    old=read_optional(current)
    # A source revision on the SAME market date is compared with prior
    # publication, never with arbitrary stale historical portfolio holdings.
    study=read_optional(docs/"quant.json")
    report=calculate(snapshot,previous=old,quant=study)
    if report["asof"]!=snapshot["date"]:
        raise RecommendationError("publication_date_inconsistent")
    history=history_update(read_optional(docs/"recommendation_history.json"),report)
    current.write_text(json.dumps(report,ensure_ascii=False,indent=2,
                                  allow_nan=False),encoding="utf-8")
    (docs/"recommendation_history.json").write_text(
        json.dumps(history,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--docs",type=Path,default=Path("docs"))
    args=parser.parse_args()
    snapshot=json.loads(args.snapshot.read_text(encoding="utf-8"))
    result=publish(snapshot,args.docs)
    print(json.dumps({"asof":result["asof"],
                      "screened":result["screened_count"],
                      "research_dossiers":result["research_dossier_count"],
                      "confirmed_buys":result["confirmed_recommendation_count"],
                      "change":result["daily_changes"]},ensure_ascii=False))


if __name__=="__main__":
    main()
