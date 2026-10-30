"""Bounded real OpenDART filing-receipt archive for historic KRX risk research.

Official list.json receipt METADATA, not disclosure-body authentication, split
certification, PIT availability, corporate action evidence, or adjusted closes.
Four filing categories B/C/E/I only (incomplete event-type coverage). Source
metadata and corp IDs are PRIVATE; no user or stock records in public docs.
"""
from __future__ import annotations

import base64
from datetime import date
import gzip
from hashlib import sha256
from io import BytesIO
import json
import os
import re
from urllib import parse, request

from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA = "v3-private-dart-historical-filing-receipts-v1"
CHECKPOINT = "derived/dart-filing-receipts/checkpoint.json"
CATEGORIES = ("B", "C", "E", "I")
# Verified 48-page live pilot succeeded: bounded 72 pages per run across four
# KST slots, at most 288 official OpenDART list calls/day from this workflow.
# Each page is strictly validated and checkpointed before fetching the next.
MAX_API_CALLS = 72
MAX_FILE_BYTES = 150_000
PRIVATE_REPO = "ykvic1/korean-stock-private-data"
FIRST = (2016, 10)
LAST = (2026, 9)  # Complete, already closed filing months only.
MONTHS = tuple(
    f"{y:04d}-{m:02d}" for y in range(FIRST[0], LAST[0] + 1)
    for m in range(1, 13) if FIRST <= (y, m) <= LAST
)
RX_RECEIPT = re.compile(r"[0-9]{14}\Z")
RX_CORP = re.compile(r"[0-9]{8}\Z")
RX_STOCK = re.compile(r"[0-9]{6}\Z")
RX_HASH = re.compile(r"[a-f0-9]{64}\Z")


class DARTEventReceiptError(ValueError):
    """Static, non-sensitive failure reason only."""


def _bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _int(value):
    try:
        if type(value) is int:
            return value
        if type(value) is str and value.isascii() and value.isdecimal():
            return int(value)
    except (ValueError, OverflowError):
        pass
    raise DARTEventReceiptError("invalid_official_page_counts")


def _dates(month):
    y, m = map(int, month.split("-"))
    after = date(y + (m == 12), m % 12 + 1, 1)
    from datetime import timedelta
    return f"{y:04d}{m:02d}01", (after - timedelta(days=1)).strftime("%Y%m%d")


def _path(month, category, page):
    return f"derived/dart-filing-receipts/{month}/{category}/{page:04d}.json.gz"


def _decode(blob, month, category, page):
    try:
        if type(blob) is not bytes or not 0 < len(blob) < MAX_FILE_BYTES:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as f:
            data = f.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError()
        x = json.loads(data)
        if (x.get("schema") != SCHEMA or x.get("month") != month
            or x.get("filing_category") != category or x.get("page") != page
            or x.get("provider") != "OpenDART_list_json"
            or x.get("request_count") != 1
            or x.get("raw_response_stored") is not False
            or x.get("split_or_merger_verified") is not False
            or x.get("adjusted_price_verified") is not False
            or x.get("historical_pit_verified") is not False
            or x.get("score_authorized") is not False):
            raise ValueError()
        total, pages = x.get("total_count"), x.get("total_pages")
        rows = x.get("receipts")
        if (type(total) is not int or type(pages) is not int
            or not 0 <= total <= 10_000_000 or not 0 <= pages <= 100_000
            or pages != (total + 99) // 100 or type(rows) is not list
            or len(rows) > 100 or len(rows) > total
            or (pages == 0 and (page != 1 or rows))
            or (pages > 0 and (page > pages or len(rows) !=
                min(100, total - (page - 1) * 100)))):
            raise ValueError()
        start, end = _dates(month)
        seen = set()
        for r in rows:
            if not isinstance(r, dict):
                raise ValueError()
            receipt = r.get("rcept_no")
            corp = r.get("corp_code")
            stock = r.get("stock_code")
            filing_date = r.get("rcept_dt")
            title = r.get("report_nm")
            if (type(receipt) is not str or not RX_RECEIPT.fullmatch(receipt)
                or receipt in seen
                or type(corp) is not str or not RX_CORP.fullmatch(corp)
                or stock is not None and
                    (type(stock) is not str or not RX_STOCK.fullmatch(stock))
                or type(filing_date) is not str or not start <= filing_date <= end
                or type(title) is not str or not 0 < len(title) <= 500):
                raise ValueError()
            try:
                if date.fromisoformat(filing_date[:4] + "-" +
                                       filing_date[4:6] + "-" +
                                       filing_date[6:]).strftime("%Y%m%d") != filing_date:
                    raise ValueError()
            except ValueError:
                raise ValueError() from None
            seen.add(receipt)
        return x
    except Exception:
        raise DARTEventReceiptError("invalid_private_dart_receipt_page") from None


