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
import time
from urllib import parse, request

from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError

SCHEMA = "v3-private-dart-historical-filing-receipts-v1"
CHECKPOINT = "derived/dart-filing-receipts/checkpoint.json"
CATEGORIES = ("B", "C", "E", "I")
# Verified 48-page live pilot succeeded: bounded 72 pages per run across twelve
# KST slots, at most 864 official OpenDART list calls/day from this workflow.
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
# Receipt metadata must preserve six-character upper-case codes, including
# alphabetic issue codes. This does NOT authenticate listing/tradability or
# authorize a join to numeric-only operational stock universes.
RX_STOCK = re.compile(r"[0-9A-Z]{6}\Z")
RX_HASH = re.compile(r"[a-f0-9]{64}\Z")
SAFE_PAGE_ISSUES = frozenset((
    "envelope", "metadata", "pagination", "record_shape", "receipt_identity",
    "company_identity", "stock_code_format", "filing_date", "filing_title",
))


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
    issue = "envelope"
    try:
        if type(blob) is not bytes or not 0 < len(blob) < MAX_FILE_BYTES:
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(blob)) as f:
            data = f.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError()
        x = json.loads(data)
        issue = "metadata"
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
        issue = "pagination"
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
            issue = "record_shape"
            if not isinstance(r, dict):
                raise ValueError()
            receipt = r.get("rcept_no")
            corp = r.get("corp_code")
            stock = r.get("stock_code")
            filing_date = r.get("rcept_dt")
            title = r.get("report_nm")
            issue = "receipt_identity"
            if (type(receipt) is not str or not RX_RECEIPT.fullmatch(receipt)
                or receipt in seen):
                raise ValueError()
            issue = "company_identity"
            if type(corp) is not str or not RX_CORP.fullmatch(corp):
                raise ValueError()
            issue = "stock_code_format"
            if stock is not None and (type(stock) is not str or not RX_STOCK.fullmatch(stock)):
                raise ValueError()
            issue = "filing_title"
            if type(title) is not str or not 0 < len(title) <= 500:
                raise ValueError()
            issue = "filing_date"
            if type(filing_date) is not str or not start <= filing_date <= end:
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
        failure = DARTEventReceiptError("invalid_private_dart_receipt_page")
        failure.safe_page_issue = issue
        raise failure from None


def _page_failure(stage, error):
    failure = DARTEventReceiptError(stage)
    issue = getattr(error, "safe_page_issue", None)
    if type(issue) is str and issue in SAFE_PAGE_ISSUES:
        failure.safe_page_issue = issue
    return failure


def _decode_stage(blob, month, category, page, stage):
    """Classify one decoded-page failure by storage stage, never by issuer/path."""
    try:
        return _decode(blob, month, category, page)
    except DARTEventReceiptError as error:
        if error.args != ("invalid_private_dart_receipt_page",):
            raise
        raise _page_failure(stage, error) from None


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
                "stock_code": None if stock == "" else stock,
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


def _write_conflict(error):
    return (type(error) is PrivateHistoryError
            and error.args == ("private_storage_api_failed",)
            and getattr(error, "safe_http_operation", None) == "write_private_content"
            and getattr(error, "safe_http_status", None) == "http_409")


def _save_immutable_page(storage, path, packed):
    message = "Save private OpenDART historic filing receipt page"
    try:
        storage.put_new(path, packed, message)
        return
    except PrivateHistoryError as error:
        if not _write_conflict(error):
            raise
    # A different private archive writer can move the Git branch while this
    # file is being created. Never replace an existing receipt page or fetch
    # the provider again. Reconcile the exact bytes before one create retry.
    for attempt in range(2):
        observed, _ = storage.get_content(path)
        if observed is not None:
            if observed != packed:
                raise DARTEventReceiptError("private_dart_immutable_page_conflict")
            return
        if attempt == 0:
            time.sleep(1)
    storage.put_new(path, packed, message)
    observed, _ = storage.get_content(path)
    if observed != packed:
        raise DARTEventReceiptError("private_dart_immutable_page_readback_failed")


def _save_cursor(storage, encoded, old_raw, oldsha):
    def write():
        if oldsha is None:
            storage.put_new(CHECKPOINT, encoded, "Start private DART filing receipt cursor")
        else:
            storage.transport("PUT", "contents/" + CHECKPOINT, {
                "message": "Advance private DART filing receipt cursor",
                "content": base64.b64encode(encoded).decode("ascii"),
                "sha": oldsha,
            })
    try:
        write()
    except PrivateHistoryError as error:
        if not _write_conflict(error):
            raise
        for attempt in range(2):
            observed, observed_sha = storage.get_content(CHECKPOINT)
            if observed == encoded and type(observed_sha) is str and observed_sha:
                return observed_sha
            if observed != old_raw or observed_sha != oldsha:
                raise DARTEventReceiptError("private_dart_cursor_concurrent_change")
            if attempt == 0:
                time.sleep(1)
        # Reuse the original CAS SHA; never adopt a competing writer's SHA.
        write()
    for attempt in range(6):
        observed, observed_sha = storage.get_content(CHECKPOINT)
        if observed == encoded and type(observed_sha) is str and observed_sha:
            return observed_sha
        if observed is not None and observed != old_raw:
            raise DARTEventReceiptError("private_dart_cursor_concurrent_change")
        if attempt < 5:
            time.sleep(.7 * (attempt + 1))
    raise DARTEventReceiptError("private_dart_cursor_readback_failed")


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
                tail_page = _decode_stage(
                    tail, month, category, pn - 1,
                    "invalid_private_dart_checkpoint_tail_page")
        else:
            tail_page = None
        saved, _ = storage.get_content(path)
        if saved is None:
            result = fetch(month, category, pn)
            calls += 1
            try:
                packed = parse_official_response(result, month, category, pn)
            except DARTEventReceiptError as error:
                if error.args != ("invalid_private_dart_receipt_page",):
                    raise
                raise _page_failure(
                    "invalid_new_official_dart_page_payload", error) from None
            page = _decode_stage(
                packed, month, category, pn,
                "invalid_new_official_dart_page_payload")
            if tail_page is not None:
                _check_page_sequence(tail_page, page)
            # Check the adjacent-page evidence BEFORE creating immutable storage:
            # a mismatched provider page must not poison every later retry.
            _save_immutable_page(storage, path, packed)
            saved = packed
            new_pages += 1
        else:
            page = _decode_stage(
                saved, month, category, pn,
                "invalid_private_dart_saved_page")
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
        oldsha = _save_cursor(storage, encoded, raw, oldsha)
        raw = encoded
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



