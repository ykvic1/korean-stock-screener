"""Unscored 20/60-session structural stop *reference*, never an order level.

The input is daily OHLCV from the upstream provider. Daily candle volume
cannot prove actual volume executed at a particular price, and regular-session
and corporate-action source verification is not performed by this module.
"""

import math

import numpy as np


def _round(value):
    return round(float(value), 2) if value is not None else None


def _pivots(lows, span=3):
    """A low is confirmed only after *span* subsequent closed sessions."""
    return [(i, float(lows[i])) for i in range(span, len(lows) - span)
            if lows[i] == min(lows[i-span:i+span+1])
            and int(np.argmin(lows[i-span:i+span+1])) == span]


def _lower_zone(high, low, close, volume, price):
    """Daily typical-price volume proxy, NOT exchange volume-at-price."""
    if len(close) < 60 or not np.any(volume > 0):
        return None
    # 2% price buckets; only positive, observed daily share volume is used.
    buckets = {}
    for h, l, c, v in zip(high[-120:], low[-120:], close[-120:], volume[-120:]):
        if v <= 0:
            continue
        typical = (h + l + c) / 3
        idx = int(math.floor(math.log(typical / price) / math.log(1.02)))
        band_low = price * (1.02 ** idx)
        if band_low * 1.02 >= price * .98:
            continue
        n, amount = buckets.get(idx, (0, 0.))
        buckets[idx] = (n + 1, amount + float(v))
    # At least 3 separate candles avoids interpreting one isolated spike as a zone.
    eligible = [(idx, n, amount) for idx, (n, amount) in buckets.items() if n >= 3]
    if not eligible:
        return None
    # More repeated volume first; tie goes to nearer support.
    idx, n, amount = max(eligible, key=lambda b: (b[2], b[0]))
    lower = price * (1.02 ** idx)
    return {'lower': _round(lower), 'upper': _round(lower * 1.02),
            'observed_daily_candles': n,
            'method': 'daily_typical_price_volume_proxy',
            'exact_volume_at_price': False}


def _pattern_support(high, low, close, patterns):
    if not isinstance(patterns, dict):
        return None
    price = float(close[-1])
    if patterns.get('is_ihs') and len(low) >= 90:
        p = _pivots(low[-90:])
        for a, b, c in reversed(list(zip(p[:-2], p[1:-1], p[2:]))):
            if (18 <= c[0] - a[0] <= 72
                    and b[1] < min(a[1], c[1]) * .955
                    and abs(a[1] - c[1]) / max(a[1], c[1]) < .09
                    and price > c[1] * 1.025 and c[1] < price):
                return {'price': _round(c[1]), 'pattern': '역헤드앤숄더',
                        'basis': '우측 어깨 저점 (휴리스틱)'}
    if len(low) >= 60 and (patterns.get('is_wedge') or patterns.get('is_triangle')):
        trend = np.polyfit(np.arange(60), low[-60:], 1)
        value = float(np.polyval(trend, 59))
        if 0 < value < price:
            return {'price': _round(value),
                    'pattern': '하락쐐기' if patterns.get('is_wedge') else '삼각수렴',
                    'basis': '최근 60일 저가 하단 추세선 근사 (확정 무효화 아님)'}
    return None


