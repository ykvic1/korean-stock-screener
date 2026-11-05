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


def _series_points(closes, *, prior, recent, hold, floor, frame):
    import numpy as np  # Only the full-market scanner needs numerical dependencies.
    parts = {}
    for period, maximum in WEIGHTS[frame].items():
        n = int(period)
        if len(closes) < n + prior + recent:
            return None
        ma = np.convolve(closes, np.ones(n) / n, mode='valid')
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
    return parts


def observe(daily):
    """Require ordered genuine completed daily bars; exclude unfinished weeks."""
    import numpy as np
    import pandas as pd
    if (not isinstance(daily, pd.DataFrame)
        or not isinstance(daily.index, pd.DatetimeIndex)
        or daily.index.has_duplicates or not daily.index.is_monotonic_increasing
        or len(daily) < 20 or not set(('Open','High','Low','Close','Volume')).issubset(daily.columns)):
        return None
    a = daily[['Open','High','Low','Close','Volume']].to_numpy(dtype=float)
    if (not np.isfinite(a).all() or np.any(a[:,:4] <= 0)
        or np.any(a[:,4] < 0) or np.any(a[:,1] < np.maximum(a[:,0], a[:,3]))
        or np.any(a[:,2] > np.minimum(a[:,0], a[:,3]))):
        return None
    day = daily.index[-1].date().isoformat()
    recent_day = _series_points(a[:,3], prior=60, recent=20, hold=5,
                                floor=.0001, frame='daily')
    weeks = daily[['Close']].resample('W-FRI').last().dropna()
    weeks = weeks.loc[weeks.index <= daily.index[-1].normalize()]
    recent_week = _series_points(weeks['Close'].to_numpy(dtype=float),
                                 prior=26, recent=8, hold=3,
                                 floor=.0005, frame='weekly')
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
                any(type(part[n]) not in (float,int) or not math.isfinite(part[n])
                    or not 0 <= part[n] <= maximum for n,maximum in specs.items())):
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
