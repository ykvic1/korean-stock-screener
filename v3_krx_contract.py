"""v3 KRX daily OHLCV evidence adapter — prevents aggregate volume leakage.

KRX daily price fields and ACC_TRDVOL may be observed in an authorized
official probe, but the daily total has NOT been established as KRX 09:00-15:30
REGULAR-ONLY volume. Do not infer it from its source, a timestamp, or a flag.

No network calls. Input from existing official_probe.validation.inspect_krx.
No scoring or confirmed production session until an independently verified,
licensed regular-only volume contract and multi-date history exist.
"""
from __future__ import annotations

import re

from session_policy import REQUIRED_SESSION, assess

SCHEMA = "v3-krx-stock-session-evidence-0.1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
CODE = re.compile(r"[0-9]{6}\Z")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def normalize_daily_observation(inspection: dict, code: str) -> dict:
    """Promote numeric observation ONLY; never promote the trading session."""
    if not isinstance(inspection, dict) or not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError("Invalid KRX observation identity")
    source_hash = inspection.get("raw_source_sha256")
    day = inspection.get("trade_date")
    market = inspection.get("market")
    if (not isinstance(source_hash, str) or not SHA.fullmatch(source_hash)
            or not isinstance(day, str) or not DATE.fullmatch(day)
            or market not in ("KOSPI", "KOSDAQ")
            or inspection.get("price_session_validated") is not False
            or inspection.get("volume_session_validated") is not False
            or inspection.get("investor_flow_validated") is not False):
        raise ValueError("Official daily report must retain its non-certified session state")
    values = inspection.get("symbol_data")
    if not isinstance(values, dict) or code not in values:
        raise ValueError("Requested stock missing from KRX daily observation")
    raw = values[code]
    if (not isinstance(raw, dict) or raw.get("code") != code
            or raw.get("market") != market
            or raw.get("trade_date") != day
            or raw.get("regular_volume") is not None
            or raw.get("price_session") != "UNVERIFIED"
            or raw.get("volume_session") != "UNVERIFIED"
            or raw.get("confirmed_scoring_allowed") is not False):
        raise ValueError("Raw numeric observation must not masquerade as regular session")
    prices = raw.get("ohlc_observed")
    if not isinstance(prices, dict) or set(prices) != {"open","high","low","close"}:
        raise ValueError("Exact KRX OHLC fields required")
    if any(type(prices[x]) is not int or prices[x] <= 0 for x in prices):
        raise ValueError("KRX OHLC must be positive integers")
    if (prices["low"] > min(prices["open"],prices["close"])
            or prices["high"] < max(prices["open"],prices["close"])
            or prices["low"] > prices["high"]):
        raise ValueError("KRX OHLC range invalid")
    aggregate = raw.get("reported_acc_trdvol")
    if type(aggregate) is not int or aggregate < 0:
        raise ValueError("KRX daily accumulated volume must be nonnegative integer")
    # Required session validation contains evidence beyond one daily row.
    session_input = {
        "asof": day, "source": "KRX_OPENAPI_STOCK_DAILY",
        "received_at": None,
        "price_session": "UNVERIFIED", "volume_session": "UNVERIFIED",
        "official_close_status": "unverified", "final": False,
        "official_source_sha256": source_hash,
        "history_session_verified": False,
    }
    policy = assess(session_input)
    if policy["confirmed_scoring_allowed"] is not False:
        raise ValueError("Unexpected regular-session promotion")
    return {
        "schema": SCHEMA,
        "code": code,
        "market": market,
        "trade_date": day,
        "source": "KRX_OPENAPI_STOCK_DAILY",
        "source_sha256": source_hash,
        "ohlc_observed": dict(prices),
        "aggregate_acc_trdvol_observed": aggregate,
        "aggregate_volume_session_certified_regular_only": False,
        "regular_only_volume": None,
        "price_session": "UNVERIFIED",
        "volume_session": "UNVERIFIED",
        "price_close_independently_reconciled": False,
        "official_eod_price_sample_received": True,
        "corporate_actions_verified": False,
        "indicator_history_session_verified": False,
        "investor_flow_confirmed": False,
        "technical_indicators_eligible": False,
        "confirmed_scoring_allowed": False,
        "confirmed_score": None,
        "reasons": list(policy["reasons"]) + [
            "KRX_ACC_TRDVOL_regular_only_basis_not_certified",
            "independent_daily_close_reconciliation_missing",
            "full_regular_session_history_and_corporate_actions_missing",
        ],
    }


def check_no_session_promotion(observation: dict) -> bool:
    """Hard-fail if any normalized candidate pretends to be a scored bar."""
    if not isinstance(observation, dict) or observation.get("schema") != SCHEMA:
        raise ValueError("Not a v3 KRX observation")
    if (observation.get("price_session") != "UNVERIFIED"
            or observation.get("volume_session") != "UNVERIFIED"
            or observation.get("regular_only_volume") is not None
            or observation.get("aggregate_volume_session_certified_regular_only") is not False
            or observation.get("technical_indicators_eligible") is not False
            or observation.get("confirmed_scoring_allowed") is not False
            or observation.get("confirmed_score") is not None):
        raise ValueError("Unverified KRX daily observation cannot earn scores")
    return True
