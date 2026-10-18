"""Pure offline paired-market evidence hash chain contract for future PRIVATE storage.

No API, no filesystem writes, no raw KRX data or credentials, no live storage.
An internally valid hash chain is tamper-evident ONLY relative to an independently
anchored previous head, which this module does not provide. Never certify PIT,
official session, copyrights/licensing, provider identity or v3 release.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import json

SCHEMA = "v3-two-market-private-manifest-contract-0.1"
ZERO_HASH = "0" * 64
MARKETS = ("KOSPI", "KOSDAQ")
MAX_RECORDS = 1800
MAX_BYTES_PER_SOURCE = 20_000_000
BLOCKED_FLAGS = (
    "independent_head_anchor_verified",
    "raw_source_bytes_authenticated",
    "real_private_persistent_storage_ready",
    "regular_only_market_volume_verified",
    "full_pit_history_verified",
    "v3_release_ready",
)


class EvidenceChainError(ValueError):
    """No secrets, URLs or provider raw data in raised messages."""


def _day(value):
    if type(value) is not str or len(value) != 10:
        raise EvidenceChainError("invalid_market_date")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise EvidenceChainError("invalid_market_date") from None
    if d.isoformat() != value:
        raise EvidenceChainError("invalid_market_date")
    return d


def _utc(value):
    if type(value) is not str or not value.endswith("Z"):
        raise EvidenceChainError("invalid_utc_receipt")
    # Exact format disallows naive dates, offsets without Z, and local-time
    # receipts accidentally treated as UTC.
    try:
        stamp = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise EvidenceChainError("invalid_utc_receipt") from None
    return stamp.replace(tzinfo=timezone.utc)


def _sha(value):
    return (type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(entry):
    return hashlib.sha256(_canonical(entry)).hexdigest()


def empty_manifest():
    return {
        "schema": SCHEMA, "entries": [],
        **{key: False for key in BLOCKED_FLAGS},
    }


def _source(src, asof):
    if not isinstance(src, dict):
        raise EvidenceChainError("invalid_source_descriptor")
    if set(src) != {"raw_sha256", "byte_count", "received_at_utc"}:
        raise EvidenceChainError("invalid_source_fields")
    if not _sha(src["raw_sha256"]):
        raise EvidenceChainError("invalid_source_digest")
    n = src["byte_count"]
    if type(n) is not int or not 0 < n <= MAX_BYTES_PER_SOURCE:
        raise EvidenceChainError("invalid_source_length")
    receipt = _utc(src["received_at_utc"])
    # A 15:30 Korea close is 06:30 UTC. This is only a minimal causality
    # constraint; late closing auctions, provider finality and holidays are
    # not authenticated.
    earliest = datetime.combine(_day(asof), time(6, 30), timezone.utc)
    if receipt < earliest:
        raise EvidenceChainError("source_received_before_nominal_eod")
    return src


def _entry_content(asof, sources, prev):
    return {"asof": asof, "markets": sources, "prev_hash": prev}


def verify_manifest(manifest):
    """Reject mutations/ambiguous schemas, but never call it independent proof."""
    if not isinstance(manifest, dict):
        raise EvidenceChainError("invalid_manifest")
    if set(manifest) != {"schema", "entries", *BLOCKED_FLAGS} or manifest.get("schema") != SCHEMA:
        raise EvidenceChainError("unsupported_manifest_schema")
    if any(manifest.get(key) is not False for key in BLOCKED_FLAGS):
        raise EvidenceChainError("unverified_claim_cannot_authorize_release")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
        raise EvidenceChainError("invalid_manifest_entries")
    prev = ZERO_HASH
    last_asof = None
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
                "asof", "markets", "prev_hash", "entry_hash"}:
            raise EvidenceChainError("invalid_chain_entry")
        day = _day(entry["asof"])
        if last_asof is not None and day <= last_asof:
            raise EvidenceChainError("nonmonotonic_manifest_date")
        last_asof = day
        sources = entry["markets"]
        if not isinstance(sources, dict) or set(sources) != set(MARKETS):
            raise EvidenceChainError("paired_market_snapshots_required")
        for market in MARKETS:
            _source(sources[market], entry["asof"])
        if sources["KOSPI"]["raw_sha256"] == sources["KOSDAQ"]["raw_sha256"]:
            raise EvidenceChainError("cross_market_source_digest_reused")
        if entry["prev_hash"] != prev or not _sha(entry["entry_hash"]):
            raise EvidenceChainError("broken_manifest_hash_link")
        expected = _hash(_entry_content(entry["asof"], sources, prev))
        if expected != entry["entry_hash"]:
            raise EvidenceChainError("manifest_entry_digest_mismatch")
        prev = expected
    return {
        "schema": SCHEMA, "records": len(entries),
        "head_digest_unanchored": prev,
        "internal_linkage_consistent": True,
        "independent_head_anchor_verified": False,
        "source_raw_bytes_verified": False,
        "persistent_private_storage_ready": False,
        "regular_session_and_pit_verified": False,
        "v3_release_ready": False,
        "confirmed_v3_score": None,
    }


def append_market_pair(manifest, *, asof, kospi, kosdaq):
    """Append a FULL two-market date or idempotently repeat the exact same pair.

    Backfill must be supplied in chronological order. A date with changed
    source bytes requires separately archived revision evidence; no overwrite.
    """
    audit = verify_manifest(manifest)
    day = _day(asof)
    sources = {
        "KOSPI": _source(kospi, asof),
        "KOSDAQ": _source(kosdaq, asof),
    }
    if sources["KOSPI"]["raw_sha256"] == sources["KOSDAQ"]["raw_sha256"]:
        raise EvidenceChainError("cross_market_source_digest_reused")
    entries = manifest["entries"]
    if entries:
        latest_day = _day(entries[-1]["asof"])
        if day < latest_day:
            raise EvidenceChainError("out_of_order_backfill")
        if day == latest_day:
            if entries[-1]["markets"] != sources:
                raise EvidenceChainError("existing_day_immutable_conflict")
            return {
                "manifest": json.loads(_canonical(manifest)),
                "outcome": "already_recorded_unchanged",
                "audit": audit,
            }
    if len(entries) >= MAX_RECORDS:
        raise EvidenceChainError("manifest_capacity_exceeded")
    prev = entries[-1]["entry_hash"] if entries else ZERO_HASH
    content = _entry_content(day.isoformat(), sources, prev)
    new = json.loads(_canonical(manifest))
    new["entries"].append({**content, "entry_hash": _hash(content)})
    return {
        "manifest": new,
        "outcome": "new_private_manifest_entry_prepared",
        "audit": verify_manifest(new),
    }
