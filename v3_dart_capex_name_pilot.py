"""Bounded live research pilot for exact nonstandard DART CAPEX labels.

No recovery of FCF, no persisted raw rows, issuer identities, amounts or
investment scores. Used to decide whether a later independently reviewed
source-expansion patch is warranted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from v3_dart_bulk_history import (
    _amount, _mapping, _inputs, verified_company_mapping_cache,
)
from v3_dart_cashflow_enrichment import YEARS, verify_state

MAX_PROBES = 12
NONSTANDARD = "-표준계정코드 미사용-"
LABELS = {
    "ppe": frozenset(("유형자산의취득", "유형자산취득")),
    "intangible": frozenset(("무형자산의취득", "무형자산취득")),
}


class PilotError(ValueError):
    """Do not expose any company code, receipt, account text or amount."""


def _label(value):
    return "".join(value.split()) if type(value) is str else ""


def candidate_status(rows, *, code, corp, year, scope, receipt, kind):
    """Only one exact nonstandard top-level CF line with same filing identity."""
    if kind not in LABELS or type(rows) is not list or len(rows) > 18000:
        raise PilotError("invalid_capex_probe_input")
    values = []
    for row in rows:
        if type(row) is not dict:
            raise PilotError("invalid_capex_probe_row")
        if (row.get("sj_div") != "CF" or
            row.get("account_id") != NONSTANDARD or
            row.get("account_detail", "-") not in ("", "-") or
            _label(row.get("account_nm")) not in LABELS[kind]):
            continue
        if (row.get("corp_code") != corp or
            ("stock_code" in row and row["stock_code"] != code) or
            str(row.get("bsns_year")) != str(year) or
            row.get("reprt_code") != "11011" or
            ("fs_div" in row and row["fs_div"] != scope) or
            row.get("rcept_no") != receipt or
            row.get("currency") != "KRW"):
            raise PilotError("candidate_account_identity_or_receipt_mismatch")
        value = _amount(row.get("thstrm_amount"))
        if value is None or value < 0:
            raise PilotError("candidate_account_amount_unverified")
        values.append(value)
    if not values:
        return "no_exact_name_candidate"
    return ("unique_exact_name_candidate" if len(values) == 1
            else "ambiguous_multiple_candidates")


def select_candidates(state, major, limit=MAX_PROBES):
    """Balance the tiny 12-call probe across year, scope and missing kind.

    The old first-12 ticker-order sample was mostly the latest CFS year.
    Stratification diagnoses whether alternate labels differ by financial
    statement scope or fiscal period, without expanding source calls or
    publishing any issuer/receipt identity.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_PROBES:
        raise PilotError("invalid_pilot_limit")
    kinds = ("ppe", "intangible")
    scopes = ("CFS", "OFS")
    strata = tuple((year, scope, kind)
                   for year in reversed(YEARS)
                   for scope in scopes for kind in kinds)
    buckets = {key: [] for key in strata}
    for code in sorted(state["records"]):
        company = state["records"][code]
        scope = company["scope"]
        if scope not in scopes:
            raise PilotError("invalid_sample_statement_scope")
        for year in reversed(YEARS):
            found = company["years"].get(str(year))
            if (not found or
                found.get("major_annual_receipt_matched") is not True or
                found.get("operating_cashflow_krw") is None):
                continue
            ppe_missing = found["ppe_capex_krw"] is None
            intangible_missing = found["intangible_capex_krw"] is None
            if ppe_missing == intangible_missing:
                continue
            source = major["records"].get(code, {}).get("years", {}).get(
                str(year), {}).get(scope, {})
            receipt = found.get("annual_receipt_id")
            if receipt != source.get("receipt_id"):
                raise PilotError("sample_major_annual_receipt_changed")
            kind = "ppe" if ppe_missing else "intangible"
            buckets[(year, scope, kind)].append(
                (code, year, scope, receipt, kind))
    chosen = []
    used_issuers = set()
    # One per available stratum first. Revisit surplus strata only after
    # the other year/scope/kind samples have been offered a chance.
    while len(chosen) < limit:
        progressed = False
        for stratum in strata:
            for candidate in buckets[stratum]:
                if candidate[0] in used_issuers:
                    continue
                chosen.append(candidate)
                used_issuers.add(candidate[0])
                progressed = True
                break
            if len(chosen) >= limit:
                break
        if not progressed:
            break
    return chosen

