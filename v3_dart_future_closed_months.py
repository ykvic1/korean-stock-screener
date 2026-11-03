"""Archive newly CLOSED OpenDART receipt months after the fixed 120-month backfill.

This independent private checkpoint starts in 2026-10. It never modifies the
2016-10..2026-09 source-locked historical archive or asserts PIT certification.
Only fully closed Korean-calendar months are archived (not intramonth alerts).
"""
from __future__ import annotations

import base64
from datetime import datetime
from hashlib import sha256
import json
import os
import re
from urllib import parse, request
from zoneinfo import ZoneInfo

from v3_dart_historical_event_receipts import (
    CATEGORIES, DARTEventReceiptError, MAX_API_CALLS, MAX_FILE_BYTES,
    PRIVATE_REPO, _check_page_sequence, _dates, _decode,
    parse_official_response, public_failure_code,
)
from v3_krx_private_github_backfill import PrivateGitStorage

KST = ZoneInfo("Asia/Seoul")
SCHEMA = "v3-private-dart-future-closed-month-receipts-v1"
PREFIX = "derived/dart-post-120-closed-months"
CHECKPOINT = PREFIX + "/checkpoint.json"
FIRST = (2026, 10)


def due_months(now=None):
    now = datetime.now(KST) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DARTEventReceiptError("invalid_dart_source_request")
    today = now.astimezone(KST)
    start = FIRST[0] * 12 + FIRST[1] - 1
    last_closed = today.year * 12 + today.month - 2
    return tuple(f"{n // 12:04d}-{n % 12 + 1:02d}"
                 for n in range(start, last_closed + 1))


def _path(month, category, page):
    return f"{PREFIX}/{month}/{category}/{page:04d}.json.gz"


def _cursor(raw, due):
    if raw is None:
        return {"schema": SCHEMA, "month_index": 0, "category_index": 0,
                "page": 1, "saved_pages": 0, "last_page_file": None,
                "last_page_sha256": None}
    try:
        x = json.loads(raw)
        m, c, p = x["month_index"], x["category_index"], x["page"]
        if (x.get("schema") != SCHEMA or type(m) is not int
            or type(c) is not int or type(p) is not int
            or not 0 <= m <= len(due) or not 0 <= c < len(CATEGORIES)
            or not 1 <= p <= 100001
            or (m == len(due) and (c != 0 or p != 1))
            or type(x.get("saved_pages")) is not int
            or x["saved_pages"] < 0):
            raise ValueError()
        f, h = x.get("last_page_file"), x.get("last_page_sha256")
        if x["saved_pages"] == 0:
            if f is not None or h is not None:
                raise ValueError()
        elif (type(f) is not str or not f.startswith(PREFIX + "/")
              or type(h) is not str or not re.fullmatch(r"[a-f0-9]{64}", h)):
            raise ValueError()
        return x
    except Exception:
        raise DARTEventReceiptError("invalid_private_dart_receipt_cursor") from None


def official_fetch_future(api_key, month, category, page, allowed):
    if (type(api_key) is not str
        or not re.fullmatch(r"[A-Za-z0-9]{40}", api_key)
        or month not in allowed or category not in CATEGORIES
        or type(page) is not int or not 1 <= page <= 100000):
        raise DARTEventReceiptError("invalid_dart_source_request")
    start, end = _dates(month)
    query = parse.urlencode({
        "crtfc_key": api_key, "bgn_de": start, "end_de": end,
        "last_reprt_at": "N", "pblntf_ty": category,
        "sort": "date", "sort_mth": "asc",
        "page_no": str(page), "page_count": "100",
    })
    # URL contains the API key; never emit it, response or provider body.
    req = request.Request("https://opendart.fss.or.kr/api/list.json?" + query,
                          headers={"User-Agent": "v3-private-future-receipts"})
    try:
        with request.urlopen(req, timeout=30) as response:
            data = response.read(1_500_001)
        if len(data) > 1_500_000:
            raise DARTEventReceiptError("dart_source_response_too_large")
        return json.loads(data)
    except DARTEventReceiptError:
        raise
    except Exception:
        raise DARTEventReceiptError("dart_source_transport_failed") from None


