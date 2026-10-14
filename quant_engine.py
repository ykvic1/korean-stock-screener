"""Conservative point-in-time OHLCV analogue-study engine (no orders).

Historical conditioning uses only pre-entry price/volume features. Returns use
NEXT session open, with stop-first assumption on ambiguous intraday bars.
These are descriptive frequencies on a present-day survivor universe, NOT
predictive probabilities, until historical universe/delisting coverage is added.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from statistics import mean

import numpy as np
import pandas as pd

HORIZONS = {
    "초단기 1주": (5, 0.05, 0.04),
    "단기 2주": (10, 0.07, 0.055),
    "스윙 1개월": (20, 0.10, 0.07),
    "스윙 2개월": (40, 0.13, 0.09),
    "중기 3개월": (60, 0.17, 0.11),
    "중기 6개월": (120, 0.25, 0.15),
    "장기 1년": (252, 0.40, 0.20),
}
# Fixed research assumptions, not claims of actual fees/taxes.
ROUND_TRIP_COST_PCT = 0.40
MIN_OBSERVATIONS = 40
MIN_SYMBOLS = 10
MIN_QUARTERS = 12
MAX_NEIGHBORS = 500
MAX_DISTANCE = 1.15
SIMILARITY_WEIGHTS = np.array([1.2, 1.5, 0.8, 0.5, 1.0, 0.8, 0.7], dtype=float)


@dataclass
class StudyCase:
    code: str
    market: str
    date: str
    quarter: str
    features: list[float]
    return_pct: float
    horizon_close_return_pct: float
    mfe_pct: float
    mae_pct: float
    win_target_first: bool
    hit_stop_first: bool
    gap_count: int


def valid_code(value: str) -> bool:
    return len(value) == 6 and value.isdigit()


def read_ohlcv(path: Path, asof: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    raw = pd.read_csv(path, dtype={"Date": "string"})
    needed = {"Date", "Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(raw.columns):
        raise ValueError(f"필수 칼럼 누락: {needed - set(raw.columns)}")
    raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
    if raw[list(needed)].isna().any().any():raise ValueError("결측 OHLCV/날짜 — 제거로 보유기간 왜곡 금지")
    raw = raw.sort_values("Date")
    if raw["Date"].duplicated().any():
        raise ValueError("중복 날짜")
    raw = raw.set_index("Date").loc[:pd.Timestamp(asof)]
    if len(raw) < 650:
        raise ValueError("120주선/과거 관측을 위한 일봉 최소 650개 미확보")
    for field in ("Open", "High", "Low", "Close", "Volume"):
        raw[field] = pd.to_numeric(raw[field], errors="coerce")
    if raw[["Open", "High", "Low", "Close", "Volume"]].isna().any().any():
        raise ValueError("OHLCV 숫자 오류")
    if (raw[["Open", "High", "Low", "Close"]] <= 0).any().any() or (raw["Volume"] < 0).any():
        raise ValueError("음수 또는 0인 가격 / 음수 거래량")
    if (raw["Low"] > raw["High"]).any() or (raw["Open"] > raw["High"]).any() or (raw["Open"] < raw["Low"]).any():
        raise ValueError("OHLC 범위 오류")
    if (raw["Close"] > raw["High"]).any() or (raw["Close"] < raw["Low"]).any():
        raise ValueError("종가 범위 오류")
    if len(raw) > 3500:
        raw = raw.iloc[-3500:].copy()
    return raw


def build_features(df: pd.DataFrame) -> np.ndarray:
    """Features are strictly trailing, including partial current week (no week-end leakage)."""
    close = df["Close"].to_numpy(dtype=float)
    volume = df["Volume"].to_numpy(dtype=float)
    n = len(close)
    feats = np.full((n, 7), np.nan, dtype=float)
    # Weekly 120 average from 119 fully completed weeks plus THIS day's close.
    last_week = None
    prior_week_ends: list[float] = []
    cums = np.concatenate(([0.], np.cumsum(close)))
    cvs = np.concatenate(([0.], np.cumsum(volume)))
    obv = np.cumsum(np.r_[0., np.sign(np.diff(close)) * volume[1:]])
    weeks = [dt.isocalendar()[:2] for dt in df.index.to_pydatetime()]
    for i in range(n):
        w = weeks[i]
        if last_week is not None and w != last_week:
            prior_week_ends.append(close[i-1])
        last_week = w
        if i < 120 or len(prior_week_ends) < 119:
            continue
        ma20 = (cums[i+1]-cums[i-19]) / 20
        ma60 = (cums[i+1]-cums[i-59]) / 60
        last20 = (cvs[i+1]-cvs[i-19])/20
        prev40 = (cvs[i-19]-cvs[i-59])/40
        ma120week = (sum(prior_week_ends[-119:]) + close[i])/120
        price20 = 100*(close[i]/close[i-20]-1)
        price60 = 100*(close[i]/close[i-60]-1)
        gap60 = 100*(close[i]/ma60-1)
        vol_ratio = last20/max(prev40, 1.)
        obv_change = (obv[i]-obv[i-60])/max(60.*last20, 1.)
        wgap = 100*(close[i]/ma120week-1)
        div = 1.0 if (-12 <= price60 <= 10 and obv_change > 0.02) else 0.
        feats[i] = [price20/15, price60/30, gap60/15,
                    math.log(max(vol_ratio, .01))/.7, obv_change/.3, wgap/25, div]
    return feats


def label_future(df: pd.DataFrame, t: int, horizon: int,
                 target: float, stop: float, cost_pct: float) -> dict | None:
    """At t close, buy t+1 open. Stops can gap through. Same-day target/stop -> stop first."""
    if t+1+horizon > len(df):
        return None
    future = df.iloc[t+1:t+1+horizon]
    entry = float(future.iloc[0]["Open"])
    if entry <= 0:
        return None
    target_px = entry*(1+target)
    stop_px = entry*(1-stop)
    # MFE/MAE cover the entire horizon, independently of earlier strategy exits.
    hi = future['High'].to_numpy(dtype=float)
    lo = future['Low'].to_numpy(dtype=float)
    opens = future['Open'].to_numpy(dtype=float)
    if not np.isfinite(np.r_[hi,lo,opens]).all() or (lo<=0).any(): return None
    mfe = max(0., float(100*(hi.max()/entry-1)))
    mae = max(0., float(100*(1-lo.min()/entry)))
    gap_count, exit_px, win, hitstop = 0, None, False, False
    stop_idx = np.flatnonzero(lo <= stop_px)
    target_idx = np.flatnonzero(hi >= target_px)
    si = int(stop_idx[0]) if len(stop_idx) else len(future)
    ti = int(target_idx[0]) if len(target_idx) else len(future)
    if si < len(future) and si <= ti:
        exit_px = min(stop_px, float(opens[si])); hitstop=True
        gap_count = int(opens[si]<stop_px)
    elif ti < len(future):
        exit_px = max(target_px, float(opens[ti])); win=True
    horizon_close_pct = 100*(float(future.iloc[-1]["Close"])/entry-1)-cost_pct
    if exit_px is None:
        exit_px = float(future.iloc[-1]["Close"])
    return {
        "return_pct":100*(exit_px/entry-1)-cost_pct,
        "horizon_close_return_pct":horizon_close_pct,
        "mfe_pct":mfe,
        "mae_pct":mae,
        "win_target_first":win,
        "hit_stop_first":hitstop,
        "gap_count":gap_count,
    }


def wilson(success: int, n: int) -> tuple[float,float]:
    if n == 0: return (float("nan"),float("nan"))
    z = 1.96
    p = success/n
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n))/denom
    spread = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denom
    return (100*max(0.,centre-spread),100*min(1.,centre+spread))


def reference_cases(cache: dict[str,pd.DataFrame], market: dict[str,str],
                    asof: str, horizon: int, target: float, stop: float,
                    cost_pct: float) -> tuple[list[StudyCase], dict[str,np.ndarray]]:
    all_cases = []
    latest = {}
    for code, df in cache.items():
        ft = df.attrs.get("study_features")
        if ft is None:
            ft=build_features(df);df.attrs["study_features"]=ft
        if np.all(np.isfinite(ft[-1])):
            latest[code] = ft[-1]
        # Sample without overlapping holding periods *per stock and horizon*.
        stride = max(horizon, 21)
        # Shift phase by symbol code, avoids all stocks sampling same dates.
        offset = int(code[-2:]) % max(1, min(stride,21))
        for t in range(640+offset, len(df)-horizon-1, stride):
            vec = ft[t]
            if not np.all(np.isfinite(vec)):
                continue
            # Splits/price adjustment errors: skip any extremely large daily jump.
            window = df["Close"].iloc[max(0,t-65):t+1]
            if window.pct_change().abs().gt(.65).any():
                continue
            label = label_future(df,t,horizon,target,stop,cost_pct)
            if label is None: continue
            dt = df.index[t].date().isoformat()
            q = (df.index[t].year-2000)*4 + (df.index[t].month-1)//3
            all_cases.append(StudyCase(code,market[code],dt,str(q),vec.tolist(),**label))
    return all_cases, latest


def analyze_nearest(query_code: str, query_vec: np.ndarray,
                    query_market: str, cases: list[StudyCase],
                    horizon: str, cost_pct:float, target:float, stop:float) -> dict:
    pool = [c for c in cases if c.market == query_market and c.code != query_code]
    base = {"horizon":horizon,"method":"OHLCV 유사조건/현재생존종목 기반",
            "historical_market_coverage":"현존 종목 중심 - 상장폐지/과거편입 보정 전",
            "round_trip_cost_pct":cost_pct,
            "target_pct":round(target*100,1),"stop_pct":round(stop*100,1)}
    if not pool:
        return {**base,"status":"insufficient","reason":"과거 데이터가 없습니다.","sample_n":0}
    mat = np.array([c.features for c in pool], dtype=float)
    diff = mat-query_vec[None,:]
    diff[:,-1] *= 1.25  # OBV divergence binary is relevant but not dominating
    distance = np.sqrt(np.sum(SIMILARITY_WEIGHTS*diff*diff,axis=1)/np.sum(SIMILARITY_WEIGHTS))
    idx = np.flatnonzero(distance <= MAX_DISTANCE)
    if len(idx) > MAX_NEIGHBORS:
        idx = idx[np.argsort(distance[idx])[:MAX_NEIGHBORS]]
    selected = [pool[int(k)] for k in idx]
    n = len(selected)
    unique_codes = len(set(c.code for c in selected))
    unique_quarters = len(set(c.quarter for c in selected))
    sufficient = (n >= MIN_OBSERVATIONS and unique_codes >= MIN_SYMBOLS
                  and unique_quarters >= MIN_QUARTERS)
    result = {**base,"sample_n":n,"symbol_n":unique_codes,
              "quarter_n":unique_quarters,"distance_mean":round(float(distance[idx].mean()),3) if n else None,
              "status":"exploratory" if sufficient else "insufficient",
              "limitation":"유사조건 과거 빈도이며 앞으로의 확률 보장 없음. 생존편향/시장동조성 잔존."}
    if not sufficient:
        result["reason"] = (f"표본 미달: {n}/{MIN_OBSERVATIONS}건, "
                            f"{unique_codes}/{MIN_SYMBOLS}종목, "
                            f"{unique_quarters}/{MIN_QUARTERS}분기")
        return result
    ret = np.array([x.return_pct for x in selected])
    endret = np.array([x.horizon_close_return_pct for x in selected])
    mfe = np.array([x.mfe_pct for x in selected])
    mae = np.array([x.mae_pct for x in selected])
    gains = int(sum(x.win_target_first for x in selected))
    stop_hits = int(sum(x.hit_stop_first for x in selected))
    up_n = int(np.count_nonzero(endret>0))
    low, high = wilson(up_n,n)
    # Chronological holdout is a diagnostic only; parameters were not independently tuned.
    cutoff = sorted(set(c.date for c in selected))[int(len(set(c.date for c in selected))*.8)]
    recent = [c for c in selected if c.date>=cutoff]
    older = [c for c in selected if c.date<cutoff]
    holdout_sufficient = len(recent)>=MIN_OBSERVATIONS and len(set(c.code for c in recent))>=MIN_SYMBOLS and len(set(c.quarter for c in recent))>=MIN_QUARTERS
    result['chronological_holdout']={'status':'exploratory' if holdout_sufficient else '미검증: 최근 홀드아웃 표본 부족', 'split_date':cutoff,'train_n':len(older),'test_n':len(recent), 'test_mean_net_return_pct':round(float(np.mean([c.return_pct for c in recent])),2) if holdout_sufficient else None}
    result.update({
      "up_pct":round(100*up_n/n,1),
      "strategy_win_pct":round(100*np.count_nonzero(ret>0)/n,1),
      "end_close_mean_pct":round(float(endret.mean()),2),
      "down_or_flat_pct":round(100*(n-up_n)/n,1),
      "up_wilson_range_pct":[round(low,1),round(high,1)],
      "target_first_pct":round(100*gains/n,1),
      "stop_first_pct":round(100*stop_hits/n,1),
      "mean_net_return_pct":round(float(ret.mean()),2),
      "median_net_return_pct":round(float(np.median(ret)),2),
      "net_return_p10_pct":round(float(np.quantile(ret,.1)),2),
      "net_return_p90_pct":round(float(np.quantile(ret,.9)),2),
      "max_favorable_p50_pct":round(float(np.quantile(mfe,.5)),2),
      "max_favorable_p75_pct":round(float(np.quantile(mfe,.75)),2),
      "max_favorable_p90_pct":round(float(np.quantile(mfe,.9)),2),
      "max_adverse_p75_pct":round(float(np.quantile(mae,.75)),2),
      "cost_sensitivity_mean_pct":{"round_trip_0.2":round(float(ret.mean())+cost_pct-.2,2),"round_trip_0.8":round(float(ret.mean())+cost_pct-.8,2)},
      "gap_through_stop_n":int(sum(x.gap_count for x in selected)),
      "sample_start":min(x.date for x in selected),
      "sample_end":max(x.date for x in selected),
    })
    return result


def entry_playbooks(stock: dict) -> dict:
    """Conservative trigger checklist, not a validated signal or an order."""
    flags = stock.get("risk_flags") or []
    pats = stock.get("patterns") or []
    stop = stock.get("stop_pct")
    flow = bool(stock.get("flows_confirmed"))
    gap120 = stock.get("ma120w_gap")
    vol = stock.get("volume_ratio")
    rel60 = stock.get("relative60")
    risk_ok = isinstance(stop, (int,float)) and -15<=stop<=-3
    pattern_ok = bool(pats)
    no_chase = not any("급등" in x or "추격" in x for x in flags)
    return {
      "초단기": {
        "rule":"거래량 증폭과 종가 돌파를 함께 확인. 갭상승 추격보다 돌파 후 눌림 우선",
        "testable_now":bool(isinstance(vol,(int,float)) and vol>=1.4 and no_chase),
        "missing":"당일 돌파 캔들·체결가·갭리스크 별도 확인",
      },
      "스윙": {
        "rule":"일봉 IHS·쐐기·삼각수렴 또는 상승 후 횡보 + 거래량 확인. 손절선은 구조적 지지 아래",
        "testable_now":bool(pattern_ok and risk_ok and no_chase and flow),
        "missing":None if flow else "기관·외국인 수급 확인 필요",
      },
      "중기": {
        "rule":"주봉 전환 + 시장대비 상대강도 개선 + 누적 수급 확인 후 분할매수",
        "testable_now":bool(gap120 is not None and -8<=gap120<=15 and rel60 is not None and rel60>=0 and flow and risk_ok),
        "missing":"실적·업종 사이클 별도 검증 필요",
      },
      "장기": {
        "rule":"주봉 상승 전환 외에도 업종 이익 사이클, 최근 3~5년 실적·현금흐름 및 밸류 검증 필수",
        "testable_now":False,
        "missing":"현재 역사적 DART 재무/업종 정보 미연결로 장기 판정 보류",
      },
    }


def grade_periods(periods: dict) -> dict:
    """Do not force a trade; alert only when tested downside and coverage acceptable."""
    eligible = []
    for name, s in periods.items():
        if s.get("status") != "exploratory": continue
        # More downside weight than upside: using worst decile net return and stop-first.
        if s["stop_first_pct"]>35 or s["net_return_p10_pct"]<-18:
            continue
        rr = (s["max_favorable_p75_pct"]/
              max(1.,s["max_adverse_p75_pct"]))
        if s["mean_net_return_pct"]<=0 or s["up_pct"]<55 or rr<1.5:
            continue
        score = (s["mean_net_return_pct"]- .5*s["max_adverse_p75_pct"]
                 +.1*s["up_pct"]-.15*s["stop_first_pct"])
        eligible.append((score,name))
    if not eligible:
        return {"decision":"관망/추가 검증","reason":"조건부 수익·손실 통제 기준을 함께 통과한 기간 없음"}
    eligible.sort(reverse=True)
    return {"decision":"후보 기간(검증 필요)","period":eligible[0][1],
            "reason":"과거 유사 조건 위험조정 순위. 실거래 매수 신호가 아님"}


def run(screener_json:Path,history_dir:Path,output:Path,
        asof:str | None=None,max_stocks:int=120) -> dict:
    d=json.loads(screener_json.read_text(encoding="utf-8"))
    if d.get("status")!="success": raise ValueError("정상 수집된 최신 스크리너 JSON이 필요")
    asof = asof or d["date"]
    if d["date"] != asof:
        raise ValueError("신호일과 백테스트 기준일이 다름. 결측 데이터를 과거 시점에 대입하지 않음")
    stocks=[s for s in d["stocks"] if valid_code(str(s.get("code","")))][:max_stocks]
    cache={}; markets={}; missing=[]
    for s in stocks:
        code = str(s["code"])
        markets[code]=s.get("market","UNKNOWN")
        try:
            df=read_ohlcv(history_dir/f"{code}.csv",asof)
            if df.index[-1].date().isoformat()!=asof:
                raise ValueError("마지막 OHLCV 날짜가 신호일과 다름")
            cache[code]=df
        except Exception as e:
            missing.append({"code":code,"error":str(e)[:120]})
    if len(cache)<20:
        result={"status":"insufficient","asof":asof,"available_symbols":len(cache),
                "required_symbols":20,"missing":missing[:30],"stocks":[],
                "disclaimer":"현재 시장 종목의 과거 유사사례를 만드는 연구용. 미래 확률 아님."}
    else:
        all_hist={};latest={}
        for name,(h,targ,stop) in HORIZONS.items():
            cases,signals=reference_cases(cache,markets,asof,h,targ,stop,ROUND_TRIP_COST_PCT)
            all_hist[name]=cases
            for code,vec in signals.items():latest[code]=vec
        examined=[]
        for s in stocks:
            code=s["code"]
            if code not in latest:
                examined.append({"code":code,"name":s.get("name"),"status":"insufficient",
                                 "reason":"가격 이력/120주선 미확보"})
                continue
            periods={}
            for name,(_,targ,stop) in HORIZONS.items():
                periods[name]=analyze_nearest(code,latest[code],markets[code],all_hist[name],
                                             name,ROUND_TRIP_COST_PCT,targ,stop)
            examined.append({"code":code,"name":s.get("name"),"market":markets[code],
                             "status":"exploratory","periods":periods,
                             "period_suggestion":grade_periods(periods),
                             "approach_rules":entry_playbooks(s),
                             "structural_stop_reference":s.get("stop_line"),
                             "structural_stop_pct":s.get("stop_pct"),
                             "reference_price":s.get("price"),
                             "reference_price_date":s.get("price_date")})
        result={"status":"exploratory","asof":asof,"version":"2.1-study",
                "available_symbols":len(cache),"requested_symbols":len(stocks),
                "missing":missing[:50],"stocks":examined,
                "methodology":{
                    "population":"현재 v2.0 후보군만 사용: 생존편향 있음",
                    "features":"신호일 이전 OHLCV만: 20/60일 변동·이평·거래량·OBV·120주 이격",
                    "entry":"신호 다음 거래일 시가",
                    "up_probability_definition":"보유기간 말일 종가 수익(비용차감)>0의 과거 빈도; 손절/목표 달성 무관. 전략수익과 분리",
                    "holding":"거래일 기준 5·10·20·40·60·120·252일",
                    "stops":"하루 고가·저가 모두 통과 시 손절 먼저 적용; 시가 갭 하락 반영",
                    "round_trip_cost_pct":ROUND_TRIP_COST_PCT,
                    "sampling":"종목별 보유기간만큼 떨어진 표본 채택; 비독립 시장동조성 잔존",
                    "similarity":"가중 정규화 특징거리 ≤1.15; 종목별 자기 유사사례 제외",
                    "minimum":"표본 40건·종목 10개·분기 12개 이상일 때만 연구용 통계 표시",
                    "caveats":"상장폐지/당시 편입종목·당시 재무/수급 미포함. 승률은 미래 확률 아님. 주가조정/배당/거래정지 검증 필요",
                    "mfe":"최대상승폭은 경로 최고가의 통계값으로 목표가/실현 가능한 수익을 보장하지 않음",
                }}
    result['bias_audit']={'lookahead_features':'trailing-only tested; next session open entry', 'overlap':'per-symbol non-overlap; cross-symbol market dependence remains', 'survivorship':'UNRESOLVED: current candidate universe excludes delisted and historical membership', 'transaction_cost':'0.40 percentage point assumed round trip; 0.2/0.8 sensitivity', 'corporate_actions':'adjusted prices/provider consistency, dividends and trading suspensions NOT verified', 'holdout':'chronological diagnostics only; no walk-forward predictive validation', 'prediction_validated':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    tmp.replace(output)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--signals",default="docs/data.json")
    parser.add_argument("--history-dir",default="cache/quant_history")
    parser.add_argument("--output",default="docs/quant.json")
    parser.add_argument("--asof",default=None)
    parser.add_argument("--max-stocks",type=int,default=120)
    args=parser.parse_args()
    result=run(Path(args.signals),Path(args.history_dir),Path(args.output),args.asof,args.max_stocks)
    print(json.dumps({k:result.get(k) for k in ("status","asof","available_symbols","requested_symbols","required_symbols")},ensure_ascii=False))
    return 0 if result["status"]=="exploratory" else 2

if __name__ == "__main__":
    sys.exit(main())
