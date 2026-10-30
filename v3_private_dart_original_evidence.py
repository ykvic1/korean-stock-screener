"""PRIVATE bounded OpenDART ORIGINAL document retrieval for title-hint candidates.

Official /api/document.xml returns ZIP bytes, not a corporate-event verdict.
Only previously archived private title-hint candidate receipts are eligible.
Original ZIPs and individual receipt metadata NEVER enter the public repo,
stdout, public artifacts, UI scores, or adjusted KRX returns.

This pilot does not parse or infer any actual corporate-action effective date.
"""
from __future__ import annotations

import base64
import gzip
from hashlib import sha256
from io import BytesIO
import json
import os
import re
from urllib import parse, request
import zipfile

from v3_dart_historical_event_receipts import (
    MONTHS, PRIVATE_REPO, _bytes as canonical,
)
from v3_private_dart_krx_filing_title_triage import (
    CHECKPOINT as TRIAGE_CHECKPOINT, SCHEMA as TRIAGE_SCHEMA,
    _cursor as triage_cursor, _path as triage_path,
)
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError


SCHEMA = "v3-private-opendart-original-document-evidence-v1"
CHECKPOINT = "derived/dart-original-evidence/checkpoint.json"
BASE = "derived/dart-original-evidence"
MAX_API_CALLS = 2
MAX_ZIP_BYTES = 900_000   # below private GitHub transport's own 1MB cap
MAX_EXPANDED_BYTES = 12_000_000
MAX_FILES = 100
MAX_METADATA_BYTES = 2_000_000
RECEIPT = re.compile(r"[0-9]{14}\Z")
CORP = re.compile(r"[0-9]{8}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


class OriginalEvidenceError(ValueError):
    """Fixed codes only; never print source ZIP, IDs, URL or credentials."""


class OriginalOversizedError(OriginalEvidenceError):
    """Capped upstream source response; no ZIP retained."""


def _pair_label(i):
    return f"{MONTHS[i]}--{MONTHS[i+1]}"


def _zip_path(i, receipt):
    return f"{BASE}/zip/{_pair_label(i)}/{receipt}.zip"


def _report_path(i, receipt):
    return f"{BASE}/reports/{_pair_label(i)}/{receipt}.json"


def _empty():
    return {
        "schema": SCHEMA, "next_pair_index": 0, "next_receipt_index": 0,
        "queue_sha256": None, "last_report_file": None,
        "last_report_digest": None,
        "corporate_actions_verified": False, "adjusted_prices_authorized": False,
        "trade_scores_authorized": False,
    }


def _cursor(raw):
    if raw is None:
        return _empty()
    try:
        x = json.loads(raw)
        i, j = x.get("next_pair_index"), x.get("next_receipt_index")
        q = x.get("queue_sha256")
        if (type(x) is not dict or x.get("schema") != SCHEMA
            or type(i) is not int or not 0 <= i <= len(MONTHS)-1
            or type(j) is not int or j < 0 or j > 10000
            or (j == 0 and q is not None)
            or (j > 0 and (type(q) is not str or not SHA.fullmatch(q)))
            or x.get("corporate_actions_verified") is not False
            or x.get("adjusted_prices_authorized") is not False
            or x.get("trade_scores_authorized") is not False):
            raise ValueError()
        p, d = x.get("last_report_file"), x.get("last_report_digest")
        if p is None:
            if d is not None or j != 0:
                raise ValueError()
        elif (type(p) is not str or not p.startswith(BASE + "/reports/")
              or not re.fullmatch(r"[0-9a-f]{64}", str(d))):
            raise ValueError()
        return x
    except Exception:
        raise OriginalEvidenceError("invalid_private_original_evidence_cursor") from None


def _candidate_queue(raw, index):
    """Require source invariants; deduplicate multiple anomaly-to-filing pairs."""
    try:
        if (type(raw) is not bytes or not 0 < len(raw) <= MAX_METADATA_BYTES
            or index < 0 or index >= len(MONTHS)-1):
            raise ValueError()
        with gzip.GzipFile(fileobj=BytesIO(raw)) as stream:
            body = stream.read(MAX_METADATA_BYTES + 1)
        if len(body) > MAX_METADATA_BYTES:
            raise ValueError()
        data = json.loads(body)
        rows = data.get("candidates")
        if (data.get("schema") != TRIAGE_SCHEMA
            or data.get("first_month") != MONTHS[index]
            or data.get("second_month") != MONTHS[index+1]
            or data.get("filing_title_or_candidate_is_event_proof") is not False
            or data.get("scores_authorized") is not False
            or type(rows) is not list
            or len(rows) != data.get("ticker_date_receipt_pairs")
            or len(rows) > 4000):
            raise ValueError()
        unique = {}
        title_pairs = 0
        for r in rows:
            if not isinstance(r, dict):
                raise ValueError()
            no, corp = r.get("filing_receipt_no"), r.get("filing_corp_code")
            hint = r.get("title_keyword_hints")
            if (type(no) is not str or not RECEIPT.fullmatch(no)
                or type(corp) is not str or not CORP.fullmatch(corp)
                or type(hint) is not list or any(type(h) is not str
                     or len(h) > 60 for h in hint)
                or r.get("event_authentication") != "not_verified"):
                raise ValueError()
            if not hint:
                continue
            title_pairs += 1
            record = {"rcept_no": no, "corp_code": corp, "title_hints": sorted(set(hint))}
            if no in unique and unique[no] != record:
                raise ValueError()
            unique[no] = record
        if title_pairs != data.get("title_keyword_pairs"):
            raise ValueError()
        queue = sorted(unique.values(), key=lambda r: r["rcept_no"])
        if len(queue) > 2000:
            raise ValueError()
        return queue, sha256(canonical(queue)).hexdigest(), title_pairs
    except Exception:
        raise OriginalEvidenceError("invalid_private_title_shortlist_source") from None


SAFE_ZIP_VALIDATION_STAGES = frozenset((
    "payload_envelope", "zip_structure", "member_count",
    "member_structure_or_security", "expanded_size",
    "document_extension", "member_integrity",
))

# Fixed source-safe ZIP classification: no member names or receipt identity.
SAFE_ZIP_MEMBER_ISSUES = frozenset((
    "missing_or_duplicate_member_name", "unsafe_member_path",
    "encrypted_or_symlink_member", "invalid_regular_member_size",
    "invalid_compressed_member_size",
))
SAFE_ZIP_PATH_ISSUES = frozenset((
    "backslash_path_separator", "absolute_or_drive_path",
    "parent_directory_component", "embedded_null_character",
))

# Transport has already replaced URLs, credentials and HTTP bodies with
# fixed codes. Print only these known private-storage codes in public logs.
SAFE_PRIVATE_STORAGE_ISSUES = frozenset((
    "private_storage_api_failed", "private_api_response_too_large",
    "target_repo_not_confirmed_private", "private_repo_size_not_reported",
    "private_repository_budget_exceeded", "untrusted_private_blob",
    "invalid_private_blob_base64", "invalid_private_blob_length",
    "invalid_private_archive_commit", "private_repository_or_token_not_configured",
))


def _check_zip(raw):
    """Validate in memory; expose only a fixed stage, never source identity."""
    stage = "payload_envelope"
    member_issue = None
    path_issue = None
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_ZIP_BYTES:
            raise ValueError()
        stage = "zip_structure"
        if not raw.startswith(b"PK"):
            raise ValueError()
        with zipfile.ZipFile(BytesIO(raw), "r") as z:
            stage = "member_count"
            files = z.infolist()
            if not 1 <= len(files) <= MAX_FILES:
                raise ValueError()
            names = set()
            total = 0
            for f in files:
                stage = "member_structure_or_security"
                name = f.filename
                if not name or name in names:
                    member_issue = "missing_or_duplicate_member_name"
                elif "\\" in name:
                    member_issue = "unsafe_member_path"
                    path_issue = "backslash_path_separator"
                elif name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                    member_issue = "unsafe_member_path"
                    path_issue = "absolute_or_drive_path"
                elif ".." in name.split("/"):
                    member_issue = "unsafe_member_path"
                    path_issue = "parent_directory_component"
                elif "\x00" in name:
                    member_issue = "unsafe_member_path"
                    path_issue = "embedded_null_character"
                elif (f.flag_bits & 1
                      or (f.external_attr >> 16) & 0o170000 == 0o120000):
                    member_issue = "encrypted_or_symlink_member"
                elif (f.file_size <= 0 and not f.is_dir()
                      or f.file_size > MAX_EXPANDED_BYTES):
                    member_issue = "invalid_regular_member_size"
                elif f.compress_size <= 0 and not f.is_dir():
                    member_issue = "invalid_compressed_member_size"
                if member_issue is not None:
                    raise ValueError()
                names.add(name)
                total += f.file_size
                stage = "expanded_size"
                if total > MAX_EXPANDED_BYTES:
                    raise ValueError()
            stage = "document_extension"
            if not any(n.lower().endswith((".xml", ".htm", ".html"))
                       for n in names):
                raise ValueError()
            stage = "member_integrity"
            if z.testzip() is not None:
                raise ValueError()
        return {
            "sha256": sha256(raw).hexdigest(),
            "zip_bytes": len(raw), "zip_member_count": len(files),
            "uncompressed_member_bytes": total,
            "archive_structure_validated_only": True,
        }
    except Exception:
        err = OriginalEvidenceError("invalid_or_unsafe_official_document_zip")
        err.safe_zip_validation_stage = stage
        if stage == "member_structure_or_security" and member_issue in SAFE_ZIP_MEMBER_ISSUES:
            err.safe_zip_member_issue = member_issue
            if path_issue in SAFE_ZIP_PATH_ISSUES:
                err.safe_zip_path_issue = path_issue
        raise err from None

def _interpret_response(raw):
    if type(raw) is not bytes or not raw:
        raise OriginalEvidenceError("invalid_dart_document_response")
    if raw.startswith(b"PK"):
        return "zip", _check_zip(raw)
    # Official non-ZIP "no data"/"file unavailable" are still not negative
    # evidence of a corporate event. All auth/quota/maintenance codes ABORT.
    if len(raw) > 3000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise OriginalEvidenceError("invalid_dart_document_status")
    statuses = re.findall(rb"<status>\s*([0-9]{3})\s*</status>", raw)
    if len(statuses) != 1:
        raise OriginalEvidenceError("unrecognized_dart_document_response")
    if statuses[0] in (b"013", b"014"):
        return "source_unavailable", {"provider_status": statuses[0].decode("ascii")}
    raise OriginalEvidenceError("dart_document_provider_rejected")


def official_fetch(api_key, rcept_no):
    if (type(api_key) is not str or not re.fullmatch(r"[A-Za-z0-9]{40}", api_key)
        or type(rcept_no) is not str or not RECEIPT.fullmatch(rcept_no)):
        raise OriginalEvidenceError("invalid_official_document_request")
    query = parse.urlencode({"crtfc_key": api_key, "rcept_no": rcept_no})
    # This URL includes a SECRET: NEVER log it or propagate transport errors.
    req = request.Request("https://opendart.fss.or.kr/api/document.xml?" + query,
                          headers={"User-Agent": "v3-private-dart-original-verification"})
    try:
        with request.urlopen(req, timeout=35) as reply:
            response = reply.read(MAX_ZIP_BYTES + 1)
        if len(response) > MAX_ZIP_BYTES:
            raise OriginalOversizedError("official_original_zip_exceeds_private_cap")
        return response
    except OriginalOversizedError:
        raise
    except Exception:
        raise OriginalEvidenceError("official_dart_document_transport_failed") from None


def _validate_report(raw, index, receipt, record):
    try:
        x = json.loads(raw)
        if (x.get("schema") != SCHEMA or x.get("rcept_no") != receipt
            or x.get("corp_code") != record["corp_code"]
            or x.get("title_hints") != record["title_hints"]
            or x.get("source_pair") != _pair_label(index)
            or x.get("corporate_action_authenticated") is not False
            or x.get("effective_date_authenticated") is not False
            or x.get("adjusted_prices_authorized") is not False
            or x.get("scores_authorized") is not False
            or x.get("source_status") not in (
                "zip_archived_unparsed", "unavailable_no_negative_inference",
                "source_oversize_not_archived",
                "unsafe_zip_quarantined_no_negative_inference")):
            raise ValueError()
        if x["source_status"] == "zip_archived_unparsed":
            if (x.get("source_zip_path") != _zip_path(index, receipt)
                or not isinstance(x.get("zip_metadata"), dict)
                or type(x["zip_metadata"].get("sha256")) is not str
                or not SHA.fullmatch(x["zip_metadata"]["sha256"])):
                raise ValueError()
        elif (x.get("source_zip_path") is not None
              or x.get("zip_metadata") is not None):
            raise ValueError()
        return x
    except Exception:
        raise OriginalEvidenceError("invalid_private_original_receipt_report") from None


def run(storage, fetch, max_calls=MAX_API_CALLS):
    if (getattr(storage, "repository", None) != PRIVATE_REPO or type(max_calls) is not int
        or not 1 <= max_calls <= MAX_API_CALLS):
        raise OriginalEvidenceError("unsafe_original_filing_destination_or_call_cap")
    storage.verify_private()
    triage_raw, _ = storage.get_content(TRIAGE_CHECKPOINT)
    try:
        triage = triage_cursor(triage_raw)
    except Exception:
        raise OriginalEvidenceError("invalid_private_title_triage_pointer") from None
    raw, original_sha = storage.get_content(CHECKPOINT)
    state = _cursor(raw)
    if state["last_report_file"]:
        last, _ = storage.get_content(state["last_report_file"])
        if last is None or sha256(last).hexdigest() != state["last_report_digest"]:
            raise OriginalEvidenceError("private_original_evidence_tail_changed")

    i = state["next_pair_index"]
    if i >= triage["next_pair_index"]:
        return {"status": "waiting_for_private_title_shortlist",
                "processed_receipts": 0, "provider_calls": 0,
                "corporate_actions_verified": 0, "scores_promoted": 0}
    body, _ = storage.get_content(triage_path(i))
    queue, digest, title_pairs = _candidate_queue(body, i)
    j = state["next_receipt_index"]
    if (j > len(queue) or
        (state["queue_sha256"] is not None and state["queue_sha256"] != digest)):
        raise OriginalEvidenceError("private_original_candidate_queue_changed")

    # A bounded run may resume previously written immutable ZIPs or reports
    # after a failed checkpoint PUT without repeating the upstream API call.
    api_calls = saved = unavailable = oversized = quarantined = reused = advanced = new_zips = 0
    last_path, last_digest = state["last_report_file"], state["last_report_digest"]
    while j < len(queue) and advanced < max_calls:
        record = queue[j]
        receipt = record["rcept_no"]
        report_path = _report_path(i, receipt)
        existing, _ = storage.get_content(report_path)
        if existing is not None:
            report = _validate_report(existing, i, receipt, record)
            if report["source_status"] == "zip_archived_unparsed":
                archived, _ = storage.get_content(_zip_path(i, receipt))
                if archived is None or _check_zip(archived) != report["zip_metadata"]:
                    raise OriginalEvidenceError("private_original_zip_readback_mismatch")
            reused += 1
        else:
            source_path = _zip_path(i, receipt)
            archived, _ = storage.get_content(source_path)
            if archived is not None:
                kind, details = "zip", _check_zip(archived)
            else:
                try:
                    returned = fetch(receipt)
                    api_calls += 1
                except OriginalOversizedError:
                    api_calls += 1
                    kind, details = "oversize", {}
                else:
                    try:
                        kind, details = _interpret_response(returned)
                    except OriginalEvidenceError as exc:
                        # Only the ZIP parser may authorize quarantine:
                        # an arbitrary transport error cannot be swallowed.
                        # Never store the unsafe bytes or infer no event.
                        if str(exc) != "invalid_or_unsafe_official_document_zip":
                            raise
                        kind, details = "unsafe_zip_quarantined", {}
            if kind == "zip" and archived is None:
                storage.put_new(source_path, returned,
                                "Save private official original ZIP source")
                new_zips += 1
                archived, _ = storage.get_content(source_path)
                if archived != returned:
                    raise OriginalEvidenceError("private_original_zip_not_read_back")
            status_by_kind = {
                "zip": "zip_archived_unparsed",
                "source_unavailable": "unavailable_no_negative_inference",
                "oversize": "source_oversize_not_archived",
                "unsafe_zip_quarantined": "unsafe_zip_quarantined_no_negative_inference",
            }
            if kind not in status_by_kind:
                raise OriginalEvidenceError("unrecognized_original_document_kind")
            status = status_by_kind[kind]
            report = {
                "schema": SCHEMA, "source_pair": _pair_label(i),
                "rcept_no": receipt, "corp_code": record["corp_code"],
                "title_hints": record["title_hints"],
                "source_status": status,
                "zip_metadata": details if kind == "zip" else None,
                "source_zip_path": source_path if kind == "zip" else None,
                "provider_status": details.get("provider_status") if kind == "source_unavailable"
                                   else None,
                "corporate_action_authenticated": False,
                "effective_date_authenticated": False,
                "historical_issuer_identity_authenticated": False,
                "adjusted_prices_authorized": False,
                "scores_authorized": False,
            }
            encoded = canonical(report)
            storage.put_new(report_path, encoded,
                            "Save private original document retrieval evidence")
            observed, _ = storage.get_content(report_path)
            if observed != encoded:
                raise OriginalEvidenceError("private_original_receipt_report_readback_failed")
            _validate_report(observed, i, receipt, record)
        saved += int(report["source_status"] == "zip_archived_unparsed")
        unavailable += int(report["source_status"] == "unavailable_no_negative_inference")
        oversized += int(report["source_status"] == "source_oversize_not_archived")
        quarantined += int(report["source_status"] == "unsafe_zip_quarantined_no_negative_inference")
        last_path, last_digest = report_path, sha256(canonical(report)).hexdigest()
        j += 1
        advanced += 1

    if j == len(queue):
        i, j, digest = i+1, 0, None
    new = canonical({
        **state, "next_pair_index": i, "next_receipt_index": j,
        "queue_sha256": digest if j else None,
        "last_report_file": last_path, "last_report_digest": last_digest,
    })
    if storage.get_content(TRIAGE_CHECKPOINT)[0] != triage_raw:
        raise OriginalEvidenceError("private_title_triage_cursor_changed")
    if original_sha is None:
        storage.put_new(CHECKPOINT, new, "Start private original document evidence cursor")
    else:
        storage.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance private original filing evidence cursor",
            "content": base64.b64encode(new).decode("ascii"),
            "sha": original_sha,
        })
    updated, _ = storage.get_content(CHECKPOINT)
    if updated != new or storage.get_content(TRIAGE_CHECKPOINT)[0] != triage_raw:
        raise OriginalEvidenceError("private_original_document_cursor_readback_failed")
    return {
        "status": "private_original_filing_retrieval_pilot_completed",
        "title_keyword_pair_inputs": title_pairs,
        "processed_receipts": advanced,
        "new_original_zips_archived": new_zips,
        "available_original_zip_receipts": saved,
        "source_unavailable_receipts": unavailable,
        "source_oversize_receipts": oversized,
        "unsafe_original_zip_receipts_quarantined": quarantined,
        "already_archived_receipts_reused": reused,
        "provider_calls": api_calls,
        "remaining_receipts_in_pair": (len(queue)-j if i == state["next_pair_index"] else 0),
        "corporate_actions_verified": 0,
        "adjusted_prices_certified": False,
        "scores_promoted": 0, "public_issuer_records_written": 0,
    }


