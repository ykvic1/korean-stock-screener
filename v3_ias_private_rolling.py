"""Combine *privately held* provider-observed signed investor flows across dates.

Never fetch remote APIs, publish rows into docs/, infer fund == pension,
certify exchange sessions, or calculate V3 IAS investment points. The existing
Kiwoom ka10059 collector only works from the operator's registered public IP.
This module prepares a reusable 20/60/120/250 observed-session candidate
ledger if such private source snapshots become available.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re

from v3_ias_flow_ledger import PERIODS, PERIOD_WEIGHTS

SCHEMA = "v3-private-ias-rolling-candidate-0.1"
SOURCE_SCHEMA = "private-kiwoom-investor-observations-v0"
SOURCE = "Kiwoom REST ka10059"
CODE = re.compile(r"[0-9]{6}\Z")
MAX_INPUTS = 400
MAX_STOCKS = 3500
MAX_ROWS = 2500
MAX_FILE_BYTES = 12_000_000
MAX_INT = 10**13
PRIVATE_ROOT = Path(__file__).resolve().parent


class PrivateRollingFlowError(ValueError):
    """Safe, fixed diagnostic categories: no credentials, source values or paths."""


def _day(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PrivateRollingFlowError("invalid_flow_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise PrivateRollingFlowError("invalid_flow_date") from None
    if parsed.isoformat() != value:
        raise PrivateRollingFlowError("invalid_flow_date")
    return parsed


def _signed(raw, *, nullable=False):
    if raw is None and nullable:
        return None
    if type(raw) is not int or abs(raw) > MAX_INT:
        raise PrivateRollingFlowError("invalid_signed_share_quantity")
    return raw


def _private_path(raw, *, is_directory=False, must_exist=True):
    path = Path(raw).expanduser()
    # Disallow directory traversals and symlinks at every existing path segment.
    for part in (path, *path.parents):
        if part.exists() and part.is_symlink():
            raise PrivateRollingFlowError("private_path_symlink_not_allowed")
    resolved = path.resolve()
    if resolved == PRIVATE_ROOT or PRIVATE_ROOT in resolved.parents:
        raise PrivateRollingFlowError("raw_flow_repository_path_forbidden")
    if must_exist:
        if is_directory and not resolved.is_dir():
            raise PrivateRollingFlowError("private_input_folder_unavailable")
        if not is_directory and not resolved.is_file():
            raise PrivateRollingFlowError("private_input_file_unavailable")
    return resolved


def _validate_dataset(code, dataset, asof):
    if type(code) is not str or not CODE.fullmatch(code):
        raise PrivateRollingFlowError("invalid_flow_stock_code")
    if (type(dataset) is not dict or dataset.get("code") != code or
        dataset.get("source") != "Kiwoom REST ka10059 KRX-code request" or
        dataset.get("asof_requested") != asof or
        dataset.get("confirmed_scoring_allowed") is not False or
        dataset.get("v3_release_ready") is not False):
        raise PrivateRollingFlowError("untrusted_private_flow_dataset")
    rows = dataset.get("rows")
    if type(rows) is not list or not 1 <= len(rows) <= 300 or dataset.get("row_count") != len(rows):
        raise PrivateRollingFlowError("invalid_private_flow_history_count")
    earliest, latest = None, None
    canonical = []
    for row in rows:
        if type(row) is not dict:
            raise PrivateRollingFlowError("invalid_private_flow_record")
        observed = _day(row.get("trade_date"))
        if observed > _day(asof) or (earliest is not None and observed >= earliest):
            raise PrivateRollingFlowError("future_duplicate_or_unsorted_trade_dates")
        earliest = observed
        if latest is None:
            latest = observed
        canonical.append({
            "date": observed.isoformat(),
            "foreign_net_shares": _signed(row.get("foreign_net_shares")),
            "institution_total_net_shares": _signed(row.get("inst_net_shares")),
            "pension_etc_net_shares": _signed(row.get("pension_etc_net_shares"), nullable=True),
        })
    if (dataset.get("first_day") != latest.isoformat()
        or dataset.get("last_day") != earliest.isoformat()):
        raise PrivateRollingFlowError("provider_snapshot_day_bounds_mismatch")
    if dataset.get("independent_exchange_session_verified") is not False:
        raise PrivateRollingFlowError("unearned_exchange_session_certification")
    return canonical


def combine_snapshots(snapshots, *, asof, max_age_days=7):
    """Validated replays: all overlapping signed quantities must agree.

    Each stock keeps independently observed *descending* dates. No synthetic
    missing weekdays/holidays, no zero imputation or institutional double count.
    """
    cutoff = _day(asof)
    if (type(snapshots) is not list or not 1 <= len(snapshots) <= MAX_INPUTS
        or type(max_age_days) is not int or not 0 <= max_age_days <= 30):
        raise PrivateRollingFlowError("invalid_private_snapshot_batch")
    observations = {}
    attempts = set()
    for sample in snapshots:
        if (type(sample) is not dict or sample.get("schema") != SOURCE_SCHEMA
            or sample.get("provider") != SOURCE
            or sample.get("published") is not False
            or sample.get("confirmed_scoring_allowed") is not False
            or sample.get("v3_release_ready") is not False
            or sample.get("status") not in ("complete", "partial_failure")
            or type(sample.get("datasets")) is not dict):
            raise PrivateRollingFlowError("untrusted_private_snapshot_header")
        source_day = _day(sample.get("asof"))
        if source_day > cutoff or source_day.isoformat() in attempts:
            raise PrivateRollingFlowError("future_or_duplicate_snapshot_day")
        attempts.add(source_day.isoformat())
        for code, dataset in sample["datasets"].items():
            if code not in observations and len(observations) >= MAX_STOCKS:
                raise PrivateRollingFlowError("oversized_private_stock_census")
            series = _validate_dataset(code, dataset, sample["asof"])
            merged = observations.setdefault(code, {})
            for row in series:
                d = row["date"]
                if d in merged and merged[d] != row:
                    raise PrivateRollingFlowError("overlapping_provider_flow_revision_conflict")
                merged[d] = row
                if len(merged) > MAX_ROWS:
                    raise PrivateRollingFlowError("unbounded_flow_history")
    results = {}
    for code, seen in sorted(observations.items()):
        sorted_days = sorted(seen, reverse=True)
        latest = _day(sorted_days[0])
        age = (cutoff-latest).days
        fresh = age <= max_age_days
        windows = {}
        for length in PERIODS:
            tail = sorted_days[:length] if len(sorted_days) >= length and fresh else []
            view = {}
            for name in ("foreign_net_shares", "institution_total_net_shares",
                         "pension_etc_net_shares"):
                values = [seen[d][name] for d in tail]
                view[name] = sum(values) if (len(values) == length and
                                            all(v is not None for v in values)) else None
            windows[str(length)] = {
                "observed_sessions_required": length,
                "observed_sessions_available": min(len(seen), length),
                "foreign_net_shares": view["foreign_net_shares"],
                "institution_total_net_shares": view["institution_total_net_shares"],
                "pension_etc_net_shares": view["pension_etc_net_shares"],
                "period_weight_within_ias_accumulation_pct": PERIOD_WEIGHTS[length],
                "institution_includes_pension_category": True,
                "pension_must_not_be_added_to_institution": True,
                "calendar_session_independently_verified": False,
                "point_score": None,
            }
        results[code] = {
            "code": code, "latest_observed_day": latest.isoformat(),
            "oldest_observed_day": sorted_days[-1],
            "distinct_observed_dates": len(sorted_days),
            "snapshot_age_days": age,
            "freshness_candidate": fresh,
            "periods": windows,
            "trading_calendar_verified": False,
            "provider_category_independently_verified": False,
            "fund_general_not_treated_as_pension": True,
            "issuer_identity_verified": False,
            "corporate_actions_adjusted": False,
            "ias_20_pillar_points": None,
            "buy_certified": False,
        }
    return {
        "schema": SCHEMA, "asof": cutoff.isoformat(),
        "provider": "Kiwoom REST ka10059 (PRIVATE, provenance unverified)",
        "distinct_snapshot_days": len(attempts),
        "distinct_stocks": len(results),
        "full_windows": {
            str(n): {
                name: sum(1 for r in results.values()
                          if r["periods"][str(n)][name] is not None)
                for name in ("foreign_net_shares","institution_total_net_shares",
                             "pension_etc_net_shares")
            }
            for n in PERIODS
        },
        "numeric_missing_values_filled_with_zero": False,
        "pension_double_counted_into_institution": False,
        "raw_source_values_publicly_published": False,
        "approved_for_v3_score": False,
        "ias_20_scored_count": 0,
        "records": results,
    }


def count_only_report(combined):
    """Only coverage totals for stdout; never signed quantities or stock IDs."""
    return {
        "schema": "v3-private-ias-rolling-coverage-0.1",
        "asof": combined["asof"], "source_snapshot_days": combined["distinct_snapshot_days"],
        "stocks_with_numeric_history": combined["distinct_stocks"],
        "periods": combined["full_windows"],
        "ias_20_scored_count": 0, "raw_shares_emitted": False,
        "session_category_and_reuse_rights_still_unverified": True,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description="Private flow history join, no network or public publication")
    parser.add_argument("--input-dir",required=True,help="Existing PRIVATE Kiwoom snapshot folder, outside Git")
    parser.add_argument("--asof",required=True,help="YYYY-MM-DD cutoff (not a provider call)")
    parser.add_argument("--output-private",required=True,help="Private JSON destination outside Git")
    args=parser.parse_args(argv)
    try:
        root=_private_path(args.input_dir,is_directory=True)
        target=_private_path(args.output_private,must_exist=False)
        if root == target or root in target.parents and target.suffix != ".json":
            raise PrivateRollingFlowError("invalid_private_output")
        inputs=sorted(root.glob("investor_????-??-??.json"))
        if not 1 <= len(inputs) <= MAX_INPUTS:
            raise PrivateRollingFlowError("no_private_source_snapshots")
        snapshots=[]
        for path in inputs:
            validated=_private_path(path)
            if validated.stat().st_size > MAX_FILE_BYTES:
                raise PrivateRollingFlowError("oversized_private_flow_snapshot")
            snapshots.append(json.loads(validated.read_text(encoding="utf8")))
        merged=combine_snapshots(snapshots,asof=args.asof)
        # Atomic write only AFTER every input and all overlaps have validated.
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.name=="posix":
            os.chmod(target.parent,0o700)
        if target.exists():
            raise PrivateRollingFlowError("existing_private_research_output_protected")
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",
                                         dir=target.parent,prefix=".ias-",delete=False) as out:
            tmp=Path(out.name)
            try:
                if os.name=="posix":
                    os.chmod(tmp,0o600)
                json.dump(merged,out,ensure_ascii=False,allow_nan=False,separators=(",",":"))
                out.write("\n")
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        os.replace(tmp,target)
        print(json.dumps(count_only_report(merged),sort_keys=True))
        return 0
    except (OSError,UnicodeError,ValueError,PrivateRollingFlowError):
        print('{"status":"blocked","reason":"private_ias_source_validation_or_access_failure"}')
        return 1


if __name__=="__main__":
    raise SystemExit(main())
