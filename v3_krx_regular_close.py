"""Regular-close cutoff for KRX equity evidence timestamps."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DEFAULT_CLOSE = time(15, 30)

ANNOUNCED_CLOSES = {
    # https://kind.krx.co.kr/external/2025/10/30/000102/20251030000137/99303.htm
    date(2025, 11, 13): time(16, 30),
    # KRX exam-day session schedule as published by Yuanta Securities.
    # https://02.myasset.com/myasset/customer/notice/CU_0201000_P2.cmd?SEQ=202611131405580000000004&gubun=norNotice
    date(2026, 11, 19): time(16, 30),
}


def regular_close_utc(day):
    """Return the cutoff for a supplied day without asserting it is a session."""
    return datetime.combine(day, ANNOUNCED_CLOSES.get(day, DEFAULT_CLOSE),
                            tzinfo=KST).astimezone(timezone.utc)
