"""Fetch OHLCV history for v2 candidates with file-based cache and strict audit trail.
Run in GitHub Actions after v2's screener.py. No secrets needed for FDR.
"""
import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import FinanceDataReader as fdr
from quant_priority_universe import prioritized_stocks, optional_recommendations
from quant_engine import read_ohlcv, source_cohort_digest, valid_code

NEEDED = ["Open","High","Low","Close","Volume"]


def invalid_tail_diagnostic(frame: pd.DataFrame | None, asof: str) -> dict | None:
    """Inspect bad vendor bars without repairing, dropping or publishing prices.

    A long clean tail is only a candidate for future independent source
    comparison. It is NOT accepted as valid history by the study engine.
    """
    if not isinstance(frame,pd.DataFrame) or not all(k in frame for k in NEEDED):
        return None
    try:
        rows=frame.loc[:,NEEDED].copy()
        rows.index=pd.to_datetime(rows.index,errors="coerce")
        if rows.index.isna().any() or rows.index.duplicated().any():
            return None
        rows=rows.sort_index().loc[:asof]
        if rows.empty or rows.index[-1].date().isoformat()!=asof:
            return None
        for key in NEEDED:
            rows[key]=pd.to_numeric(rows[key],errors="coerce")
        bad=(rows[NEEDED].isna().any(axis=1)|
             (rows[["Open","High","Low","Close"]]<=0).any(axis=1)|
             (rows["Volume"]<0)|
             (rows["Low"]>rows["High"])|
             (rows["Open"]>rows["High"])|
             (rows["Open"]<rows["Low"])|
             (rows["Close"]>rows["High"])|
             (rows["Close"]<rows["Low"]))
        # A >65% close-to-close jump is a suspicious unverified event,
        # not a price that this collector may silently adjust.
        jumps=rows["Close"].pct_change().abs().gt(.65)
        suspect=bad|jumps
        if not suspect.any():
            return None
        last_bad=int(suspect.to_numpy().nonzero()[0][-1])
        tail=len(rows)-last_bad-1
        return {
            "suspect_bar_count":int(suspect.sum()),
            "last_suspect_date":rows.index[last_bad].date().isoformat(),
            "trailing_valid_sessions":tail,
            "trailing_650_session_candidate":tail>=650,
            "research_only":True,
            "official_adjustment_certified":False,
        }
    except (TypeError,ValueError,KeyError,IndexError,OverflowError):
        return None


def collect(signals: Path, cache_dir: Path, asof: str, years:int=9,
            max_stocks:int=120, sleep:float=.35, force:bool=False,
            recommendations:Path|None=Path('docs/recommendations.json')):
    data=json.loads(signals.read_text(encoding="utf-8"))
    if data.get("status")!="success" or data.get("date")!=asof:
        raise RuntimeError("시세 기준일과 신호 기준일 다름: 과거 신호값 오염 방지")
    stocks=prioritized_stocks(data,optional_recommendations(recommendations),max_stocks)
    cache_dir.mkdir(parents=True,exist_ok=True)
    saved=0; fail=[]; fresh=0; diagnostics=[]
    for i,stock in enumerate(stocks,1):
        code=str(stock.get("code",""))
        if not valid_code(code): continue
        path=cache_dir/f"{code}.csv"
        if path.exists() and not force:
            try:
                # Same source-quality gate as the research engine. A fresh
                # date alone is NOT permission to reuse corrupt OHLCV.
                cached=read_ohlcv(path,asof)
                if cached.index[-1].date().isoformat()==asof:
                    fresh+=1; continue
            except (ValueError,FileNotFoundError,IndexError,TypeError):
                pass
        start=(date.fromisoformat(asof)-timedelta(days=365*years+30)).isoformat()
        error=None
        tmp=path.with_suffix('.tmp')
        for attempt in range(3):
            df=None
            try:
                df=fdr.DataReader(code,start,(date.fromisoformat(asof)+timedelta(days=1)).isoformat())
                if df is None or len(df)<650:
                    raise ValueError("650봉 미만으로 120주 지표/과거 사례 부족")
                if not all(k in df for k in NEEDED):
                    raise ValueError("OHLCV 컬럼 누락")
                df.index=pd.to_datetime(df.index)
                df=df.sort_index().loc[:asof,NEEDED]
                if df.isna().any().any():raise ValueError('OHLCV 결측 — 날짜 제거로 기간 왜곡 금지')
                if len(df)<650 or df.index[-1].strftime('%Y-%m-%d')!=asof:
                    raise ValueError(f"가격 기준일 불일치: {df.index[-1].date()} vs {asof}")
                df.to_csv(tmp,index_label="Date",date_format="%Y-%m-%d",float_format="%.5f")
                # Reject malformed vendor bars BEFORE promoting the cache.
                read_ohlcv(tmp,asof)
                os.replace(tmp,path)
                saved+=1;error=None;break
            except Exception as e:
                error=str(e)[:170]
                # Only our own unfinished write is removed. Preserve prior
                # caches for diagnosis, but the scorer still rejects them.
                tmp.unlink(missing_ok=True)
                if attempt == 2:
                    diagnostic=invalid_tail_diagnostic(df,asof)
                    if diagnostic is not None:
                        diagnostics.append([code,diagnostic])
                # The final failed attempt is terminal; waiting cannot retry.
                if attempt < 2:
                    time.sleep((attempt+1)*1.5)
        if error:fail.append([code,error])
        time.sleep(sleep)
        if i%25==0:logging.info("OHLCV cache %s/%s saved=%s reused=%s failed=%s",i,len(stocks),saved,fresh,len(fail))
    result={"asof":asof,"requested":len(stocks),
            "cohort_sha256":source_cohort_digest(
                asof,[str(s["code"]) for s in stocks]),
            "fetched":saved,"reused":fresh,
            "failed_n":len(fail),"failed":fail,
            "failed_bar_diagnostics":diagnostics}
    (cache_dir/"cache_status.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--signals',default='docs/data.json')
    p.add_argument('--cache-dir',default='cache/quant_history')
    p.add_argument('--asof',required=True)
    p.add_argument('--years',type=int,default=9)
    p.add_argument('--max-stocks',type=int,default=120)
    p.add_argument('--force',action='store_true')
    p.add_argument('--recommendations',default='docs/recommendations.json')
    a=p.parse_args()
    logging.basicConfig(level=logging.INFO)
    result=collect(Path(a.signals),Path(a.cache_dir),a.asof,a.years,a.max_stocks,
                   force=a.force,recommendations=Path(a.recommendations))
    print(json.dumps(result,ensure_ascii=False))
    if result['fetched']+result['reused']<20:raise SystemExit(2)
