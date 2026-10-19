"""Current listed under-160-day stocks: quality-gated partial daily observations.

This module is NOT the v2.2 full stock score. No weekly 120-week MA,
stop/buy signal or historical quant return is inferred from 60-159 bars.
Only current source-day observed derived MA/volume/OBV references are public.
"""
from __future__ import annotations
from datetime import datetime,timedelta
from pathlib import Path
from collections import Counter
import json
import math
import re
import time

from v22_short_history_validation import (
    source_day_diagnostics, MIN_SHORT_RESEARCH_BARS,
)

SCHEMA="v22-current-listed-partial-daily-60to159-v0.1"
VALID_CODE=re.compile(r"\d{6}\Z")
MAX_ISSUERS=80


def _observed_number(value,digits=3):
    number=float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite_observation")
    return round(number,digits)


def _partial(frame,day):
    import numpy as np
    status,n=source_day_diagnostics(frame,day)
    if status!="partial_60_plus_observed_not_scored":
        return {"status":"not_observable_partial","bars":n,
                "unavailable_reason":status}
    if not MIN_SHORT_RESEARCH_BARS<=n<160:
        # A ticker crossing >=160 bars belongs back to FULL v2.2 analysis
        # on the next day's scan, not silently inserted as a limited score.
        return {"status":"not_observable_partial","bars":n,
                "unavailable_reason":"not_in_60_to_159_window"}
    df=frame.loc[frame.index<=day].sort_index()
    close=df["Close"].to_numpy(dtype=float)
    volumes=df["Volume"].to_numpy(dtype=float)
    ma20=float(np.mean(close[-20:]))
    ma60=float(np.mean(close[-60:]))
    ma120=float(np.mean(close[-120:])) if n>=120 else None
    prev20=float(np.mean(volumes[-40:-20]))
    current20=float(np.mean(volumes[-20:]))
    volume_ratio=(current20/prev20) if prev20>0 else None
    last60=close[-60:]
    obv_change=float(np.sum(np.sign(
        np.diff(last60,prepend=last60[0]))*volumes[-60:]))
    return {
        "status":"partial_technical_observed",
        "bars":n,
        "close_reference":_observed_number(close[-1],2),
        "ma20":_observed_number(ma20,2),
        "ma60":_observed_number(ma60,2),
        "ma120_daily":_observed_number(ma120,2) if ma120 is not None else None,
        "close_vs_ma20_pct":_observed_number((close[-1]/ma20-1)*100,2),
        "close_vs_ma60_pct":_observed_number((close[-1]/ma60-1)*100,2),
        "volume_ratio_20_vs_prior_20":_observed_number(volume_ratio,3)
                                   if volume_ratio is not None else None,
        "obv_direction_60_daily":"rising" if obv_change>0 else
                             "falling" if obv_change<0 else "flat",
        "short_daily_ma_aligned":bool(close[-1]>=ma20>=ma60),
    }


def build_short_history(snapshot,reader,*,asof=None,cfg=None,sleep=lambda x:None):
    """Input is the full signed source-day public snapshot, reader injected in tests."""
    if (not isinstance(snapshot,dict)
        or snapshot.get("status")!="success"
        or snapshot.get("version")!="2.0"
        or not isinstance(snapshot.get("history_exclusions"),list)
        or not isinstance(snapshot.get("stocks"),list)):
        raise ValueError("current_market_snapshot_required")
    date=asof or snapshot.get("date")
    if date!=snapshot.get("date"):
        raise ValueError("source_day_mismatch")
    exclusions=snapshot["history_exclusions"]
    targets=[x[0] for x in exclusions if (
        isinstance(x,list) and len(x)==2 and
        x[1]=="insufficient_valid_price_history" and
        isinstance(x[0],str) and VALID_CODE.fullmatch(x[0]))]
    if len(targets)!=len(exclusions) or not 0<=len(targets)<=MAX_ISSUERS:
        raise ValueError("partial_history_population_invalid")
    if len(set(targets))!=len(targets):
        raise ValueError("partial_history_duplicate_stock")
    if set(targets)&{x.get("code") for x in snapshot["stocks"]}:
        raise ValueError("partial_history_already_full_scored")
    if cfg is None:
        import screener
        cfg=screener.cfg_read()
    start=(datetime.fromisoformat(date)-timedelta(days=int(cfg["historical_days"]))).strftime("%Y-%m-%d")
    end=(datetime.fromisoformat(date)+timedelta(days=1)).strftime("%Y-%m-%d")
    out=[]
    for i,code in enumerate(sorted(targets)):
        try:
            record=_partial(reader(code,start,end),date)
        except Exception:
            record={"status":"not_observable_partial","bars":0,
                    "unavailable_reason":"provider_error_redacted"}
        out.append({"code":code,**record})
        if i+1<len(targets):sleep(float(cfg.get("request_sleep_seconds",0.18)))
    partial_count=sum(x["status"]=="partial_technical_observed" for x in out)
    limited=sum(x["status"]=="not_observable_partial" for x in out)
    if partial_count+limited!=len(targets):
        raise ValueError("partial_price_outcome_count_mismatch")
    return {
        "schema":SCHEMA,"asof":date,
        "listed_insufficient_history_count":len(targets),
        "partial_observed_count":partial_count,
        "still_unavailable_count":limited,
        "minimum_bars":MIN_SHORT_RESEARCH_BARS,
        "maximum_bars_exclusive":160,
        "source_label":"FDR-derived short daily observations; unverified secondary price source",
        "full_v22_score_allowed":False,
        "v3_validated_technical_score_allowed":False,
        "confirmed_buy_signal":False,
        "quant_forecast_validated":False,
        "daily_ma120_is_not_weekly_ma120":True,
        "raw_ohlcv_published":False,
        "rows":out,
    }


def write_short_history(snapshot,path,reader=None,*,asof=None,cfg=None,sleep=None):
    if reader is None:
        import FinanceDataReader as fdr
        reader=fdr.DataReader
    result=build_short_history(snapshot,reader,asof=asof,cfg=cfg,
                               sleep=sleep or time.sleep)
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    if len(raw.encode("utf8"))>250_000:
        raise ValueError("partial_price_public_data_limit")
    tmp=target.with_suffix(".tmp")
    tmp.write_text(raw+"\n",encoding="utf8")
    tmp.replace(target)
    return {k:v for k,v in result.items() if k!="rows"}
