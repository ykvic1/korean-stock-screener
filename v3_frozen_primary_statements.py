"""Compare FY2024 primary-statement rows with frozen, receipt-matched values."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path

from official_probe.clients import DART_BASE, DARTClient
from v3_dart_bulk_history import FIELDS, _amount, _explicit_krw_currency, _mapping
from v3_historical_annual_receipt_recovery import CUTOFF, PINNED_HASHES, RECEIPT

# Standard primary-statement concepts. Unmapped issuer extensions remain unresolved.
CONCEPTS = {
    ("BS", "ifrs-full_Assets"): "assets",
    ("BS", "ifrs-full_Liabilities"): "liabilities",
    ("BS", "ifrs-full_Equity"): "equity",
    ("IS", "ifrs-full_Revenue"): "revenue",
    ("IS", "dart_OperatingIncomeLoss"): "operating_income",
}


def compare(rows, corp, receipt, scope, saved):
    candidates = defaultdict(list)
    for row in rows:
        if (row.get("rcept_no") != receipt or row.get("corp_code") != corp
                or row.get("reprt_code") != "11011" or row.get("bsns_year") != "2024"
                or row.get("fs_div", scope) != scope):
            continue
        statement = row.get("sj_div")
        metric = CONCEPTS.get(("IS" if statement == "CIS" else statement, row.get("account_id")))
        if metric:
            amount = _amount(row.get("thstrm_amount")) if _explicit_krw_currency(row.get("currency")) else None
            candidates[metric].append(amount)
    result = {}
    for metric in FIELDS:
        values = set(candidates[metric])
        if saved.get(metric) is None:
            status = "stored_value_missing"
        elif not values:
            status = "no_matching_primary_row"
        elif None in values or len(values) != 1:
            status = "ambiguous_or_unusable"
        elif saved[metric] in values:
            status = "matched"
        else:
            status = "different_value"
        result[metric] = status
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("financial", type=Path)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("start", type=int)
    parser.add_argument("end", type=int)
    args = parser.parse_args()
    raw = args.financial.read_bytes()
    if sha256(raw).hexdigest() != PINNED_HASHES[1]:
        raise ValueError("frozen_financial_digest_mismatch")
    history = json.loads(raw)["records"]
    records = {}
    offsets = set()
    for path in args.inputs.rglob("historical-annual-receipt-recovery.json"):
        batch = json.loads(path.read_bytes())
        offset = batch["reference_offset"]
        if not args.start <= offset < args.end:
            continue
        if (batch["pinned_sources_sha256"] != list(PINNED_HASHES) or not batch["live"]
                or batch["anchor"] != "2025-11-13" or batch["provider_or_contract_failure"]
                or batch["eligible_cohort_size"] != 2267 or offset in offsets
                or len(batch["records"]) != min(25, 2267 - offset)
                or records.keys() & batch["records"].keys()):
            raise ValueError("invalid_frozen_receipt_batch")
        offsets.add(offset)
        records.update(batch["records"])
    if offsets != set(range(args.start, args.end, 25)):
        raise ValueError("missing_frozen_receipt_batch")

    client = DARTClient(os.environ["DART_API_KEY"])
    statuses = Counter()
    by_metric = {metric: Counter() for metric in FIELDS}
    outcomes = {}
    try:
        master = client._research_get("corp_mapping_zip", DART_BASE + "corpCode.xml",
                                      params={"crtfc_key": os.environ["DART_API_KEY"]}, limit=25_000_000)
        mapping = _mapping(master, records)
        for code, record in sorted(records.items()):
            receipt = record.get("selected_receipt")
            if record["status"] != "prior_document_zip_verified":
                statuses["original_document_unresolved"] += 1
                continue
            if not RECEIPT.fullmatch(receipt or "") or receipt[:8] >= CUTOFF:
                raise ValueError("unexpected_historical_receipt_date")
            annual = history[code]["years"]["2024"]
            scope = "CFS" if "CFS" in annual else "OFS"
            saved = annual[scope]
            if saved["receipt_id"] != receipt:
                statuses["historical_and_saved_receipt_differ"] += 1
                continue
            if code not in mapping:
                statuses["issuer_mapping_unavailable"] += 1
                continue
            corp = mapping[code]
            response = client._api("fnlttSinglAcntAll.json", corp_code=corp, bsns_year="2024",
                                   reprt_code="11011", fs_div=scope)
            rows = response["list"]
            result = compare(rows, corp, receipt, scope, saved["values"])
            outcomes[code] = {"receipt": receipt, "scope": scope, "fields": result}
            statuses["compared"] += 1
            for metric, state in result.items():
                by_metric[metric][state] += 1
    finally:
        client.session.close()
    summary = {"anchor": "2025-11-13", "start": args.start, "end": args.end,
               "issuers": len(records), "statuses": statuses, "primary_statement_fields": by_metric}
    print(json.dumps(summary, sort_keys=True))
    Path("primary-statement-evidence.json").write_text(json.dumps(
        {**summary, "outcomes": outcomes, "pinned_sources_sha256": PINNED_HASHES}, sort_keys=True))


if __name__ == "__main__":
    main()
