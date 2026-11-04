"""Read-only structural audit of a pinned PRIVATE 120-month receipt snapshot.

No provider calls, source mutations, original-document authentication, legal
effective-date certification, adjusted prices or investment-score promotion.
Only anonymous counts and one-way digests may leave the private snapshot.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from v3_dart_historical_event_receipts import (
    CATEGORIES, MAX_FILE_BYTES, MONTHS, _check_cursor, _decode, _path,
)

SCHEMA = "v3-private-dart-receipt-archive-structure-audit-v1"
MAX_TOTAL_PAGES = 100_000
NOTE = ("Complete selected B/C/E/I receipt-metadata snapshot structure only. "
        "Not independent provider-snapshot, original-document, corporate-action, "
        "effective-date, historical-universe or investment-score certification.")
FALSE_FIELDS = (
    "provider_snapshot_authenticated", "original_documents_authenticated",
    "corporate_actions_or_effective_dates_certified", "historical_adjusted_prices_certified",
    "historical_issuer_universe_pit_certified", "v3_trade_scores_promoted",
    "public_identifiers_or_original_bytes_published",
)


class ReceiptArchiveAuditError(ValueError):
    """Fixed diagnostics only; never include paths, receipts or source text."""


def completed_cursor(raw):
    try:
        value = _check_cursor(raw)
        if (value["month_index"] != len(MONTHS)
            or value["category_index"] != 0 or value["page"] != 1
            or not 1 <= value["saved_pages"] <= MAX_TOTAL_PAGES):
            raise ValueError()
        return value
    except Exception:
        raise ReceiptArchiveAuditError("historical_receipt_archive_not_complete") from None


def _read(path, limit):
    if path.is_symlink() or not path.is_file():
        raise ReceiptArchiveAuditError("private_receipt_snapshot_file_invalid")
    with path.open("rb") as handle:
        value = handle.read(limit + 1)
    if not value or len(value) > limit:
        raise ReceiptArchiveAuditError("private_receipt_snapshot_file_invalid")
    return value


def _inventory(path, expected):
    if path.is_symlink() or not path.is_dir():
        raise ReceiptArchiveAuditError("private_receipt_snapshot_inventory_invalid")
    if {item.name for item in path.iterdir()} != set(expected):
        raise ReceiptArchiveAuditError("private_receipt_snapshot_inventory_invalid")


def audit(root):
    root = Path(root)
    if root.is_symlink():
        raise ReceiptArchiveAuditError("private_receipt_snapshot_inventory_invalid")
    cursor_raw = _read(root / "checkpoint.json", MAX_FILE_BYTES)
    cursor = completed_cursor(cursor_raw)
    _inventory(root, (*MONTHS, "checkpoint.json"))
    seen = set()
    structure = sha256()
    page_count = receipt_count = 0
    final_path = final_digest = None
    for month in MONTHS:
        _inventory(root / month, CATEGORIES)
        for category in CATEGORIES:
            folder = root / month / category
            if folder.is_symlink() or not folder.is_dir():
                raise ReceiptArchiveAuditError("private_receipt_snapshot_inventory_invalid")
            first_raw = _read(folder / "0001.json.gz", MAX_FILE_BYTES)
            try:
                first = _decode(first_raw, month, category, 1)
            except Exception:
                raise ReceiptArchiveAuditError("private_receipt_snapshot_page_invalid") from None
            expected_pages = max(1, first["total_pages"])
            if page_count + expected_pages > MAX_TOTAL_PAGES:
                raise ReceiptArchiveAuditError("private_receipt_snapshot_page_budget_exceeded")
            _inventory(folder, (f"{page:04d}.json.gz" for page in range(1, expected_pages + 1)))
            category_receipts = 0
            for page in range(1, expected_pages + 1):
                raw = first_raw if page == 1 else _read(folder / f"{page:04d}.json.gz", MAX_FILE_BYTES)
                try:
                    value = first if page == 1 else _decode(raw, month, category, page)
                except Exception:
                    raise ReceiptArchiveAuditError("private_receipt_snapshot_page_invalid") from None
                if (value["total_count"] != first["total_count"]
                    or value["total_pages"] != first["total_pages"]):
                    raise ReceiptArchiveAuditError("private_receipt_snapshot_pagination_changed")
                for row in value["receipts"]:
                    receipt = row["rcept_no"]
                    if receipt in seen:
                        raise ReceiptArchiveAuditError("duplicate_receipt_in_private_archive")
                    seen.add(receipt)
                category_receipts += len(value["receipts"])
                page_count += 1
                final_path = _path(month, category, page)
                final_digest = sha256(raw).hexdigest()
                structure.update(final_path.encode("ascii") + b"\0" + bytes.fromhex(final_digest))
            if category_receipts != first["total_count"]:
                raise ReceiptArchiveAuditError("private_receipt_snapshot_receipt_count_mismatch")
            receipt_count += category_receipts
    if page_count != cursor["saved_pages"] or receipt_count != cursor["saved_receipts"]:
        raise ReceiptArchiveAuditError("private_receipt_snapshot_cursor_totals_mismatch")
    if (final_path != cursor["last_page_file"]
        or final_digest != cursor["last_page_sha256"]):
        raise ReceiptArchiveAuditError("private_receipt_snapshot_tail_mismatch")
    return {
        "schema": SCHEMA, "months_scanned": len(MONTHS),
        "category_scope": list(CATEGORIES), "pages_scanned": page_count,
        "receipts_scanned": receipt_count, "unique_receipts": len(seen),
        "source_checkpoint_sha256": sha256(cursor_raw).hexdigest(),
        "archive_structure_sha256": structure.hexdigest(),
        "selected_receipt_metadata_structure_verified": True,
        "provider_api_calls": 0, **{name: False for name in FALSE_FIELDS}, "note": NOTE,
    }


def audit_matches_checkpoint(report, raw):
    """Skip repeated automatic scans of an already audited immutable cursor.

    Manual live requests always re-scan in the workflow. This check never
    establishes provider authenticity or authorizes a trading score.
    """
    try:
        cursor = completed_cursor(raw)
        expected_keys = {
            "schema", "months_scanned", "category_scope", "pages_scanned",
            "receipts_scanned", "unique_receipts", "source_checkpoint_sha256",
            "archive_structure_sha256", "selected_receipt_metadata_structure_verified",
            "provider_api_calls", "note", *FALSE_FIELDS,
        }
        return (type(report) is dict and set(report) == expected_keys
            and report["schema"] == SCHEMA and report["months_scanned"] == len(MONTHS)
            and report["category_scope"] == list(CATEGORIES)
            and type(report["pages_scanned"]) is int and report["pages_scanned"] == cursor["saved_pages"]
            and type(report["receipts_scanned"]) is int and report["receipts_scanned"] == cursor["saved_receipts"]
            and type(report["unique_receipts"]) is int and report["unique_receipts"] == cursor["saved_receipts"]
            and report["source_checkpoint_sha256"] == sha256(raw).hexdigest()
            and type(report["archive_structure_sha256"]) is str
            and re.fullmatch(r"[a-f0-9]{64}", report["archive_structure_sha256"]) is not None
            and report["selected_receipt_metadata_structure_verified"] is True
            and type(report["provider_api_calls"]) is int and report["provider_api_calls"] == 0
            and all(report[name] is False for name in FALSE_FIELDS)
            and report["note"] == NOTE)
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = Path(args.private_root).resolve()
    target = Path(args.output).resolve()
    if target == root or root in target.parents:
        raise ReceiptArchiveAuditError("audit_output_must_not_modify_private_snapshot")
    report = audit(args.private_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"status": "selected_private_receipt_metadata_audited",
                      "months": report["months_scanned"], "pages": report["pages_scanned"],
                      "receipts": report["receipts_scanned"], "provider_calls": 0,
                      "scores_promoted": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("blocked: private_dart_receipt_archive_structure_invalid") from None
