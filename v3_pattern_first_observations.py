"""Causal, unscored daily-pattern observations for a chart-first user watchlist.

These are fixed geometric heuristics, not chart recognition certifications.
Never create investor flow, official-price evidence, or investment decisions.
"""
import numpy as np
import pandas as pd
from technical_v22 import rsi, divergence, pattern_snapshot

SCHEMA = "v3-pattern-first-daily-research-0.1"
# The oldest OHLCV error must not suppress a current 90-bar pattern when a
# separate, long-enough recent window is independently clean. Daily RSI has
# 70 additional warm-up observations; weekly RSI has ~30 extra weeks.
DAILY_CLEAN_TAIL = 160
WEEKLY_CLEAN_TAIL_DAYS = 600


def _clean_ohlcv(frame):
    a = frame[["Open", "High", "Low", "Close", "Volume"]].to_numpy(dtype=float)
    valid = (np.isfinite(a).all() and np.all(a[:, :4] > 0) and
            np.all(a[:, 4] >= 0) and
            np.all(a[:, 1] >= np.maximum(a[:, 0], a[:, 3])) and
            np.all(a[:, 2] <= np.minimum(a[:, 0], a[:, 3])) and
            np.all(a[:, 2] <= a[:, 1]))
    return a if valid else None


def _recent_contiguous_clean_tail(frame, *, maximum_bars, minimum_bars=90):
    """Use only actual consecutive valid source rows after the last bad bar.

    An older zero/invalid price is neither a verified trading suspension nor
    proof of pre-listing. Never fill or carry it forward; discard that prefix
    only when the remaining *real* bars independently meet the study window.
    """
    tail = frame.iloc[-maximum_bars:]
    if _clean_ohlcv(tail) is not None:
        return (tail, True) if len(frame) > len(tail) else (frame, False)
    a = tail[["Open", "High", "Low", "Close", "Volume"]].to_numpy(dtype=float)
    valid = (np.isfinite(a).all(axis=1) & np.all(a[:, :4] > 0, axis=1)
             & (a[:, 4] >= 0)
             & (a[:, 1] >= np.maximum(a[:, 0], a[:, 3]))
             & (a[:, 2] <= np.minimum(a[:, 0], a[:, 3]))
             & (a[:, 2] <= a[:, 1]))
    bad = np.flatnonzero(~valid)
    suffix = tail.iloc[int(bad[-1]) + 1:]
    if len(suffix) < minimum_bars or _clean_ohlcv(suffix) is None:
        return None, False
    return suffix, True


def _pivots_low(a, span=3, start=0):
    # A trough needs span *subsequent* candles to be confirmed.
    result = []
    for i in range(max(span, start), len(a) - span):
        window = a[i - span:i + span + 1]
        if a[i] == np.min(window) and np.count_nonzero(window == a[i]) == 1:
            result.append(i)
    return result


def _double_bottom(close, high, low, volume):
    n = len(close)
    piv = _pivots_low(low, span=3, start=n - 90)
    avg20 = float(np.mean(volume[-21:-1]))
    for a, b in reversed(list(zip(piv[:-1], piv[1:]))):
        if not 9 <= b - a <= 50 or n - 1 - b > 22:
            continue
        bottom1, bottom2 = float(low[a]), float(low[b])
        if abs(bottom2 / bottom1 - 1) > 0.065:
            continue
        neckline = float(max(high[a:b + 1]))
        base = max(bottom1, bottom2)
        if neckline < base * 1.08 or neckline > min(bottom1, bottom2) * 1.45:
            continue
        breakout = close[-1] > neckline * 1.003 and volume[-1] >= 1.2 * avg20
        forming = (close[-1] >= base * 1.018 and close[-1] <= neckline * 1.03
                   and close[-1] >= min(bottom1, bottom2) * 1.02)
        if breakout or forming:
            return {"type": "double_bottom",
                    "stage": "breakout_observed" if breakout else "forming",
                    "reference_neckline": round(neckline, 2),
                    "reference_low": round(min(bottom1, bottom2), 2)}
    return None


def _bull_flag(close, high, low, volume):
    # 23 prior candles for the rising pole; 11 completed flag candles;
    # the latest candle is evaluated only against previous flag resistance.
    n = len(close)
    if n < 90:
        return None
    pole_start = float(close[-36])
    pole_high = float(np.max(high[-19:-12]))
    if pole_start <= 0:
        return None
    gain = pole_high / pole_start - 1
    if not 0.16 <= gain <= 0.90:
        return None
    # Pole high must occur near the end of the pole, not 25 days earlier.
    if np.max(high[-35:-19]) > pole_high:
        return None
    flag_high = high[-12:-1]
    flag_low = low[-12:-1]
    top = float(np.max(flag_high))
    bottom = float(np.min(flag_low))
    retracement = (pole_high - bottom) / max(1e-9, pole_high - pole_start)
    drawdown = 1 - bottom / pole_high
    if not (0.15 <= retracement <= 0.55 and 0.025 <= drawdown <= 0.18):
        return None
    # Flag cannot itself be a steep new uptrend.
    slope = float(np.polyfit(np.arange(len(flag_high)),
                             np.log(flag_high), 1)[0])
    if slope > 0.003:
        return None
    pole_vol = float(np.mean(volume[-30:-13]))
    flag_vol = float(np.mean(volume[-12:-1]))
    if pole_vol <= 0 or flag_vol <= 0 or flag_vol >= 0.9 * pole_vol:
        return None
    if close[-1] < bottom * 0.985 or close[-1] > pole_high * 1.15:
        return None
    breakout = close[-1] > top * 1.002 and volume[-1] >= 1.3 * flag_vol
    forming = close[-1] >= bottom * 1.015 and close[-1] <= top * 1.012
    if not breakout and not forming:
        return None
    return {"type": "bull_flag",
            "stage": "breakout_observed" if breakout else "forming",
            "reference_breakout": round(top, 2),
            "reference_flag_low": round(bottom, 2)}


