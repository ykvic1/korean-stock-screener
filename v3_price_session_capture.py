"""Trace capture time without claiming a feed is KRX regular-session-only.

NXT aftermarket runs from 15:40 to 20:00, so 15:40-16:00 cannot be
classified as a pre-aftermarket interval even if a different venue begins
its aftermarket later. The capture bucket measures *exposure*, not proof
that any particular price was traded in that venue or interval.
"""
from datetime import datetime, time

FEED = 'NAVER_FCHART_DAY_UNVERIFIED_SESSION'
WINDOWS = ('before_regular', 'regular_market',
           'post_regular_pre_aftermarket_1530_1540',
           'nxt_aftermarket_1540_1600', 'aftermarket_1600_2000',
           'after_aftermarket', 'unknown')


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
    if moment < time(15, 40):
        return 'post_regular_pre_aftermarket_1530_1540'
    if moment < time(16):
        return 'nxt_aftermarket_1540_1600'
    if moment < time(20):
        return 'aftermarket_1600_2000'
    return 'after_aftermarket'
