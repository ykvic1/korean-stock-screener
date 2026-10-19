"""PRIVATE local SQLite prototype for approved future two-market KRX archives.

No live KRX calls, credentials, public Pages/Excel writes or automatic jobs.
Raw bytes stay in a chmod-0600 DB outside this PUBLIC repository.
Storage does not imply provider licensing, auth, finality, or PIT verification.
Use only locally after checking contractual permission to retain raw data.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

from v3_evidence_chain import (
    SCHEMA as CHAIN_SCHEMA, MAX_BYTES_PER_SOURCE, ZERO_HASH,
    EvidenceChainError, append_market_pair, empty_manifest, verify_manifest
)

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = Path.home() / ".local" / "share" / "korean-stock-screener" / "v3" / "private_market.sqlite3"
SCHEMA = "v3-private-local-sqlite-0.1"


class PrivateStoreError(ValueError):
    """Sanitized failure codes; never echo private filenames or source bytes."""


def _validate_path(db_path: Any, *, create: bool):
    if not isinstance(db_path, (str, Path)):
        raise PrivateStoreError("invalid_private_database_path")
    path = Path(db_path).expanduser()
    if path.suffix != ".sqlite3":
        raise PrivateStoreError("private_sqlite_extension_required")
    if path.is_symlink() or path.parent.is_symlink():
        raise PrivateStoreError("symlink_private_store_forbidden")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise PrivateStoreError("public_repo_storage_forbidden")
    if not path.parent.exists():
        if not create:
            raise PrivateStoreError("private_store_not_found")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise PrivateStoreError("unsafe_private_directory")
    if os.name == "posix" and path.parent.stat().st_mode & 0o077:
        raise PrivateStoreError("private_directory_permissions_too_open")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PrivateStoreError("unsafe_existing_private_db")
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise PrivateStoreError("private_database_permissions_too_open")
    elif not create:
        raise PrivateStoreError("private_store_not_found")
    else:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
            os.close(fd)
        except OSError:
            raise PrivateStoreError("private_database_create_failed") from None
    return path


def _connect(path, *, read_only=False):
    try:
        if read_only:
            # Inspect never creates a database/table or a journal.
            from urllib.parse import quote
            conn = sqlite3.connect("file:" + quote(str(path)) + "?mode=ro",
                                   uri=True, isolation_level=None, timeout=10)
            conn.execute("PRAGMA query_only=ON")
            return conn
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS source_pairs ("
                     "asof TEXT PRIMARY KEY,"
                     "chain_entry_json TEXT NOT NULL,"
                     "kospi_raw BLOB NOT NULL,"
                     "kosdaq_raw BLOB NOT NULL)")
        return conn
    except sqlite3.Error:
        raise PrivateStoreError("private_database_open_failed") from None


def _checked_bytes(value):
    if type(value) is not bytes or not 0 < len(value) <= MAX_BYTES_PER_SOURCE:
        raise PrivateStoreError("invalid_raw_source_bytes")
    return value


def _read_verified_pairs(con):
    try:
        rows = con.execute(
            "SELECT asof,chain_entry_json,kospi_raw,kosdaq_raw "
            "FROM source_pairs ORDER BY asof ASC")
    except sqlite3.Error:
        raise PrivateStoreError("private_archive_unreadable") from None
    manifest = empty_manifest()
    for asof, serial, kpi, kdq in rows:
        try:
            entry = json.loads(serial)
        except (TypeError, ValueError):
            raise PrivateStoreError("private_archive_entry_corrupt") from None
        if not isinstance(entry, dict) or entry.get("asof") != asof:
            raise PrivateStoreError("private_archive_entry_identity_corrupt")
        if (type(kpi) is not bytes or type(kdq) is not bytes
                or not isinstance(entry.get("markets"), dict)):
            raise PrivateStoreError("private_archive_pair_missing")
        for market, blob in (("KOSPI", kpi), ("KOSDAQ", kdq)):
            metadata = entry["markets"].get(market)
            if (not isinstance(metadata, dict)
                    or metadata.get("byte_count") != len(blob)
                    or metadata.get("raw_sha256") != hashlib.sha256(blob).hexdigest()):
                raise PrivateStoreError("raw_bytes_and_manifest_disagree")
        manifest["entries"].append(entry)
    try:
        audit = verify_manifest(manifest)
    except EvidenceChainError:
        raise PrivateStoreError("private_archive_chain_inconsistent") from None
    return manifest, audit


def _safe_audit(report):
    return {
        "schema": SCHEMA,
        "archived_days": report["records"],
        "last_chain_digest_unanchored": report["head_digest_unanchored"],
        "raw_sources_rehashed_against_manifest": True,
        "internal_chain_consistent": True,
        "stored_outside_public_repo": True,
        "third_party_provenance_authenticated": False,
        "independent_head_anchor_verified": False,
        "private_at_rest_encryption_provided": False,
        "contractual_storage_permission_verified": False,
        "historical_universe_or_regular_volume_verified": False,
        "authorized_for_v3_score": False,
        "confirmed_buy": False,
    }


def store_pair(*, database_path=DEFAULT_DB, asof, kospi_raw, kosdaq_raw,
               kospi_received_at_utc, kosdaq_received_at_utc):
    """Atomically append two markets; unchanged replay okay, conflict rejected.

    The timestamp arguments are caller assertions, NOT independently signed
    receipt records. No real provider source is fetched by this function.
    """
    kpi = _checked_bytes(kospi_raw)
    kdq = _checked_bytes(kosdaq_raw)
    path = _validate_path(database_path, create=True)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        manifest, _ = _read_verified_pairs(conn)
        sources = [
            {"raw_sha256": hashlib.sha256(blob).hexdigest(),
             "byte_count": len(blob), "received_at_utc": received}
            for blob, received in (
                (kpi, kospi_received_at_utc), (kdq, kosdaq_received_at_utc))
        ]
        try:
            appended = append_market_pair(
                manifest, asof=asof, kospi=sources[0], kosdaq=sources[1])
        except EvidenceChainError:
            raise PrivateStoreError("private_pair_rejected_by_manifest_contract") from None
        if appended["outcome"] == "new_private_manifest_entry_prepared":
            entry = appended["manifest"]["entries"][-1]
            conn.execute(
                "INSERT INTO source_pairs (asof,chain_entry_json,kospi_raw,kosdaq_raw) "
                "VALUES (?,?,?,?)",
                (asof, json.dumps(entry, sort_keys=True, separators=(",", ":")),
                 kpi, kdq))
        conn.commit()
        return {
            "status": "private_pair_added" if appended["outcome"].startswith("new_")
                      else "private_pair_already_present_unchanged",
            **_safe_audit(appended["audit"]),
        }
    except (sqlite3.Error, PrivateStoreError):
        conn.rollback()
        raise PrivateStoreError("private_storage_transaction_rejected") from None
    finally:
        conn.close()


def archived_trade_dates(*, database_path=DEFAULT_DB):
    """Read-only verified list of paired market dates; never expose raw bytes.

    Used solely for an append-only local backfill planner. Returns only
    YYYY-MM-DD strings; validates entire private archive and hash chain.
    """
    path = _validate_path(database_path, create=False)
    conn = _connect(path, read_only=True)
    try:
        manifest, _ = _read_verified_pairs(conn)
        return [entry["asof"] for entry in manifest["entries"]]
    finally:
        conn.close()


def inspect_private_store(*, database_path=DEFAULT_DB):
    """Rehash and verify all stored bytes without returning or printing them."""
    path = _validate_path(database_path, create=False)
    conn = _connect(path, read_only=True)
    try:
        _, report = _read_verified_pairs(conn)
        return _safe_audit(report)
    finally:
        conn.close()
