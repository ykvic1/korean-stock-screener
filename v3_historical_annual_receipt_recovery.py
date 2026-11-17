"""Recover the annual disclosure versions available at the historical score anchor."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
from xml.etree import ElementTree

from official_probe.clients import DART_BASE, DARTClient, ProbeError
from official_probe.revisions import _zip_evidence
from v3_dart_bulk_history import BulkHistoryError, _mapping
from v3_historical_market_coverage import MARKETS, SIGNAL, require
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS

SCHEMA = "v3-historical-annual-receipt-recovery-v1"
PIN_DAY = "2026-11-13"
PINNED_HASHES = (
    SOURCE_DIGESTS[0],
    "991b4765f806932fe810d7a21aa7565809517e925da5d7c8d7c99ea462d23a8c",
    "41e82e39460d38091c5eeeefe3d20c72f51a7a36651e810efbaf1dcadde303f4",
)
TARGETS = 51
WORKERS = 4
CUTOFF = SIGNAL.strftime("%Y%m%d")
START = "20240101"
RECEIPT = re.compile(r"[0-9]{14}\Z")
TITLE = re.compile(r"(?P<prefix>(?:\s*\[[^\[\]]+\])*)\s*사업보고서\s*"
                   r"\(\s*(?P<year>20[0-9]{2})\s*\.\s*(?P<month>0?[1-9]|1[0-2])\s*\)\s*")
CLASSES = {"KOSPI": "Y", "KOSDAQ": "K"}


def load_targets(prices, financial, catalog):
    import pyarrow.parquet as pq

    for path, expected in zip((prices, financial, catalog), PINNED_HASHES, strict=True):
        require(sha256(path.read_bytes()).hexdigest() == expected, "historical_annual_input_digest_mismatch")
    statements = json.loads(financial.read_bytes())
    census = json.loads(catalog.read_bytes())
    require(statements["asof"] == census["date"] == PIN_DAY, "historical_annual_input_date_mismatch")
    anchored = pq.read_table(prices, columns=["Code", "Name", "Market"], filters=[
        ("Date", "=", datetime.combine(SIGNAL, time.min)), ("Market", "in", list(MARKETS)),
    ]).to_pylist()
    historical = {row["Code"]: (row["Name"], MARKETS[row["Market"]]) for row in anchored}
    require(len(historical) == len(anchored) == 2761, "historical_annual_anchor_census_mismatch")
    targets = {}
    for row in census["rows"]:
        code, name, market = (row[key] for key in ("code", "name", "market"))
        if historical.get(code) != (name, market):
            continue
        years = statements["records"].get(code, {}).get("years", {})
        receipts = {scope: record.get("receipt_id") for scope, record in years.get("2024", {}).items()}
        if not any(RECEIPT.fullmatch(value or "") and value[:8] > CUTOFF
                   for value in receipts.values()):
            continue
        require(code not in targets and len(set(receipts.values())) == 1
                and re.fullmatch(r"[0-9]{6}", code), "historical_annual_source_receipt_ambiguous")
        targets[code] = {"name": name, "market": market,
                         "retained_later_receipt": next(iter(receipts.values())),
                         "retained_scopes": sorted(receipts)}
    require(Counter(row["market"] for row in targets.values()) == {"KOSPI": 15, "KOSDAQ": 36}
            and len(targets) == TARGETS, "historical_annual_target_census_mismatch")
    return dict(sorted(targets.items()))


def _period(title):
    match = TITLE.fullmatch(title)
    if not match:
        return None, None
    labels = re.findall(r"\[([^\[\]]+)\]", match["prefix"])
    if not labels:
        kind = "original"
    elif labels == ["기재정정"]:
        kind = "body_correction"
    elif labels in (["첨부정정"], ["첨부추가"]):
        kind = "attachment"
    else:
        kind = "other_label"
    return f"{match['year']}-{int(match['month']):02d}", kind


def choose_version(response, *, corp, code, market, later):
    result = {"status": "missing_retained_receipt_in_official_list", "versions": [],
              "report_period": None, "selected_receipt": None}
    if response.get("status") == "013":
        return result
    items = response.get("list")
    try:
        count, pages = int(response["total_count"]), int(response["total_page"])
    except (KeyError, TypeError, ValueError):
        raise ProbeError("Invalid historical annual filing pagination") from None
    if pages != 1 or not isinstance(items, list) or not 0 < count == len(items) <= 100:
        raise ProbeError("Incomplete historical annual filing list")
    listings = {}
    for row in items:
        if not isinstance(row, dict):
            raise ProbeError("Invalid historical annual filing row")
        receipt, received = row.get("rcept_no"), row.get("rcept_dt")
        title = row.get("report_nm")
        if (not isinstance(receipt, str) or not RECEIPT.fullmatch(receipt)
                or received != receipt[:8] or not START <= received <= later[:8]
                or receipt in listings or row.get("corp_code") != corp
                or row.get("stock_code") != code or row.get("corp_cls") != CLASSES[market]
                or not isinstance(title, str)):
            raise ProbeError("Invalid historical annual filing identity")
        try:
            date(int(received[:4]), int(received[4:6]), int(received[6:]))
        except ValueError:
            raise ProbeError("Invalid historical annual filing date") from None
        listings[receipt] = _period(title)
    period, _ = listings.get(later, (None, None))
    if period is None:
        return result

    versions = [{"receipt": receipt, "kind": kind} for receipt, (label, kind)
                in sorted(listings.items()) if label == period]
    result.update(report_period=period, versions=versions)
    timely = [row for row in versions if row["receipt"][:8] <= CUTOFF
              and row["kind"] != "attachment"]
    if not timely:
        result["status"] = "no_body_filed_by_cutoff"
    elif any(row["receipt"][:8] == CUTOFF for row in timely):
        result["status"] = "same_day_body_publication_time_unverified"
    elif any(row["kind"] == "other_label" for row in timely):
        result["status"] = "body_version_label_unresolved"
    elif timely[0]["kind"] != "original" or sum(row["kind"] == "original" for row in timely) != 1:
        result["status"] = "original_chain_unresolved"
    elif sum(row["receipt"][:8] == timely[-1]["receipt"][:8] for row in timely) != 1:
        result["status"] = "multiple_bodies_on_last_prior_day"
    else:
        result.update(status="prior_body_version_identified", selected_receipt=timely[-1]["receipt"])
    return result


def verify_document(client, receipt):
    blob = client.original_document_zip(receipt)
    if len(blob) < 8192 and not blob.startswith(b"PK"):
        upper = blob.upper()
        if b"<!DOCTYPE" not in upper and b"<!ENTITY" not in upper:
            try:
                if ElementTree.fromstring(blob).findtext("status") in ("013", "014"):
                    return None
            except ElementTree.ParseError:
                pass
        raise ProbeError("Unexpected historical annual document response")
    try:
        evidence = _zip_evidence(blob)
    except ProbeError:
        return {}
    return {key: evidence[key] for key in
            ("archive_sha256", "internal_content_manifest_sha256", "entry_count")}


def recover_one(client, code, record, corp):
    if not corp:
        return {**record, "status": "issuer_mapping_unavailable"}
    later = record["retained_later_receipt"]
    response = client._api("list.json", corp_code=corp, bgn_de=START, end_de=later[:8],
                           pblntf_ty="A", pblntf_detail_ty="A001", last_reprt_at="N",
                           page_no=1, page_count=100)
    selected = choose_version(response, corp=corp, code=code, market=record["market"], later=later)
    row = {**record, **selected}
    if selected["selected_receipt"]:
        evidence = verify_document(client, selected["selected_receipt"])
        if evidence:
            row.update(status="prior_document_zip_verified", document=evidence)
        elif evidence is None:
            row["status"] = "prior_body_version_identified_document_unavailable"
        else:
            row["status"] = "prior_body_version_identified_invalid_document_archive"
    return row


def collect(targets, key):
    main_client = DARTClient(key)
    main_client.set_research_timeout(18)
    try:
        raw = main_client._research_get("corp_mapping_zip", DART_BASE + "corpCode.xml",
                                        params={"crtfc_key": key}, limit=25_000_000)
        mapping = _mapping(raw, targets.keys())
    finally:
        main_client.session.close()
    local = threading.local()
    clients = []

    def recover(item):
        if not hasattr(local, "client"):
            local.client = DARTClient(key)
            local.client.set_research_timeout(18)
            clients.append(local.client)
        code, record = item
        try:
            return code, recover_one(local.client, code, record, mapping.get(code))
        except ProbeError as error:
            # These are local, fixed diagnostics. Vendor messages are suppressed
            # by the official client. Unknown service errors stop further calls.
            issue = str(error)
            if issue.startswith("Invalid historical annual filing"):
                return code, {**record, "status": "official_receipt_identity_or_format_unresolved"}
            if issue == "Incomplete historical annual filing list":
                return code, {**record, "status": "official_receipt_listing_incomplete"}
            raise

    records = {}
    failure = None
    items = list(targets.items())
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as readers:
            for offset in range(0, len(items), WORKERS):
                futures = [readers.submit(recover, item) for item in items[offset:offset + WORKERS]]
                for future in futures:
                    try:
                        code, row = future.result()
                        records[code] = row
                    except ProbeError as error:
                        failure = str(error)
                if failure:
                    break
    finally:
        for client in clients:
            client.session.close()
    calls = len(main_client.research_request_timings)
    calls += sum(len(client.research_request_timings) for client in clients)
    return records, calls, failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prices", type=Path)
    parser.add_argument("financial", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    targets = load_targets(args.prices, args.financial, args.catalog)
    records, calls, failure = targets, 0, None
    if args.live:
        try:
            records, calls, failure = collect(targets, os.environ.get("DART_API_KEY", ""))
        except (ProbeError, BulkHistoryError, ValueError) as error:
            raise SystemExit(str(error)) from None
    report = {
        "schema": SCHEMA, "anchor": SIGNAL.isoformat(), "financial_pin": PIN_DAY,
        "pinned_sources_sha256": list(PINNED_HASHES), "target_count": len(targets),
        "live": args.live, "official_requests": calls, "provider_or_contract_failure": failure,
        "target_by_market": dict(Counter(row["market"] for row in targets.values())),
        "counts": dict(Counter(row["status"] for row in records.values())) if args.live else {},
        "targets_with_result": len(records) if args.live else 0,
        "records": records, "numeric_financial_inputs_restored": 0,
        "point_in_time_scores_verified": False, "performance_validated": False,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if failure:
        raise SystemExit("historical_annual_recovery_incomplete")


if __name__ == "__main__":
    main()
