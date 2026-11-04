"""Private derived-only actual KRX trading-value five-session research.

Read ACC_TRDVAL directly; never derive turnover from Close*Volume.
Do not retain source daily amount, OHLCV, response bytes or vendor keys.
Observed KRX EOD scope is NOT independently certified regular-session only.
No money signal is admitted to investment scores by this module.
"""
from __future__ import annotations

import gzip
from hashlib import sha256
import json
import math
import re
import time as retry_clock

from official_probe.validation import _numeric
from v3_krx_history_collection import _snapshot_market
from v3_krx_daily_anomaly_triage import classify_row
from v3_krx_derived_5day import (
    _check_days, _private_new_blob_readback, DerivedSourceError,
)
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError

SCHEMA = "v3-krx-private-actual-money-five-session-research-0.1"
MAX_DATA_BYTES = 900_000
MONEY_BUCKET_KRW = 100_000
MARKETS = ("KOSPI", "KOSDAQ")


def _row_values(row):
    """Return source-reported amount and observed close; fail closed."""
    if classify_row(row)[0] != "valid_numeric_observation":
        return None
    try:
        amount = _numeric(row["ACC_TRDVAL"], "ACC_TRDVAL")
        close = _numeric(row["TDD_CLSPRC"], "TDD_CLSPRC", positive=True)
    except (KeyError, TypeError, ValueError):
        return None
    if amount >= 10**17:
        return None
    return float(close), amount


def _bounded_money_pressure(amounts, closes):
    # One of five days has no preceding close INSIDE this five-day cohort.
    signed = sum((1 if closes[i] > closes[i-1] else
                  -1 if closes[i] < closes[i-1] else 0) * amounts[i]
                 for i in range(1, 5))
    reference = sum(amounts[1:])
    return round(10000 * signed / reference) if reference else None


