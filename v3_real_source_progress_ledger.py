"""Append bounded, source-fingerprinted progress snapshots for real V3 coverage.

The six-area coverage audit is recomputed independently from saved market,
DART, and weekly technical sources. This module NEVER calls providers and
NEVER manufactures scores; it records successive verified audit measurements.
Historical PIT and full six-pillar certification remain explicitly unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from v3_real_six_pillar_coverage_audit import SCHEMA as AUDIT_SCHEMA
from v3_dart_bulk_history import write_json

SCHEMA = "v3-saved-six-area-real-source-progress-ledger-0.1"
AREAS = ("ias","technical","financial","valuation","industry","momentum")
MAX_ENTRIES = 120
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ProgressLedgerError(ValueError):
    """No source payload, individual issuer IDs, or provider error text."""


def digest(value):
    return hashlib.sha256(json.dumps(
        value,sort_keys=True,ensure_ascii=False,separators=(",",":"),
        allow_nan=False).encode("utf8")).hexdigest()


def _int(value, maximum):
    return type(value) is int and 0<=value<=maximum


def _day(value):
    if type(value) is not str:
        raise ProgressLedgerError("invalid_progress_market_date")
    try:
        normalized=date.fromisoformat(value).isoformat()
    except ValueError:
        raise ProgressLedgerError("invalid_progress_market_date") from None
    if normalized!=value:
        raise ProgressLedgerError("invalid_progress_market_date")
    return value


def observation(audit):
    """Only validated, provenanced current-listed research counts."""
    if (type(audit) is not dict or audit.get("schema")!=AUDIT_SCHEMA or
        audit.get("buy_recommendations_authorized") is not False or
        audit.get("investment_grade_scores_confirmed") is not False or
        audit.get("six_pillar_official_complete_100pt_issuer_count")!=0 or
        audit.get("official_investor_IAS_source_issuer_count")!=0 or
        audit.get("industry_scored_rotation_issuer_count")!=0 or
        audit.get("official_pbr_verified_count")!=0):
        raise ProgressLedgerError("uncertified_progress_audit")
    asof=_day(audit.get("market_asof"))
    total=audit.get("listed_total")
    if not _int(total,10000) or total==0:
        raise ProgressLedgerError("invalid_progress_universe")
    areas=audit.get("six_pillar_numeric_provisional_issuer_counts")
    if (type(areas) is not dict or set(areas)!=set(AREAS) or
        any(not _int(areas[a],total) for a in AREAS) or
        areas["ias"]!=0 or areas["industry"]!=0):
        raise ProgressLedgerError("invalid_provisional_area_counts")
    years=audit.get("historical_dart_company_years_with_accounts_by_fiscal")
    queried=audit.get("historical_dart_company_years_attempted_by_fiscal")
    expected={str(y) for y in range(int(asof[:4])-4,int(asof[:4]))}
    if (type(years) is not dict or set(years)!=expected or
        type(queried) is not dict or set(queried)!=expected or
        any(not _int(years[y],total) or not _int(queried[y],total)
            or years[y]>queried[y] for y in expected)):
        raise ProgressLedgerError("invalid_real_fiscal_coverage")
    counts={}
    fields=(
        "verified_four_year_income_reference_issuers",
        "three_year_actual_margin_source_issuers",
        "three_year_margin_provisional_score_issuers",
        "historical_dart_distinct_issuer_count",
        "technical_weekly_source_processed_issuer_count",
        "simple_pbr_reference_provisional_issuers",
        "earnings_yield_reference_provisional_issuers",
        "four_numerical_areas_same_date_overlap_count",
        "three_or_more_provisional_numerical_areas",
    )
    for name in fields:
        value=audit.get(name)
        if not _int(value,total):
            raise ProgressLedgerError("invalid_real_coverage_metric")
        counts[name]=value
    if (counts["verified_four_year_income_reference_issuers"]>
        counts["three_year_actual_margin_source_issuers"] or
        counts["three_year_margin_provisional_score_issuers"]>
        counts["three_year_actual_margin_source_issuers"] or
        counts["four_numerical_areas_same_date_overlap_count"]>
        min(areas["technical"],areas["financial"],areas["valuation"],
            areas["momentum"]) or
        counts["verified_four_year_income_reference_issuers"]>
        areas["financial"]):
        raise ProgressLedgerError("inconsistent_real_source_cohorts")
    sources=audit.get("source_sha256_canonical")
    if (type(sources) is not dict or
        set(sources)!={"market_catalog","dart_four_year_archive",
                      "dart_partial_score","weekly_technical"} or
        any(type(v) is not str or not SHA256.fullmatch(v)
            for v in sources.values())):
        raise ProgressLedgerError("invalid_saved_source_fingerprints")
    return {
        "market_asof":asof,
        "listed_total":total,
        "audit_sha256":digest(audit),
        "source_sha256":dict(sources),
        "provisional_issuers_by_area":{a:areas[a] for a in AREAS},
        "source_observed_company_years":dict(sorted(years.items())),
        "source_attempted_company_years":dict(sorted(queried.items())),
        "other_measured_counts":counts,
        "official_full_six_pillar_scores":0,
        "historical_PIT_certified":False,
        "buy_signals_authorized":False,
    }


def _validate_existing(previous):
    if previous is None:
        return []
    if (type(previous) is not dict or previous.get("schema")!=SCHEMA or
        previous.get("historical_PIT_certified") is not False or
        previous.get("buy_signals_authorized") is not False or
        type(previous.get("snapshots")) is not list or
        not 1<=len(previous["snapshots"])<=MAX_ENTRIES):
        raise ProgressLedgerError("untrusted_previous_progress_ledger")
    snapshots=previous["snapshots"]
    for index,snap in enumerate(snapshots):
        if (type(snap) is not dict or type(snap.get("observation")) is not dict or
            snap.get("sequence")!=previous.get("first_sequence",1)+index or
            not _int(snap["sequence"],10**9)):
            raise ProgressLedgerError("invalid_progress_sequence")
        recorded=snap["observation"]
        day=_day(recorded.get("market_asof"))
        hs=recorded.get("audit_sha256")
        if type(hs) is not str or not SHA256.fullmatch(hs):
            raise ProgressLedgerError("invalid_progress_audit_hash")
        if index and (day<snapshots[index-1]["observation"]["market_asof"] or
                      hs==snapshots[index-1]["observation"]["audit_sha256"]):
            raise ProgressLedgerError("noncausal_or_repeated_progress_snapshot")
        if snap.get("progress_since_previous") is not None:
            delta=snap["progress_since_previous"]
            if type(delta) is not dict or set(delta)!=set(AREAS):
                raise ProgressLedgerError("invalid_progress_area_delta")
            if not all(type(v) is int for v in delta.values()):
                raise ProgressLedgerError("invalid_progress_area_delta")
    if previous.get("last_sequence")!=snapshots[-1]["sequence"]:
        raise ProgressLedgerError("corrupted_progress_sequence")
    return snapshots


def append(audit,previous=None):
    current=observation(audit)
    old=_validate_existing(previous)
    if old and old[-1]["observation"]["audit_sha256"]==current["audit_sha256"]:
        return previous
    if old and current["market_asof"]<old[-1]["observation"]["market_asof"]:
        raise ProgressLedgerError("previous_source_date_ahead_of_current")
    last=old[-1]["observation"] if old else None
    same_date=last is not None and last["market_asof"]==current["market_asof"]
    delta={area:(current["provisional_issuers_by_area"][area]-
                 last["provisional_issuers_by_area"][area])
           for area in AREAS} if same_date else None
    sequence=old[-1]["sequence"]+1 if old else 1
    snaps=(old+[{
        "sequence":sequence,
        "observation":current,
        "progress_since_previous":delta,
        "same_market_asof_as_previous":bool(same_date),
    }])[-MAX_ENTRIES:]
    return {
        "schema":SCHEMA,
        "last_sequence":snaps[-1]["sequence"],
        "first_sequence":snaps[0]["sequence"],
        "snapshots":snaps,
        "historical_PIT_certified":False,
        "buy_signals_authorized":False,
        "caveat":"Source-observed provisional scores only; no 100-point or PIT claim",
    }


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit",default="docs/v3-real-source-six-area-coverage.json")
    ap.add_argument("--progress",default="docs/v3-real-source-six-area-progress.json")
    ap.add_argument("--mode",choices=("write","verify"),default="write")
    args=ap.parse_args(argv)
    audit=json.loads(Path(args.audit).read_text(encoding="utf8"))
    path=Path(args.progress)
    prior=json.loads(path.read_text(encoding="utf8")) if path.exists() else None
    current=observation(audit)
    if args.mode=="verify":
        snapshots=_validate_existing(prior)
        if not snapshots or snapshots[-1]["observation"]!=current:
            raise ProgressLedgerError("unpublished_or_stale_coverage_progress")
        print("verified_current_source_six_area_coverage_progress")
        return
    updated=append(audit,prior)
    write_json(path,updated)
    last=updated["snapshots"][-1]
    print(json.dumps({
        "market_asof":current["market_asof"],
        "source_snapshots_retained":len(updated["snapshots"]),
        "last_sequence":updated["last_sequence"],
        "six_area_numeric_coverage":current["provisional_issuers_by_area"],
        "changed_area_counts":last["progress_since_previous"],
        "full_investment_grade":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
