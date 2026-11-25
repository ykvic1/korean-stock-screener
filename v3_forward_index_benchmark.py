"""Date-matched market index reference; reuse the screener's existing FDR index source."""
from datetime import date, timedelta
import math

MARKETS = {'KOSPI': 'KS11', 'KOSDAQ': 'KQ11'}
SOURCE = 'FinanceDataReader KS11/KQ11 (secondary index reference)'


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def index_return(frame, entry, evaluation):
    """Require exact dates and positive OHLC. No nearest-day fallback."""
    if frame is None or frame.empty or not {'Open', 'Close'} <= set(frame.columns):
        return None
    import pandas as pd
    try:
        days = pd.to_datetime(frame.index, errors='raise')
        if days.isna().any() or days.tz is not None or days.normalize().duplicated().any():
            return None
        dates = days.strftime('%Y-%m-%d')
        left = frame.loc[dates == entry]
        right = frame.loc[dates == evaluation]
        if len(left) != 1 or len(right) != 1:
            return None
        opening, close = float(left.iloc[0]['Open']), float(right.iloc[0]['Close'])
        if not math.isfinite(opening) or not math.isfinite(close) or min(opening, close) <= 0:
            return None
        value = (close / opening - 1) * 100
        return round(value, 4) if math.isfinite(value) else None
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return None


def reference(entry, evaluation, *, reader=None, previous=None):
    """At most one free existing-source query per unresolved market, only after maturity."""
    if date.fromisoformat(entry) > date.fromisoformat(evaluation):
        raise ValueError('benchmark_invalid_window')
    if reader is None:
        try:
            import FinanceDataReader as fdr
            reader = fdr.DataReader
        except ImportError:
            reader = None
    prior = previous or {}
    result = {}
    for market, symbol in MARKETS.items():
        saved = prior.get(market)
        if finite(saved) and saved > -100:
            result[market] = saved
            continue
        result[market] = None
        if reader is None:
            continue
        try:
            frame = reader(symbol, entry, (date.fromisoformat(evaluation)+timedelta(days=1)).isoformat())
            result[market] = index_return(frame, entry, evaluation)
        except Exception:
            # Secondary-source outage must not block core price-path publication.
            continue
    return result


def summarize(groups, references, entry, evaluation, attempted_on):
    """Compare observed issuer returns with their own market, not an arbitrary index."""
    result = {}
    for label, group in groups.items():
        observed = group['valued_count']
        matched = 0
        weighted = 0.0
        details = {}
        for market in MARKETS:
            row = group['market_breakdown'][market]
            n, avg = row['valued_count'], row['observed_mean_return_pct']
            idx = references.get(market)
            excess = round(avg-idx,4) if n and finite(avg) and finite(idx) else None
            if excess is not None:
                matched += n
                weighted += n * excess
            details[market] = {'valued_count':n,'mean_return_pct':avg,'index_return_pct':idx,
                               'excess_return_pct_points':excess}
        result[label] = {'matched_valued_count':matched,'valued_count':observed,
                         'observed_mean_excess_return_pct_points':round(weighted/matched,4)
                         if observed and matched == observed else None,'markets':details}
    return {'source':SOURCE,'entry_session':entry,'evaluation_session':evaluation,
            'last_attempt_on':attempted_on,'official_krx_index_certified':False,
            'index_return_pct':references,'groups':result,
            'status':'observed_secondary_reference' if all(finite(references.get(m)) for m in MARKETS)
            else 'partial_secondary_reference' if any(finite(references.get(m)) for m in MARKETS)
            else 'index_source_unavailable'}
