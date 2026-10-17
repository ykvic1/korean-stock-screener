"""KIS documented investor-history *schema* adapter for offline v3 research.

Korea Investment official code sample:
examples_llm/domestic_stock/inquire_investor/inquire_investor.py
GET /uapi/domestic-stock/v1/quotations/inquire-investor
TR FHKST01010900; FID_COND_MRKT_DIV_CODE=J for KRX.

Only parse explicitly supplied historical output rows. No network, tokens,
orders, secrets, publishing, or implicit KIS account/service authorization.
Any Npay cross-source equality is RESEARCH only, not regular-session proof.
"""
from __future__ import annotations

from datetime import date

from v3_flow_crosscheck import (
    ASOF, CODE, audit_research_pair,
)
from v3_npay_numeric_probe import _parse_day, _shares

KIS_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-investor"
KIS_TR_ID = "FHKST01010900"
KIS_MARKET = "J"
DATE_KEY = "stck_bsop_date"
FOREIGN_KEY = "frgn_ntby_qty"
INSTITUTION_KEY = "orgn_ntby_qty"

class InvalidKisInvestorEvidence(ValueError):
    pass


def parse_kis_investor_history(document, *, requested_code=CODE,
                               market=KIS_MARKET, asof=ASOF):
    """Validate successful KIS-style response, never silently fill gaps."""
    if (requested_code != CODE or market != KIS_MARKET
            or not isinstance(document,dict) or document.get("rt_cd") != "0"):
        raise InvalidKisInvestorEvidence("Wrong KIS response identity or non-success")
    raw = document.get("output")
    if not isinstance(raw,list) or not (20 <= len(raw) <= 40):
        raise InvalidKisInvestorEvidence("Missing/unbounded date series")
    try:
        cutoff=date.fromisoformat(asof)
    except (ValueError,TypeError):
        raise InvalidKisInvestorEvidence("Bad research cutoff") from None
    observed = []
    seen = set()
    for item in raw:
        if not isinstance(item,dict):
            raise InvalidKisInvestorEvidence("Malformed KIS item")
        try:
            day=_parse_day(item.get(DATE_KEY))
            inst=_shares(item.get(INSTITUTION_KEY))
            foreign=_shares(item.get(FOREIGN_KEY))
        except (ValueError,TypeError):
            raise InvalidKisInvestorEvidence("Non-integer or missing KIS investor data") from None
        if day in seen or date.fromisoformat(day)>cutoff:
            raise InvalidKisInvestorEvidence("Duplicate or future KIS trading date")
        seen.add(day)
        observed.append({
            "code":requested_code,
            "trade_date":day,
            "inst_net_shares":inst,
            "foreign_net_shares":foreign,
        })
    observed.sort(key=lambda x:x["trade_date"],reverse=True)
    # Require >=20 actual date-specific observations, do not pretend
    # calendar weekdays or a 30-day response prove full historical universe.
    return observed


def crosscheck_kis_vs_npay(npay_payload, kis_document,
                           *, asof=ASOF):
    """Use existing exact-day signed-share comparator, never approve scoring."""
    rows=parse_kis_investor_history(kis_document,asof=asof)
    result=audit_research_pair(npay_payload,rows,asof=asof)
    # The generic comparator's alternate label defaults to pykrx; override
    # only the provider label because this path is a distinct official
    # *documented* KIS contract, not a currently connected authenticated feed.
    result["independent_source"] = (
        "Korea Investment official documented investor-history API schema")
    result["kis_api_endpoint"] = KIS_ENDPOINT
    result["kis_tr_id"] = KIS_TR_ID
    result["requested_market_division"] = KIS_MARKET
    result["kis_live_authentication_and_service_access_verified"] = False
    result["kis_historical_source_retrieval_verified"] = False
    result["regular_session_and_category_definitions_verified"] = False
    result["independent_licensed_official_exchange_api_verified"] = False
    result["vendor_automated_reuse_permission_verified"] = False
    result["confirmed_scoring_allowed"] = False
    result["v3_release_ready"] = False
    return result
