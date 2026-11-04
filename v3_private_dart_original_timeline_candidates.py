"""Private, conservative timeline-date *candidates* from OpenDART original ZIPs.

This scanner never establishes an event, revision linkage, legal effective
date, adjusted KRX price, or point-in-time investment score. It works only on
bounded, already structure-validated source ZIPs, stores no raw text in its
summary, and never makes network requests.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from html import unescape
from io import BytesIO
import re
from zipfile import ZipFile, BadZipFile

SCHEMA = "v3-private-dart-original-date-candidate-inspection-v1"
LABELS = (
    "합병기일", "분할기일", "분할합병기일", "주식분할기준일",
    "주식병합기준일", "감자기준일", "신주상장예정일",
    "변경상장예정일", "효력발생일", "신주배정기준일",
)
STATUS = frozenset((
    "candidate_dates_proposed", "no_supported_date_cue",
    "unreadable_or_unsupported",
))
MAX_EXPANDED_BYTES = 12_000_000
MAX_CANDIDATES = 32
MAX_MEMBERS = 100
_DATE_SEPARATED = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:[./-]|년)\s*(\d{1,2})\s*"
    r"(?:[./-]|월)\s*(\d{1,2})(?:일)?(?!\d)"
)
_DATE_COMPACT = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_TAG = re.compile(r"<[^>]{0,2000}>", re.DOTALL)
_ROW_END = re.compile(r"</\s*tr\s*>", re.IGNORECASE)
_ROW_BOUNDARY = "\ue000"
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
# Prefer the longest cue, so "분할합병기일" cannot also emit "합병기일".
_LABEL_PATTERN = re.compile("|".join(
    re.escape(label) for label in sorted(LABELS, key=len, reverse=True)
))


class TimelineCandidateError(ValueError):
    """Static validation codes; no ZIP member names or source text."""


def _day_after_label(text):
    # A malformed first date must not silently fall through to a later date.
    matches = sorted(
        (match.start(), match)
        for expression in (_DATE_SEPARATED, _DATE_COMPACT)
        for match in expression.finditer(text)
    )
    if not matches:
        return None
    try:
        return date(*(int(group) for group in matches[0][1].groups())).isoformat()
    except ValueError:
        return None


def _text(raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            content = raw.decode(encoding, errors="strict")
            # Retain table row boundaries before stripping tags. A missing
            # date must never borrow an unrelated date from the following row.
            flattened = unescape(_TAG.sub(" ", _ROW_END.sub(_ROW_BOUNDARY, content)))
            return re.sub(r"\s+", " ", flattened)
        except UnicodeError:
            continue
    return None


def inspect_original_zip(raw):
    """Return source-hashed dates for human review, never authenticated facts.

    Original ZIP safety/path/member checks MUST run first in the caller. This
    function independently caps member counts, expanded size and row counts.
    An unreadable document is not negative evidence of a corporate action.
    """
    if type(raw) is not bytes or not raw.startswith(b"PK") or len(raw) > 900_000:
        raise TimelineCandidateError("invalid_original_timeline_source")
    seen = set()
    scanned = 0
    total = 0
    correction_mentioned = False
    try:
        with ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= MAX_MEMBERS:
                raise TimelineCandidateError("invalid_original_timeline_member_count")
            for member in members:
                if member.is_dir() or not member.filename.lower().endswith(
                    (".xml", ".htm", ".html")
                ):
                    continue
                total += member.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise TimelineCandidateError("original_timeline_expanded_budget")
                raw_member = archive.read(member)
                source = _text(raw_member)
                if source is None:
                    continue
                scanned += 1
                correction_mentioned |= "정정" in source
                member_sha = sha256(raw_member).hexdigest()
                for mark in _LABEL_PATTERN.finditer(source):
                    label = mark.group()
                    next_field = _LABEL_PATTERN.search(source, mark.end())
                    row_end = source.find(_ROW_BOUNDARY, mark.end())
                    boundary = min(
                        mark.end() + 100,
                        next_field.start() if next_field else len(source),
                        row_end if row_end >= 0 else len(source),
                    )
                    # Never borrow a date from the next named field.
                    candidate = _day_after_label(source[mark.end():boundary])
                    if candidate is not None:
                        seen.add((label, candidate, member_sha))
    except (BadZipFile, OSError, RuntimeError, ValueError) as error:
        if isinstance(error, TimelineCandidateError):
            raise
        raise TimelineCandidateError("invalid_original_timeline_zip_content") from None
    ordered = sorted(seen)
    candidates = [
        {"label": label, "candidate_date": day,
         "source_member_sha256": digest}
        for label, day, digest in ordered[:MAX_CANDIDATES]
    ]
    return {
        "schema": SCHEMA,
        "inspection_status": (
            "unreadable_or_unsupported" if not scanned
            else "candidate_dates_proposed" if ordered
            else "no_supported_date_cue"
        ),
        "candidate_dates": candidates,
        "candidate_list_truncated": len(ordered) > MAX_CANDIDATES,
        "source_members_decoded": scanned,
        "correction_keyword_mentioned_not_revision_proof": bool(correction_mentioned),
        "filing_revision_link_authenticated": False,
        "corporate_action_authenticated": False,
        "effective_date_authenticated": False,
        "adjusted_prices_authorized": False,
        "scores_authorized": False,
    }


def validate_candidate_summary(value):
    """Prevent a fabricated/oversharing private candidate field in reports."""
    try:
        if type(value) is not dict or set(value) != {
            "schema", "inspection_status", "candidate_dates",
            "candidate_list_truncated", "source_members_decoded",
            "correction_keyword_mentioned_not_revision_proof",
            "filing_revision_link_authenticated",
            "corporate_action_authenticated", "effective_date_authenticated",
            "adjusted_prices_authorized", "scores_authorized",
        } or value["schema"] != SCHEMA or value["inspection_status"] not in STATUS:
            raise ValueError()
        rows = value["candidate_dates"]
        if (type(rows) is not list or len(rows) > MAX_CANDIDATES
            or type(value["source_members_decoded"]) is not int
            or not 0 <= value["source_members_decoded"] <= MAX_MEMBERS
            or type(value["candidate_list_truncated"]) is not bool
            or type(value["correction_keyword_mentioned_not_revision_proof"]) is not bool
            or any(value[name] is not False for name in (
                "filing_revision_link_authenticated",
                "corporate_action_authenticated", "effective_date_authenticated",
                "adjusted_prices_authorized", "scores_authorized"
            ))):
            raise ValueError()
        for row in rows:
            if (type(row) is not dict
                or set(row) != {"label", "candidate_date", "source_member_sha256"}
                or row["label"] not in LABELS
                or type(row["source_member_sha256"]) is not str
                or not _DIGEST.fullmatch(row["source_member_sha256"])
                or type(row["candidate_date"]) is not str
                or date.fromisoformat(row["candidate_date"]).isoformat()
                != row["candidate_date"]):
                raise ValueError()
        if (value["inspection_status"] == "candidate_dates_proposed") != bool(rows):
            raise ValueError()
        if value["inspection_status"] == "unreadable_or_unsupported" and value[
                "source_members_decoded"] != 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise TimelineCandidateError("invalid_private_timeline_candidate_summary") from None
    return value
