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

NEEDED = ["Open","High","Low","Close","Volume"]


def collect(signals: Path, cache_dir: Path, asof: str, years:int=9,
            max_stocks:int=120, sleep:float=.35, force:bool=False):
    data=json.loads(signals.read_text(encoding="utf-8"))
    if data.get("status")!="success" or data.get("date")!=asof:
        raise RuntimeError("시세 기준일과 신호 기준일 다름: 과거 신호값 오염 방지")
    stocks=data.get("stocks",[])[:max_stocks]
    cache_dir.mkdir(parents=True,exist_ok=True)
    saved=0; fail=[]; fresh=0
    for i,stock in enumerate(stocks,1):
        code=str(stock.get("code",""))
        if len(code)!=6 or not code.isdigit(): continue
        path=cache_dir/f"{code}.csv"
        if path.exists() and not force:
            try:
                old=pd.read_csv(path)
                if len(old)>=650 and str(old.iloc[-1].Date)[:10]==asof:
                    fresh+=1; continue
            except Exception:pass
        start=(date.fromisoformat(asof)-timedelta(days=365*years+30)).isoformat()
        error=None
        for attempt in range(3):
            try:
                df=fdr.DataReader(code,start,(date.fromisoformat(asof)+timedelta(days=1)).isoformat())
                if df is None or len(df)<650:
                    raise ValueError("650봉 미만으로 120주 지표/과거 사례 부족")
                if not all(k in df for k in NEEDED):
                    raise ValueError("OHLCV 컬럼 누락")
                df.index=pd.to_datetime(df.index)
                df=df.sort_index().loc[:asof,NEEDED].dropna()
                if len(df)<650 or df.index[-1].strftime('%Y-%m-%d')!=asof:
                    raise ValueError(f"가격 기준일 불일치: {df.index[-1].date()} vs {asof}")
                tmp=path.with_suffix('.tmp')
                df.to_csv(tmp,index_label="Date",date_format="%Y-%m-%d",float_format="%.5f")
                os.replace(tmp,path)
                saved+=1;error=None;break
            except Exception as e:
                error=str(e)[:170]
                time.sleep((attempt+1)*1.5)
        if error:fail.append([code,error])
        time.sleep(sleep)
        if i%25==0:logging.info("OHLCV cache %s/%s saved=%s reused=%s failed=%s",i,len(stocks),saved,fresh,len(fail))
    result={"asof":asof,"requested":len(stocks),"fetched":saved,"reused":fresh,
            "failed_n":len(fail),"failed":fail}
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
    a=p.parse_args()
    logging.basicConfig(level=logging.INFO)
    result=collect(Path(a.signals),Path(a.cache_dir),a.asof,a.years,a.max_stocks,force=a.force)
    print(json.dumps(result,ensure_ascii=False))
    if result['fetched']+result['reused']<20:raise SystemExit(2)
