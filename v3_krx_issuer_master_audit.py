"""Offline KRX day-market vs approved daily instrument-master shape audit.

Source contracts: day-market ISU_CD is a ticker-like six-char identifier;
instrument-master ISU_CD is *standard* code, ISU_SRT_CD is short code.
Those similarly named fields MUST NOT be joined to each other directly.
This module NEVER requests unapproved extra KRX APIs.
"""
from __future__ import annotations
from hashlib import sha256
import json
import re

CODE = re.compile(r"[A-Z0-9]{6}\Z")
STANDARD = re.compile(r"[A-Z0-9]{12}\Z")


class MasterJoinError(ValueError):
    """Whitelisted safe errors only; never issuer identifiers/vendor values."""


def _check_json_rows(payload,rows):
    if type(payload) is not bytes or not 0 < len(payload) <= 20_000_000:
        raise MasterJoinError("missing_raw_provider_source")
    try:
        actual=json.loads(payload)
        if (not isinstance(actual,dict)
                or actual.get("OutBlock_1") != rows):
            raise ValueError()
    except (ValueError,TypeError,UnicodeError):
        raise MasterJoinError("raw_source_rows_disagree") from None


def audit_market_master(*,trade_rows,master_rows,
                        trade_raw,master_raw,market,asof):
    """Return counts/hashes only. No names, codes, corporate action claims."""
    if market not in ("KOSPI","KOSDAQ"):
        raise MasterJoinError("invalid_market")
    if (type(asof) is not str
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",asof)):
        raise MasterJoinError("invalid_asof")
    if (not isinstance(trade_rows,list) or not isinstance(master_rows,list)
            or not trade_rows or not master_rows
            or max(len(trade_rows),len(master_rows))>20000):
        raise MasterJoinError("empty_or_oversized_master_audit")
    _check_json_rows(trade_raw,trade_rows)
    _check_json_rows(master_raw,master_rows)
    short_to_standard={}
    master_without_proven_standard=0
    for entry in master_rows:
        if not isinstance(entry,dict):
            raise MasterJoinError("invalid_master_row")
        short=entry.get("ISU_SRT_CD")
        standard=entry.get("ISU_CD")
        if type(short) is not str or not CODE.fullmatch(short):
            raise MasterJoinError("unusable_master_short_code")
        if short in short_to_standard:
            raise MasterJoinError("duplicate_master_short_code")
        # Only use official standard code if 12 ASCII uppercase alnum chars.
        # A missing/odd standard code cannot claim verified identity.
        if type(standard) is not str or not STANDARD.fullmatch(standard):
            master_without_proven_standard+=1
            short_to_standard[short]=None
        else:
            short_to_standard[short]=standard
    # 12-char standard code should not identify multiple DIFFERENT short
    # codes for a unique day-market: require explicit review if encountered.
    valid_std=[v for v in short_to_standard.values() if v is not None]
    if len(valid_std)!=len(set(valid_std)):
        raise MasterJoinError("duplicate_master_standard_identity")
    trade_seen=set()
    matched=0
    matched_alphanumeric=0
    unmatched=0
    unresolved_master=0
    for entry in trade_rows:
        if not isinstance(entry,dict):
            raise MasterJoinError("invalid_trade_row")
        short=entry.get("ISU_CD")
        if type(short) is not str or not CODE.fullmatch(short):
            raise MasterJoinError("unusable_trade_short_code")
        if short in trade_seen:
            raise MasterJoinError("duplicate_trade_short_code")
        trade_seen.add(short)
        if str(entry.get("BAS_DD","")).replace("-","")!=asof.replace("-",""):
            raise MasterJoinError("trade_master_date_mismatch")
        if short not in short_to_standard:
            unmatched+=1
        elif short_to_standard[short] is None:
            unresolved_master+=1
        else:
            matched+=1
            if not short.isdecimal():
                matched_alphanumeric+=1
    return {
        "schema":"v3-krx-issuer-master-offline-audit-0.1",
        "market":market,"asof":asof,
        "trade_rows_observed":len(trade_rows),
        "master_rows_observed":len(master_rows),
        "matched_unique_short_codes":matched,
        "matched_six_character_alphanumeric_codes":matched_alphanumeric,
        "unmatched_trade_short_codes":unmatched,
        "matched_but_invalid_standard_code":unresolved_master,
        "master_rows_without_valid_standard_code":master_without_proven_standard,
        "trade_source_sha256":sha256(trade_raw).hexdigest(),
        "master_source_sha256":sha256(master_raw).hexdigest(),
        "master_source_timestamp_and_service_approval_verified":False,
        "same_day_master_pit_independently_verified":False,
        "historical_universe_and_delistings_verified":False,
        "corporate_actions_verified":False,
        "regular_only_volume_verified":False,
        "can_release_v3_scoring":False,
        "confirmed_buy":False,
    }