def collect_closed_months(storage, fetch, *, now=None, max_calls=MAX_API_CALLS):
    months = due_months(now)
    if (storage.repository != PRIVATE_REPO or type(max_calls) is not int
        or not 1 <= max_calls <= MAX_API_CALLS):
        raise DARTEventReceiptError("untrusted_private_dart_collection_request")
    storage.verify_private()
    raw, oldsha = storage.get_content(CHECKPOINT)
    cursor = _cursor(raw, months)
    tail = None
    tail_page = None
    if cursor["last_page_file"] is not None:
        tail, _ = storage.get_content(cursor["last_page_file"])
        if tail is None or sha256(tail).hexdigest() != cursor["last_page_sha256"]:
            raise DARTEventReceiptError("private_dart_receipt_tail_changed")
    calls = saved_pages = reused_pages = 0
    while calls < max_calls and cursor["month_index"] < len(months):
        mi, ci, pn = cursor["month_index"], cursor["category_index"], cursor["page"]
        month, category = months[mi], CATEGORIES[ci]
        path = _path(month, category, pn)
        if pn > 1:
            if cursor["last_page_file"] != _path(month, category, pn - 1):
                raise DARTEventReceiptError("private_dart_receipt_cursor_chain_gap")
            if tail_page is None:
                tail_page = _decode(tail, month, category, pn - 1)
        else:
            tail_page = None
        archived, _ = storage.get_content(path)
        if archived is None:
            response = fetch(month, category, pn, months)
            calls += 1
            archived = parse_official_response(response, month, category, pn)
            page = _decode(archived, month, category, pn)
            if tail_page is not None:
                _check_page_sequence(tail_page, page)
            storage.put_new(path, archived, "Save private DART post-120 receipt page")
            saved_pages += 1
        else:
            page = _decode(archived, month, category, pn)
            if tail_page is not None:
                _check_page_sequence(tail_page, page)
            reused_pages += 1
        if page["total_pages"] == 0 or pn == page["total_pages"]:
            ci += 1
            pn = 1
            if ci == len(CATEGORIES):
                mi += 1
                ci = 0
        else:
            pn += 1
        next_cursor = {**cursor, "month_index": mi,
                       "category_index": ci, "page": pn,
                       "saved_pages": cursor["saved_pages"] + 1,
                       "last_page_file": path,
                       "last_page_sha256": sha256(archived).hexdigest()}
        encoded = json.dumps(next_cursor, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        if oldsha is None:
            storage.put_new(CHECKPOINT, encoded,
                            "Start private post-120 DART receipt cursor")
        else:
            storage.transport("PUT", "contents/" + CHECKPOINT, {
                "message": "Advance private post-120 DART receipt cursor",
                "content": base64.b64encode(encoded).decode("ascii"),
                "sha": oldsha,
            })
        observed, oldsha = storage.get_content(CHECKPOINT)
        if observed != encoded or type(oldsha) is not str:
            raise DARTEventReceiptError("private_dart_cursor_readback_failed")
        cursor = next_cursor
        tail, tail_page = archived, page
        if reused_pages > max_calls:
            break
    return {"status": "bounded_post_120_closed_month_ingestion",
            "closed_months_due": len(months),
            "closed_months_completed": cursor["month_index"],
            "source_provider_calls": calls, "new_pages": saved_pages,
            "reused_orphans": reused_pages,
            "newest_closed_month": months[-1] if months else None,
            "historical_pit_certified": False,
            "correction_effective_dates_certified": False,
            "scores_promoted": False}


def main():
    if (os.environ.get("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.environ.get("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20
        or len(os.environ.get("DART_API_KEY", "")) != 40):
        raise SystemExit("blocked: required private DART research credentials absent")
    store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
    api_key = os.environ["DART_API_KEY"]
    try:
        result = collect_closed_months(
            store, lambda month, cat, page, due:
            official_fetch_future(api_key, month, cat, page, due))
    except Exception as error:
        raise SystemExit("blocked: " + public_failure_code(error)) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
