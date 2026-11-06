"""Bounded FY2025 annual-CF comparative research for one-gap R1d FCF issuers.

The official FY2025 annual API carries FY2024 and FY2023 comparison columns.
These are NOT the original FY2024/FY2023 filing receipts, so this pilot never
stores a fiscal amount, patches the FCF ledger or promotes any score weight.
It asks whether a missing historical fact has a same-scope, same-2025-receipt,
same-current-fact comparison candidate worth independently verifying next.
Only aggregate counts leave the workflow. One API call per selected issuer.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import re

from v3_dart_bulk_history import (
    _amount, _mapping, _valid_receipt,
    verified_company_mapping_cache, BulkHistoryError,
)
from v3_dart_cashflow_enrichment import (
    verify_state, CFO_IDS, PPE_IDS, INTANGIBLE_IDS, NONSTANDARD_ACCOUNT_ID,
    NONSTANDARD_CAPEX_NAMES,
)
from v3_v30b_release_gate import r1d_public_reconciles

MAX_ISSUERS = 20
CAPEX_FIELDS = {
    "operating_cashflow_krw": CFO_IDS,
    "ppe_capex_krw": PPE_IDS,
    "intangible_capex_krw": INTANGIBLE_IDS,
}
COMP_FIELDS = {"2024": "frmtrm_amount", "2023": "bfefrmtrm_amount"}
CODE = re.compile(r"[0-9]{6}\Z")


class ComparativeProbeError(ValueError):
    """Provider data and private identifiers never appear in error messages."""


def source_day_aligned(market, catalog, bulk, book, cashflow,
                       operational, comparison, priority):
    """Routine source-day rollover waits without making an official API call."""
    raw = (market.get("date"), catalog.get("date"), bulk.get("asof"),
           book.get("asof"), cashflow.get("asof"), operational.get("asof"),
           comparison.get("asof"), priority.get("asof"))
    try:
        days = [date.fromisoformat(x) for x in raw]
        if any(d.isoformat() != x for d, x in zip(days, raw)):
            raise ValueError()
    except (TypeError, ValueError):
        raise ComparativeProbeError("invalid_comparative_source_day") from None
    if (max(days)-min(days)).days > 9:
        raise ComparativeProbeError("expired_comparative_source_day")
    return len(set(days)) == 1


def select_targets(market, catalog, bulk, book, cashflow, operational,
                   comparison, priority, *, limit=MAX_ISSUERS, cohort_index=0):
    """Check saved sources before selecting any official API request."""
    if (type(limit) is not int or not 1 <= limit <= MAX_ISSUERS
        or type(cohort_index) is not int or not 0 <= cohort_index <= 1000000):
        raise ComparativeProbeError("invalid_comparative_budget")
    day = catalog.get("date")
    if (type(day) is not str or
        book.get("asof") != day or priority.get("asof") != day or
        priority.get("operational_source_digest") != operational.get("source_digest")
        or book.get("historical_point_in_time_verified") is not False
        or r1d_public_reconciles(operational, comparison, catalog) is None):
        raise ComparativeProbeError("unreconciled_comparative_priority")
    try:
        verify_state(cashflow, market, catalog, bulk, strict_fingerprint=True)
    except (BulkHistoryError, ValueError, KeyError, TypeError) as error:
        raise ComparativeProbeError("untrusted_comparative_cashflow_source") from None
    members = {r["code"]: r for r in catalog["rows"]}
    books = {r["code"]: r for r in book.get("records", [])}
    if len(members) != catalog["listed_total"] or len(books) != len(book.get("records", [])):
        raise ComparativeProbeError("duplicate_comparative_issuer")
    selected = []
    for item in priority.get("single_component_queue", []):
        gaps = item.get("gaps", [])
        if (len(gaps) != 1 or gaps[0].get("area") != "financial"
            or gaps[0].get("component") != "cashflow_fcf_persistence"):
            continue
        code = item.get("code")
        if type(code) is not str or not CODE.fullmatch(code) or code not in members:
            raise ComparativeProbeError("unlisted_comparative_candidate")
        row = operational["rows"].get(code, {})
        # An untrusted priority file cannot nominate an otherwise complete
        # issuer. Verify the actual two source-account parts by identity.
        if (row.get("name") != item.get("name") or
            row.get("market") != item.get("market") or
            row.get("available") != item.get("available") or
            row.get("parts", {}).get("financial", {}).get("cashflow_fcf_persistence") is not None):
            raise ComparativeProbeError("invalid_comparative_gap_identity")
        source = cashflow["records"].get(code, {})
        scope = source.get("scope")
        annual = books.get(code, {}).get("annual") or {}
        current = source.get("years", {}).get("2025", {})
        if (scope not in ("CFS", "OFS") or annual.get("fs_div") != scope
            or annual.get("year") != 2025 or annual.get("currency") != "KRW"
            or annual.get("receipt_id") != current.get("annual_receipt_id")
            or not _valid_receipt(annual.get("receipt_id"), 2025, date.fromisoformat(day))
            or current.get("major_annual_receipt_matched") is not True):
            continue
        missing = {}
        for year in COMP_FIELDS:
            source_year = source.get("years", {}).get(year, {})
            if source_year.get("major_annual_receipt_matched") is not True:
                continue
            fields = tuple(field for field in CAPEX_FIELDS
                           if source_year.get(field) is None)
            if fields:
                missing[year] = fields
        if missing:
            selected.append((code, scope, annual["receipt_id"], current, missing))
    selected.sort(key=lambda x: (members[x[0]]["market"] != "KOSPI", x[0]))
    if not selected:
        return day, members, [], 0
    # Every scheduled cohort advances by one disjoint bounded slice, wrapping
    # after the full set. No stale or completed-account provider calls.
    start = (cohort_index * limit) % len(selected)
    window = (selected * 2)[start:start + min(limit, len(selected))]
    return day, members, window, len(selected)


def _kind(row):
    if row.get("sj_div") != "CF" or row.get("account_detail", "-") not in ("", "-"):
        return None
    account = row.get("account_id")
    for field, ids in CAPEX_FIELDS.items():
        if account in ids:
            return field
    if account == NONSTANDARD_ACCOUNT_ID:
        for name, label in NONSTANDARD_CAPEX_NAMES.items():
            if row.get("account_nm") == label:
                return name + "_capex_krw"
    return None


def inspect(payload, *, corp, code, scope, receipt, current, missing):
    """Candidate booleans only: source year/period is NOT certified."""
    if type(payload) is not dict:
        raise ComparativeProbeError("invalid_comparative_payload")
    if payload.get("status") == "013":
        rows = []
    elif payload.get("status") == "000" and type(payload.get("list")) is list:
        rows = payload["list"]
    else:
        raise ComparativeProbeError("comparative_source_unavailable")
    if len(rows) > 18000:
        raise ComparativeProbeError("oversized_comparative_source")
    values = {year: {field: [] for field in fields}
              for year, fields in missing.items()}
    for row in rows:
        if type(row) is not dict or (row.get("corp_code") != corp
            or ("stock_code" in row and row["stock_code"] != code)
            or str(row.get("bsns_year")) != "2025"
            or row.get("reprt_code") != "11011"
            or ("fs_div" in row and row["fs_div"] != scope)):
            raise ComparativeProbeError("comparative_company_or_period_mismatch")
        field = _kind(row)
        if field is None or not any(field in fields for fields in missing.values()):
            continue
        if row.get("currency") != "KRW" or row.get("rcept_no") != receipt:
            raise ComparativeProbeError("comparative_currency_or_receipt_mismatch")
        # Same fact in the existing FY2025 receipt is an independent
        # identity/scope consistency check. A numerical prior-year value
        # without this matching anchor cannot be treated as a candidate.
        current_value = _amount(row.get("thstrm_amount"))
        if current_value is None or current_value != current.get(field):
            continue
        for year, fields in missing.items():
            if field not in fields:
                continue
            value = _amount(row.get(COMP_FIELDS[year]))
            if value is not None and (field == "operating_cashflow_krw" or value >= 0):
                values[year][field].append(value)
    output = Counter()
    required = sum(len(fields) for fields in missing.values())
    candidate_count = 0
    for year, fields in values.items():
        for field, amounts in fields.items():
            output["checked_" + year + "_" + field] += 1
            if amounts and len(set(amounts)) == 1:
                output["candidate_" + year + "_" + field] += 1
                candidate_count += 1
            elif len(set(amounts)) > 1:
                output["ambiguous_" + year + "_" + field] += 1
    # Fact counts alone can overstate how many ISSUERS could eventually close
    # their entire three-year FCF gap. These are still comparative candidates,
    # never observed FCF source facts or score weights.
    output["issuers_with_any_comparative_candidate"] = int(candidate_count > 0)
    output["issuers_with_all_missing_comparatives"] = int(
        required > 0 and candidate_count == required)
    return output


def audit_annual_receipts(payload, *, corp, original_receipts, asof):
    """Match FY2023/24 source receipts to independent annual filing metadata.

    List metadata never authenticates statement facts or correction effective
    dates. If one page does not cover the full response, certify *nothing*.
    """
    if (type(original_receipts) is not dict or not original_receipts
        or set(original_receipts) - set(COMP_FIELDS)
        or any(not _valid_receipt(receipt, int(year), date.fromisoformat(asof))
               for year, receipt in original_receipts.items())):
        raise ComparativeProbeError("invalid_original_annual_receipt_request")
    if type(payload) is not dict:
        raise ComparativeProbeError("invalid_official_annual_receipt_list")
    if payload.get("status") == "013":
        rows = []
    elif payload.get("status") == "000":
        try:
            count = int(payload["total_count"])
            pages = int(payload["total_page"])
            page_no = int(payload["page_no"])
            page_count = int(payload["page_count"])
        except (TypeError, ValueError, KeyError):
            raise ComparativeProbeError("invalid_annual_receipt_pagination") from None
        if (any(type(payload[x]) not in (str, int) for x in
                ("total_count", "total_page", "page_no", "page_count"))
            or not 0 <= count <= 100000 or pages != (count + 99) // 100
            or page_no != 1 or page_count != 100):
            raise ComparativeProbeError("invalid_annual_receipt_pagination")
        if pages > 1:
            return Counter({"annual_receipt_list_censored_multi_page": 1})
        rows = payload.get("list")
        if (type(rows) is not list or len(rows) != count):
            raise ComparativeProbeError("invalid_annual_receipt_pagination")
    else:
        raise ComparativeProbeError("official_annual_receipt_list_unavailable")
    seen = set()
    by_year = {year: [] for year in original_receipts}
    for row in rows:
        if type(row) is not dict or row.get("corp_code") != corp:
            raise ComparativeProbeError("annual_receipt_issuer_mismatch")
        receipt, submitted, title = (row.get("rcept_no"), row.get("rcept_dt"),
                                      row.get("report_nm"))
        if (type(receipt) is not str or not re.fullmatch(r"[0-9]{14}", receipt)
            or receipt in seen or type(submitted) is not str
            or not re.fullmatch(r"[0-9]{8}", submitted)
            or receipt[:8] != submitted or type(title) is not str
            or not 0 < len(title) <= 500):
            raise ComparativeProbeError("invalid_annual_receipt_metadata")
        try:
            submitted_day = date.fromisoformat(
                submitted[:4] + "-" + submitted[4:6] + "-" + submitted[6:])
        except ValueError:
            raise ComparativeProbeError("invalid_annual_receipt_metadata") from None
        if submitted_day > date.fromisoformat(asof):
            raise ComparativeProbeError("future_annual_receipt_metadata")
        seen.add(receipt)
        for year in original_receipts:
            if re.search(r"사업보고서\s*\(\s*" + year + r"\.(?:0[1-9]|1[0-2])\s*\)", title):
                by_year[year].append(receipt)
    output = Counter()
    for year, expected in original_receipts.items():
        output["checked_historical_annual_receipt_" + year] += 1
        found = sorted(by_year[year])
        if expected in found:
            output["matched_historical_annual_receipt_" + year] += 1
            if expected == found[0]:
                output["source_receipt_earliest_listed_" + year] += 1
            if any(r > expected for r in found):
                output["later_annual_filing_exists_" + year] += 1
        elif found:
            output["other_annual_filing_only_" + year] += 1
    output["issuers_with_all_historical_annual_receipts_listed"] = int(all(
        original_receipts[year] in by_year[year] for year in original_receipts))
    return output


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="docs")
    parser.add_argument("--cache", default=".cache/v3-dart-company-codes.json")
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    parser.add_argument("--limit", type=int, default=MAX_ISSUERS)
    parser.add_argument("--cohort-index", type=int, default=0)
    parser.add_argument("--audit-annual-receipts", action="store_true",
                        help="Also compare FY2023/24 receipt IDs against official annual filing listings")
    args = parser.parse_args(argv)
    root = Path(args.docs)
    def read(path):
        return json.loads((root/path).read_text(encoding="utf8"))
    market = read("data.json")
    catalog = read("v22-market-catalog.json")
    bulk = read("v3-dart-bulk-history.json")
    book = read("v3-dart-book-reference.json")
    cashflow = read("v3-dart-cashflow-reference.json")
    operational = read("v3-operational-research-scores.json")
    comparison = read("v3-operational-research-comparison.json")
    priority = read("v3-operational-evidence-priority.json")
    if not source_day_aligned(market, catalog, bulk, book, cashflow,
                              operational, comparison, priority):
        output = {"status": "waiting_exact_source_day_alignment",
                  "asof": catalog.get("date"), "provider_calls": 0,
                  "sampled_issuers": 0,
                  "eligible_one_gap_fcf_issuers": None,
                  "amounts_published": False,
                  "scores_or_fcf_source_promoted": False}
        print(json.dumps(output, sort_keys=True))
        return output
    day, members, selected, total = select_targets(
        market, catalog, bulk, book, cashflow, operational, comparison, priority,
        limit=args.limit, cohort_index=args.cohort_index)
    output = {"asof": day, "status": "comparison_candidates_not_original_receipts",
              "eligible_one_gap_fcf_issuers": total, "sampled_issuers": len(selected),
              "provider_calls": 0, "amounts_published": False,
              "scores_or_fcf_source_promoted": False,
              "historical_filing_effective_dates_verified": False,
              "comparison_values_used_as_original_filing": False}
    if args.mode == "dry-run" or not selected:
        print(json.dumps(output, sort_keys=True))
        return output
    key = os.getenv("DART_API_KEY", "")
    if len(key) < 20:
        raise ComparativeProbeError("official_dart_api_key_missing")
    import requests
    cache_path = Path(args.cache)
    try:
        cache = json.loads(cache_path.read_text(encoding="utf8")) if cache_path.is_file() else None
    except (ValueError, OSError, UnicodeError):
        raise ComparativeProbeError("untrusted_comparative_company_cache") from None
    try:
        mapping = verified_company_mapping_cache(cache, day, members)
    except BulkHistoryError:
        raise ComparativeProbeError("untrusted_comparative_company_cache") from None
    with requests.Session() as session:
        if mapping is None:
            try:
                response = session.get("https://opendart.fss.or.kr/api/corpCode.xml",
                                       params={"crtfc_key": key}, timeout=(20, 75))
                response.raise_for_status()
                mapping = _mapping(response.content, set(members))
            except Exception:
                # The requests exception can include the query URL and API key.
                raise ComparativeProbeError("official_comparative_identity_unavailable") from None
            output["provider_calls"] += 1
        counts = Counter()
        for code, scope, receipt, current, missing in selected:
            if code not in mapping:
                counts["missing_verified_company_mapping"] += 1
                continue
            try:
                response = session.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                                       params={"crtfc_key": key, "corp_code": mapping[code],
                                               "bsns_year": "2025", "reprt_code": "11011",
                                               "fs_div": scope}, timeout=(20, 40))
                response.raise_for_status()
                payload = response.json()
            except Exception:
                raise ComparativeProbeError("official_comparative_transport_unavailable") from None
            output["provider_calls"] += 1
            counts.update(inspect(payload, corp=mapping[code], code=code,
                                  scope=scope, receipt=receipt,
                                  current=current, missing=missing))
            counts["sampled_official_responses"] += 1
            if args.audit_annual_receipts:
                years = cashflow["records"][code]["years"]
                original_receipts = {
                    year: years[year]["annual_receipt_id"] for year in missing}
                # Receipt IDs must be anchored in the original fiscal year's
                # reconciled cashflow source, never inferred from comparison.
                if any(not _valid_receipt(value, int(year), date.fromisoformat(day))
                       for year, value in original_receipts.items()):
                    raise ComparativeProbeError("invalid_original_annual_receipt_request")
                try:
                    response = session.get("https://opendart.fss.or.kr/api/list.json",
                        params={"crtfc_key": key, "corp_code": mapping[code],
                                "bgn_de": "20240101", "end_de": day.replace("-", ""),
                                "pblntf_detail_ty": "A001", "last_reprt_at": "N",
                                "page_no": "1", "page_count": "100"}, timeout=(20, 40))
                    response.raise_for_status()
                    listing = response.json()
                except Exception:
                    raise ComparativeProbeError("official_annual_receipt_transport_unavailable") from None
                output["provider_calls"] += 1
                counts.update(audit_annual_receipts(listing, corp=mapping[code],
                    original_receipts=original_receipts, asof=day))
                counts["sampled_official_annual_lists"] += 1
    output["candidate_fact_counts"] = dict(sorted(counts.items()))
    print(json.dumps(output, sort_keys=True))
    return output


if __name__ == "__main__":
    run()
