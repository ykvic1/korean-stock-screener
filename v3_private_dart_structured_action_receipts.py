"""PRIVATE OpenDART structured-JSON corporate action crosscheck; no ZIP parsing.

An exact receipt+issuer match to the official merger, demerger or bonus-issue
decision endpoint confirms the DISCLOSURE CATEGORY ONLY. It does not certify
a corporate-action effective date, actual share adjustment, historical issuer
continuity, point-in-time comparability, or an investment score.
"""
from __future__ import annotations

import base64
from datetime import date
from hashlib import sha256
import json
import os
import re
import time
from urllib import parse, request

from v3_dart_historical_event_receipts import (
    MONTHS, PRIVATE_REPO, _bytes as canonical, _dates,
)
from v3_private_dart_krx_filing_title_triage import (
    CHECKPOINT as TRIAGE_CHECKPOINT, _cursor as triage_cursor,
    _path as triage_path,
)
from v3_private_dart_original_evidence import _candidate_queue
from v3_krx_private_github_backfill import PrivateGitStorage

SCHEMA = "v3-private-dart-structured-action-receipt-v1"
BASE = "derived/dart-structured-action-receipts"
CHECKPOINT = BASE + "/checkpoint.json"
ENDPOINTS = ("cmpMgDecsn", "cmpDvDecsn", "fricDecsn")
SUPPLEMENTAL_ENDPOINTS = (
    "piicDecsn", "pifricDecsn", "crDecsn", "cmpDvmgDecsn", "stkExtrDecsn",
)
ALLOWED_OFFICIAL_ENDPOINTS = ENDPOINTS + SUPPLEMENTAL_ENDPOINTS
MAX_CANDIDATES = 2
MAX_CALLS = MAX_CANDIDATES * len(ENDPOINTS)
MAX_BODY_BYTES = 1_000_000
RX_CORP = re.compile(r"[0-9]{8}\Z")
RX_RECEIPT = re.compile(r"[0-9]{14}\Z")
RX_SHA = re.compile(r"[a-f0-9]{64}\Z")


class StructuredActionError(ValueError):
    """All raised messages are fixed redacted codes."""


def _empty():
    return {
        "schema": SCHEMA, "next_pair_index": 0, "next_candidate_index": 0,
        "queue_sha256": None, "last_report_file": None,
        "last_report_sha256": None, "original_source_retained": False,
        "effective_dates_certified": False, "adjusted_prices_authorized": False,
        "scores_authorized": False,
    }


def _valid_checkpoint_report_pointer(i, j, path, digest, base):
    """Last private receipt must belong to a genuine, already traversed pair."""
    if path is None:
        return digest is None and j == 0
    if (type(path) is not str or type(digest) is not str or
        not RX_SHA.fullmatch(digest)):
        return False
    prefix = base + "/reports/"
    if not path.startswith(prefix):
        return False
    rest = path[len(prefix):]
    if re.fullmatch(r"[0-9]{6}--[0-9]{6}/[0-9]{14}\.json", rest) is None:
        return False
    pair = rest.split("/", 1)[0]
    for index in range(len(MONTHS) - 1):
        if pair == MONTHS[index] + "--" + MONTHS[index + 1]:
            return index == i if j else index < i
    return False


def _cursor(raw):
    if raw is None:
        return _empty()
    try:
        x = json.loads(raw)
        i, j = x.get("next_pair_index"), x.get("next_candidate_index")
        q, p, h = x.get("queue_sha256"), x.get("last_report_file"), x.get("last_report_sha256")
        if (x.get("schema") != SCHEMA or type(i) is not int or
            not 0 <= i <= len(MONTHS)-1 or type(j) is not int or
            not 0 <= j <= 2000 or
            (j == 0 and q is not None) or
            (j > 0 and (type(q) is not str or not RX_SHA.fullmatch(q))) or
            not _valid_checkpoint_report_pointer(i, j, p, h, BASE) or
            x.get("original_source_retained") is not False or
            x.get("effective_dates_certified") is not False or
            x.get("adjusted_prices_authorized") is not False or
            x.get("scores_authorized") is not False):
            raise ValueError()
        return x
    except Exception:
        raise StructuredActionError("invalid_private_structured_action_checkpoint") from None


def _path(index, receipt):
    return (BASE + "/reports/" + MONTHS[index] + "--" + MONTHS[index+1] +
            "/" + receipt + ".json")


def _check_request(endpoint, corp, start, end):
    if (endpoint not in ALLOWED_OFFICIAL_ENDPOINTS or type(corp) is not str or
        not RX_CORP.fullmatch(corp) or type(start) is not str or
        type(end) is not str or len(start) != 8 or len(end) != 8):
        raise StructuredActionError("invalid_structured_action_request")
    try:
        d1 = date.fromisoformat(start[:4] + "-" + start[4:6] + "-" + start[6:])
        d2 = date.fromisoformat(end[:4] + "-" + end[4:6] + "-" + end[6:])
        if not date(2015, 1, 1) <= d1 <= d2:
            raise ValueError()
    except ValueError:
        raise StructuredActionError("invalid_structured_action_dates") from None