def run_pilot(market, catalog, major, state, *, mode, key="", session=None,
              corp_cache=None, limit=MAX_PROBES):
    try:
        verify_state(state, market, catalog, major)
        asof, members = _inputs(market, catalog)
    except (ValueError, TypeError, KeyError):
        raise PilotError("invalid_saved_source_and_market_identity") from None
    choices = select_candidates(state, major, limit)
    report = {
        "schema": "v3-capex-nonstandard-label-pilot-0.1",
        "asof": asof,
        "mode": mode,
        "eligible_sampled_issuer_years": len(choices),
        "sample_strategy": "distinct_issuer_year_scope_missing_component_strata",
        "sampled_by_fiscal_year": {
            str(year): sum(item[1] == year for item in choices)
            for year in YEARS},
        "sampled_by_scope": {
            scope: sum(item[2] == scope for item in choices)
            for scope in ("CFS", "OFS")},
        "sampled_by_missing_component": {
            kind: sum(item[4] == kind for item in choices)
            for kind in ("ppe", "intangible")},
        "provider_source_calls": 0,
        "provider_corp_master_calls": 0,
        "unique_exact_name_candidate": 0,
        "ambiguous_multiple_candidates": 0,
        "no_exact_name_candidate": 0,
        "no_report_rows": 0,
        "missing_amount_never_interpreted_as_zero": True,
        "no_derived_fcf_or_investment_score": True,
        "no_raw_names_ids_amounts_or_receipts_published": True,
        "note": "Potential match only; no FCF/source overwrite or scoring.",
    }
    if mode == "dry-run" or not choices:
        return report
    if (mode != "live" or type(key) is not str or len(key) < 20
        or session is None):
        raise PilotError("live_pilot_credentials_or_session_unavailable")
    try:
        mapping = verified_company_mapping_cache(corp_cache,asof,members)
        if mapping is None:
            response = session.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key":key}, timeout=(20,95))
            response.raise_for_status()
            mapping = _mapping(response.content, set(members))
            report["provider_corp_master_calls"] = 1
    except Exception:
        raise PilotError("pilot_official_company_mapping_unavailable") from None
    for code,year,scope,receipt,kind in choices:
        if code not in mapping:
            # Never assign another company's annual statement to this ticker.
            continue
        try:
            response = session.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key":key,"corp_code":mapping[code],
                        "bsns_year":str(year),"reprt_code":"11011",
                        "fs_div":scope},timeout=32)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise PilotError("pilot_official_provider_unavailable") from None
        report["provider_source_calls"] += 1
        if type(payload) is not dict:
            raise PilotError("pilot_provider_payload_invalid")
        if payload.get("status") == "013":
            report["no_report_rows"] += 1
            continue
        if payload.get("status") != "000" or type(payload.get("list")) is not list:
            raise PilotError("pilot_provider_status_not_success")
        result = candidate_status(payload["list"],code=code,corp=mapping[code],
                                  year=year,scope=scope,receipt=receipt,kind=kind)
        report[result] += 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market",default="docs/data.json")
    parser.add_argument("--catalog",default="docs/v22-market-catalog.json")
    parser.add_argument("--major",default="docs/v3-dart-bulk-history.json")
    parser.add_argument("--state",default="docs/v3-dart-cashflow-reference.json")
    parser.add_argument("--cache",default=".cache/v3-dart-company-codes.json")
    parser.add_argument("--mode",choices=("dry-run","live"),default="dry-run")
    parser.add_argument("--limit",type=int,default=MAX_PROBES)
    args = parser.parse_args(argv)
    def read(name):
        return json.loads(Path(name).read_text(encoding="utf8"))
    session = None
    if args.mode == "live":
        import requests
        session = requests.Session()
    report = run_pilot(
        read(args.market),read(args.catalog),read(args.major),read(args.state),
        mode=args.mode,key=os.getenv("DART_API_KEY",""),session=session,
        corp_cache=read(args.cache) if Path(args.cache).is_file() else None,
        limit=args.limit)
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))


if __name__ == "__main__":
    main()
