"""Reusable conservative source-day price sanity gate for recent KOSPI/KOSDAQ listings.

Return stable status codes only; never export OHLCV rows, vendor exceptions,
stock identifiers or false complete 160-day/120-week technical observations.
"""
from __future__ import annotations
from datetime import datetime

MIN_SHORT_RESEARCH_BARS=60


def source_day_diagnostics(frame,day):
    """Inspect an in-memory DataFrame; do not infer suspended vs unlisted."""
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