def official_fetch(api_key, endpoint, corp, start, end):
    _check_request(endpoint, corp, start, end)
    if type(api_key) is not str or not re.fullmatch(r"[A-Za-z0-9]{40}", api_key):
        raise StructuredActionError("invalid_structured_action_credential")
    params = parse.urlencode({
        "crtfc_key": api_key, "corp_code": corp,
        "bgn_de": start, "end_de": end,
    })
    # Query contains a secret and must never appear in logs/exceptions.
    url = "https://opendart.fss.or.kr/api/" + endpoint + ".json?" + params
    req = request.Request(url, headers={"User-Agent": "v3-private-dart-structured-events"})
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            raise ValueError()
        return json.loads(raw)
    except Exception:
        raise StructuredActionError("official_structured_action_transport_or_json_failed") from None


def inspect_response(body, endpoint, corp, receipt):
    if (endpoint not in ALLOWED_OFFICIAL_ENDPOINTS or type(corp) is not str or not RX_CORP.fullmatch(corp)
        or type(receipt) is not str or not RX_RECEIPT.fullmatch(receipt)
        or type(body) is not dict):
        raise StructuredActionError("invalid_structured_action_response")
    status = body.get("status")
    if status == "013":
        if body.get("list") not in (None, []):
            raise StructuredActionError("invalid_structured_action_no_data")
        return {"endpoint": endpoint, "provider_status": "013",
                "list_rows": 0, "exact_receipt_match": False}
    if status != "000" or type(body.get("list")) is not list:
        # Never equate auth, quota, maintenance or shape failures to zero events.
        raise StructuredActionError("structured_action_provider_rejected")
    rows = body["list"]
    if len(rows) > 200:
        raise StructuredActionError("structured_action_row_limit_exceeded")
    seen = set()
    for row in rows:
        if type(row) is not dict:
            raise StructuredActionError("invalid_structured_action_row")
        number, code = row.get("rcept_no"), row.get("corp_code")
        if (type(number) is not str or not RX_RECEIPT.fullmatch(number)
            or type(code) is not str or code != corp or number in seen):
            raise StructuredActionError("invalid_structured_action_row_identity")
        seen.add(number)
    return {"endpoint": endpoint, "provider_status": "000",
            "list_rows": len(rows), "exact_receipt_match": receipt in seen}


def _report(index, record, details):
    matches = [x["endpoint"] for x in details if x["exact_receipt_match"]]
    return {
        "schema": SCHEMA,
        "source_pair": MONTHS[index] + "--" + MONTHS[index+1],
        "rcept_no": record["rcept_no"], "corp_code": record["corp_code"],
        "endpoint_results": details,
        "exact_receipt_event_categories": matches,
        "official_structured_category_match": bool(matches),
        "complete_corporate_action_universe_checked": False,
        "effective_date_authenticated": False,
        "share_adjustment_authenticated": False,
        "historical_issuer_identity_authenticated": False,
        "absolute_ohlcv_retained": False,
        "raw_provider_response_retained": False,
        "scores_authorized": False,
    }


def _validate_report(raw, index, record):
    try:
        if type(raw) is not bytes or not 0 < len(raw) < 80_000:
            raise ValueError()
        x = json.loads(raw)
        if (x.get("schema") != SCHEMA or
            x.get("source_pair") != MONTHS[index] + "--" + MONTHS[index+1] or
            x.get("rcept_no") != record["rcept_no"] or
            x.get("corp_code") != record["corp_code"] or
            x.get("complete_corporate_action_universe_checked") is not False or
            x.get("effective_date_authenticated") is not False or
            x.get("share_adjustment_authenticated") is not False or
            x.get("historical_issuer_identity_authenticated") is not False or
            x.get("raw_provider_response_retained") is not False or
            x.get("absolute_ohlcv_retained") is not False or
            x.get("scores_authorized") is not False):
            raise ValueError()
        results = x.get("endpoint_results")
        if type(results) is not list or len(results) != len(ENDPOINTS):
            raise ValueError()
        expected = []
        for endpoint, info in zip(ENDPOINTS, results):
            if (type(info) is not dict or info.get("endpoint") != endpoint or
                info.get("provider_status") not in ("000", "013") or
                type(info.get("list_rows")) is not int or
                not 0 <= info["list_rows"] <= 200 or
                type(info.get("exact_receipt_match")) is not bool or
                (info["provider_status"] == "013" and
                 (info["list_rows"] != 0 or info["exact_receipt_match"])) or
                (info["exact_receipt_match"] and info["list_rows"] == 0)):
                raise ValueError()
            if info["exact_receipt_match"]:
                expected.append(endpoint)
        if (x.get("exact_receipt_event_categories") != expected or
            x.get("official_structured_category_match") is not bool(expected)):
            raise ValueError()
        return x
    except Exception:
        raise StructuredActionError("invalid_private_structured_action_report") from None


