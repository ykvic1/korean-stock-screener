"""Single bounded live cross-source trial, never a production data collector.

External Npay GET (one) then pykrx library function call (one), only stock
252990. No credentials/retries/background/scheduling/published raw figures.
We do not assert that pykrx interface semantics meet KRX 09:00-15:30 contract.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from v3_npay_schema_probe import ENDPOINT, MAX_BYTES
from v3_npay_numeric_probe import normalize_anonymous_npay_rows
from v3_flow_crosscheck import CODE, audit_research_pair, from_pykrx_dataframe

START, END = "20260901", "20261016"


def single_trial(*, opener=urlopen, investor_query=None):
    """The only network-owning function; sanitized outputs, no exception text."""
    req = Request(ENDPOINT, headers={"Accept":"application/json"})
    try:
        with opener(req, timeout=12) as rsp:
            if (not str(rsp.geturl()).startswith("https://stock.naver.com/")
                    or rsp.getcode()!=200
                    or "json" not in rsp.headers.get("Content-Type","").lower()):
                return {"status":"npay_http_or_domain_invalid",
                        "confirmed_scoring_allowed":False}
            raw=rsp.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:
                return {"status":"npay_json_too_large",
                        "confirmed_scoring_allowed":False}
            payload=json.loads(raw.decode("utf-8"))
        # Strict validation before any alternate query; no wasted requests.
        normalized=normalize_anonymous_npay_rows(payload)
        if not normalized["windows"]["20"]["numerical_window_complete"]:
            return {"status":"npay_twenty_days_incomplete",
                    "confirmed_scoring_allowed":False}
    except Exception:
        return {"status":"npay_validation_or_connection_failed",
                "confirmed_scoring_allowed":False}
    if investor_query is None:
        from pykrx import stock
        investor_query=stock.get_market_trading_volume_by_date
    try:
        # No KRX_ID/PW or other credentials passed; no automatic retry.
        frame=investor_query(START,END,CODE)
        other=from_pykrx_dataframe(frame)
        return audit_research_pair(payload,other)
    except Exception as exc:
        return {
            "status":"alternate_krx_interface_unavailable_or_invalid",
            "exception_type":type(exc).__name__[:48],
            "npay_twenty_days_numerically_observed":True,
            "independent_crosscheck_verified":False,
            "confirmed_scoring_allowed":False,
            "v3_release_ready":False,
        }


def main():
    print(json.dumps(single_trial(),sort_keys=True,ensure_ascii=False))


if __name__=="__main__":
    main()
