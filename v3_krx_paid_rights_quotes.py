"""Fetch bounded, dated KRX subscription-right quotes for the historical replay."""

import json
import os
from hashlib import sha256
from pathlib import Path
import re
from sys import argv

import requests
from official_probe.clients import KRX_BASE, KRXClient, KRXProbeError, ProbeError, _get, _json

ROUTE = "sto/sr_bydd_trd"
ISSUES = {
    "2026-04-07": ("328130", "3281301G", "KRA3281301G5", "루닛", "루닛57R"),
    "2026-05-18": ("462350", "4623501G", "KRA4623501G5", "이노스페이스", "이노스페이스40R"),
}


def positive_whole(value):
    token = str(value).replace(",", "").strip()
    if not re.fullmatch(r"[0-9]+", token) or int(token) <= 0:
        raise ValueError("invalid_positive_krx_quantity")
    return int(token)


def read_subject(body, session, subject):
    issuer, short, isin, underlying, name = subject
    data = _json(body)
    rows = data.get("OutBlock_1")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid_krx_rights_response")
    candidates = [row for row in rows if row.get("ISU_CD") in {short, "J" + short, isin}]
    if len(candidates) != 1:
        raise ValueError("missing_or_duplicate_rights_instrument")
    row = candidates[0]
    asof = session.replace("-", "")
    if (row.get("BAS_DD") not in {asof, session}
            or str(row.get("MKT_NM", "")).upper() not in {"KOSDAQ", "코스닥"}
            or "".join(str(row.get("ISU_NM", "")).split()) != name
            or row.get("TARSTK_ISU_NM") != underlying):
        raise ValueError("krx_rights_identity_or_date_mismatch")
    prices = {key: positive_whole(row.get(field)) for key, field in (
        ("open", "TDD_OPNPRC"), ("high", "TDD_HGPRC"), ("low", "TDD_LWPRC"), ("close", "TDD_CLSPRC")
    )}
    if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
        raise ValueError("invalid_krx_rights_ohlc")
    return {"session": session, "issuer_code": issuer, "rights_isin": isin,
            **prices, "volume": positive_whole(row.get("ACC_TRDVOL")),
            "source_sha256": sha256(body).hexdigest()}


def fetch(key):
    report = {"schema": "v3-krx-paid-rights-opening-observations-v1",
              "source": "KRX_OPEN_API_SUBSCRIPTION_RIGHTS_DAILY", "attempts": [], "rows": []}
    if not key.strip():
        report["attempts"] = [{"status": "provider_key_unavailable"}]
        return report
    with requests.Session() as http:
        try:
            common, _ = KRXClient(key, session=http).daily_market("KOSDAQ", "2026-04-07")
        except KRXProbeError as exc:
            report["ordinary_kosdaq_control"] = {"status": exc.reason, "http_status": exc.http_status}
        else:
            report["ordinary_kosdaq_control"] = {"status": "received", "row_count": len(common)}
        for day, subject in ISSUES.items():
            attempt = {"session": day, "rights_isin": subject[2]}
            try:
                body = _get(http, KRX_BASE + ROUTE, headers={"AUTH_KEY": key.strip()},
                            params={"basDd": day.replace("-", "")}, krx_market="KOSDAQ")
                report["rows"].append(read_subject(body, day, subject))
            except KRXProbeError as exc:
                attempt.update(status=exc.reason, http_status=exc.http_status)
            except ProbeError:
                attempt.update(status="invalid_response")
            except ValueError:
                attempt.update(status="target_row_unverified")
            else:
                attempt.update(status="traded")
            report["attempts"].append(attempt)
    return report


if __name__ == "__main__":
    result = fetch(os.environ.get("KRX_API_KEY", ""))
    Path(argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print("ordinary_kosdaq_control", result.get("ordinary_kosdaq_control", {}))
    for attempt in result["attempts"]:
        print(attempt["session"] if "session" in attempt else "-", attempt["status"], attempt.get("http_status", ""))
