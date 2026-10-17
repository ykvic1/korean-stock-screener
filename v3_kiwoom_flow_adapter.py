"""Offline-only Kiwoom REST ka10059 signed-share investor candidate validator.

Official schema: https://github.com/Kiwoom-Securities/Kiwoom-REST-API
examples/국내주식/종목정보/get_domestic_stock_investor_by_institution.py

This module NEVER obtains a token, accesses a network, trades or publishes.
Even perfect candidate agreement cannot authorize IAS scores.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Any

API_ID = "ka10059"
PATH = "/api/dostk/stkinfo"
CODE = "252990"
QUANTITY = "2"       # 1 means amount, 2 means signed quantity
NET_BUY = "0"        # 0 net, 1 gross buy, 2 gross sale
SINGLE_SHARE = "1"   # 1000 means thousand shares; NEVER silently scale
MAX_PAGES = 10
MAX_ROWS = 250
FIELDS = {
    "inst_net_shares": "orgn", "foreign_net_shares": "frgnr_invsr",
    "pension_etc_net_shares": "penfnd_etc",
    "investment_trust_net_shares": "invtrt",
    "insurance_net_shares": "insrnc",
    "financial_investment_net_shares": "fnnc_invt",
    "private_fund_net_shares": "samo_fund",
}
REQUIRED = ("inst_net_shares", "foreign_net_shares")
NUMERIC = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)")
DAY = re.compile(r"\d{8}")
SYMBOL = re.compile(r"\d{6}")


class InvalidKiwoomFlowEvidence(ValueError):
    pass


def _date(value: Any) -> str:
    if type(value) is not str or DAY.fullmatch(value) is None:
        raise InvalidKiwoomFlowEvidence("Bad trade date")
    try:
        return date.fromisoformat(
            f"{value[:4]}-{value[4:6]}-{value[6:]}").isoformat()
    except ValueError:
        raise InvalidKiwoomFlowEvidence("Invalid trade date") from None


def _shares(value: Any) -> int:
    if type(value) is not str or NUMERIC.fullmatch(value) is None:
        raise InvalidKiwoomFlowEvidence("Bad investor share quantity")
    result = int(value.replace(",", ""))
    if abs(result) > 10**13:
        raise InvalidKiwoomFlowEvidence("Unreasonable investor share count")
    return result


def _expected_body(code: str, asof: str) -> dict[str, str]:
    return {"dt": asof.replace("-", ""), "stk_cd": code,
            "amt_qty_tp": QUANTITY, "trde_tp": NET_BUY,
            "unit_tp": SINGLE_SHARE}


def normalize_offline_receipts(receipts: Any, *,
                               code: str = CODE,
                               asof: str = "2026-10-16") -> list[dict]:
    """Validate bounded pre-supplied request/response/continuation receipts.

    Exactly: {request:{api_id,path,body,cont_yn,next_key},
              response:{return_code,stk_invsr_orgn},
              response_cont_yn, response_next_key}.
    'Y' continuation MUST supply next_key and a corresponding next page.
    All observations must strictly descend, with no inferred missing days.
    No KRX actual market session, official settlement or provenance claims.
    """
    try:
        cutoff = date.fromisoformat(asof)
    except (ValueError, TypeError):
        raise InvalidKiwoomFlowEvidence("Bad cutoff") from None
    if (type(code) is not str or SYMBOL.fullmatch(code) is None
            or type(receipts) is not list or not 1 <= len(receipts) <= MAX_PAGES):
        raise InvalidKiwoomFlowEvidence("Bad code or page count")
    next_flag, next_key = "", ""
    last_day = None
    seen: set[str] = set()
    results: list[dict] = []
    coverage = {name: 0 for name in FIELDS if name not in REQUIRED}
    for index, receipt in enumerate(receipts):
        if (not isinstance(receipt, dict) or set(receipt) !=
                {"request", "response", "response_cont_yn", "response_next_key"}):
            raise InvalidKiwoomFlowEvidence("Invalid page receipt")
        request, response = receipt["request"], receipt["response"]
        if request != {"api_id": API_ID, "path": PATH,
                       "body": _expected_body(code, asof),
                       "cont_yn": next_flag, "next_key": next_key}:
            raise InvalidKiwoomFlowEvidence("Wrong API, market code, unit or continuation")
        if (not isinstance(response, dict) or
                type(response.get("return_code")) not in (int, str) or
                response.get("return_code") not in (0, "0") or
                not isinstance(response.get("stk_invsr_orgn"), list) or
                not response["stk_invsr_orgn"]):
            raise InvalidKiwoomFlowEvidence("Non-success or malformed response")
        flag, key = receipt["response_cont_yn"], receipt["response_next_key"]
        if (type(flag) is not str or flag not in ("Y", "N") or
                type(key) is not str or
                (flag == "Y" and (not key or index == len(receipts) - 1)) or
                (flag == "N" and (key or index != len(receipts) - 1))):
            raise InvalidKiwoomFlowEvidence("Incomplete or contradictory continuation")
        for raw in response["stk_invsr_orgn"]:
            if not isinstance(raw, dict):
                raise InvalidKiwoomFlowEvidence("Invalid investor observation")
            day = _date(raw.get("dt"))
            if (date.fromisoformat(day) > cutoff or day in seen or
                    (last_day is not None and day >= last_day)):
                raise InvalidKiwoomFlowEvidence("Duplicate, future or unordered trade day")
            seen.add(day)
            last_day = day
            row = {"code": code, "trade_date": day}
            for label, field in FIELDS.items():
                if field not in raw and label not in REQUIRED:
                    row[label] = None
                else:
                    row[label] = _shares(raw.get(field))
                    if label in coverage:
                        coverage[label] += 1
            results.append(row)
            if len(results) > MAX_ROWS:
                raise InvalidKiwoomFlowEvidence("Maximum offline observations exceeded")
        next_flag, next_key = "Y", key
    if len(results) < 20:
        raise InvalidKiwoomFlowEvidence("Fewer than 20 signed-share observations")
    if any(0 < n < len(results) for n in coverage.values()):
        raise InvalidKiwoomFlowEvidence("Partial optional investor-subject coverage")
    return results


def audit_kiwoom_against_npay(npay: Any, receipts: Any,
                              *, asof: str = "2026-10-16") -> dict:
    """Crosscheck exact latest 20 days, without approving numeric scoring."""
    from v3_flow_crosscheck import audit_research_pair

    observed = normalize_offline_receipts(receipts, asof=asof)
    recent = [{key: row[key] for key in
              ("code", "trade_date", "inst_net_shares", "foreign_net_shares")}
              for row in observed[:20]]
    report = audit_research_pair(npay, recent, asof=asof)
    report.update({
        "independent_source": "Kiwoom ka10059 unauthenticated offline candidate",
        "api_id": API_ID, "candidate_page_count": len(receipts),
        "candidate_observed_days": len(observed),
        "subgroup_coverage_rows": {
            label: len(observed) if observed[0][label] is not None else None
            for label in coverage_names()
        },
        "kiwoom_api_live_access_verified": False,
        "live_origin_authenticated": False,
        "krx_regular_session_verified": False,
        "pension_only_identity_verified": False,
        "full_60_120_250_trading_days_verified": False,
        "automated_reuse_rights_verified": False,
        "ias_flow_component": None,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    })
    return report


def coverage_names() -> tuple[str, ...]:
    return tuple(label for label in FIELDS if label not in REQUIRED)