def parse_official_response(body, month, category, page):
    """Require exact official pagination and receipt dates; no guessing."""
    if type(body) is not dict or type(page) is not int or page < 1:
        raise DARTEventReceiptError("invalid_official_dart_response")
    status = body.get("status")
    if status == "013" and page == 1:
        total, pages, receipts = 0, 0, []
    elif status == "000":
        total, pages = _int(body.get("total_count")), _int(body.get("total_page"))
        if (pages != (total + 99) // 100 or page > pages
            or _int(body.get("page_no")) != page
            or _int(body.get("page_count")) != 100
            or type(body.get("list")) is not list):
            raise DARTEventReceiptError("inconsistent_dart_pagination")
        receipts = []
        for row in body["list"]:
            if type(row) is not dict:
                raise DARTEventReceiptError("invalid_official_dart_record")
            stock = row.get("stock_code")
            receipts.append({
                "rcept_no": row.get("rcept_no"),
                "corp_code": row.get("corp_code"),
                "stock_code": stock if stock else None,
                "rcept_dt": row.get("rcept_dt"),
                "report_nm": row.get("report_nm"),
            })
    else:
        # In particular, never retry authorization, quota, maintenance or
        # invalid-parameter responses as though they meant 'no filings'.
        raise DARTEventReceiptError("dart_provider_rejected_or_unavailable")
    data = {
        "schema": SCHEMA, "provider": "OpenDART_list_json",
        "month": month, "filing_category": category, "page": page,
        "total_count": total, "total_pages": pages, "receipts": receipts,
        "request_count": 1, "raw_response_stored": False,
        "split_or_merger_verified": False,
        "adjusted_price_verified": False, "historical_pit_verified": False,
        "score_authorized": False,
    }
    packed = gzip.compress(_bytes(data), compresslevel=9, mtime=0)
    _decode(packed, month, category, page)
    return packed


def official_fetch(api_key, month, category, page):
    if (type(api_key) is not str or not re.fullmatch(r"[A-Za-z0-9]{40}", api_key)
        or month not in MONTHS or category not in CATEGORIES
        or type(page) is not int or not 1 <= page <= 100_000):
        raise DARTEventReceiptError("invalid_dart_source_request")
    start, end = _dates(month)
    query = parse.urlencode({
        "crtfc_key": api_key, "bgn_de": start, "end_de": end,
        "last_reprt_at": "N", "pblntf_ty": category,
        "sort": "date", "sort_mth": "asc", "page_no": str(page),
        "page_count": "100",
    })
    # Do not log URL: it embeds the API key. No full response in errors.
    req = request.Request("https://opendart.fss.or.kr/api/list.json?" + query,
                          headers={"User-Agent": "v3-private-filing-receipts"})
    try:
        with request.urlopen(req, timeout=30) as resp:
            data = resp.read(1_500_001)
        if len(data) > 1_500_000:
            raise DARTEventReceiptError("dart_source_response_too_large")
        return json.loads(data)
    except DARTEventReceiptError:
        raise
    except Exception:
        raise DARTEventReceiptError("dart_source_transport_failed") from None


def _empty():
    return {"schema": SCHEMA, "month_index": 0, "category_index": 0,
            "page": 1, "saved_pages": 0, "saved_receipts": 0,
            "last_page_file": None, "last_page_sha256": None,
            "source_raw_retained": False, "corporate_events_verified": False}


def _check_cursor(raw):
    if raw is None:
        return _empty()
    try:
        obj = json.loads(raw)
        a, b, c = obj.get("month_index"), obj.get("category_index"), obj.get("page")
        if (obj.get("schema") != SCHEMA or type(a) is not int or
            type(b) is not int or type(c) is not int
            or not 0 <= a <= len(MONTHS) or not 0 <= b < len(CATEGORIES)
            or not 1 <= c <= 100_001
            or (a == len(MONTHS) and (b != 0 or c != 1))
            or type(obj.get("saved_pages")) is not int or obj["saved_pages"] < 0
            or type(obj.get("saved_receipts")) is not int or obj["saved_receipts"] < 0
            or obj.get("source_raw_retained") is not False
            or obj.get("corporate_events_verified") is not False):
            raise ValueError()
        f, h = obj.get("last_page_file"), obj.get("last_page_sha256")
        if obj["saved_pages"] == 0:
            if f is not None or h is not None:
                raise ValueError()
        elif (type(f) is not str or not f.startswith("derived/dart-filing-receipts/")
              or type(h) is not str or not RX_HASH.fullmatch(h)):
            raise ValueError()
        return obj
    except Exception:
        raise DARTEventReceiptError("invalid_private_dart_receipt_cursor") from None