def derive(client, days):
    """Derive bounded cross-cohort-combinable money aggregates in process."""
    days = _check_days(days)
    issuer_rows = {market: {} for market in MARKETS}
    coverage = {}
    raw_bytes = 0
    completed_calls = 0
    stage = "transport"
    current_day = None
    current_market = None
    try:
        for day in days:
            for market in MARKETS:
                current_day, current_market = day, market
                stage = "transport"
                rows, raw = client.daily_market(market, day)
                stage = "source_validation"
                checked = _snapshot_market(rows, raw, market=market, asof=day)
                raw_bytes += len(raw)
                completed_calls += 1
                coverage[f"{day}/{market}"] = {
                    "source_rows": checked["provider_rows_observed"],
                    "ohlcv_usable": checked["observed_numeric_candidates"],
                    "amount_usable": 0,
                    "amount_missing_or_invalid": 0,
                }
                stage = "issuer_identity"
                for row in rows:
                    code = row["ISU_CD"]
                    if type(code) is not str or not re.fullmatch(r"[0-9A-Z]{6}", code):
                        raise DerivedSourceError("bad_observed_stock_identity")
                    pair = _row_values(row)
                    if pair is None:
                        coverage[f"{day}/{market}"]["amount_missing_or_invalid"] += 1
                        continue
                    issuer_rows[market].setdefault(code, {})[day] = pair
                    coverage[f"{day}/{market}"]["amount_usable"] += 1
                # A market-wide missing ACC_TRDVAL field is a provider-schema
                # regression, not a successfully collected zero-money cohort.
                if coverage[f"{day}/{market}"]["amount_usable"] == 0:
                    raise DerivedSourceError("market_missing_reported_money_field")
                del rows, raw
    except Exception as failure:
        err = DerivedSourceError("official_money_window_or_validation_failed")
        err.safe_failure_phase = stage
        err.safe_source_day = current_day
        err.safe_source_market = current_market
        err.safe_completed_market_calls = completed_calls
        from official_probe.clients import KRXProbeError
        if stage == "transport" and isinstance(failure, KRXProbeError):
            err.safe_krx_reason = failure.reason
            err.safe_krx_http_status = failure.http_status
        raise err from None
    if completed_calls != 10 or len(coverage) != 10:
        raise DerivedSourceError("incomplete_official_money_window")
    factors = {}
    for market, by_code in issuer_rows.items():
        for code, obs in sorted(by_code.items()):
            if len(obs) != 5:
                continue
            closes = [obs[d][0] for d in days]
            amounts = [obs[d][1] for d in days]
            total = sum(amounts)
            head, tail = sum(amounts[:2]), sum(amounts[-2:])
            # Symmetric bounded relative increase: never a discontinuous step.
            persistence_bp = round(10000 * (tail-head) / (tail+head)) if tail+head else None
            price_move = (closes[-1] / closes[0] - 1) * 10000
            factors[f"{market}:{code}"] = {
                "market": market,
                "issuer_code": code,
                "observed_sessions": 5,
                "total_actual_amount_100k_krw": round(total / MONEY_BUCKET_KRW),
                "money_pressure_intrabucket_bp": _bounded_money_pressure(amounts, closes),
                "late2_vs_early2_money_pressure_bp": persistence_bp,
                "intrabucket_price_change_bp": round(price_move)
                if math.isfinite(price_move) and abs(price_move) < 1e7 else None,
                "positive_amount_days": sum(a > 0 for a in amounts),
                "money_amount_is_explicit_acc_trdval": True,
                "five_day_cohort_boundary_price_sign_missing": True,
                "historical_issuer_identity_verified": False,
                "regular_session_only_verified": False,
                "corporate_actions_verified": False,
                "score_authorized": False,
            }
    result = {
        "schema": SCHEMA, "window_start": days[0], "window_end": days[-1],
        "source": "KRX_OPEN_API_ACC_TRDVAL_AGGREGATE_UNVERIFIED",
        "source_api_calls": completed_calls,
        "market_date_coverage": coverage,
        "complete_five_session_issuer_market_pairs": len(factors),
        "derived_issuer_money_factors": factors,
        "amount_bucket_krw": MONEY_BUCKET_KRW,
        "daily_amount_or_price_bars_retained": False,
        "raw_vendor_source_retained": False,
        "publicly_published": False,
        "investment_scoring_allowed": False,
    }
    blob = gzip.compress(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf8"), compresslevel=9, mtime=0)
    if not 0 < len(blob) < MAX_DATA_BYTES:
        raise DerivedSourceError("money_derived_payload_size_invalid")
    return result, blob, raw_bytes


def validate_money_blob(blob, start, end):
    try:
        if type(blob) is not bytes or not 0 < len(blob) < MAX_DATA_BYTES:
            raise ValueError()
        decoded = gzip.decompress(blob)
        if len(decoded) > 6_000_000:
            raise ValueError()
        result = json.loads(decoded)
        if (result.get("schema") != SCHEMA or result.get("window_start") != start
            or result.get("window_end") != end
            or result.get("source_api_calls") != 10
            or result.get("daily_amount_or_price_bars_retained") is not False
            or result.get("raw_vendor_source_retained") is not False
            or result.get("investment_scoring_allowed") is not False
            or type(result.get("derived_issuer_money_factors")) is not dict
            or type(result.get("market_date_coverage")) is not dict
            or len(result["market_date_coverage"]) != 10):
            raise ValueError()
        factors = result["derived_issuer_money_factors"]
        if len(factors) != result.get("complete_five_session_issuer_market_pairs"):
            raise ValueError()
        for key, factor in factors.items():
            if (key != factor.get("market", "") + ":" + factor.get("issuer_code", "")
                or factor.get("observed_sessions") != 5
                or type(factor.get("total_actual_amount_100k_krw")) is not int
                or factor["total_actual_amount_100k_krw"] < 0
                or factor.get("score_authorized") is not False
                or factor.get("money_amount_is_explicit_acc_trdval") is not True
                or any(raw in factor for raw in
                       ("ACC_TRDVAL", "TDD_CLSPRC", "Amount", "Close", "Volume"))):
                raise ValueError()
        return result
    except Exception:
        raise DerivedSourceError("private_money_derived_blob_invalid") from None


def store_money_five_days(storage: PrivateGitStorage, client, days):
    days = _check_days(days)
    if storage.repository != "ykvic1/korean-stock-private-data":
        raise DerivedSourceError("unexpected_destination_for_money_research")
    storage.verify_private()
    path = f"derived/money-five-session/{days[0]}_{days[-1]}.json.gz"
    existing, _ = storage.get_content(path)
    if existing is not None:
        data = validate_money_blob(existing, days[0], days[-1])
        return {"status": "verified_existing_money", "source_api_calls": 0,
                "derived_file_bytes": len(existing),
                "complete_issuer_pairs": data["complete_five_session_issuer_market_pairs"]}
    data, blob, _ = derive(client, days)
    commit_message = "Archive derived KRX actual-value factors " + days[-1]
    try:
        storage.put_new(path, blob, commit_message)
    except PrivateHistoryError as err:
        if not (
            type(err) is PrivateHistoryError
            and str(err) == "private_storage_api_failed"
            and getattr(err, "safe_http_operation", None) == "write_private_content"
            and getattr(err, "safe_http_status", None) == "http_409"
        ):
            raise
        # Source bytes were already retrieved. Never overwrite another
        # writer's immutable cohort. Read it back twice before one attempt
        # to create the SAME content without SHA (cannot replace a file).
        for attempt in range(2):
            observed, _ = storage.get_content(path)
            if observed is not None:
                if observed != blob:
                    raise DerivedSourceError("conflicting_private_money_immutable_window")
                break
            if attempt == 0:
                retry_clock.sleep(1)
        if observed is None:
            storage.put_new(path, blob, commit_message)
    verified = _private_new_blob_readback(storage, path, blob)
    validate_money_blob(verified, days[0], days[-1])
    return {"status": "persisted_and_verified_money", "source_api_calls": 10,
            "derived_file_bytes": len(blob),
            "complete_issuer_pairs": data["complete_five_session_issuer_market_pairs"]}
