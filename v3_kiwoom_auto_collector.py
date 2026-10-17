"""Opt-in Kiwoom daily investor-flow collector for a REGISTERED-IP server.

Not wired into v2.2, GitHub Actions, Pages, stock scoring or orders.
Inputs are Kiwoom real-API client credentials from environment variables.
Raw daily quantities are written ONLY to an operator-owned private directory
outside the Git repository (atomic 0600 files). No remote publication.
No KRX/Npay numerical agreement is required to store vendor-labelled flows.
An actual fixed-IP host, appropriate data-use rights and schedules must be
configured separately by the user/operator. No running server is provisioned.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib import error, request
from zoneinfo import ZoneInfo

BASE = "https://api.kiwoom.com"
API_ID = "ka10059"
HOST_ROOT = Path(__file__).resolve().parent
DEFAULT_PRIVATE = Path.home() / ".local" / "share" / "korean-stock-screener" / "kiwoom"
CODE_RE = re.compile(r"[0-9]{6}\Z")
DAY_RE = re.compile(r"[0-9]{8}\Z")
SIGNED_RE = re.compile(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\Z")
SUBGROUPS = {"pension_etc": "penfnd_etc", "investment_trust": "invtrt",
             "insurance": "insrnc", "financial_investment": "fnnc_invt",
             "private_fund": "samo_fund"}
MAX_CODES = 35
MAX_PAGES = 3
MAX_ROWS = 300
MAX_HTTP_BYTES = 512_000


class KiwoomCollectionError(ValueError):
    """Safe static error codes only; remote response never enters exception."""


def _date(text: object) -> date:
    if type(text) is not str or DAY_RE.fullmatch(text) is None:
        raise KiwoomCollectionError("invalid_date")
    try:
        return date.fromisoformat(text[:4] + "-" + text[4:6] + "-" + text[6:])
    except ValueError:
        raise KiwoomCollectionError("invalid_date") from None


def _signed(value: object) -> int:
    if type(value) is not str or SIGNED_RE.fullmatch(value) is None:
        raise KiwoomCollectionError("invalid_signed_share_count")
    val = int(value.replace(",", ""))
    if abs(val) > 10**13:
        raise KiwoomCollectionError("out_of_range_signed_share_count")
    return val


def _codes(value: object) -> list[str]:
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= MAX_CODES:
        raise KiwoomCollectionError("invalid_watchlist_size")
    if any(type(code) is not str or CODE_RE.fullmatch(code) is None for code in value):
        raise KiwoomCollectionError("invalid_watchlist_code")
    if len(set(value)) != len(value):
        raise KiwoomCollectionError("duplicate_watchlist_code")
    return list(value)


def _post(url: str, payload: dict, headers: dict) -> dict:
    """Transport isolated for offline testing. No response or secret logging."""
    wire = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = request.Request(url, data=wire, method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                raise KiwoomCollectionError("provider_http_failure")
            buf = resp.read(MAX_HTTP_BYTES + 1)
            cont_flag = resp.headers.get("cont-yn", "")
            cont_key = resp.headers.get("next-key", "")
    except (error.HTTPError, error.URLError, TimeoutError, OSError):
        raise KiwoomCollectionError("provider_network_or_ip_failure") from None
    if len(buf) > MAX_HTTP_BYTES:
        raise KiwoomCollectionError("provider_response_too_large")
    try:
        body = json.loads(buf)
    except (UnicodeError, ValueError):
        raise KiwoomCollectionError("provider_non_json") from None
    if type(body) is not dict:
        raise KiwoomCollectionError("provider_non_object")
    return {"body": body, "cont_yn": cont_flag or "", "next_key": cont_key or ""}


def _token(app_key: str, app_secret: str, post) -> str:
    if type(app_key) is not str or not app_key.strip() or type(app_secret) is not str or not app_secret.strip():
        raise KiwoomCollectionError("missing_credentials")
    result = post(BASE + "/oauth2/token",
                  {"grant_type": "client_credentials",
                   "appkey": app_key, "secretkey": app_secret},
                  {"Content-Type": "application/json;charset=UTF-8"})
    if type(result) is not dict or type(result.get("body")) is not dict:
        raise KiwoomCollectionError("invalid_token_envelope")
    body = result["body"]
    code = body.get("return_code")
    if code is not None and (type(code) not in (str, int) or code not in (0, "0")):
        raise KiwoomCollectionError("token_denied")
    token = body.get("token")
    if type(token) is not str or not token.strip():
        raise KiwoomCollectionError("token_missing")
    return token


def fetch_one(code: str, asof: str, token: str, *,
              post=_post, pause=time.sleep, max_pages: int = MAX_PAGES) -> dict:
    """Bounded Kiwoom-only observation, never exchange-calendar certified."""
    _codes([code])
    cutoff = _date(asof)
    if type(token) is not str or not token or type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise KiwoomCollectionError("invalid_fetch_config")
    body = {"dt": asof, "stk_cd": code, "amt_qty_tp": "2",
            "trde_tp": "0", "unit_tp": "1"}
    previous = None
    all_rows = []
    present = {field: 0 for field in SUBGROUPS}
    pages = 0
    cursor = ""
    has_next = False
    for page_no in range(max_pages):
        headers = {"Content-Type": "application/json;charset=UTF-8",
                   "api-id": API_ID, "authorization": "Bearer " + token}
        if page_no:
            if not has_next or not cursor:
                raise KiwoomCollectionError("unexpected_page")
            headers.update({"cont-yn": "Y", "next-key": cursor})
            pause(1.0)
        reply = post(BASE + "/api/dostk/stkinfo", body, headers)
        if type(reply) is not dict or type(reply.get("body")) is not dict:
            raise KiwoomCollectionError("invalid_flow_envelope")
        response = reply["body"]
        if (type(response.get("return_code")) not in (str, int)
                or response.get("return_code") not in (0, "0")):
            raise KiwoomCollectionError("flow_denied")
        original = response.get("stk_invsr_orgn")
        if type(original) is not list or not 1 <= len(original) <= 250:
            raise KiwoomCollectionError("bad_flow_page_size")
        for source in original:
            if type(source) is not dict:
                raise KiwoomCollectionError("invalid_flow_row")
            trade_day = _date(source.get("dt"))
            if trade_day > cutoff or (previous is not None and trade_day >= previous):
                raise KiwoomCollectionError("future_duplicate_or_unordered_day")
            previous = trade_day
            values = {"trade_date": trade_day.isoformat(),
                      "inst_net_shares": _signed(source.get("orgn")),
                      "foreign_net_shares": _signed(source.get("frgnr_invsr"))}
            for name, field in SUBGROUPS.items():
                if field in source:
                    values[name + "_net_shares"] = _signed(source[field])
                    present[name] += 1
                else:
                    values[name + "_net_shares"] = None
            all_rows.append(values)
            if len(all_rows) > MAX_ROWS:
                raise KiwoomCollectionError("too_many_history_rows")
        pages += 1
        flag, next_key = reply.get("cont_yn"), reply.get("next_key")
        if type(flag) is not str or flag not in ("", "Y", "N") or type(next_key) is not str:
            raise KiwoomCollectionError("invalid_continuation_header")
        has_next = flag == "Y"
        if (has_next and not next_key) or (not has_next and next_key):
            raise KiwoomCollectionError("invalid_continuation_cursor")
        cursor = next_key
        if not has_next:
            break
    if any(0 < count < len(all_rows) for count in present.values()):
        raise KiwoomCollectionError("partial_subgroup_coverage")
    return {
        "code": code, "source": "Kiwoom REST ka10059 KRX-code request",
        "asof_requested": cutoff.isoformat(),
        "provider_reports_no_more_pages": not has_next,
        "official_history_completeness_verified": False,
        "older_pages_available": has_next,
        "observed_pages": pages,
        "row_count": len(all_rows),
        "first_day": all_rows[0]["trade_date"],
        "last_day": all_rows[-1]["trade_date"],
        "rows": all_rows,
        "windows": {str(n): _feature(all_rows, n) for n in (20, 60, 120, 250)},
        "source_agreement_check_required": False,
        "independent_exchange_session_verified": False,
        "official_exchange_calendar_verified": False,
        "confirmed_scoring_allowed": False,
        "ias_flow_component": None,
        "v3_release_ready": False,
    }


def _feature(rows: list[dict], days: int) -> dict | None:
    """Only n observed unique descending records, NOT official n trading days."""
    if len(rows) < days:
        return None
    subset = rows[:days]
    result = {
        "observed_rows": days,
        "inst_sum_shares": sum(r["inst_net_shares"] for r in subset),
        "foreign_sum_shares": sum(r["foreign_net_shares"] for r in subset),
        "inst_positive_days": sum(r["inst_net_shares"] > 0 for r in subset),
        "foreign_positive_days": sum(r["foreign_net_shares"] > 0 for r in subset),
    }
    for name in SUBGROUPS:
        key = name + "_net_shares"
        result[name + "_sum_shares"] = (
            sum(r[key] for r in subset)
            if all(r[key] is not None for r in subset) else None)
    return result


def _private_destination(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == HOST_ROOT or HOST_ROOT in resolved.parents:
        raise KiwoomCollectionError("output_must_be_outside_git_repo")
    if resolved.exists() and resolved.is_symlink():
        raise KiwoomCollectionError("symlink_output_not_allowed")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(resolved, 0o700)
    return resolved


def _private_write(directory: Path, asof: str, payload: dict) -> None:
    root = _private_destination(directory)
    filename = root / ("investor_" + asof + ".json")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".pending-", suffix=".json",
                                         delete=False) as fp:
            tmp = Path(fp.name)
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
            fp.write("\n")
        os.replace(tmp, filename)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def collect(*, codes: list[str], asof: str, app_key: str, app_secret: str,
            destination: Path, post=_post, pause=time.sleep) -> dict:
    codes = _codes(codes)
    asof_date = _date(asof)
    if asof_date > datetime.now(ZoneInfo("Asia/Seoul")).date():
        raise KiwoomCollectionError("future_collection_date")
    # Never emit the token or any remote exception text.
    bearer = _token(app_key, app_secret, post)
    datasets, failures = {}, {}
    for i, code in enumerate(codes):
        if i:
            pause(1.0)
        try:
            datasets[code] = fetch_one(code, asof, bearer, post=post, pause=pause)
        except KiwoomCollectionError as exc:
            failures[code] = str(exc)
        except Exception:
            failures[code] = "unexpected_source_error"
    result = {
        "schema": "private-kiwoom-investor-observations-v0",
        "provider": "Kiwoom REST ka10059",
        "asof": asof_date.isoformat(),
        "captured_at_kst": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "status": "complete" if not failures else "partial_failure",
        "datasets": datasets,
        "failures": failures,
        "published": False,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    }
    # Live tokens are never stored; normalized signed investor quantities are private.
    _private_write(destination, asof, result)
    return {"status": result["status"], "asof": result["asof"],
            "symbols_ok": len(datasets), "symbols_failed": len(failures),
            "failure_categories": failures, "private_output": "written",
            "published": False, "v3_release_ready": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiwoom authorized fixed-IP private scheduled collection")
    parser.add_argument("--live", action="store_true",
                        help="Explicit live opt-in on previously registered fixed-IP host")
    parser.add_argument("--codes", required=True, help="Comma-separated 6-digit codes (max 35)")
    parser.add_argument("--asof", default=None, help="YYYYMMDD, default KST today")
    parser.add_argument("--private-dir", default=str(DEFAULT_PRIVATE))
    args = parser.parse_args(argv)
    if not args.live:
        print("not_run: must explicitly enable live mode on registered static public IP")
        return 2
    try:
        requested = args.asof or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        report = collect(codes=args.codes.split(","), asof=requested,
                         app_key=os.getenv("KIWOOM_APP_KEY", ""),
                         app_secret=os.getenv("KIWOOM_APP_SECRET", ""),
                         destination=Path(args.private_dir))
    except KiwoomCollectionError as exc:
        print(json.dumps({"status": "collection_failed", "reason": str(exc)}))
        return 1
    except Exception:
        print('{"status":"collection_failed","reason":"unexpected_local_error"}')
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["symbols_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