def rsi_observation(close):
    """Compute the existing RSI evidence from chronological closing prices."""
    rs = rsi(close)
    bull, _ = divergence(close, rs, span=3, max_age=40)
    cross_idx = np.where((rs[1:] >= 30) & (rs[:-1] < 30))[0] + 1
    return {
        "rsi14": round(float(rs[-1]), 2) if np.isfinite(rs[-1]) else None,
        "rsi30_cross_recent": bool(len(cross_idx) and cross_idx[-1] >= len(rs)-7
                                   and 30 <= rs[-1] <= 60),
        "rsi_bullish_divergence_recent": bool(np.any(bull[-12:])),
    }


def observe(df):
    """Use only supplied daily OHLCV <= the source date, no future candles."""
    out = {"schema": SCHEMA, "status": "insufficient", "timeframe": "daily",
           "patterns": [], "rsi14": None, "rsi30_cross_recent": False,
           "rsi_bullish_divergence_recent": False,
           "rsi_bullish_divergence_basis": "confirmed closing-price pivot",
           "official_regular_session_verified": False, "scoring_allowed": False,
           "buy_signal": False}
    if df is None or len(df) < 90 or not isinstance(df.index, pd.DatetimeIndex) or (
        df.index.has_duplicates or not df.index.is_monotonic_increasing) or not all(
        key in df.columns for key in ("Open", "High", "Low", "Close", "Volume")
    ):
        return out
    clean_tail_only = False
    if _clean_ohlcv(df) is None:
        df, clean_tail_only = _recent_contiguous_clean_tail(
            df, maximum_bars=DAILY_CLEAN_TAIL)
        if df is None:
            return out
    a = _clean_ohlcv(df)
    close, high, low, vol = a[:, 3], a[:, 1], a[:, 2], a[:, 4]
    patterns = [p for p in (_bull_flag(close, high, low, vol),
                              _double_bottom(close, high, low, vol)) if p]
    out.update(asof=df.index[-1].date().isoformat(),
               status="heuristic_unverified",
               pattern_geometry=pattern_snapshot(df),
               source_recent_clean_window_only=clean_tail_only,
               patterns=patterns, **rsi_observation(close))
    return out


def observe_completed_weekly(daily):
    """Independently inspect ONLY fully completed Friday-ending OHLCV weeks.

    A Monday-Thursday partial week has artificially low cumulative volume,
    so letting it form a flag or RSI cross would leak unfinished evidence.
    A holiday-shortened week is included once its calendar Friday is reached.
    """
    result = {"schema": SCHEMA, "timeframe": "weekly",
              "status": "insufficient", "asof": None,
              "patterns": [], "rsi14": None,
              "rsi30_cross_recent": False,
              "rsi_bullish_divergence_recent": False,
              "official_regular_session_verified": False,
              "scoring_allowed": False, "buy_signal": False}
    if daily is None or len(daily) < 90 or not isinstance(daily.index, pd.DatetimeIndex):
        return result
    if not daily.index.is_monotonic_increasing or daily.index.has_duplicates:
        return result
    keys = ["Open", "High", "Low", "Close", "Volume"]
    if not all(k in daily.columns for k in keys):
        return result
    # A source row with no trades can carry the last observed close while
    # reporting no intraday prices. Build OHLC from observed trades in that
    # week, keeping a whole no-trade week unavailable.
    d = daily[keys]
    no_trade_marker = (
        (d['Volume'] == 0) & (d['Close'] > 0)
        & (d['Close'] == d['Close'].shift())
        & (d[['Open', 'High', 'Low']] == 0).all(axis=1))
    if no_trade_marker.any():
        week_labels = d.index.to_period('W-FRI')
        traded_weeks = week_labels[d['Volume'] > 0]
        removable = no_trade_marker & week_labels.isin(traded_weeks)
        d = d.loc[~removable]
    # Validate every remaining daily bar before aggregation.
    clean_tail_only = False
    if _clean_ohlcv(d) is None:
        d, clean_tail_only = _recent_contiguous_clean_tail(
            d, maximum_bars=WEEKLY_CLEAN_TAIL_DAYS)
        if d is None:
            return result
    week = d.resample("W-FRI").agg({"Open": "first", "High": "max",
                                    "Low": "min", "Close": "last",
                                    "Volume": "sum"}).dropna()
    # Weekend of the most recent daily candle may still be in the future.
    # Drop that partial bin rather than using inflated/deflated week volume.
    latest = daily.index[-1].normalize()
    week = week.loc[week.index <= latest]
    if len(week) < 90:
        return result
    study = observe(week)
    if study["status"] != "heuristic_unverified":
        return result
    study["timeframe"] = "weekly"
    study["source_recent_clean_window_only"] = clean_tail_only
    # Same-price-source geometry: falling wedge, inverse H&S, triangle and
    # post-rise compression. Each is only a *heuristic candidate*.
    study["pattern_geometry"] = pattern_snapshot(week)
    study["completed_week_only"] = True
    study["weekly_ma120_gap_pct"] = (
        round(100 * (float(week["Close"].iloc[-1]) /
                     float(week["Close"].iloc[-120:].mean()) - 1), 4)
        if len(week) >= 120 else None
    )
    study["asof"] = week.index[-1].date().isoformat()
    return study