def main():
    if (os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or len(os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "")) < 20
        or len(os.getenv("DART_API_KEY", "")) != 40):
        raise SystemExit("blocked: private original source credentials unavailable")
    try:
        store = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        key = os.environ["DART_API_KEY"]
        result = run(store, lambda no: official_fetch(key, no))
    except OriginalEvidenceError as exc:
        # Only this static allowlist survives a ZIP validation failure.
        # No receipt, original member names, URL, source bytes or credentials.
        stage = getattr(exc, "safe_zip_validation_stage", None)
        if (str(exc) == "invalid_or_unsafe_official_document_zip"
            and stage in SAFE_ZIP_VALIDATION_STAGES):
            issue = getattr(exc, "safe_zip_member_issue", None)
            detail = (";zip_member_issue=" + issue
                      if stage == "member_structure_or_security"
                      and issue in SAFE_ZIP_MEMBER_ISSUES else "")
            path_reason = getattr(exc, "safe_zip_path_issue", None)
            if (issue == "unsafe_member_path"
                and path_reason in SAFE_ZIP_PATH_ISSUES):
                detail += ";zip_path_issue=" + path_reason
            raise SystemExit("blocked: invalid_or_unsafe_official_document_zip"
                             " [zip_validation_stage=" + stage + detail + "]") from None
        raise SystemExit("blocked: " + str(exc)) from None
    except PrivateHistoryError as exc:
        reason = str(exc)
        if reason not in SAFE_PRIVATE_STORAGE_ISSUES:
            reason = "unclassified_private_storage_error"
        raise SystemExit("blocked: private original source archive [storage_reason="
                         + reason + "]") from None
    except Exception:
        raise SystemExit("blocked: private original filing source validation failed") from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
