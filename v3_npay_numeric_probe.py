"""Strict, read-only 20-row Npay investor-trend numerical research probe.

The stock.naver.com script-backed endpoint was confirmed via two one-shot
anonymous HTTP 200 schema probes on 2026-10-17, not a licensed KRX feed.
Do not treat 20 observed Npay rows as independently validated investor-flow
data or certified exchange/session origin. No production collector is changed.
"""
from __future__ import annotations

from datetime import date
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v3_npay_schema_probe import ENDPOINT, CODE, MAX_BYTES
from v3_flow_readiness import examine_daily_flow_candidate

FIELD_INST = "organPureBuyQuant"
FIELD_FOREIGN = "foreignerPureBuyQuant"
DATE_FIELD = "bizdate"
MAX_ROWS = 20
NUMBER = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)")
DATE8 = re.compile(r"\d{8}")
DATE_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_DOTS = re.compile(r"(?:\d{2}|\d{4})\.\s*\d{2}\.\s*\d{2}\.?")

class UnverifiedFlowShape(ValueError):
    pass


def _parse_day(value):
    if not isinstance(value,str):
        raise UnverifiedFlowShape("date_not_string")
    val=value.strip()
    if DATE8.fullmatch(val):
        iso=f"{val[:4]}-{val[4:6]}-{val[6:8]}"
    elif DATE_ISO.fullmatch(val):
        iso=val
    elif DATE_DOTS.fullmatch(val):
        parts=val.rstrip(".").split(".")
        yr=parts[0].strip()
        if len(yr)==2:
            yr="20"+yr
        iso=f"{yr}-{parts[1].strip()}-{parts[2].strip()}"
    else:
        raise UnverifiedFlowShape("unsupported_date_format")
    try:
        return date.fromisoformat(iso).isoformat()
    except ValueError:
        raise UnverifiedFlowShape("invalid_calendar_date") from None


def _shares(value):
    # The real page returns strings, not integers. Reject missing markers,
    # decimals, scientific notation, percentages, negatives in parentheses.
    if type(value) is not str:
        raise UnverifiedFlowShape("shares_not_string")
    v=value.strip().replace("\u2212","-")
    if not v or NUMBER.fullmatch(v) is None:
        raise UnverifiedFlowShape("shares_not_signed_integer")
    amount=int(v.replace(",",""))
    if abs(amount)>10**13:
        raise UnverifiedFlowShape("shares_out_of_bounds")
    return amount


def normalize_anonymous_npay_rows(payload, *, asof="2026-10-16"):
    """Actual observed numbers into an offline unverified research candidate.

    Do not reproduce numeric values in the live probe summary, logs or site.
    """
    if not isinstance(payload,list) or len(payload)!=MAX_ROWS:
        raise UnverifiedFlowShape("expected_exactly_20_rows")
    converted=[]
    for row in payload:
        if not isinstance(row,dict) or row.get("itemCode") != CODE:
            raise UnverifiedFlowShape("wrong_stock_or_row_shape")
        converted.append({
            "code":CODE,
            "trade_date":_parse_day(row.get(DATE_FIELD)),
            "inst_net_shares":_shares(row.get(FIELD_INST)),
            "foreign_net_shares":_shares(row.get(FIELD_FOREIGN)),
        })
    try:
        result=examine_daily_flow_candidate(CODE,asof,converted,max_age_days=6)
    except ValueError:
        raise UnverifiedFlowShape("invalid_or_duplicate_or_future_day") from None
    return result


def probe_numeric_rows(*, opener=urlopen):
    """Exactly one fixed-host anonymous GET; no retries, dynamic URLs or auth."""
    req=Request(ENDPOINT,headers={"Accept":"application/json"})
    try:
        with opener(req,timeout=12) as response:
            if not str(response.geturl()).startswith("https://stock.naver.com/"):
                return {"status":"unexpected_redirect","scoring_allowed":False}
            if response.getcode()!=200:
                return {"status":"http_non_200","scoring_allowed":False}
            if "json" not in response.headers.get("Content-Type","").lower():
                return {"status":"non_json","scoring_allowed":False}
            body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES:
                return {"status":"too_large","scoring_allowed":False}
            try: payload=json.loads(body.decode("utf-8"))
            except (ValueError,UnicodeError):
                return {"status":"invalid_json","scoring_allowed":False}
            try: report=normalize_anonymous_npay_rows(payload)
            except UnverifiedFlowShape as e:
                return {"status":"unverified_row_shape",
                        "reason_code":str(e)[:70],
                        "scoring_allowed":False}
            return {
                "status":"twenty_numeric_rows_parsed_as_unverified_candidate",
                "stock_code":CODE,
                "observed_numeric_rows":report["observed_numeric_rows"],
                "date_freshness_observed":report["freshness_observed"],
                "window_20_numerically_complete":report["windows"]["20"][
                    "numerical_window_complete"],
                "windows_60_120_250_unavailable":all(
                    not report["windows"][str(n)]["numerical_window_complete"]
                    for n in (60,120,250)),
                "market_participant_identity_verified":False,
                "independent_source_crosscheck_passed":False,
                "regular_session_and_market_verified":False,
                "automated_data_use_permission_verified":False,
                "investor_flow_scoring_allowed":False,
                "confirmed_scoring_allowed":False,
                "v3_release_ready":False,
            }
    except HTTPError as exc:
        return {"status":"http_error","code":int(exc.code),
                "scoring_allowed":False}
    except (URLError,OSError,TimeoutError):
        return {"status":"network_unavailable","scoring_allowed":False}


def main():
    print(json.dumps(probe_numeric_rows(),sort_keys=True,ensure_ascii=False))


if __name__=="__main__":
    main()