def _same_bytes(storage, path, expected):
    for attempt in range(4):
        observed, sha = storage.get_content(path)
        if observed == expected and type(sha) is str and sha:
            return sha
        if attempt < 3:
            time.sleep(.7 * (attempt + 1))
    raise StructuredActionError("private_structured_action_readback_failed")


def run(storage, fetch, limit=MAX_CANDIDATES):
    if (getattr(storage, "repository", None) != PRIVATE_REPO or
        type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES):
        raise StructuredActionError("invalid_private_structured_action_destination")
    storage.verify_private()
    triage_raw, _ = storage.get_content(TRIAGE_CHECKPOINT)
    try:
        triage = triage_cursor(triage_raw)
    except Exception:
        raise StructuredActionError("invalid_private_title_triage_pointer") from None
    raw, checkpoint_sha = storage.get_content(CHECKPOINT)
    state = _cursor(raw)
    if state["last_report_file"]:
        last, _ = storage.get_content(state["last_report_file"])
        if (last is None or
            sha256(last).hexdigest() != state["last_report_sha256"]):
            raise StructuredActionError("private_structured_action_tail_mutated")
    i = state["next_pair_index"]
    if i >= triage["next_pair_index"]:
        return {"status": "waiting_for_private_title_shortlist",
                "provider_calls": 0, "processed_candidates": 0,
                "scores_promoted": 0}
    queue_raw, _ = storage.get_content(triage_path(i))
    queue, queue_digest, _ = _candidate_queue(queue_raw, i)
    j = state["next_candidate_index"]
    if j > len(queue) or (j and state["queue_sha256"] != queue_digest):
        raise StructuredActionError("private_structured_action_queue_changed")
    if j and state["last_report_file"] != _path(i, queue[j-1]["rcept_no"]):
        raise StructuredActionError("private_structured_action_checkpoint_queue_tail_mismatch")
    start = _dates(MONTHS[i])[0]
    end = _dates(MONTHS[i+1])[1]
    calls = advanced = exact_matches = reused = 0
    last_path = state["last_report_file"]
    last_digest = state["last_report_sha256"]
    while j < len(queue) and advanced < limit:
        record = queue[j]
        path = _path(i, record["rcept_no"])
        existing, _ = storage.get_content(path)
        if existing is None:
            checks = []
            for endpoint in ENDPOINTS:
                result = fetch(endpoint, record["corp_code"], start, end)
                calls += 1
                checks.append(inspect_response(
                    result, endpoint, record["corp_code"], record["rcept_no"]))
            report = _report(i, record, checks)
            encoded = canonical(report)
            storage.put_new(path, encoded, "Save private structured action receipt research")
            _same_bytes(storage, path, encoded)
        else:
            encoded = existing
            reused += 1
        validated = _validate_report(encoded, i, record)
        exact_matches += int(validated["official_structured_category_match"])
        last_path, last_digest = path, sha256(encoded).hexdigest()
        j += 1
        advanced += 1

    if j == len(queue):
        i, j, queue_digest = i + 1, 0, None
    if storage.get_content(triage_path(state["next_pair_index"]))[0] != queue_raw:
        raise StructuredActionError("private_structured_action_source_queue_changed")
    updated = canonical({
        **state, "next_pair_index": i, "next_candidate_index": j,
        "queue_sha256": queue_digest if j else None,
        "last_report_file": last_path,
        "last_report_sha256": last_digest,
    })
    if checkpoint_sha is None:
        storage.put_new(CHECKPOINT, updated, "Start private structured action research cursor")
    else:
        storage.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance private structured action receipt cursor",
            "content": base64.b64encode(updated).decode("ascii"),
            "sha": checkpoint_sha,
        })
    _same_bytes(storage, CHECKPOINT, updated)
    return {
        "status": "private_structured_action_receipt_research",
        "provider_calls": calls, "processed_candidates": advanced,
        "previously_saved_reports_reused": reused,
        "exact_receipt_categories_matched": exact_matches,
        "next_pair_index": i,
        "corporate_action_effective_dates_certified": False,
        "adjusted_prices_certified": False, "scores_promoted": 0,
        "public_issuer_records_written": 0,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO or
        len(os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20 or
        len(os.getenv("DART_API_KEY", "")) != 40):
        print('{"status":"blocked","reason":"private_dart_research_credentials_missing"}')
        return 1
    try:
        store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        key = os.environ["DART_API_KEY"]
        report = run(store, lambda ep, corp, start, end:
                     official_fetch(key, ep, corp, start, end))
        print(json.dumps(report, sort_keys=True))
        return 0
    except StructuredActionError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "provider_source_values_exposed": False}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"private_structured_action_unavailable","provider_source_values_exposed":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
