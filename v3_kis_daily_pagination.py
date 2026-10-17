"""Fail-closed OFFLINE pagination integrity research for KIS stock-daily flow.

Accept only pre-provided request/response *candidates* and header continuations.
KIS official examples use response output2 and request continuation "N"
when header tr_cont is "M" or "F". Neither a synthetic matching response
nor page-completeness establishes origin, KRX session, rights or IAS evidence.

No HTTP, credentials, brokerage activity, score/publishing or storage.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from v3_flow_crosscheck import ASOF, CODE, audit_research_pair
from v3_kis_daily_investor_adapter import (
    DAILY_ENDPOINT, DAILY_TR_ID, MARKET, SUBGROUPS, REQUIRED, DATE_FIELD,
)
from v3_npay_numeric_probe import _parse_day, _shares

MAX_PAGES = 16
MAX_ROWS_PER_PAGE = 40
MAX_OBSERVATIONS = 250
CONTINUATION_MARKERS = frozenset(("F", "M"))
TERMINAL_MARKERS = frozenset(("", "D", "E"))


class InvalidKisDailyPagination(ValueError):
    pass


def parse_offline_pages(pages: Any, *, asof: str = ASOF,
                        code: str = CODE, market: str = MARKET,
                        request_date: str = "20261016") -> list[dict]:
    """Parse bounded offline request/response receipts, preserving gaps as gaps.

    Each receipt must contain:
    request: explicit KIS request parameters incl. request_tr_cont
    reply: observed document with output2 rows
    response_tr_cont: official header candidate ("F"/"M" more, ""/"D"/"E" end).

    No calendar or license certification. Dates must descend across pages;
    day counts are only numeric candidate observations, never a verified window.
    """
    try:
        cutoff = date.fromisoformat(asof)
    except (TypeError, ValueError):
        raise InvalidKisDailyPagination("Invalid cutoff") from None
    if (code != CODE or market != MARKET or type(request_date) is not str
            or request_date != cutoff.strftime("%Y%m%d")
            or not isinstance(pages, list)
            or not (1 <= len(pages) <= MAX_PAGES)):
        raise InvalidKisDailyPagination("Invalid identity, cutoff or page count")

    rows: list[dict] = []
    seen: set[str] = set()
    expected_request_cont = ""
    last_day: str | None = None
    optional_presence = {name: 0 for name in SUBGROUPS}
    for position, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != {
                "request", "reply", "response_tr_cont"}:
            raise InvalidKisDailyPagination("Malformed pagination receipt")
        request = page["request"]
        doc = page["reply"]
        response_cont = page["response_tr_cont"]
        if not isinstance(request, dict) or request != {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": request_date,
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
            "request_tr_cont": expected_request_cont,
        }:
            raise InvalidKisDailyPagination("Request/continuation identity mismatch")
        if (not isinstance(doc, dict) or doc.get("rt_cd") != "0"
                or "output" in doc or
                not isinstance(doc.get("output2"), list)
                or not (1 <= len(doc["output2"]) <= MAX_ROWS_PER_PAGE)):
            raise InvalidKisDailyPagination("Invalid bounded output2 page")
        if (type(response_cont) is not str or
                response_cont not in CONTINUATION_MARKERS | TERMINAL_MARKERS):
            raise InvalidKisDailyPagination("Unsupported continuation header")
        continuation_required = response_cont in CONTINUATION_MARKERS
        if continuation_required == (position == len(pages)-1):
            raise InvalidKisDailyPagination("Truncated or extra pagination page")

        for item in doc["output2"]:
            if not isinstance(item, dict):
                raise InvalidKisDailyPagination("Malformed investor trade")
            try:
                day = _parse_day(item.get(DATE_FIELD))
                org = _shares(item.get(REQUIRED["inst_net_shares"]))
                foreign = _shares(item.get(REQUIRED["foreign_net_shares"]))
                details = {}
                for label, field in SUBGROUPS.items():
                    if field in item:
                        details[label] = _shares(item[field])
                        optional_presence[label] += 1
                    else:
                        details[label] = None
            except (ValueError, TypeError):
                raise InvalidKisDailyPagination(
                    "Invalid date or signed investor share count") from None
            if (day in seen or date.fromisoformat(day) > cutoff
                    or (last_day is not None and day >= last_day)):
                raise InvalidKisDailyPagination(
                    "Non-descending, duplicate or future trading date")
            seen.add(day)
            last_day = day
            rows.append({"code": code, "trade_date": day,
                         "inst_net_shares": org,
                         "foreign_net_shares": foreign,
                         **details})
            if len(rows) > MAX_OBSERVATIONS:
                raise InvalidKisDailyPagination("Unbounded investor history")
        expected_request_cont = "N"

    if len(rows) < 20:
        raise InvalidKisDailyPagination("Fewer than 20 dated observations")
    if any(0 < count < len(rows) for count in optional_presence.values()):
        raise InvalidKisDailyPagination(
            "Optional investor subgroup only partly present across pages")
    return rows


def compare_offline_pages_with_npay(npay_payload: Any, pages: Any, *,
                                    asof: str = ASOF,
                                    request_date: str = "20261016") -> dict:
    """Strict most-recent-20 day comparison; never certify source/score."""
    parsed = parse_offline_pages(pages, asof=asof,
                                 request_date=request_date)
    # audit_research_pair limits independent samples to 80; all earlier
    # pages have already been checked for overlaps, ordering and fields.
    latest = [{key: row[key] for key in (
        "code", "trade_date", "inst_net_shares", "foreign_net_shares")}
        for row in parsed[:20]]
    result = audit_research_pair(npay_payload, latest, asof=asof)
    result.update({
        "independent_source": (
            "KIS stock-daily documented pagination; UNAUTHENTICATED OFFLINE INPUT"),
        "kis_daily_endpoint": DAILY_ENDPOINT,
        "kis_daily_tr_id": DAILY_TR_ID,
        "received_page_count": len(pages),
        "candidate_observed_dates": len(parsed),
        "subgroup_coverage_rows": {
            name: len(parsed) if parsed[0][name] is not None else None
            for name in SUBGROUPS
        },
        "pagination_structure_consistent": True,
        "genuine_exchange_trading_calendar_verified": False,
        "live_response_origin_authenticated": False,
        "kis_account_service_approved": False,
        "full_history_60_120_250_certified": False,
        "category_and_session_scope_verified": False,
        "fund_general_equals_pension_verified": False,
        "vendor_automated_reuse_permission_verified": False,
        "ias_flow_component": None,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    })
    return result
