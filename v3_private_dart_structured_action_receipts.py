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
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError

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


# Exact schedule-date keys independently confirmed in official OpenDART DS005
# guides. These include new-share PLANNED LISTING days; neither an
# allotment/reference date nor a proposed listing is proof of actual completion,
# exchange adjustments or point-in-time trading evidence.
SCHEDULE_DATE_FIELDS = {
    "cmpMgDecsn": ("mgsc_mgdt",),
    "cmpDvDecsn": ("dvdt", "abcr_nstklstprd"),
    "fricDecsn": ("nstk_asstd", "nstk_lstprd"),
    "pifricDecsn": ("fric_nstk_asstd", "fric_nstk_lstprd"),
    "crDecsn": ("cr_std", "crsc_nstklstprd"),
    "cmpDvmgDecsn": ("dvmgsc_dvmgdt",),
    "stkExtrDecsn": ("extrsc_extrdt", "extrsc_nstklstprd"),
}
SCHEDULE_DATE_MAX_YEAR = 2100


def _schedule_day(value):
    if type(value) is not str:
        return None
    if re.fullmatch(r"[0-9]{8}", value):
        encoded = value[:4] + "-" + value[4:6] + "-" + value[6:]
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        encoded = value
    else:
        return None
    try:
        parsed = date.fromisoformat(encoded)
        if not 2015 <= parsed.year <= SCHEDULE_DATE_MAX_YEAR:
            return None
        return parsed.isoformat()
    except ValueError:
        return None


def _reported_schedule_candidates(endpoint, exact_row):
    if exact_row is None:
        return []
    rows = []
    for field in SCHEDULE_DATE_FIELDS.get(endpoint, ()):
        when = _schedule_day(exact_row.get(field))
        if when is not None:
            rows.append({"field": field, "reported_date": when})
    return rows


def _valid_schedule_candidates(info, endpoint):
    # Legacy immutable reports without this optional field remain readable;
    # no candidate is ever interpreted as actual corporate-action completion.
    if "reported_schedule_date_candidates" not in info:
        return True
    rows = info["reported_schedule_date_candidates"]
    allowed = SCHEDULE_DATE_FIELDS.get(endpoint, ())
    if (type(rows) is not list or len(rows) > len(allowed) or
        (rows and info["exact_receipt_match"] is not True)):
        return False
    seen = set()
    for row in rows:
        if (type(row) is not dict or set(row) != {"field", "reported_date"}
            or row["field"] not in allowed or row["field"] in seen
            or type(row["reported_date"]) is not str
            or _schedule_day(row["reported_date"]) != row["reported_date"]):
            return False
        seen.add(row["field"])
    return [r["field"] for r in rows] == [f for f in allowed if f in seen]


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
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}--[0-9]{4}-[0-9]{2}/[0-9]{14}\.json", rest) is None:
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
                "list_rows": 0, "exact_receipt_match": False,
                "reported_schedule_date_candidates": []}
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
    exact_row = next((row for row in rows if row["rcept_no"] == receipt), None)
    return {"endpoint": endpoint, "provider_status": "000",
            "list_rows": len(rows), "exact_receipt_match": exact_row is not None,
            "reported_schedule_date_candidates":
                _reported_schedule_candidates(endpoint, exact_row)}


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
                (info["exact_receipt_match"] and info["list_rows"] == 0) or
                not _valid_schedule_candidates(info, endpoint)):
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


def _put_immutable_report_with_retry(storage, path, encoded):
    """Recover only transient private-Git write conflicts, without a DART refetch.

    A timed-out PUT may already have committed. Re-read first on every attempt;
    NEVER overwrite a different blob, and never retry authentication/shape
    failures. The official three-endpoint response is held in memory throughout.
    """
    for attempt in range(4):
        try:
            existing, sha = storage.get_content(path)
            if existing is not None:
                if existing != encoded or type(sha) is not str or not sha:
                    raise StructuredActionError("private_structured_action_write_conflict")
                return
            storage.put_new(path, encoded, "Save private structured action receipt research")
            return
        except PrivateHistoryError as exc:
            if str(exc) != "private_storage_api_failed" or attempt == 3:
                raise
            time.sleep(.7 * (attempt + 1))


