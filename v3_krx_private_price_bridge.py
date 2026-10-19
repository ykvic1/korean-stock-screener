"""Read-only bridge: PRIVATE observed KRX paired snapshots -> per-ticker research panel.

This is a reusable *in-process* bridge for up to 100 targets per scan, NOT an
investable price adapter. KRX ACC_TRDVOL is left explicitly AGGREGATE and no
'session' or 'volume' field is emitted, to prevent accidental promotion to
v3_price_feature_adapter's verified-regular-session input contract.

Raw bars and ticker identifiers never leave local process except when an
authorized local Python caller explicitly reads the return object's private
'panels'. CLI prints safe aggregate counts only and never calls networks.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re
import sqlite3

from official_probe.validation import _numeric
from official_probe.clients import ProbeError
from v3_krx_archive_reaudit import audit_private_archive, ArchiveReauditError
from v3_local_private_store import _connect, _validate_path, PrivateStoreError
from v3_krx_daily_anomaly_triage import PRICE_FIELDS, classify_row

MARKETS = ("KOSPI", "KOSDAQ")
CODE = re.compile(r"[A-Z0-9]{6}\Z")
MAX_TARGETS = 100
RAW_FIELDS = {
    "open": "TDD_OPNPRC", "high": "TDD_HGPRC",
    "low": "TDD_LWPRC", "close": "TDD_CLSPRC",
}
# IAS uses 250 sessions; the 120-week indicator additionally needs at
# least 128 actual observed weeks for its historical 8-week reclaim test.
REQUIRED_LOOKBACKS = (20, 60, 120, 250)


class PrivatePanelError(ValueError):
    """Only static reason codes, never private ticker/path/vendor source."""


def _targets(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_TARGETS:
        raise PrivatePanelError("one_to_100_explicit_targets_required")
    output = []
    seen = set()
    for target in values:
        if not isinstance(target, dict) or set(target) != {"market", "code"}:
            raise PrivatePanelError("invalid_private_panel_target")
        market, code = target["market"], target["code"]
        if market not in MARKETS or type(code) is not str or not CODE.fullmatch(code):
            raise PrivatePanelError("invalid_private_panel_identity")
        pair = (market, code)
        if pair in seen:
            raise PrivatePanelError("duplicate_private_panel_target")
        seen.add(pair)
        output.append(pair)
    return output


def _numeric_observation(row, day):
    """Only after independent two-market full-archive semantic audit."""
    try:
        prices = {name: _numeric(row[source], source)
                  for name, source in RAW_FIELDS.items()}
        volume = _numeric(row["ACC_TRDVOL"], "ACC_TRDVOL")
    except (ProbeError, ValueError, KeyError, TypeError):
        raise PrivatePanelError("post_audit_raw_numeric_disagreement") from None
    if any(v <= 0 for v in prices.values()) or volume < 0:
        raise PrivatePanelError("post_audit_raw_numeric_disagreement")
    return {
        "date": day,
        **prices,
        "aggregate_acc_trdvol_observed": volume,
        # Deliberately no 'volume' or 'session' keys.
        "source_scope": "KRX_EOD_AGGREGATE_UNVERIFIED",
        "corporate_action_adjustment_verified": False,
        "issuer_identity_verified": False,
        "regular_only_volume_verified": False,
    }


def build_private_price_panels(*, start, end, private_db, targets,
                               calendar_source="xkrx"):
    """Returns PRIVATE in-memory panels plus safe aggregate-only summary.

    Never persist or print the returned private panels in a public repository.
    Same six-character code is not assumed to be a stable historic issuer.
    """
    keys = _targets(targets)
    try:
        audit = audit_private_archive(
            start=start, end=end, private_db=private_db,
            calendar_source=calendar_source)
    except ArchiveReauditError:
        raise PrivatePanelError("private_archive_reaudit_required") from None

    panels = {(market, code): {
        "market": market, "code": code, "observed_bars": [],
        "quarantined_dates": [], "missing_in_archived_pairs": 0,
        "present_in_archived_pairs": 0,
    } for market, code in keys}
    lookup = {m: set(c for market, c in keys if market == m) for m in MARKETS}
    if audit["archive_paired_dates"]:
        path = Path(private_db).expanduser()
        connection = None
        try:
            _validate_path(path, create=False)
            connection = _connect(path, read_only=True)
            for day, kospi, kosdaq in connection.execute(
                "SELECT asof,kospi_raw,kosdaq_raw FROM source_pairs ORDER BY asof"):
                for market, blob in (("KOSPI", kospi), ("KOSDAQ", kosdaq)):
                    rows = json.loads(blob)["OutBlock_1"]
                    # Global re-audit already rejected duplicate ticker,
                    # wrong BAS_DD, out-of-market collision and raw mismatch.
                    found = set()
                    for row in rows:
                        code = row["ISU_CD"]
                        if code not in lookup[market]:
                            continue
                        key = (market, code)
                        found.add(code)
                        panel = panels[key]
                        panel["present_in_archived_pairs"] += 1
                        reason, _ = classify_row(row)
                        if reason == "valid_numeric_observation":
                            panel["observed_bars"].append(_numeric_observation(row, day))
                        else:
                            panel["quarantined_dates"].append(day)
                    for code in lookup[market] - found:
                        panels[(market, code)]["missing_in_archived_pairs"] += 1
        except (PrivateStoreError, sqlite3.Error, ValueError, KeyError, TypeError,
                json.JSONDecodeError):
            raise PrivatePanelError("private_panel_read_failed") from None
        finally:
            if connection is not None:
                connection.close()

    statuses = Counter()
    observations = 0
    missing = 0
    quarantined = 0
    total_complete_250 = 0
    observed_128_weeks = 0
    for panel in panels.values():
        seen = panel["observed_bars"]
        weeks = len({date.fromisoformat(x["date"]).isocalendar()[:2] for x in seen})
        panel["observed_distinct_weeks"] = weeks
        panel["sample_size"] = len(seen)
        panel["available_observation_lookbacks"] = [
            x for x in REQUIRED_LOOKBACKS if len(seen) >= x
        ]
        panel["complete_candidate_pair_coverage"] = (
            len(seen) == audit["candidate_date_count"]
            and audit["archive_paired_dates"] == audit["candidate_date_count"]
            and not panel["quarantined_dates"]
            and panel["missing_in_archived_pairs"] == 0
        )
        if panel["complete_candidate_pair_coverage"] and len(seen) >= 250:
            total_complete_250 += 1
        if weeks >= 128:
            observed_128_weeks += 1
        observations += len(seen)
        missing += panel["missing_in_archived_pairs"]
        quarantined += len(panel["quarantined_dates"])
        statuses["has_observed_bars" if seen else "no_observed_bars"] += 1

    summary = {
        "schema": "v3-private-krx-observed-panel-bridge-0.1",
        "mode": "offline_read_only",
        "status": "internal_research_observations_only",
        "actual_api_calls": 0, "storage_created_or_mutated": False,
        "requested_targets": len(keys),
        "archived_two_market_pairs": audit["archive_paired_dates"],
        "candidate_dates_total_unverified": audit["candidate_date_count"],
        "observed_valid_price_rows_for_targets": observations,
        "quarantined_target_rows": quarantined,
        "target_absent_in_archived_pair_rows": missing,
        "targets_with_any_valid_rows": statuses["has_observed_bars"],
        "targets_with_250_contiguous_candidate_rows": total_complete_250,
        "targets_with_128_observed_distinct_weeks": observed_128_weeks,
        "official_session_calendar_verified": False,
        "historical_issuer_identity_verified": False,
        "corporate_actions_adjusted_verified": False,
        "regular_only_volume_verified": False,
        "250_official_sessions_verified": False,
        "weekly_120_ma_source_verified": False,
        "independent_price_source_verified": False,
        "eligible_for_v3_price_feature_adapter": False,
        "eligible_for_investment_score": False,
        "v3_release_ready": False, "confirmed_buy": False,
    }
    # Returned 'panels' may contain confidential licensed data: no CLI export.
    return {"summary": summary, "panels": panels}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="No-network, counts-only audit of PRIVATE KRX price coverage")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--private-db", required=True)
    parser.add_argument("--calendar-source", choices=("xkrx", "weekdays"),
                        default="xkrx")
    parser.add_argument("--market", choices=MARKETS, required=True)
    parser.add_argument("--code", required=True,
                        help="One private target, never printed")
    args = parser.parse_args(argv)
    try:
        result = build_private_price_panels(
            start=args.start, end=args.end, private_db=args.private_db,
            targets=[{"market": args.market, "code": args.code}],
            calendar_source=args.calendar_source)
        print(json.dumps(result["summary"], sort_keys=True))
        return 0
    except PrivatePanelError as exc:
        print(json.dumps({
            "status": "blocked", "reason": str(exc), "actual_api_calls": 0,
            "v3_release_ready": False, "confirmed_buy": False
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
