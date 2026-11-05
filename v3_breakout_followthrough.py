"""Bounded, price-only R1d follow-through after a 60-session closing breakout.

This is deliberately NOT the same-day volume breakout, MA cross, pattern
candidate or post-volume-spike absorption signal. It requires five completed
sessions after an independently reconstructed resistance break. All ratings
remain source-provisional until official OHLCV and corporate-action audits.
"""
from datetime import date
import math

SCHEMA='v3-price-only-60-session-breakout-followthrough-0.1'


def _day(value):
    if type(value) is date:return value
    if callable(getattr(value,'date',None)):
        result=value.date()
        return result if type(result) is date else None
    return None


def observe(close,*,asof,session_days):
    try:
        target=date.fromisoformat(asof)
        if target.isoformat()!=asof:return None
    except (ValueError,TypeError):return None
    if session_days is None or len(close)!=len(session_days) or len(close)<90:return None
    prices=list(close[-90:]);dates=[_day(x) for x in session_days[-90:]]
    try:
        if any(isinstance(x,bool) for x in prices):return None
        prices=[float(x) for x in prices]
    except (ValueError,TypeError,OverflowError):return None
    if (any(x is None for x in dates) or dates[-1]!=target or
        any(not 0<(b-a).days<=10 for a,b in zip(dates,dates[1:])) or
        any(not math.isfinite(x) or x<=0 for x in prices)):
        return None
    if any(abs(prices[i]/prices[i-1]-1)>.5 for i in range(1,len(prices))):
        return None
    # Event must be 6..20 complete trading sessions old. A fresh break with
    # fewer than five follow-through sessions CANNOT be prematurely confirmed.
    held=[];now=len(prices)-1
    for i in range(now-20,now-5):
        resistance=max(prices[i-60:i])
        if prices[i]<resistance*1.005:continue
        if min(prices[i+1:i+6])<resistance*.985:continue
        if prices[-1]<resistance:continue
        extension=prices[-1]/resistance
        held.append((i,extension))
    if held:
        index,extension=held[-1]
        score=1.2 if extension<=1.08 else .6 if extension<=1.15 else 0.
        age=now-index
    else:
        score=0.;age=None
    item={'schema':SCHEMA,'asof':asof,'lookback_sessions':90,
          'confirmed_breakout_age_sessions':age,'points':score,
          'confirmation_sessions':5,'price_only_and_independent_of_volume':True,
          'fresh_breakout_without_followthrough_not_confirmed':True,
          'source_regular_session_verified':False,'corporate_actions_verified':False,
          'investment_score_certified':False}
    return item if valid(item,asof) else None


def valid(item,asof):
    if type(item) is not dict or set(item)!={
        'schema','asof','lookback_sessions','confirmed_breakout_age_sessions',
        'points','confirmation_sessions','price_only_and_independent_of_volume',
        'fresh_breakout_without_followthrough_not_confirmed',
        'source_regular_session_verified','corporate_actions_verified',
        'investment_score_certified'}:
        return False
    age=item['confirmed_breakout_age_sessions']
    return (item['schema']==SCHEMA and item['asof']==asof and
        item['lookback_sessions']==90 and item['confirmation_sessions']==5 and
        type(item['points']) in (float,int) and item['points'] in (0,.6,1.2) and
        (age is None and item['points']==0 or type(age) is int and 6<=age<=20) and
        item['price_only_and_independent_of_volume'] is True and
        item['fresh_breakout_without_followthrough_not_confirmed'] is True and
        item['source_regular_session_verified'] is False and
        item['corporate_actions_verified'] is False and
        item['investment_score_certified'] is False)