def run(storage, fetch, limit=MAX_CANDIDATES, stage_hook=None):
    def mark(name):
        if stage_hook is not None:
            stage_hook(name)

    mark("private_auth")
    if (getattr(storage, "repository", None) != PRIVATE_REPO or
        type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES):
        raise StructuredActionError("invalid_private_structured_action_destination")
    storage.verify_private()
    mark("triage_checkpoint_read")
    triage_raw, _ = storage.get_content(TRIAGE_CHECKPOINT)
    try:
        triage = triage_cursor(triage_raw)
    except Exception:
        raise StructuredActionError("invalid_private_title_triage_pointer") from None
    mark("action_checkpoint_read")
    raw, checkpoint_sha = storage.get_content(CHECKPOINT)
    state = _cursor(raw)
    if state["last_report_file"]:
        mark("last_immutable_report_read")
        last, _ = storage.get_content(state["last_report_file"])
        if (last is None or
            sha256(last).hexdigest() != state["last_report_sha256"]):
            raise StructuredActionError("private_structured_action_tail_mutated")
    i = state["next_pair_index"]
    if i >= triage["next_pair_index"]:
        return {"status": "waiting_for_private_title_shortlist",
                "provider_calls": 0, "processed_candidates": 0,
                "scores_promoted": 0}
    mark("candidate_queue_read")
    queue_raw, _ = storage.get_content(triage_path(i))
    queue, queue_digest, _ = _candidate_queue(queue_raw, i)
    j = state["next_candidate_index"]
    if j > len(queue) or (j and state["queue_sha256"] != queue_digest):
        raise StructuredActionError("private_structured_action_queue_changed")
    if j and state["last_report_file"] != _path(i, queue[j-1]["rcept_no"]):
        raise StructuredActionError("private_structured_action_checkpoint_queue_tail_mismatch")
    start = _dates(MONTHS[i])[0]
    end = _dates(MONTHS[i+1])[1]
    calls = advanced = exact_matches = reused = schedule_candidates = 0
    last_path = state["last_report_file"]
    last_digest = state["last_report_sha256"]
    while j < len(queue) and advanced < limit:
        record = queue[j]
        path = _path(i, record["rcept_no"])
        mark("candidate_report_read")
        existing, _ = storage.get_content(path)
        if existing is None:
            # A transient provider timeout may consume a real API request.
            # Count ATTEMPTS before fetch, retry once only for the fixed
            # transport/JSON code, and never exceed six calls per job.
            # Do not start a partial candidate when fewer than three
            # endpoints can be requested within the remaining budget.
            if MAX_CALLS - calls < len(ENDPOINTS):
                break
            checks = []
            for endpoint_index, endpoint in enumerate(ENDPOINTS):
                mark("official_endpoint_read")
                calls += 1
                try:
                    result = fetch(endpoint, record["corp_code"], start, end)
                except StructuredActionError as error:
                    if (str(error) != "official_structured_action_transport_or_json_failed"
                            or calls + len(ENDPOINTS) - endpoint_index > MAX_CALLS):
                        raise
                    # Only transient transport/JSON failures retry. Provider
                    # auth/quota/status/identity errors NEVER retry and an
                    # incomplete receipt is NEVER persisted as a false match.
                    time.sleep(0.5)
                    calls += 1
                    result = fetch(endpoint, record["corp_code"], start, end)
                checks.append(inspect_response(
                    result, endpoint, record["corp_code"], record["rcept_no"]))
            report = _report(i, record, checks)
            encoded = canonical(report)
            mark("immutable_report_write")
            _put_immutable_report_with_retry(storage, path, encoded)
            mark("immutable_report_readback")
            _same_bytes(storage, path, encoded)
        else:
            encoded = existing
            reused += 1
        mark("report_validation")
        validated = _validate_report(encoded, i, record)
        exact_matches += int(validated["official_structured_category_match"])
        schedule_candidates += sum(
            len(row.get("reported_schedule_date_candidates", []))
            for row in validated["endpoint_results"])
        last_path, last_digest = path, sha256(encoded).hexdigest()
        j += 1
        advanced += 1

    if j == len(queue):
        i, j, queue_digest = i + 1, 0, None
    mark("candidate_queue_readback")
    if storage.get_content(triage_path(state["next_pair_index"]))[0] != queue_raw:
        raise StructuredActionError("private_structured_action_source_queue_changed")
    updated = canonical({
        **state, "next_pair_index": i, "next_candidate_index": j,
        "queue_sha256": queue_digest if j else None,
        "last_report_file": last_path,
        "last_report_sha256": last_digest,
    })
    mark("action_checkpoint_write")
    if checkpoint_sha is None:
        storage.put_new(CHECKPOINT, updated, "Start private structured action research cursor")
    else:
        storage.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance private structured action receipt cursor",
            "content": base64.b64encode(updated).decode("ascii"),
            "sha": checkpoint_sha,
        })
    mark("action_checkpoint_readback")
    _same_bytes(storage, CHECKPOINT, updated)
    return {
        "status": "private_structured_action_receipt_research",
        "provider_calls": calls, "processed_candidates": advanced,
        "previously_saved_reports_reused": reused,
        "exact_receipt_categories_matched": exact_matches,
        "reported_schedule_date_candidates_observed": schedule_candidates,
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
    stage = ["private_archive_init"]
    try:
        store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        key = os.environ["DART_API_KEY"]
        report = run(store, lambda ep, corp, start, end:
                     official_fetch(key, ep, corp, start, end),
                     stage_hook=lambda name: stage.__setitem__(0, name))
        print(json.dumps(report, sort_keys=True))
        return 0
    except StructuredActionError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "provider_source_values_exposed": False}))
        return 1
    except Exception as exc:
        # Static stage + allowlisted class family only. Never log exception
        # text, URLs, issuer IDs, receipt IDs, private paths or API responses.
        if isinstance(exc, PrivateHistoryError):
            category = "private_storage_error"
        elif isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            category = "transport_error"
        elif isinstance(exc, (KeyError, ValueError, TypeError)):
            category = "unexpected_local_validation_error"
        else:
            category = "unclassified_internal_error"
        print(json.dumps({
            "status": "blocked", "reason": "private_structured_action_unavailable",
            "phase": stage[0], "error_category": category,
            "provider_source_values_exposed": False}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
