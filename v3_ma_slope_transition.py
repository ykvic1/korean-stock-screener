"""Derived, provisional MA down-to-up transitions from already fetched price history.

The six daily/weekly components share a five-point budget. This is a bounded
observation, not independent KRX adjustment verification or a trading signal.
Neither original OHLCV bars nor a reconstructable MA series leave the scanner.
"""
from datetime import date
import math

SCHEMA = 'v3-ma-log-slope-transition-provisional-0.1'
WEIGHTS = {'daily': {'20': .5, '60': .6, '120': .9},
           'weekly': {'20': .6, '60': .9, '120': 1.5}}
DAILY_REQUIRED = 120 + 60 + 20
WEEKLY_REQUIRED = 120 + 26 + 8


def _series_points(closes, *, prior, recent, hold, floor, frame, trusted_window):
    import numpy as np  # Only the full-market scanner needs numerical dependencies.
    parts = {}
    for period, maximum in WEIGHTS[frame].items():
        n = int(period)
        required = n + prior + recent
        if len(closes) < required or not trusted_window(required):
            # A shorter MA is independently observable even when the 120-week
            # window is missing, stale, or invalid. Never award its weight.
            parts[period] = None
            continue
        ma = np.convolve(closes[-required:], np.ones(n) / n, mode='valid')
        early, mid, late = ma[-1-recent-prior], ma[-1-recent], ma[-1]
        prior_rate = math.log(mid / early) / prior
        recent_rate = math.log(late / mid) / recent
        rising = int(np.count_nonzero(np.diff(ma[-hold-1:]) > 0))
        # Current positive slope alone earns no turn score. A sustained
        # historical decline must precede a recent positive reversal.
        if prior_rate >= -floor or recent_rate <= 0 or rising < hold - 1:
            points = 0.
        else:
            strength = min(1., recent_rate / abs(prior_rate))
            points = maximum * strength * (.4 + .6*rising/hold)
        parts[period] = round(max(0., min(maximum, points)), 6)
    return parts if any(points is not None for points in parts.values()) else None


def observe(daily):
    """Validate each MA's necessary history, not unrelated older raw bars.

    A bad older candle can invalidate the 154-week calculation without
    erasing an independently intact 200-session daily calculation. Neither
    frame is scored when its own required candles fail OHLCV checks.
    """
    import numpy as np
    import pandas as pd
    if (not isinstance(daily, pd.DataFrame)
        or not isinstance(daily.index, pd.DatetimeIndex)
        or daily.index.has_duplicates or not daily.index.is_monotonic_increasing
        or len(daily) < 20 or not set(('Open','High','Low','Close','Volume')).issubset(daily.columns)):
        return None
    def trusted(frame, *, require_traded_last):
        a = frame[['Open','High','Low','Close','Volume']].to_numpy(dtype=float)
        if (not np.isfinite(a).all() or np.any(a[:,:4] <= 0)
            or np.any(a[:,4] < 0)
            or (require_traded_last and a[-1, 4] <= 0)
            or np.any(a[:,1] < np.maximum(a[:,0], a[:,3]))
            or np.any(a[:,2] > np.minimum(a[:,0], a[:,3]))):
            return False
        # Price-only MAs may retain a genuine no-trade session, but a bar
        # reporting zero transactions cannot independently create a new close.
        # Never replace an absent/zero close or infer a suspension from it.
        # A split-like jump in the required window can manufacture a turn.
        closes = a[:,3]
        return (not np.any(np.abs(closes[1:] / closes[:-1] - 1) > .5)
                and not np.any((a[1:,4] == 0) & (closes[1:] != closes[:-1])))

    day = daily.index[-1].date().isoformat()
    recent_day = _series_points(daily['Close'].to_numpy(dtype=float),
        prior=60, recent=20, hold=5, floor=.0001, frame='daily',
        trusted_window=lambda required: trusted(
            daily.iloc[-required:], require_traded_last=True))
    weeks = daily[['Close']].resample('W-FRI').last().dropna()
    weeks = weeks.loc[weeks.index <= daily.index[-1].normalize()]
    def trusted_week_window(required):
        weekly_tail = weeks.iloc[-required:]
        bars_in_weekly_window = daily.loc[
            weekly_tail.index[0] - pd.Timedelta(days=6):weekly_tail.index[-1]]
        # A completed Friday can legitimately have no transactions. Validate
        # each MA against only the OHLCV dates it actually consumes.
        return bool(len(bars_in_weekly_window)) and trusted(
            bars_in_weekly_window, require_traded_last=False)

    recent_week = _series_points(weeks['Close'].to_numpy(dtype=float),
        prior=26, recent=8, hold=3, floor=.0005, frame='weekly',
        trusted_window=trusted_week_window)
    if recent_day is None and recent_week is None:
        return None
    return {'schema': SCHEMA, 'asof': day,
            'weekly_asof': weeks.index[-1].date().isoformat() if recent_week is not None else None,
            'parts': {'daily': recent_day, 'weekly': recent_week},
            'source_regular_session_verified': False,
            'source_corporate_action_adjusted_verified': False,
            'raw_ohlcv_exported': False, 'investment_score_certified': False}


def valid_observation(o, asof):
    try:
        if (type(o) is not dict or set(o) != {'schema','asof','weekly_asof','parts',
            'source_regular_session_verified','source_corporate_action_adjusted_verified',
            'raw_ohlcv_exported','investment_score_certified'} or o['schema'] != SCHEMA
            or o['asof'] != asof or date.fromisoformat(asof).isoformat() != asof
            or any(o[k] is not False for k in ('source_regular_session_verified',
                'source_corporate_action_adjusted_verified','raw_ohlcv_exported',
                'investment_score_certified')) or set(o['parts']) != set(WEIGHTS)):
            return False
        for frame, specs in WEIGHTS.items():
            part = o['parts'][frame]
            if part is not None and (type(part) is not dict or set(part) != set(specs) or
                not any(value is not None for value in part.values()) or
                any(part[n] is not None and (type(part[n]) not in (float,int)
                    or not math.isfinite(part[n]) or not 0 <= part[n] <= maximum)
                    for n,maximum in specs.items())):
                return False
        if o['parts']['daily'] is None and o['parts']['weekly'] is None:
            return False
        w = o['weekly_asof']
        return ((w is None and o['parts']['weekly'] is None) or
                (type(w) is str and date.fromisoformat(w).isoformat() == w
                 and w <= asof and date.fromisoformat(w).weekday() == 4
                 and o['parts']['weekly'] is not None))
    except (KeyError,TypeError,ValueError,AttributeError):
        return False
