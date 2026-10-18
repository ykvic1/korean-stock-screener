"""Offline two-market ingestion resiliency with injected read-only page source.

No network, key handling, automatic retries beyond bounded attempts, archive
writes, uploads or public release. Both markets are ALL-OR-NOTHING, matching
a separately declared historical issuer roster (still unauthenticated).
"""
from __future__ import annotations
import hashlib
import json
import math
import re

MARKETS=("KOSPI","KOSDAQ")
MAX_MARKET_ROWS=20000

class RetryableCollectionError(Exception):
    """Only this exception can be automatically retried a bounded number of times."""

class IngestionError(ValueError):
    """Fixed reason codes, no raw values or credentials."""


def collect_atomic_pair(fetch_page, *, asof, expected_roster,
                        max_pages_per_market=100, attempts_per_page=3):
    """Return private in-memory canonical byte pairs; nothing committed on failure.

    fetch_page(market, asof, cursor) -> {market, asof, rows, next_cursor}.
    Network/rate limiting are external caller responsibilities.
    """
    from datetime import date
    try:
        if type(asof) is not str or date.fromisoformat(asof).isoformat()!=asof:
            raise ValueError()
    except ValueError:
        raise IngestionError("invalid_ingestion_date") from None
    if (not callable(fetch_page) or not isinstance(expected_roster,dict)
            or set(expected_roster)!=set(MARKETS)
            or type(max_pages_per_market) is not int
            or not 1<=max_pages_per_market<=200
            or type(attempts_per_page) is not int
            or not 1<=attempts_per_page<=5):
        raise IngestionError("invalid_ingestion_settings")
    expected={}
    for market in MARKETS:
        rows=expected_roster[market]
        if not isinstance(rows,list) or not 1<=len(rows)<=MAX_MARKET_ROWS:
            raise IngestionError("invalid_expected_roster")
        checked=set()
        tickers=set()
        for row in rows:
            if not isinstance(row,dict):
                raise IngestionError("invalid_roster_member")
            code,issuer=row.get("code"),row.get("issuer_id")
            if (type(code) is not str or not re.fullmatch(r"[0-9]{6}",code)
                    or type(issuer) is not str or not re.fullmatch(r"[0-9]{8}",issuer)
                    or (code in tickers) or (code,issuer) in checked):
                raise IngestionError("duplicated_or_invalid_roster_identity")
            tickers.add(code);checked.add((code,issuer))
        expected[market]=checked
    # Stage privately IN MEMORY, never fall back to yesterday's snapshot.
    staged={};audit={}
    for market in MARKETS:
        data={}
        visited=set()
        cursor=None
        pages=0
        retry_counts=0
        while True:
            if pages>=max_pages_per_market:
                raise IngestionError("collection_page_limit_or_missing_terminal_page")
            key=cursor if cursor is not None else "__START__"
            if key in visited:
                raise IngestionError("repeated_pagination_cursor")
            visited.add(key)
            page=None
            for attempt in range(attempts_per_page):
                try:
                    page=fetch_page(market,asof,cursor)
                    break
                except RetryableCollectionError:
                    retry_counts+=1
                    if attempt+1==attempts_per_page:
                        raise IngestionError("retry_budget_exhausted") from None
                except Exception:
                    raise IngestionError("provider_fatal_or_unexpected_error") from None
            if not isinstance(page,dict):
                raise IngestionError("invalid_provider_page")
            if page.get("asof")!=asof or page.get("market")!=market:
                raise IngestionError("wrong_day_or_market_response")
            rows=page.get("rows")
            nxt=page.get("next_cursor")
            if not isinstance(rows,list) or not rows or len(rows)>1000:
                raise IngestionError("empty_or_oversized_provider_page")
            if nxt is not None and (
                    type(nxt) is not str or not 1<=len(nxt)<=128
                    or nxt=="__START__"):
                raise IngestionError("invalid_pagination_cursor")
            for row in rows:
                if not isinstance(row,dict):
                    raise IngestionError("invalid_provider_record")
                code,issuer=row.get("code"),row.get("issuer_id")
                identity=(code,issuer)
                if (row.get("market")!=market or row.get("asof")!=asof
                        or identity not in expected[market]
                        or code in data):
                    raise IngestionError("unexpected_duplicate_or_stale_security")
                volume=row.get("volume")
                close=row.get("close")
                if (type(volume) is not int or volume<0 or
                        type(close) not in (int,float) or
                        not math.isfinite(close) or close<=0):
                    raise IngestionError("invalid_price_volume_record")
                data[code]={
                    "market":market,"code":code,"issuer_id":issuer,
                    "asof":asof,"close":float(close),"volume":volume
                }
            pages+=1
            if nxt is None:
                break
            cursor=nxt
        got={(r["code"],r["issuer_id"]) for r in data.values()}
        if got!=expected[market]:
            raise IngestionError("incomplete_or_survivor_only_roster")
        raw=json.dumps([data[code] for code in sorted(data)],
                       sort_keys=True,separators=(",",":"),allow_nan=False
                       ).encode("utf-8")
        staged[market]=raw
        audit[market]={
            "records":len(data),"pages":pages,"retryable_failures":retry_counts,
            "canonical_sha256":hashlib.sha256(raw).hexdigest()
        }
    return {
        "schema":"v3-offline-two-market-atomic-ingestion-0.1",
        "asof":asof,"market_audit":audit,
        "private_unpublished_payload_bytes":staged,
        "both_markets_captured_together":True,
        "collection_adapter_injected_no_builtin_network":True,
        "expected_rosters_independently_authenticated":False,
        "official_provider_data_verified":False,
        "safe_for_public_release":False,
        "confirmed_buy":False,
    }
