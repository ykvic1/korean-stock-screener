"""Offline contract for KIS *stock daily investor-trade* API (not inquire-investor).

Official reference, [domestic_stock] investor_trade_by_stock_daily:
https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py
Its tr_id FHPTJ04160001, path /investor-trade-by-stock-daily and output2
are DISTINCT from basic-price investor TR FHKST01010900 and its output.

No network, credentials, orders, live claim, license claim, score or publishing.
Input data must be supplied offline; names "fund" and "pension" are not
interchangeable without verified source/category definitions.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from v3_flow_crosscheck import ASOF, CODE, audit_research_pair
from v3_npay_numeric_probe import _parse_day, _shares

DAILY_ENDPOINT = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
DAILY_TR_ID = "FHPTJ04160001"
MARKET = "J"  # KRX-requested, but response market/session is NOT certified.
DATE_FIELD = "stck_bsop_date"
REQUIRED = {"inst_net_shares": "orgn_ntby_qty",
            "foreign_net_shares": "frgn_ntby_qty"}
# Optional distinct investor categories. fund == '기금', NOT certified 연기금.
SUBGROUPS = {"investment_trust": "ivtr_ntby_qty",
             "insurance": "insu_ntby_qty",
             "fund_general": "fund_ntby_qty"}
MIN_ROWS = 20
MAX_ROWS = 40  # Bounded single-offline-response experiment, not 250-day support.


class InvalidKisDailyInvestorEvidence(ValueError):
    """Malformed/unsupported *candidate* evidence; no fallback to guessed data."""


def parse_kis_stock_daily(document: Any, *, code: str = CODE,
                          market: str = MARKET, asof: str = ASOF,
                          request_date: str = "20261016") -> list[dict]:
    """Check a single already-obtained raw output2 list without any API request.

    For now intentionally no auto-pagination, gap-filling or aggregation.
    A valid 20+ row sample is not a licensed/independent or session proof.
    """
    try:
        cutoff = date.fromisoformat(asof)
    except (TypeError, ValueError):
        raise InvalidKisDailyInvestorEvidence("Bad cutoff date") from None
    if (code != CODE or market != MARKET or type(request_date) is not str
            or request_date != cutoff.strftime("%Y%m%d")):
        raise InvalidKisDailyInvestorEvidence("Wrong stock, market or request cutoff")
    if not isinstance(document, dict) or document.get("rt_cd") != "0":
        raise InvalidKisDailyInvestorEvidence("KIS stock daily response not successful")
    # Do not silently accept the different basic-current-investor endpoint.
    raw = document.get("output2")
    if (not isinstance(raw, list) or not (MIN_ROWS <= len(raw) <= MAX_ROWS)
            or "output" in document):
        raise InvalidKisDailyInvestorEvidence("Expected bounded stock-daily output2 only")

    result = []
    dates = set()
    category_presence = {key: 0 for key in SUBGROUPS}
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidKisDailyInvestorEvidence("Invalid stock-daily record")
        try:
            day = _parse_day(item.get(DATE_FIELD))
            institutional = _shares(item.get(REQUIRED["inst_net_shares"]))
            foreign = _shares(item.get(REQUIRED["foreign_net_shares"]))
            parsed = {}
            for label, field in SUBGROUPS.items():
                if field in item:
                    parsed[label] = _shares(item[field])
                    category_presence[label] += 1
                else:
                    parsed[label] = None
        except (TypeError, ValueError):
            raise InvalidKisDailyInvestorEvidence("Invalid date or signed share field") from None
        if day in dates or date.fromisoformat(day) > cutoff:
            raise InvalidKisDailyInvestorEvidence("Repeated or future stock-daily date")
        dates.add(day)
        result.append({"code": code, "trade_date": day,
                       "inst_net_shares": institutional,
                       "foreign_net_shares": foreign, **parsed})

    # A missing optional category is unknown (null), never zero. A category
    # present for only some dates must not masquerade as a complete window.
    if any(0 < count < len(result) for count in category_presence.values()):
        raise InvalidKisDailyInvestorEvidence("Incomplete optional investor category")
    return sorted(result, key=lambda x: x["trade_date"], reverse=True)


def crosscheck_kis_stock_daily(npay_payload: Any, document: Any,
                               *, asof: str = ASOF,
                               request_date: str = "20261016") -> dict:
    """Twenty matching dated share observations -> research result only."""
    daily = parse_kis_stock_daily(document, asof=asof,
                                 request_date=request_date)
    # Only the total institution/foreign fields go into cross-source matching.
    # Subcategories are INSIDE institutional totals, not independent additions.
    totals = [{k: row[k] for k in
               ("code", "trade_date", "inst_net_shares", "foreign_net_shares")}
              for row in daily]
    outcome = audit_research_pair(npay_payload, totals, asof=asof)
    outcome["independent_source"] = (
        "KIS documented stock-level daily investor-trade API; OFFLINE INPUT ONLY")
    outcome["kis_daily_endpoint"] = DAILY_ENDPOINT
    outcome["kis_daily_tr_id"] = DAILY_TR_ID
    outcome["requested_market_division"] = MARKET
    # Report structural presence, not a sum or raw numeric observation.
    outcome["subgroup_coverage_rows"] = {
        label: len(daily) if daily[0][label] is not None else None
        for label in SUBGROUPS
    }
    outcome["fund_general_equals_pension_verified"] = False
    outcome["observed_60_120_250_day_windows_verified"] = False
    outcome["kis_live_authentication_and_service_access_verified"] = False
    outcome["kis_historical_source_retrieval_verified"] = False
    outcome["kis_response_origin_authenticated"] = False
    outcome["independent_licensed_official_exchange_api_verified"] = False
    outcome["regular_session_and_category_definitions_verified"] = False
    outcome["vendor_automated_reuse_permission_verified"] = False
    outcome["ias_flow_component"] = None
    outcome["confirmed_scoring_allowed"] = False
    outcome["v3_release_ready"] = False
    return outcome
