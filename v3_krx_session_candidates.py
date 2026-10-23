"""Third-party XKRX candidate calendar for a local KRX history backfill.

This is an UNVERIFIED research schedule, NOT KRX's authenticated session list.
No API requests, credentials, vendor source data, or automatic trading claims.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json


class CandidateCalendarError(ValueError):
    """Only static errors: never include filesystem paths or vendor text."""


# Officially announced 2026 KRX stock-market closures, missing from the
# pinned third-party XKRX candidate calendar (election and Constitution Day).
# These exclusions are verified CLOSED days, NOT proof that other XKRX
# candidate dates actually traded.
CONFIRMED_KRX_2026_CLOSURES = frozenset(("2026-06-03", "2026-07-17"))


def candidate_dates(weekdays, *, source="weekdays"):
    if source not in ("weekdays", "xkrx"):
        raise CandidateCalendarError("unsupported_calendar_source")
    if (not isinstance(weekdays,list) or not weekdays
            or weekdays!=sorted(set(weekdays))
            or any(type(v) is not str for v in weekdays)):
        raise CandidateCalendarError("invalid_weekday_range")
    removed=[]
    original_candidate_hash=None
    if source == "weekdays":
        selected=weekdays
        version="internal-weekday-v1"
    else:
        try:
            import exchange_calendars as xc
            cal=xc.get_calendar("XKRX")
            sessions=cal.sessions_in_range(weekdays[0],weekdays[-1])
            selected=[x.strftime("%Y-%m-%d") for x in sessions]
            # Keep the exact historic XKRX-list hash for *prefix-verified*
            # checkpoint migration, never reinterpret past stored windows.
            old_payload=json.dumps(selected,separators=(",",":"),ensure_ascii=True).encode("ascii")
            original_candidate_hash=hashlib.sha256(old_payload).hexdigest()
            removed=sorted(CONFIRMED_KRX_2026_CLOSURES.intersection(selected))
            selected=[d for d in selected if d not in CONFIRMED_KRX_2026_CLOSURES]
            version=str(xc.__version__)
        except (ImportError, AttributeError, ValueError, KeyError, TypeError):
            raise CandidateCalendarError("xkrx_candidate_dependency_unavailable_or_invalid") from None
        if not selected or selected!=sorted(set(selected)):
            raise CandidateCalendarError("invalid_xkrx_session_candidate_set")
        if not set(selected).issubset(set(weekdays)):
            raise CandidateCalendarError("xkrx_candidate_not_subset_of_weekdays")
    payload=json.dumps(selected,separators=(",",":"),ensure_ascii=True).encode("ascii")
    return {
        "dates":list(selected),
        "calendar_candidate_source":source,
        "calendar_candidate_library_version":version,
        "candidate_dates_sha256":hashlib.sha256(payload).hexdigest(),
        "uncorrected_xkrx_candidate_sha256":original_candidate_hash,
        "confirmed_krx_2026_closures_removed":removed,
        "excluded_weekdays_by_third_party_calendar":
            len(weekdays)-len(selected) if source=="xkrx" else 0,
        "official_krx_session_calendar_authenticated":False,
        # A fixed official closure is verified, not a complete calendar audit.
        "krx_ad_hoc_closure_or_future_revision_verified":False,
    }
