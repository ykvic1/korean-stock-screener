"""Read-only semantic re-audit of an explicitly selected PRIVATE KRX paired archive.

A consistent hash chain is NOT proof that JSON belongs to that market/date.
Re-check original raw bytes, each dated issuer and OHLCV quality without
issuing any network request or disclosing prices, issuers, source rows or keys.

Neither valid raw shape nor 250 candidate dates authenticate the official
exchange calendar, historical issuer identity, corporate actions or PIT.
Never publish this report to an investor-facing screen.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from v3_evidence_chain import (MAX_RECORDS, MAX_BYTES_PER_SOURCE,
                               empty_manifest, verify_manifest,
                               EvidenceChainError)
from v3_local_private_store import (ROOT, _validate_path, _connect,
                                    PrivateStoreError)
from v3_krx_history_collection import (_snapshot_market, HistoryCollectionError)
from v3_krx_private_backfill_plan import (
    calendar_weekday_candidates, plan_next_batch, BackfillPlanError)
from v3_krx_session_candidates import candidate_dates, CandidateCalendarError


class ArchiveReauditError(ValueError):
    """Fixed safe failure reasons; no filesystem path or vendor data."""


def _raw_market(blob, *, market, asof):
    if type(blob) is not bytes or not 0 < len(blob) <= MAX_BYTES_PER_SOURCE:
        raise ArchiveReauditError("archive_raw_source_shape_rejected")
    try:
        body = json.loads(blob)
        rows = body["OutBlock_1"]
        if not isinstance(body, dict) or not isinstance(rows, list):
            raise ValueError()
        inspected = _snapshot_market(rows, blob, market=market, asof=asof)
    except (ValueError, TypeError, KeyError, UnicodeError, HistoryCollectionError):
        raise ArchiveReauditError("archive_market_date_or_ohlcv_rejected") from None
    return inspected, {row["ISU_CD"] for row in rows}


def audit_private_archive(*, start, end, private_db, calendar_source="xkrx",
                          batch_size=5):
    """Audit historical candidates and a read-only private SQLite, if present.

    Memory overhead for source rows is bounded to one date-market at a time.
    Only small metadata descriptors enter the chain manifest. The database is
    never created or mutated and no official API is called.
    """
    if type(private_db) not in (str, Path) or not str(private_db):
        raise ArchiveReauditError("explicit_private_database_path_required")
    path = Path(private_db).expanduser()
    if path.suffix != ".sqlite3":
        raise ArchiveReauditError("private_sqlite_extension_required")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents or path.is_symlink():
        raise ArchiveReauditError("public_or_unsafe_archive_path_rejected")
    try:
        weekdays = calendar_weekday_candidates(start, end)
        candidate = candidate_dates(weekdays, source=calendar_source)
    except (BackfillPlanError, CandidateCalendarError):
        raise ArchiveReauditError("candidate_calendar_rejected") from None

    all_dates = candidate["dates"]
    archive_dates = []
    manifest = empty_manifest()
    previous_codes = {}
    totals = {m: {"source_rows": 0, "valid_numeric_rows": 0,
                  "quarantined_rows": 0, "numeric_code_rows": 0,
                  "alphanumeric_code_rows": 0, "new_codes_vs_prior_day": 0,
                  "missing_codes_vs_prior_day": 0}
              for m in ("KOSPI", "KOSDAQ")}
    connection = None
    if path.exists():
        try:
            _validate_path(path, create=False)
            connection = _connect(path, read_only=True)
            cursor = connection.execute(
                "SELECT asof,chain_entry_json,kospi_raw,kosdaq_raw "
                "FROM source_pairs ORDER BY asof ASC")
            for asof, serial, kospi, kosdaq in cursor:
                if len(archive_dates) >= MAX_RECORDS:
                    raise ArchiveReauditError("archive_over_maximum_records")
                if (type(asof) is not str or
                        not re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof)):
                    raise ArchiveReauditError("archive_date_identity_rejected")
                try:
                    entry = json.loads(serial)
                    if not isinstance(entry, dict) or entry.get("asof") != asof:
                        raise ValueError()
                    sources = entry["markets"]
                    if not isinstance(sources, dict):
                        raise ValueError()
                except (ValueError, TypeError, KeyError, UnicodeError):
                    raise ArchiveReauditError("archive_manifest_entry_rejected") from None
                examined_codes = {}
                for market, blob in (("KOSPI", kospi), ("KOSDAQ", kosdaq)):
                    source = sources.get(market)
                    if (not isinstance(source, dict) or type(blob) is not bytes
                            or source.get("byte_count") != len(blob)
                            or source.get("raw_sha256") != sha256(blob).hexdigest()):
                        raise ArchiveReauditError("archive_raw_digest_or_length_mismatch")
                    observed, codes = _raw_market(blob, market=market, asof=asof)
                    examined_codes[market] = codes
                    t = totals[market]
                    t["source_rows"] += observed["provider_rows_observed"]
                    t["valid_numeric_rows"] += observed["observed_numeric_candidates"]
                    t["quarantined_rows"] += observed["quarantined_unpriced_candidates"]
                    t["numeric_code_rows"] += observed["six_digit_numeric_code_rows"]
                    t["alphanumeric_code_rows"] += observed[
                        "six_character_alphanumeric_code_rows"]
                    if market in previous_codes:
                        t["new_codes_vs_prior_day"] += len(codes - previous_codes[market])
                        t["missing_codes_vs_prior_day"] += len(previous_codes[market] - codes)
                    previous_codes[market] = codes
                if examined_codes["KOSPI"] & examined_codes["KOSDAQ"]:
                    raise ArchiveReauditError("cross_market_duplicate_short_code")
                archive_dates.append(asof)
                manifest["entries"].append(entry)
        except (PrivateStoreError, sqlite3.Error):
            raise ArchiveReauditError("private_archive_read_or_permission_rejected") from None
        finally:
            if connection is not None:
                connection.close()

    try:
        verified = verify_manifest(manifest)
        planned = plan_next_batch(all_dates, already_archived=archive_dates,
                                  batch_size=batch_size)
    except EvidenceChainError:
        raise ArchiveReauditError("private_archive_chain_rejected") from None
    except BackfillPlanError:
        raise ArchiveReauditError("archive_not_candidate_prefix") from None

    return {
        "schema": "v3-krx-private-archive-reaudit-0.1",
        "mode": "offline_read_only", "status": (
            "internally_reaudited_unverified_source"
            if archive_dates else "no_archived_market_pairs"),
        "actual_api_calls": 0, "private_archive_mutated": False,
        "candidate_calendar_source": candidate["calendar_candidate_source"],
        "calendar_candidate_library_version": candidate[
            "calendar_candidate_library_version"],
        "candidate_dates_sha256": candidate["candidate_dates_sha256"],
        "candidate_date_count": len(all_dates),
        "archive_paired_dates": len(archive_dates),
        "first_archived_date": archive_dates[0] if archive_dates else None,
        "last_archived_date": archive_dates[-1] if archive_dates else None,
        "next_batch_dates": planned["next_batch_dates"],
        "remaining_candidate_dates": planned["remaining_weekday_candidates"],
        "last_chain_digest_unanchored": verified["head_digest_unanchored"],
        "internal_chain_and_source_shape_consistent": bool(archive_dates),
        "by_market": totals,
        "independent_raw_source_authentication_verified": False,
        "independent_archive_head_anchor_verified": False,
        "official_exchange_calendar_authenticated": False,
        "historic_issuer_master_and_delistings_verified": False,
        "corporate_action_and_adjusted_prices_verified": False,
        "regular_session_only_volume_verified": False,
        "external_pit_financials_and_investor_flow_verified": False,
        "historical_250_sessions_verified": False,
        "v3_release_ready": False,
        "confirmed_scoring_allowed": False, "confirmed_buy": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="No-network PRIVATE local archive re-audit; counts only.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--private-db", required=True)
    parser.add_argument("--calendar-source", choices=("weekdays", "xkrx"),
                        default="xkrx")
    parser.add_argument("--batch-size", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        report = audit_private_archive(
            start=args.start, end=args.end, private_db=args.private_db,
            calendar_source=args.calendar_source, batch_size=args.batch_size)
        print(json.dumps(report, sort_keys=True, ensure_ascii=True))
        return 0
    except ArchiveReauditError as error:
        print(json.dumps({
            "status": "blocked", "reason": str(error), "actual_api_calls": 0,
            "v3_release_ready": False, "confirmed_buy": False,
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
