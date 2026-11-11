"""Trace observation time without claiming a feed is KRX regular-session-only.

As of 2026-11, Naver FChart day bars can include prices from the KRX
16:00-20:00 aftermarket.  A capture-time bucket is evidence of *exposure*,
not proof that any particular closing price came from that session.
"""
from datetime import datetime, time

FEED = 'NAVER_FCHART_DAY_UNVERIFIED_SESSION'
WINDOWS = ('before_regular', 'regular_market', 'post_regular_pre_aftermarket',
           'aftermarket_1600_2000', 'after_aftermarket', 'unknown')


def capture_window(stamp):
    if not isinstance(stamp, str):
        return 'unknown'
    parsed = None
    for fmt in ('%Y-%m-%d %H:%M:%S KST', '%Y-%m-%d %H:%M KST'):
        try:
            parsed = datetime.strptime(stamp, fmt)
            break
        except ValueError:
            pass
    if parsed is None:
        return 'unknown'
    moment = parsed.time()
    if moment < time(9):
        return 'before_regular'
    if moment < time(15, 30):
        return 'regular_market'
    if moment < time(16):
        return 'post_regular_pre_aftermarket'
    if moment < time(20):
        return 'aftermarket_1600_2000'
    return 'after_aftermarket'
