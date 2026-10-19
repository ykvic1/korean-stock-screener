"""Safe v2.2 price exception diagnostics: never copy provider exception text.

No changes to price validation, no stale-price promotion, no vendor OHLCV
storage. Outcome names are intentionally fixed and suitable for public daily
coverage aggregation.
"""
from __future__ import annotations

import re

REASONS = frozenset({
    "price_empty_history", "price_missing_ohlcv_columns",
    "price_null_ohlcv", "price_stale_history", "price_zero_last_volume_or_close",
    "price_zero_latest_close", "price_zero_latest_volume",
    "price_numeric_conversion", "price_other_value_error",
    "price_network_timeout", "price_network_connection",
    "price_unexpected_exception",
})


def reason(exc: Exception) -> str:
    """Classify only known local failures, never emit arbitrary supplier text."""
    if isinstance(exc, TimeoutError):
        return "price_network_timeout"
    if isinstance(exc, (ConnectionError, BrokenPipeError)):
        return "price_network_connection"
    if isinstance(exc, ValueError):
        msg = str(exc)
        literal = {
            "가격 이력 없음": "price_empty_history",
            "필수 OHLCV 누락": "price_missing_ohlcv_columns",
            "OHLCV 결측 데이터": "price_null_ohlcv",
            "현재가/거래량 오류": "price_zero_last_volume_or_close",
            "현재 종가 0이하": "price_zero_latest_close",
            "현재 거래량 0이하": "price_zero_latest_volume",
            "가격 이력 최신성 오류": "price_stale_history",
        }
        if msg in literal:
            return literal[msg]
        # Only numeric conversion subclasses from the underlying DataReader,
        # never store their potentially sensitive embedded values.
        if type(exc) is ValueError and (
            msg.startswith("could not convert string to float")
            or msg.startswith("could not convert string to int")
        ):
            return "price_numeric_conversion"
        return "price_other_value_error"
    return "price_unexpected_exception"


def public_reason(exc: Exception) -> str:
    label = reason(exc)
    if label not in REASONS or not re.fullmatch(r"[a-z_]{1,64}", label):
        return "price_unexpected_exception"
    return label