def _check_page_sequence(previous, current):
    """Fail closed on snapshot drift or overlapping receipts across DART pages.

    A valid individual response is not enough: page 2 could have been fetched
    from a different result set if historical filings were reindexed mid-run.
    This check never claims that DART's pagination is a signed snapshot.
    """
    if (previous["month"] != current["month"]
        or previous["filing_category"] != current["filing_category"]
        or previous["page"] + 1 != current["page"]
        or previous["total_count"] != current["total_count"]
        or previous["total_pages"] != current["total_pages"]
        or {r["rcept_no"] for r in previous["receipts"]}.intersection(
            r["rcept_no"] for r in current["receipts"])):
        raise DARTEventReceiptError("inconsistent_dart_page_sequence")


def run(storage, fetch, max_calls=MAX_API_CALLS):
    if (storage.repository != PRIVATE_REPO or type(max_calls) is not int
        or not 1 <= max_calls <= MAX_API_CALLS):
        raise DARTEventReceiptError("untrusted_private_dart_collection_request")
    storage.verify_private()
    raw, oldsha = storage.get_content(CHECKPOINT)
    cursor = _check_cursor(raw)
    tail = None
    tail_page = None
    if cursor["last_page_file"] is not None:
        tail, _ = storage.get_content(cursor["last_page_file"])
        if (tail is None or sha256(tail).hexdigest() != cursor["last_page_sha256"]):
            raise DARTEventReceiptError("private_dart_receipt_tail_changed")
    calls = new_pages = reused_pages = new_receipts = 0
    while calls < max_calls and cursor["month_index"] < len(MONTHS):
        mi, ci, pn = (cursor["month_index"], cursor["category_index"],
                       cursor["page"])
        month, category = MONTHS[mi], CATEGORIES[ci]
        path = _path(month, category, pn)
        if pn > 1:
            if cursor["last_page_file"] != _path(month, category, pn - 1):
                raise DARTEventReceiptError("private_dart_receipt_cursor_chain_gap")
            if tail_page is None:
                tail_page = _decode(tail, month, category, pn - 1)
        else:
            tail_page = None
        saved, _ = storage.get_content(path)
        if saved is None:
            result = fetch(month, category, pn)
            calls += 1
            packed = parse_official_response(result, month, category, pn)
            page = _decode(packed, month, category, pn)
            if tail_page is not None:
                _check_page_sequence(tail_page, page)
            # Check the adjacent-page evidence BEFORE creating immutable storage:
            # a mismatched provider page must not poison every later retry.
            storage.put_new(path, packed, "Save private OpenDART historic filing receipt page")
            saved = packed
            new_pages += 1
        else:
            page = _decode(saved, month, category, pn)
            if tail_page is not None:
                _check_page_sequence(tail_page, page)
            reused_pages += 1
        n = len(page["receipts"])
        new_receipts += n
        if page["total_pages"] == 0 or pn == page["total_pages"]:
            ci += 1
            pn = 1
            if ci == len(CATEGORIES):
                mi += 1
                ci = 0
        else:
            pn += 1
        checkpoint = {**cursor, "month_index": mi, "category_index": ci,
                      "page": pn,
                      "saved_pages": cursor["saved_pages"] + 1,
                      "saved_receipts": cursor["saved_receipts"] + n,
                      "last_page_file": path,
                      "last_page_sha256": sha256(saved).hexdigest()}
        encoded = _bytes(checkpoint)
        if oldsha is None:
            storage.put_new(CHECKPOINT, encoded, "Start private DART filing receipt cursor")
        else:
            storage.transport("PUT", "contents/" + CHECKPOINT, {
                "message": "Advance private DART filing receipt cursor",
                "content": base64.b64encode(encoded).decode("ascii"),
                "sha": oldsha,
            })
        observed, oldsha = storage.get_content(CHECKPOINT)
        if observed != encoded or type(oldsha) is not str:
            raise DARTEventReceiptError("private_dart_cursor_readback_failed")
        cursor = checkpoint
        tail, tail_page = saved, page
        # An already-completed immutable page reuses evidence without API.
        # A hard cap on recovered orphan pages prevents unbounded loops.
        if reused_pages > max_calls:
            break
    return {
        "status": "bounded_private_dart_receipt_collection",
        "provider_calls": calls, "new_pages": new_pages,
        "previously_saved_pages_reused": reused_pages,
        "newly_checkpointed_receipts": new_receipts,
        "cumulative_pages": cursor["saved_pages"],
        "cumulative_receipts": cursor["saved_receipts"],
        "months_total": len(MONTHS),
        "months_completed": cursor["month_index"],
        "all_corporate_actions_verified": False,
        "historical_pit_verified": False,
        "historical_adjusted_prices_certified": False,
        "public_issuer_data_published": False,
    }


def main():
    if (os.environ.get("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.environ.get("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20
        or len(os.environ.get("DART_API_KEY", "")) != 40):
        raise SystemExit("blocked: required private DART research credentials absent")
    store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
    key = os.environ["DART_API_KEY"]
    try:
        summary = run(store, lambda m, c, p: official_fetch(key, m, c, p))
    except Exception:
        raise SystemExit("blocked: private DART filing collection validation or transport failed") from None
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
