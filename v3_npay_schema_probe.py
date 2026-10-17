"""One-shot read-only Npay *schema* diagnostic for a single public stock.

Only one anonymous GET for 252990, at most 5 rows. No retries, scraping loops,
credentials, persistent data, DOM selector guesses, or investment scoring.
The endpoint is third-party documented, NOT yet confirmed by live execution.
Intended for a short-lived research workflow, not a production collector.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CODE = "252990"
ENDPOINT = (
    "https://stock.naver.com/api/domestic/detail/252990/trend"
    "?tradeType=KRX&startIdx=0&pageSize=5"
)
MAX_BYTES = 160_000
MAX_KEYS = 24
MAX_ITEMS = 5


def _key(key):
    return (isinstance(key, str) and len(key) <= 64
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) is not None)


def _array_shapes(doc, path="root", depth=0):
    """Schema paths only; never include values from the external response."""
    if depth > 3:
        return []
    if isinstance(doc, list):
        fields = []
        if doc and isinstance(doc[0], dict):
            fields = sorted(x for x in doc[0] if _key(x))[:MAX_KEYS]
        return [{"json_path": path, "observed_items": len(doc),
                 "first_item_key_names": fields}]
    if not isinstance(doc, dict):
        return []
    out = []
    for k in sorted(doc):
        if not _key(k):
            continue
        value = doc[k]
        if isinstance(value, (dict, list)):
            out.extend(_array_shapes(value, path + "." + k, depth + 1))
            if len(out) >= MAX_ITEMS:
                return out[:MAX_ITEMS]
    return out


def summarize_response(status, data):
    """No raw stock prices, flow numbers, cookies, URLs or internal IDs."""
    if type(status) is not int or status < 200 or status >= 300:
        return {"status": "http_not_success", "http_status":status,
                "json_object_observed":False, "data_shape_validated":False,
                "flow_values_verified":False, "regular_session_verified":False,
                "scoring_allowed":False}
    if not isinstance(data, (dict,list)):
        return {"status":"unexpected_json_shape","http_status":status,
                "json_object_observed":False, "data_shape_validated":False,
                "flow_values_verified":False, "regular_session_verified":False,
                "scoring_allowed":False}
    return {
        "status":"json_schema_observed",
        "http_status":status,
        "stock_code":CODE,
        "trade_type_requested":"KRX",
        "root_key_names":sorted(k for k in data if _key(k))[:MAX_KEYS]
            if isinstance(data,dict) else [],
        "nested_array_shapes":_array_shapes(data),
        "json_object_observed":True,
        "data_shape_validated":False,
        "flow_values_verified":False,
        "investor_category_semantics_verified":False,
        "data_provider_licensed_for_automated_collection":False,
        "regular_session_verified":False,
        "scoring_allowed":False,
        "v3_release_ready":False,
    }


def probe(*, opener=urlopen):
    """Anonymous bounded fetch, no HTTP redirect to another host accepted."""
    req=Request(ENDPOINT, headers={"Accept":"application/json"})
    try:
        with opener(req, timeout=12) as response:
            url=response.geturl()
            if not isinstance(url,str) or not url.startswith(
                    "https://stock.naver.com/"):
                return {"status":"unexpected_redirect_or_domain",
                        "flow_values_verified":False,"scoring_allowed":False}
            code=response.getcode()
            content_type=response.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                return {"status":"response_not_json",
                        "http_status":code,
                        "flow_values_verified":False,"scoring_allowed":False}
            body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES:
                return {"status":"response_exceeds_limit",
                        "flow_values_verified":False,"scoring_allowed":False}
            try:
                doc=json.loads(body.decode("utf-8"))
            except (ValueError,UnicodeError):
                return {"status":"invalid_json",
                        "flow_values_verified":False,"scoring_allowed":False}
            return summarize_response(code,doc)
    except HTTPError as e:
        return {"status":"http_error","http_status":int(e.code),
                "flow_values_verified":False,"scoring_allowed":False}
    except (URLError,TimeoutError,OSError):
        return {"status":"network_unavailable",
                "flow_values_verified":False,"scoring_allowed":False}


def main():
    print(json.dumps(probe(),ensure_ascii=False,sort_keys=True))


if __name__=="__main__":
    main()