def public_progress_complete(raw):
    """Conservative, anonymous public gate: completion never means certification.

    A stale public counter only permits a harmless private-cursor no-op; it
    cannot make this worker exceed its enforced 72-call per-run limit.
    """
    return (type(raw) is dict
        and raw.get("schema") == "v3-public-dart-historical-receipt-progress-v1"
        and raw.get("filing_months_total") == len(MONTHS)
        and raw.get("filing_months_checkpointed") == len(MONTHS)
        and raw.get("filing_months_remaining") == 0
        and raw.get("filing_months_checkpointed_ratio") == 1
        and raw.get("last_checkpointed_filing_month") == MONTHS[-1]
        and raw.get("current_collection_filing_month") is None
        and raw.get("filing_category_scope") == list(CATEGORIES)
        and raw.get("private_tail_digest_and_schema_verified") is True
        and raw.get("private_checkpoint_stable_during_read") is True
        and raw.get("provider_api_calls") == 0
        and raw.get("public_issuer_receipts_or_source_raw_published") is False
        and raw.get("v3_trade_scores_promoted") is False
        and raw.get("full_historical_archive_independently_audited") is False)



# All names are immutable in-code diagnostics. Never propagate raw HTTP text,
# response bodies, private repository URLs, tokens or unexpected exception args.
SAFE_PUBLIC_DART_FAILURE_CODES = frozenset({
    "invalid_official_page_counts",
    "invalid_private_dart_receipt_page",
    "invalid_private_dart_checkpoint_tail_page",
    "invalid_new_official_dart_page_payload",
    "invalid_private_dart_saved_page",
    "invalid_official_dart_response",
    "inconsistent_dart_pagination",
    "invalid_official_dart_record",
    "dart_provider_rejected_or_unavailable",
    "invalid_dart_source_request",
    "dart_source_response_too_large",
    "dart_source_transport_failed",
    "invalid_private_dart_receipt_cursor",
    "inconsistent_dart_page_sequence",
    "untrusted_private_dart_collection_request",
    "private_dart_receipt_tail_changed",
    "private_dart_receipt_cursor_chain_gap",
    "private_dart_cursor_readback_failed",
    "private_dart_cursor_concurrent_change",
    "private_dart_immutable_page_conflict",
    "private_dart_immutable_page_readback_failed",
})


def public_failure_code(error):
    """Strictly bounded diagnostic label, never the exception's raw message."""
    # A 403 secondary/primary limit and a 429 are quota signals, not bad
    # DART receipt data. Report only a fixed label, no private HTTP details.
    if (type(error) is PrivateHistoryError
        and error.args == ("private_storage_api_failed",)
        and getattr(error, "safe_http_status", None) in (
            "http_403_rate_limited", "http_429")):
        return "private_dart_github_api_rate_limited"
    if type(error) is PrivateHistoryError and error.args == ("private_storage_api_failed",):
        operation = getattr(error, "safe_http_operation", None)
        status = getattr(error, "safe_http_status", None)
        if (type(operation) is str and operation in {
                "read_repository_metadata", "read_private_content", "write_private_content"}
            and type(status) is str and status in {
                "http_401", "http_403", "http_404", "http_409", "http_422",
                "http_500", "http_502", "http_503", "http_504",
                "transport_or_response"}):
            return "private_dart_storage_" + operation + "_" + status
    if (type(error) is DARTEventReceiptError and len(error.args)==1
        and type(error.args[0]) is str
        and error.args[0] in SAFE_PUBLIC_DART_FAILURE_CODES):
        issue = getattr(error, "safe_page_issue", None)
        if (error.args[0] in {
                "invalid_private_dart_receipt_page",
                "invalid_new_official_dart_page_payload",
                "invalid_private_dart_saved_page",
                "invalid_private_dart_checkpoint_tail_page"}
            and type(issue) is str and issue in SAFE_PAGE_ISSUES):
            return error.args[0] + ":" + issue
        return error.args[0]
    return "private_dart_collection_validation_or_transport_failed"


def candidate_review_segments(progress):
    """Keep the shared ingestion lock short until all 120 months are saved."""
    return 4 if public_progress_complete(progress) else 1


def main():
    if (os.environ.get("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.environ.get("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20
        or len(os.environ.get("DART_API_KEY", "")) != 40):
        raise SystemExit("blocked: required private DART research credentials absent")
    store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
    key = os.environ["DART_API_KEY"]
    try:
        summary = run(store, lambda m, c, p: official_fetch(key, m, c, p))
    except Exception as error:
        raise SystemExit("blocked: "+public_failure_code(error)) from None
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
