"""Bounded read-only KRX daily-market history collection, never a live signal.

Each explicitly selected historical day requests BOTH KOSPI and KOSDAQ once.
A single date's raw source bytes are never confused with a 09:00–15:30-only
session, adjusted prices, independent history certification, or investor flow.
Default mode does not call KRX and does not save vendor data. Live is opt-in.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone, time
from zoneinfo import ZoneInfo
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from official_probe.clients import KRXClient, KRXProbeError, ProbeError
from official_probe.validation import inspect_krx
from v3_krx_daily_anomaly_triage import classify_row, new_counts, validate_quality_counts
from v3_krx_zero_price_detail import (
    empty_zero_details, observe_zero_price_row, validate_zero_details)
from v3_local_private_store import store_pair, PrivateStoreError, inspect_private_store

MAX_DATES_PER_BATCH = 5
TARGET_PAIRED_DAYS = 250
MARKETS = ("KOSPI", "KOSDAQ")


class HistoryCollectionError(ValueError):
    """Public-safe fixed code and bounded context, never vendor messages."""

    def __init__(self, reason, *, market=None, trade_date=None,
                 completed_pairs=0, http_status=None,
                 code_anomaly_counts=None):
        safe = (reason if type(reason) is str
                and re.fullmatch(r"[a-z0-9_]{1,100}", reason)
                else "unclassified_collection_error")
        if market not in MARKETS:
            market = None
        if (type(trade_date) is not str
                or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", trade_date)):
            trade_date = None
        self.market = market
        self.trade_date = trade_date
        self.completed_pairs = completed_pairs if (type(completed_pairs) is int
                                                    and 0 <= completed_pairs <= MAX_DATES_PER_BATCH) else 0
        self.http_status = http_status if (type(http_status) is int
                                           and 100 <= http_status <= 599) else None
        # Fixed-size numerical aggregate only. Never include source codes,
        # issuer names, untrusted vendor strings, response body or auth key.
        allowed = ("missing_code", "non_string_code", "non_six_digit_code",
                   "duplicate_six_digit_code", "prefixed_six_digit_pattern",
                   "twelve_character_pattern")
        if (isinstance(code_anomaly_counts, dict)
                and set(code_anomaly_counts) == set(allowed)
                and all(type(code_anomaly_counts[key]) is int
                        and 0 <= code_anomaly_counts[key] <= 20000
                        for key in allowed)):
            self.code_anomaly_counts = {
                key: code_anomaly_counts[key] for key in allowed}
        else:
            self.code_anomaly_counts = None
        super().__init__(safe)


def validate_dates(dates):
    if (not isinstance(dates, list) or not 1 <= len(dates) <= MAX_DATES_PER_BATCH
            or any(type(x) is not str for x in dates)):
        raise HistoryCollectionError("one_to_five_explicit_trade_dates_required")
    accepted = []
    for item in dates:
        try:
            if (not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item)
                    or date.fromisoformat(item).isoformat() != item):
                raise ValueError()
            day = date.fromisoformat(item)
        except ValueError:
            raise HistoryCollectionError("invalid_trade_date") from None
        # Weekends are not active KRX regular sessions. Weekdays are merely
        # CANDIDATES, never independently certified exchange sessions.
        if day.weekday() >= 5:
            raise HistoryCollectionError("non_session_weekend_date")
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        if day > now_kst.date():
            raise HistoryCollectionError("future_trade_date_not_allowed")
        if day == now_kst.date() and now_kst.time() < time(15, 30):
            raise HistoryCollectionError("session_not_yet_completed")
        if accepted and item <= accepted[-1]:
            raise HistoryCollectionError("duplicate_or_unsorted_dates")
        accepted.append(item)
    return accepted


def _inspect_market_code_shapes(rows):
    """Inspect every code before rejection; return only privacy-safe counts.

    KRX ISU_CD is a string, NOT a six-decimal-digit guarantee.
    Some Korean share tickers use six ASCII uppercase alphanumeric chars,
    including letters in positions other than the last. Preserve the opaque
    value exactly, never guess issuer identity or dedupe by name.
    """
    counts = {
        "missing_code": 0, "non_string_code": 0,
        "non_six_digit_code": 0, "duplicate_six_digit_code": 0,
        "prefixed_six_digit_pattern": 0,
        "twelve_character_pattern": 0,
    }
    seen = set()
    for record in rows:
        if not isinstance(record, dict):
            raise HistoryCollectionError("invalid_market_member")
        code = record.get("ISU_CD")
        if code is None or code == "":
            counts["missing_code"] += 1
        elif type(code) is not str:
            counts["non_string_code"] += 1
        elif not re.fullmatch(r"[0-9A-Z]{6}", code):
            # Legacy reason key retained for historical diagnostic continuity.
            # It now means unsupported six-character uppercase alnum shape.
            counts["non_six_digit_code"] += 1
            if re.fullmatch(r"A[0-9]{6}", code):
                counts["prefixed_six_digit_pattern"] += 1
            if len(code) == 12:
                counts["twelve_character_pattern"] += 1
        elif code in seen:
            counts["duplicate_six_digit_code"] += 1
        else:
            seen.add(code)
    kinds = [
        name for name in ("missing_code", "non_string_code",
                          "non_six_digit_code", "duplicate_six_digit_code")
        if counts[name] > 0
    ]
    if kinds:
        reason = (kinds[0] if len(kinds) == 1
                  else "multiple_market_code_identity_anomalies")
        raise HistoryCollectionError(reason, code_anomaly_counts=counts)
    return counts


def _snapshot_market(rows, raw, *, market, asof):
    """Internal market rows and OHLC quarantine, never print price values."""
    if (not isinstance(rows, list) or not 0 < len(rows) <= 20000
            or type(raw) is not bytes or not raw):
        raise HistoryCollectionError("invalid_market_snapshot")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get("OutBlock_1") != rows:
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise HistoryCollectionError("market_rows_disagree_with_raw_source") from None
    _inspect_market_code_shapes(rows)
    seen = set()
    usable = []
    quarantined = 0
    reason_counts = new_counts()
    zero_price_details = empty_zero_details()
    zero_aggregate_volume_rows = 0
    for item in rows:
        if not isinstance(item, dict):
            raise HistoryCollectionError("invalid_market_member")
        code = item.get("ISU_CD")
        if (type(code) is not str or not re.fullmatch(r"[0-9A-Z]{6}", code)
                or code in seen):
            raise HistoryCollectionError("ambiguous_or_duplicate_market_code")
        seen.add(code)
        if str(item.get("BAS_DD", "")).replace("-", "") != asof.replace("-", ""):
            raise HistoryCollectionError("cross_date_market_data")
        reason, zero_vol = classify_row(item)
        reason_counts[reason] += 1
        if reason == "zero_or_negative_price":
            try:
                observe_zero_price_row(item, zero_price_details)
            except ValueError:
                raise HistoryCollectionError("zero_detail_accounting_invalid") from None
        if reason != "valid_numeric_observation":
            # Invalid OHLC cannot be fabricated or silently repaired.
            # No evidence yet whether a halt, corporate action or vendor
            # missing value was responsible.
            quarantined += 1
            continue
        if zero_vol:
            zero_aggregate_volume_rows += 1
        usable.append(item)
    try:
        validate_quality_counts(reason_counts, len(rows), zero_aggregate_volume_rows)
        validate_zero_details(zero_price_details, reason_counts["zero_or_negative_price"])
    except ValueError:
        raise HistoryCollectionError("daily_anomaly_accounting_invalid") from None
    if not usable:
        raise HistoryCollectionError("no_usable_ohlcv_market_rows")
    numeric_codes = [r for r in usable if re.fullmatch(r"[0-9]{6}", r["ISU_CD"])]
    alphanumeric_codes = [
        r for r in usable if not re.fullmatch(r"[0-9]{6}", r["ISU_CD"])]
    # Existing independent OHLC check deliberately has a decimal-only code
    # contract. Use it unchanged for that subset, NOT by stripping letters
    # from the other valid six-character KRX identifiers.
    if numeric_codes:
        try:
            check = inspect_krx(numeric_codes, market, asof,
                                {r["ISU_CD"] for r in numeric_codes}, raw)
        except (ValueError, ProbeError):
            raise HistoryCollectionError("market_ohlcv_contract_failed") from None
        if check["missing_symbols"] or len(check["found_symbols"]) != len(numeric_codes):
            raise HistoryCollectionError("market_member_count_inconsistent")
    if len(numeric_codes) + len(alphanumeric_codes) != len(usable):
        raise HistoryCollectionError("market_member_count_inconsistent")
    return {
        "market": market, "asof": asof,
        "provider_rows_observed": len(rows),
        "observed_numeric_candidates": len(usable),
        "six_digit_numeric_code_rows": len(numeric_codes),
        "six_character_alphanumeric_code_rows": len(alphanumeric_codes),
        "opaque_alphanumeric_identity_reconciled_with_official_master": False,
        "quarantined_unpriced_candidates": quarantined,
        "price_volume_anomaly_reason_counts": reason_counts,
        "zero_price_ohlc_field_counts": zero_price_details,
        "zero_accumulated_volume_on_numeric_rows": zero_aggregate_volume_rows,
        "halt_or_delisting_cause_verified": False,
        "source_sha256": sha256(raw).hexdigest(),
        "regular_session_volume_verified": False,
        "corporate_actions_verified": False,
        "historical_issuer_universe_verified": False,
        "confirmed_scoring_allowed": False,
    }


def collect_krx_history(dates, *, live=False, key=None,
                        client=None, archive_path=None,
                        consent_to_private_retention=False):
    """Collect up to 5 explicitly selected session pairs; no silent fallback.

    If archiving is enabled, commit each complete two-market day atomically.
    A later day failing preserves previous complete pairs; the whole batch
    raises an error and does not emit a fake successful batch report.
    """
    valid = validate_dates(dates)
    if live is not True:
        if archive_path is not None:
            raise HistoryCollectionError("dry_run_cannot_archive")
        return {
            "schema": "v3-krx-bounded-daily-history-0.1",
            "mode": "dry_run", "requested_days": valid,
            "planned_market_calls": len(valid)*2,
            "target_paired_trading_sessions": TARGET_PAIRED_DAYS,
            "observed_days": 0, "markets": MARKETS,
            "verified_history_250_sessions": False,
            "official_regular_only_volume_verified": False,
            "v3_release_ready": False, "confirmed_buy": False,
        }
    if not isinstance(key, str) or not key.strip():
        raise HistoryCollectionError("krx_key_required")
    if archive_path is not None and consent_to_private_retention is not True:
        raise HistoryCollectionError("explicit_private_storage_consent_required")
    # Never archive raw vendor payloads in GitHub Actions default ephemeral
    # workspace. Explicit local database must be outside the public repository.
    api = client if client is not None else KRXClient(key)
    completed = []
    for day in valid:
        raws = {}
        inspections = {}
        received = {}
        for market in MARKETS:
            try:
                rows, raw = api.daily_market(market, day)
                inspections[market] = _snapshot_market(
                    rows, raw, market=market, asof=day)
            except KRXProbeError as err:
                raise HistoryCollectionError(
                    "krx_" + market.lower() + "_" + err.reason,
                    market=market, trade_date=day,
                    completed_pairs=len(completed),
                    http_status=err.http_status) from None
            except HistoryCollectionError as err:
                # Exact, internally coded market-schema reason is safe to
                # retain; previously this was mistaken for a transport error.
                raise HistoryCollectionError(
                    str(err), market=market, trade_date=day,
                    completed_pairs=len(completed),
                    code_anomaly_counts=err.code_anomaly_counts) from None
            except Exception:
                # Never echo third-party exceptions; they can contain a
                # request URL, header or token. Unknown failures stay unknown.
                raise HistoryCollectionError(
                    "unclassified_market_processing_error",
                    market=market, trade_date=day,
                    completed_pairs=len(completed)) from None
            raws[market] = raw
            received[market] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        if archive_path is not None:
            try:
                store_pair(
                    database_path=archive_path, asof=day,
                    kospi_raw=raws["KOSPI"], kosdaq_raw=raws["KOSDAQ"],
                    kospi_received_at_utc=received["KOSPI"],
                    kosdaq_received_at_utc=received["KOSDAQ"])
            except PrivateStoreError:
                raise HistoryCollectionError(
                    "private_two_market_atomic_archive_rejected",
                    market=None, trade_date=day,
                    completed_pairs=len(completed)) from None
        completed.append({
            "asof": day,
            "markets": [inspections[m] for m in MARKETS],
            "stored_in_private_sqlite": archive_path is not None,
        })
    return {
        "schema": "v3-krx-bounded-daily-history-0.1",
        "mode": "live", "observed_days": len(completed),
        "requested_days": valid, "days": completed,
        "target_paired_trading_sessions": TARGET_PAIRED_DAYS,
        "paired_dates_unverified_as_true_exchange_calendar": True,
        "official_regular_only_volume_verified": False,
        "adjusted_price_events_verified": False,
        "foreign_institution_pension_flows_verified": False,
        "historical_full_universe_verified": False,
        "verified_history_250_sessions": False,
        "archive_is_independently_authenticated": False,
        "v3_release_ready": False, "confirmed_buy": False,
    }


def _approved_summary_path(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HistoryCollectionError("invalid_summary_location")
    dest = Path(value)
    project = Path(__file__).resolve().parent
    artifact_root = project / "evidence" / "krx_history"
    resolved = dest.resolve()
    if (resolved == project or
            (project in resolved.parents
             and artifact_root != resolved
             and artifact_root not in resolved.parents)):
        raise HistoryCollectionError("summary_location_not_approved")
    return dest


def _write_private_summary(dest, payload):
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="KRX two-market bounded historical collection, private only")
    parser.add_argument("--dates", required=True,
                        help="1-5 ascending completed weekdays, comma-separated YYYY-MM-DD")
    parser.add_argument("--live", action="store_true", help="Opt in to 2 requests per selected date")
    parser.add_argument("--private-db", help="Optional durable SQLite outside repository")
    parser.add_argument("--ack-private-storage", action="store_true",
                        help="Affirm you reviewed official contract for local raw data retention")
    parser.add_argument("--output", help="Optional PRIVATE summary JSON, never under docs/")
    args = parser.parse_args(argv)
    dest = None
    try:
        dest = _approved_summary_path(args.output)
        if args.private_db and not args.live:
            raise HistoryCollectionError("dry_run_cannot_archive")
        result = collect_krx_history(
            [x.strip() for x in args.dates.split(",")],
            live=args.live, key=os.getenv("KRX_API_KEY"),
            archive_path=args.private_db,
            consent_to_private_retention=args.ack_private_storage)
        _write_private_summary(dest, result)
        # Summary contains aggregate counts and hashes, never original payload.
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except HistoryCollectionError as exc:
        failure = {
            "schema": "v3-krx-bounded-daily-history-failure-0.1",
            "status": "blocked",
            "reason": str(exc),
            "failed_market": exc.market,
            "failed_date": exc.trade_date,
            "completed_pairs_before_failure": exc.completed_pairs,
            "http_status": exc.http_status,
            "code_anomaly_counts": exc.code_anomaly_counts,
            "v3_release_ready": False,
            "verified_history_250_sessions": False,
            "confirmed_buy": False,
        }
        # Unlike old code, retain a SAFE diagnostic artifact when KRX fails.
        # Never include vendor exception bodies, token, request URL or raw bytes.
        _write_private_summary(dest, failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
