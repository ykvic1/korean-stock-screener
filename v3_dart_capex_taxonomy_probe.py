"""Bounded official OpenDART CF taxonomy gap pilot; NO scoring or raw rows.

Read previously validated annual cash-flow observations, re-fetch at most
eight missing-CAPEX company-years, and report only anonymous candidate counts.
A textual resemblance is NOT proof of the accounting meaning of a cash flow.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
import re
from pathlib import Path

from v3_dart_bulk_history import _amount, _day, _mapping, _valid_receipt
from v3_dart_capex_official_taxonomy import official_candidates, novel_account_id
from v3_dart_cashflow_enrichment import (
    INTANGIBLE_IDS, PPE_IDS, CashflowError,
    asof_members, normalize_year, verify_state,
)

MAX_YEAR_CALLS = 8
MAX_TOTAL_CALLS = 13  # four published taxonomy sections + mapping + eight annual statements
PURCHASE = re.compile(r"취득|구입|매입|지급|purchase|acquisit|payment", re.I)
EXCLUDE = re.compile(r"처분|매각|매도|회수|상환|유입|sale|disposal|proceeds|repayment", re.I)
PPE_NAME = re.compile(r"유형\s*자산|건설중인자산|기계장치|토지|건물|property.*plant|equipment", re.I)
INTANGIBLE_NAME = re.compile(r"무형\s*자산|소프트웨어|산업재산권|개발비|intangible|software", re.I)
CLASSES = ("missing_both_capex", "missing_intangible_only", "missing_ppe_only")


class TaxonomyPilotError(ValueError):
    """No company identity or response content in error messages."""


def missing_class(row):
    if row["ppe_capex_krw"] is None and row["intangible_capex_krw"] is None:
        return "missing_both_capex"
    if row["intangible_capex_krw"] is None:
        return "missing_intangible_only"
    if row["ppe_capex_krw"] is None:
        return "missing_ppe_only"
    return None


def targets(state, market, catalog, major, limit=MAX_YEAR_CALLS):
    if type(limit) is not int or not 1 <= limit <= MAX_YEAR_CALLS:
        raise TaxonomyPilotError("invalid_pilot_budget")
    try:
        verify_state(state, market, catalog, major)
        _, members = asof_members(market, catalog, major)
    except (CashflowError, TypeError, ValueError):
        raise TaxonomyPilotError("invalid_verified_cashflow_context") from None
    buckets = defaultdict(list)
    for code, item in state["records"].items():
        for year, row in item["years"].items():
            kind = missing_class(row)
            if kind is not None:
                buckets[(kind, members[code]["market"])].append(
                    (code, int(year), item["scope"], row))
    for v in buckets.values():
        v.sort(key=lambda entry: (-entry[1], entry[0]))
    selected = []
    # Balance evidence across deficit types and KOSPI/KOSDAQ; no issuer bias
    # from one giant alphabetically sorted queue.
    while len(selected) < limit:
        advanced = False
        for kind in CLASSES:
            for exchange in ("KOSPI", "KOSDAQ"):
                bucket = buckets[(kind, exchange)]
                if bucket and len(selected) < limit:
                    selected.append((kind, exchange, *bucket.pop(0)))
                    advanced = True
        if not advanced:
            break
    return selected


def candidate_fields(row, *, year, scope, corp, stock, receipt, cutoff):
    """Only same annual report/scope/receipt KRW CF line-item hints.

    Return *hints*, never monetary estimates or alternative IFRS mappings.
    Names remain ephemeral, and source identifiers are never emitted.
    """
    if (type(row) is not dict or row.get("sj_div") != "CF" or
        row.get("account_detail", "-") not in ("", "-") or
        row.get("currency") != "KRW" or
        row.get("corp_code") != corp or
        ("stock_code" in row and row["stock_code"] != stock) or
        str(row.get("bsns_year")) != str(year) or
        row.get("reprt_code") != "11011" or
        ("fs_div" in row and row["fs_div"] != scope) or
        row.get("rcept_no") != receipt or
        not _valid_receipt(receipt, year, cutoff)):
        return ()
    label = row.get("account_nm")
    if type(label) is not str or len(label) > 160 or not PURCHASE.search(label) or EXCLUDE.search(label):
        return ()
    tags = []
    if PPE_NAME.search(label):
        tags.append("ppe")
    if INTANGIBLE_NAME.search(label):
        tags.append("intangible")
    return tuple(tags)


def summarize_candidates(rows, *, stock, corp, year, scope, receipt, cutoff, missing):
    info = {k: {"rows": 0, "with_numeric_amount": 0, "nonstandard_rows": 0,
                "already_supported_id_rows": 0} for k in ("ppe", "intangible")}
    observed = {"ppe": False, "intangible": False}
    for row in rows:
        for kind in candidate_fields(row, year=year, scope=scope, corp=corp,
                                     stock=stock, receipt=receipt, cutoff=cutoff):
            if not missing[kind]:
                continue
            info[kind]["rows"] += 1
            if _amount(row.get("thstrm_amount")) is not None:
                info[kind]["with_numeric_amount"] += 1
            if row.get("account_id") == "-표준계정코드 미사용-":
                info[kind]["nonstandard_rows"] += 1
            elif row.get("account_id") in (
                    PPE_IDS if kind == "ppe" else INTANGIBLE_IDS):
                info[kind]["already_supported_id_rows"] += 1
            observed[kind] = True
    return info, observed


SUPPORTED_GAP_CAUSES = (
    "no_supported_flat_id",
    "invalid_or_missing_amount",
    "conflicting_duplicate_amounts",
    "negative_purchase_sign_not_certified",
)
CANDIDATE_ID_FAMILIES = (
    "already_supported", "ifrs_other", "dart_other",
    "nonstandard", "other_or_missing",
)


def candidate_id_family(account_id, kind):
    """Only fixed taxonomy classes are disclosed, never source identifiers."""
    known = PPE_IDS if kind == "ppe" else INTANGIBLE_IDS
    if account_id in known:
        return "already_supported"
    if account_id == "-표준계정코드 미사용-":
        return "nonstandard"
    if type(account_id) is str and re.fullmatch(r"ifrs(?:-full)?_[A-Za-z0-9]{2,120}", account_id):
        return "ifrs_other"
    if type(account_id) is str and re.fullmatch(r"dart_[A-Za-z0-9]{2,120}", account_id):
        return "dart_other"
    return "other_or_missing"


def supported_gap_cause(rows, *, kind, stock, corp, year, scope, receipt, cutoff):
    """Explain why an exact supported ID stayed NULL; never repair a figure.

    The previous normalized annual observation must already be proven
    identical. Ambiguous signs or duplicate amounts remain unscored.
    """
    known = PPE_IDS if kind == "ppe" else INTANGIBLE_IDS
    amounts = []
    for row in rows:
        if (type(row) is not dict or row.get("account_id") not in known or
            row.get("sj_div") != "CF" or
            row.get("account_detail", "-") not in ("", "-") or
            row.get("currency") != "KRW" or
            row.get("corp_code") != corp or
            ("stock_code" in row and row["stock_code"] != stock) or
            str(row.get("bsns_year")) != str(year) or
            row.get("reprt_code") != "11011" or
            ("fs_div" in row and row["fs_div"] != scope) or
            row.get("rcept_no") != receipt or
            not _valid_receipt(receipt, year, cutoff)):
            continue
        amounts.append(_amount(row.get("thstrm_amount")))
    if not amounts:
        return "no_supported_flat_id"
    if any(x is None for x in amounts):
        return "invalid_or_missing_amount"
    if len(set(amounts)) != 1:
        return "conflicting_duplicate_amounts"
    if amounts[0] < 0:
        return "negative_purchase_sign_not_certified"
    # Such a nonnegative observation should have been recognized by the
    # frozen production normalizer. Fail closed on inconsistent evidence.
    raise TaxonomyPilotError("unexplained_supported_amount_gap")


def probe(state, market, catalog, major, *, key, session,
          limit=MAX_YEAR_CALLS, check_official_taxonomy=False):
    selected = targets(state, market, catalog, major, limit)
    result = {
        "status": "official_capex_taxonomy_anonymous_pilot_only",
        "asof": state["asof"], "target_year_call_limit": limit,
        "eligible_selected_years": len(selected), "completed_year_calls": 0,
        "provider_calls": 0, "source_revision_or_missing_receipt": 0,
        "classes_checked": {n: 0 for n in CLASSES},
        "candidate_line_items": {"ppe": 0, "intangible": 0},
        "candidate_numeric_rows": {"ppe": 0, "intangible": 0},
        "nonstandard_label_rows": {"ppe": 0, "intangible": 0},
        "previously_supported_id_rows": {"ppe": 0, "intangible": 0},
        "issuer_years_with_candidate": {"ppe": 0, "intangible": 0},
        "candidate_numeric_account_id_families": {k: {c: 0 for c in CANDIDATE_ID_FAMILIES} for k in ("ppe", "intangible")},
        "saved_supported_id_gap_causes": {k: {c: 0 for c in SUPPORTED_GAP_CAUSES} for k in ("ppe", "intangible")},
        "no_new_fcf_scores_or_zero_imputation": True,
        "no_company_or_receipt_identity_emitted": True,
        "account_labels_not_accepted_as_accounting_proof": True,
        "official_taxonomy_section_calls": 0,
        "official_standard_acquisition_id_counts": {"ppe": 0, "intangible": 0},
        "official_taxonomy_novel_nonnegative_numeric_rows": {"ppe": 0, "intangible": 0},
        "official_taxonomy_novel_negative_numeric_rows": {"ppe": 0, "intangible": 0},
        "official_taxonomy_novel_issuer_years": {"ppe": 0, "intangible": 0},
        "official_taxonomy_novel_matching_ids": {"ppe": [], "intangible": []},
        "official_taxonomy_match_does_not_certify_sign_or_fcf": True,
    }
    if not selected:
        return result
    if type(key) is not str or len(key) < 20:
        raise TaxonomyPilotError("official_key_unavailable")
    official = None
    matched_ids = {"ppe": set(), "intangible": set()}
    if check_official_taxonomy:
        official = official_candidates(key, session)
        result["official_taxonomy_section_calls"] = 4
        result["provider_calls"] += 4
        for name in ("ppe", "intangible"):
            result["official_standard_acquisition_id_counts"][name] = len(official[name])
    _, members = asof_members(market, catalog, major)
    # Mapping is derived only from official corpCode.xml, never name guesses.
    try:
        response = session.get("https://opendart.fss.or.kr/api/corpCode.xml",
                               params={"crtfc_key": key}, timeout=(20, 95))
        result["provider_calls"] += 1
        response.raise_for_status()
        mapping = _mapping(response.content, set(members))
    except Exception:
        raise TaxonomyPilotError("official_mapping_unavailable") from None
    for kind, exchange, stock, year, scope, old in selected:
        corp = mapping.get(stock)
        if corp is None:
            continue
        if result["provider_calls"] >= MAX_TOTAL_CALLS:
            raise TaxonomyPilotError("official_pilot_call_budget_exceeded")
        try:
            response = session.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": key, "corp_code": corp,
                        "bsns_year": str(year), "reprt_code": "11011",
                        "fs_div": scope}, timeout=(20, 45))
            result["provider_calls"] += 1
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise TaxonomyPilotError("official_cashflow_request_failed") from None
        if type(payload) is not dict:
            raise TaxonomyPilotError("official_cashflow_payload_unrecognized")
        if payload.get("status") == "013":
            rows = []
        elif payload.get("status") == "000" and type(payload.get("list")) is list:
            rows = payload["list"]
        else:
            raise TaxonomyPilotError("official_cashflow_status_stop")
        if len(rows) > 18000:
            raise TaxonomyPilotError("official_cashflow_rows_excessive")
        try:
            reobserved = normalize_year(
                rows, code=stock, corp=corp, year=year, scope=scope,
                asof=state["asof"],
                major_receipt=major["records"][stock]["years"][str(year)][scope]["receipt_id"])
        except (CashflowError, ValueError, TypeError):
            raise TaxonomyPilotError("official_cashflow_evidence_rejected") from None
        result["completed_year_calls"] += 1
        # A changed official statement cannot be merged with the saved one.
        if reobserved != old or old["annual_receipt_id"] is None:
            result["source_revision_or_missing_receipt"] += 1
            continue
        result["classes_checked"][kind] += 1
        info, hits = summarize_candidates(
            rows, stock=stock, corp=corp, year=year, scope=scope,
            receipt=old["annual_receipt_id"], cutoff=_day(state["asof"]),
            missing={"ppe": old["ppe_capex_krw"] is None,
                     "intangible": old["intangible_capex_krw"] is None})
        for name in ("ppe", "intangible"):
            result["candidate_line_items"][name] += info[name]["rows"]
            result["candidate_numeric_rows"][name] += info[name]["with_numeric_amount"]
            result["nonstandard_label_rows"][name] += info[name]["nonstandard_rows"]
            result["previously_supported_id_rows"][name] += info[name]["already_supported_id_rows"]
            result["issuer_years_with_candidate"][name] += int(hits[name])
            if old["ppe_capex_krw" if name == "ppe" else "intangible_capex_krw"] is None:
                cause = supported_gap_cause(
                    rows, kind=name, stock=stock, corp=corp, year=year,
                    scope=scope, receipt=old["annual_receipt_id"],
                    cutoff=_day(state["asof"]))
                result["saved_supported_id_gap_causes"][name][cause] += 1
        year_matches = {"ppe": set(), "intangible": set()}
        for row in rows:
            for name in candidate_fields(
                    row, stock=stock, corp=corp, year=year, scope=scope,
                    receipt=old["annual_receipt_id"],
                    cutoff=_day(state["asof"])):
                if old["ppe_capex_krw" if name == "ppe" else "intangible_capex_krw"] is None and _amount(row.get("thstrm_amount")) is not None:
                    family = candidate_id_family(row.get("account_id"), name)
                    result["candidate_numeric_account_id_families"][name][family] += 1
                    if official is not None and novel_account_id(row.get("account_id"), name, official):
                        amount = _amount(row.get("thstrm_amount"))
                        if amount >= 0:
                            result["official_taxonomy_novel_nonnegative_numeric_rows"][name] += 1
                            matched_ids[name].add(row["account_id"])
                            year_matches[name].add(row["account_id"])
                        else:
                            result["official_taxonomy_novel_negative_numeric_rows"][name] += 1
        for name in ("ppe", "intangible"):
            result["official_taxonomy_novel_issuer_years"][name] += int(bool(year_matches[name]))
    for name in ("ppe", "intangible"):
        result["official_taxonomy_novel_matching_ids"][name] = sorted(matched_ids[name])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cashflow", default="docs/v3-dart-cashflow-reference.json")
    parser.add_argument("--market", default="docs/data.json")
    parser.add_argument("--catalog", default="docs/v22-market-catalog.json")
    parser.add_argument("--major", default="docs/v3-dart-bulk-history.json")
    parser.add_argument("--mode", choices=("dry_run", "live"), default="dry_run")
    parser.add_argument("--limit", type=int, default=MAX_YEAR_CALLS)
    parser.add_argument("--official-taxonomy", action="store_true",
                        help="Cross-check against four official FSS standard CF taxonomies")
    args = parser.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))
    state, market, catalog, major = map(read, (
        args.cashflow, args.market, args.catalog, args.major))
    selected = targets(state, market, catalog, major, args.limit)
    if args.mode == "dry_run":
        print(json.dumps({"status": "dry_run_no_provider",
            "candidate_years": len(selected), "provider_calls": 0,
            "score_promoted": False}, sort_keys=True))
        return
    import requests
    report = probe(state, market, catalog, major,
                   key=os.getenv("DART_API_KEY", ""),
                   session=requests.Session(), limit=args.limit,
                   check_official_taxonomy=args.official_taxonomy)
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
