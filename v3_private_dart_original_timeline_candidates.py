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
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


class TimelineCandidateError(ValueError):
    """Static validation codes; no ZIP member names or source text."""


def _day_after_label(text):
    possibilities = []
    for expression in (_DATE_SEPARATED, _DATE_COMPACT):
        match = expression.search(text)
        if match:
            try:
                day = date(*(int(group) for group in match.groups()))
                possibilities.append((match.start(), day.isoformat()))
            except ValueError:
                continue
    return min(possibilities)[1] if possibilities else None


def _text(raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            content = raw.decode(encoding, errors="strict")
            flattened = unescape(_TAG.sub(" ", content))
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
                for label in LABELS:
                    for mark in re.finditer(re.escape(label), source):
                        context = source[mark.end():mark.end() + 100]
                        # If a second known field starts before the first date,
                        # do not attribute that other field's value to this one.
                        candidate = _day_after_label(context)
                        if candidate is None:
                            continue
                        first_date = min(
                            (m.start() for expression in
                             (_DATE_SEPARATED, _DATE_COMPACT)
                             for m in expression.finditer(context)),
                            default=101,
                        )
                        next_field = min(
                            (context.find(other) for other in LABELS
                             if other != label and context.find(other) >= 0),
                            default=101,
                        )
                        if next_field < first_date:
                            continue
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