def observe(df, *, asof, patterns=None):
    """Calculate an advisory support with traceable inputs; never award points."""
    result = {'schema': 1, 'asof': asof, 'score_impact': 0,
              'unscored_reference_only': True,
              'krx_regular_session_verified': False,
              'corporate_actions_verified': False,
              'low20': None, 'low60': None, 'confirmed_swing_low': None,
              'lower_volume_zone': None, 'pattern_invalidation': None,
              'reference_line': None, 'distance_pct': None,
              'basis': [], 'confluence_count': 0,
              'confidence': 'unavailable', 'warning': None}
    if df is None or len(df) < 20 or not {'High', 'Low', 'Close'}.issubset(df.columns):
        result['warning'] = '20거래일 이상 유효한 일봉 자료 부족'
        return result
    try:
        high = df['High'].to_numpy(dtype=float)
        low = df['Low'].to_numpy(dtype=float)
        close = df['Close'].to_numpy(dtype=float)
        volume = (df['Volume'].to_numpy(dtype=float)
                  if 'Volume' in df.columns else np.full(len(df), np.nan))
    except (ValueError, TypeError):
        result['warning'] = '일봉 자료 형식 오류'
        return result
    if (not (np.isfinite(high).all() and np.isfinite(low).all()
             and np.isfinite(close).all()) or np.any(low <= 0)
            or np.any(high < low) or np.any(close < low)
            or np.any(close > high)):
        result['warning'] = '가격 원천값 무효 또는 결측'
        return result
    price = float(close[-1])
    result['low20'] = _round(np.min(low[-20:]))
    if len(low) >= 60:
        result['low60'] = _round(np.min(low[-60:]))
    swings = [(i, v) for i, v in _pivots(low[-60:]) if v < price]
    if swings:
        # Most recently confirmed 60-day swing, rather than a hindsight trough.
        result['confirmed_swing_low'] = _round(swings[-1][1])
    if (np.isfinite(volume).all() and np.all(volume >= 0)
            and len(low) >= 60):
        result['lower_volume_zone'] = _lower_zone(high, low, close, volume, price)
    result['pattern_invalidation'] = _pattern_support(high, low, close, patterns)

    # 20/60 lows and a swing point may be the *same trading day's low*.
    # Treat them as one price-structure family, not independent confirmations.
    evidence = [('20일 저점', result['low20'], 'price'),
                ('60일 저점', result['low60'], 'price'),
                ('확인된 스윙 저점', result['confirmed_swing_low'], 'price'),
                ('일봉 기반 추정 하단 매물대',
                 (result['lower_volume_zone'] or {}).get('lower'), 'volume'),
                ('패턴 무효화 추정',
                 (result['pattern_invalidation'] or {}).get('price'), 'pattern')]
    levels = [(name, value, family) for name, value, family in evidence
              if value is not None and 0 < value < price * .98]
    if not levels:
        result['warning'] = '현 종가보다 충분히 아래에 있는 유효 지지 후보 없음'
        return result
    # Identify the strongest *distinct evidence* cluster within a 4% band.
    # Equal-size clusters prefer the one closest below today's close.
    clusters = []
    for _, anchor, _ in levels:
        group = [(n, v, family) for n, v, family in levels
                 if abs(v / anchor - 1) <= .04]
        clusters.append(group)
    best = max(clusters, key=lambda group: (len({family for _, _, family in group}),
                                           len(group), min(v for _, v, _ in group)))
    if len({family for _, _, family in best}) < 2:
        # No confluence: use the latest confirmed swing, then 60/20 day low.
        preferred = ['확인된 스윙 저점', '60일 저점', '20일 저점',
                     '일봉 기반 추정 하단 매물대', '패턴 무효화 추정']
        best = [next((item for item in levels if item[0] == name), None)
                for name in preferred]
        best = [next(item for item in best if item is not None)]
    support = min(value for _, value, _ in best)
    # Half-percent confirmation buffer below supporting price, not a promise
    # of executable exit price. Supports are invalidation *references* only.
    reference = support * .995
    result.update(reference_line=_round(reference),
                  distance_pct=round(100 * (price - reference) / price, 2),
                  basis=[name for name, _, _ in best],
                  confluence_count=len({family for _, _, family in best}),
                  confidence=('confluence' if len({family for _, _, family in best}) >= 2
                              else 'single_support'))
    if result['distance_pct'] > 15:
        result['warning'] = '참고선까지 하락 여지가 15% 초과 · 추격 진입 주의'
    elif result['distance_pct'] < 3:
        result['warning'] = '참고선이 종가에 너무 근접 · 일간 변동성 확인 필요'
    return result
