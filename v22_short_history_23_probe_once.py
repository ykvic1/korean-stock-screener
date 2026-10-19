"""Bounded one-time *read-only* 23 younger-current-issuer price coverage probe.

Retain strict v2.2 160-day full score bar count and no historical data export.
Collect only aggregate band counts; no stock identities/ohlcv are logged.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import re

CODES=re.compile(r"\d{6}\Z")
MIN_SHORT_RESEARCH_BARS=60


def selection(catalog,asof):
    if (not isinstance(catalog,dict)
        or catalog.get("schema")!="v22-current-listed-catalog-0.1"
        or catalog.get("date")!=asof
        or catalog.get("listed_total")!=len(catalog.get("rows",[]))):
        raise ValueError("current_listed_catalog_mismatch")
    symbols=sorted(x["code"] for x in catalog["rows"]
           if isinstance(x,dict)
           and x.get("price_history_status")=="insufficient_history"
           and isinstance(x.get("code"),str) and CODES.fullmatch(x["code"]))
    if len(symbols)>50 or len(symbols)<1 or len(set(symbols))!=len(symbols):
        raise ValueError("bounded_short_history_universe_invalid")
    return symbols


def length_band(count):
    if count<=0:return "none"
    if count<20:return "1_to_19"
    if count<60:return "20_to_59"
    if count<120:return "60_to_119"
    if count<160:return "120_to_159"
    return "160_or_more"


def source_day_diagnostics(frame,day):
    """Evaluate raw source only in memory and report fixed non-sensitive codes."""
    import numpy as np
    import pandas as pd
    cols=("Open","High","Low","Close","Volume")
    if frame is None or not hasattr(frame,"index") or frame.empty:
        return "empty",0
    if any(x not in frame.columns for x in cols):
        return "missing_columns",0
    try:
        df=frame.loc[frame.index<=pd.Timestamp(day)].sort_index()
        count=len(df)
        if count<MIN_SHORT_RESEARCH_BARS:
            return "not_enough_60",count
        if df.index.duplicated().any():
            return "duplicate_dates",count
        vals=df[list(cols)].apply(pd.to_numeric,errors="coerce")
        if vals.isna().any().any() or not np.isfinite(vals.to_numpy()).all():
            return "bad_or_nonfinite_ohlcv",count
        if (vals[list(cols[:4])]<=0).any().any() or (vals["Volume"]<0).any():
            return "nonpositive_price_or_negative_volume",count
        if ((vals["Low"]>vals["High"])|(vals["Open"]>vals["High"])|
            (vals["Open"]<vals["Low"])|(vals["Close"]>vals["High"])|
            (vals["Close"]<vals["Low"])).any():
            return "ohlc_range_inconsistent",count
        if df.index[-1].date()!=datetime.fromisoformat(day).date():
            return "last_close_not_source_day",count
        if vals["Volume"].iloc[-1]<=0:
            return "last_session_zero_volume",count
        return "partial_60_plus_observed_not_scored",count
    except Exception:
        return "source_frame_unverified",0


def main():
    import FinanceDataReader as fdr
    import time
    day="2026-10-19"
    rows=json.loads(Path("docs/v22-market-catalog.json").read_text(encoding="utf8"))
    targets=selection(rows,day)
    start=(datetime.fromisoformat(day)-timedelta(days=1100)).strftime("%Y-%m-%d")
    end=(datetime.fromisoformat(day)+timedelta(days=1)).strftime("%Y-%m-%d")
    bands=Counter()
    reasons=Counter()
    for idx,code in enumerate(targets):
        try:
            frame=fdr.DataReader(code,start,end)
            result,n=source_day_diagnostics(frame,day)
        except Exception:
            result,n="vendor_error_without_raw",0
        bands[length_band(n)]+=1
        reasons[result]+=1
        if idx<len(targets)-1:time.sleep(0.15)
    assert sum(bands.values())==len(targets)
    assert sum(reasons.values())==len(targets)
    print(json.dumps({
        "asof":day,
        "status":"research_only_partial_history_bands",
        "current_short_history_issuers":len(targets),
        "history_bands":dict(sorted(bands.items())),
        "source_status":dict(sorted(reasons.items())),
        "full_v2_160_bar_score_unaltered":True,
        "historical_price_data_or_symbol_ids_exported":False,
    },ensure_ascii=False,sort_keys=True))


if __name__=="__main__":
    main()
