"""Opt-in, ONE-PAGE, READ-ONLY Kiwoom ka10059 pilot (no orders or persistence).

Never run from an unapproved IP. Do not run inside regular GitHub Actions:
GitHub-hosted runner egress IPs change and Kiwoom only permits registered IPs.

Official:
 https://openapi.kiwoom.com/intro/serviceInfo
 https://github.com/Kiwoom-Securities/Kiwoom-REST-API
Environment secrets: KIWOOM_APP_KEY, KIWOOM_APP_SECRET (no plaintext files).
No token, key, raw response, stock flows, IP or account identifiers are logged.
Not connected to v2.2 or v3 scoring and NEVER enables confirmed IAS.
"""
from __future__ import annotations

import argparse
import getpass
from datetime import date
import json
import os
import re
from urllib import error, request

HOSTS = {"real": "https://api.kiwoom.com",
         "demo": "https://mockapi.kiwoom.com"}
TOKEN_PATH = "/oauth2/token"
STOCK_PATH = "/api/dostk/stkinfo"
API_ID = "ka10059"
MAX_RESPONSE_BYTES = 512_000
DATE_RE = re.compile(r"\d{8}")
SYMBOL_RE = re.compile(r"\d{6}")
SIGNED_INT_RE = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)")


class KiwoomPilotError(RuntimeError):
    """Explicit diagnostic category with no remote text or secret material."""


def _post(url: str, payload: dict, headers: dict[str, str]) -> dict:
    """Transport isolated for mocking, and deliberately without retries."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                raise KiwoomPilotError("api_http_failure")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        raise KiwoomPilotError("api_network_or_ip_failure") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise KiwoomPilotError("response_too_large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise KiwoomPilotError("invalid_json_response") from None
    if type(result) is not dict:
        raise KiwoomPilotError("invalid_response_object")
    return result


def _date_code(value: str) -> date:
    if type(value) is not str or not DATE_RE.fullmatch(value):
        raise KiwoomPilotError("invalid_trade_date")
    try:
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    except ValueError:
        raise KiwoomPilotError("invalid_trade_date") from None


def _quantity(value: object) -> bool:
    return type(value) is str and SIGNED_INT_RE.fullmatch(value) is not None


def _zero(response: dict) -> bool:
    return response.get("return_code") in (0, "0") and type(response.get("return_code")) in (str, int)


def probe(*, code: str, asof: str, mode: str, app_key: str,
          app_secret: str, post=_post) -> dict:
    """Two POST requests maximum: one token, one investor page; no retries.

    Transport is injectable for offline tests. Return only safe summary flags;
    do not compare, score or persist any financial values.
    """
    if mode not in HOSTS:
        raise KiwoomPilotError("mode_must_match_issued_key")
    if type(code) is not str or not SYMBOL_RE.fullmatch(code):
        raise KiwoomPilotError("invalid_stock_code")
    cutoff = _date_code(asof)
    if cutoff > date.today():
        raise KiwoomPilotError("future_asof")
    if (type(app_key) is not str or not app_key.strip() or
            type(app_secret) is not str or not app_secret.strip()):
        raise KiwoomPilotError("credentials_missing")
    host = HOSTS[mode]
    token_response = post(
        host + TOKEN_PATH,
        {"grant_type": "client_credentials", "appkey": app_key,
         "secretkey": app_secret},
        {"Content-Type": "application/json;charset=UTF-8"},
    )
    if not _zero(token_response):
        raise KiwoomPilotError("token_auth_not_accepted")
    token = token_response.get("token")
    if type(token) is not str or not token.strip():
        raise KiwoomPilotError("token_missing")
    response = post(
        host + STOCK_PATH,
        {"dt": asof, "stk_cd": code, "amt_qty_tp": "2",
         "trde_tp": "0", "unit_tp": "1"},
        {"Content-Type": "application/json;charset=UTF-8",
         "api-id": API_ID, "authorization": "Bearer " + token},
    )
    if not _zero(response):
        raise KiwoomPilotError("flow_query_not_accepted")
    rows = response.get("stk_invsr_orgn")
    if type(rows) is not list or not 1 <= len(rows) <= 250:
        raise KiwoomPilotError("missing_or_oversized_flow_rows")
    prev_date = None
    seen = set()
    optional_count = 0
    for row in rows:
        if type(row) is not dict:
            raise KiwoomPilotError("invalid_flow_row")
        row_date = _date_code(row.get("dt"))
        if row_date > cutoff or row_date in seen or (prev_date is not None and row_date >= prev_date):
            raise KiwoomPilotError("duplicate_future_or_unordered_flow_date")
        prev_date = row_date
        seen.add(row_date)
        if not _quantity(row.get("orgn")) or not _quantity(row.get("frgnr_invsr")):
            raise KiwoomPilotError("invalid_primary_flow_units")
        if "penfnd_etc" in row:
            if not _quantity(row["penfnd_etc"]):
                raise KiwoomPilotError("invalid_pension_etc_units")
            optional_count += 1
    if 0 < optional_count < len(rows):
        raise KiwoomPilotError("partial_pension_etc_coverage")
    return {
        "status": "readonly_probe_passed",
        "mode": mode,
        "api_id": API_ID,
        "received_rows_one_page": len(rows),
        "latest_date": max(seen).isoformat(),
        "earliest_date": min(seen).isoformat(),
        "institution_foreign_signed_quantity_schema_valid": True,
        "pension_etc_field_complete": optional_count == len(rows),
        "ip_origin_verified_by_kiwoom": True,  # only when two real HTTPS calls succeed
        "source_crosscheck_verified": False,
        "krx_regular_session_verified": False,
        "historical_20_60_120_250_verified": False,
        "ias_flow_component": None,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiwoom ka10059 read-only manual one-page pilot")
    parser.add_argument("--mode", required=True, choices=("real", "demo"),
                        help="Must match the issued App Key's mode")
    parser.add_argument("--code", default="252990",
                        help="Six-digit KRX code; no trading occurs")
    parser.add_argument("--asof", required=True, help="YYYYMMDD, past trading date")
    parser.add_argument("--prompt-credentials", action="store_true",
                        help="Read keys through hidden terminal prompts instead of shell history")
    parser.add_argument("--live", action="store_true",
                        help="Explicitly confirm execution on Kiwoom-registered public IP")
    args = parser.parse_args(argv)
    if not args.live:
        print("pilot_not_run: live_requires_explicit_opt_in_and_registered_ip")
        return 2
    key = os.environ.get("KIWOOM_APP_KEY", "")
    secret = os.environ.get("KIWOOM_APP_SECRET", "")
    if args.prompt_credentials:
        key = getpass.getpass("App Key (hidden): ")
        secret = getpass.getpass("App Secret (hidden): ")
    try:
        summary = probe(
            code=args.code, asof=args.asof, mode=args.mode,
            app_key=key, app_secret=secret,
        )
    except KiwoomPilotError as exc:
        print("pilot_failed: " + str(exc))
        return 1
    except Exception:
        # Unknown SDK/transport error texts may echo tokens. Never print them.
        print("pilot_failed: unexpected_local_error")
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
