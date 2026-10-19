"""Step 12: bounded LIVE KRX-to-v3 first-pipeline evidence, entirely transient.

Five *fixed* completed weekday candidates, KOSPI + KOSDAQ = <=10 HTTP
attempts. Observed raw vendor bytes are stored ONLY inside caller-provided
ephemeral private directory for in-process semantic audit, then deleted.
Never publish price rows, stock identifiers, provider payloads, tokens, raw
hashes, source files or reports. All printed output is aggregate count/flags.

This does not authenticate historical PIT, corporate actions, investor flow,
regular-only volume, permanent licensed storage, returns or buy recommendations.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import tempfile

from official_probe.clients import KRXClient
from v3_krx_history_collection import collect_krx_history, HistoryCollectionError
from v3_krx_archive_reaudit import audit_private_archive, ArchiveReauditError
from v3_optional_technical_research import (
    run_optional_technical_research, PartialTechnicalError,
)

FIRST_DAY = "2026-10-12"
LAST_DAY = "2026-10-16"
FIVE_SESSIONS = [
    "2026-10-12", "2026-10-13", "2026-10-14",
    "2026-10-15", "2026-10-16",
]
# These identities are accessed in the private local process only.
PRIVATE_TARGETS = [
    {"market": "KOSPI", "code": "005930"},
    {"market": "KOSDAQ", "code": "247540"},
]


class LiveEvidenceError(ValueError):
    """Fixed safe code only, no stock or credential disclosure."""


def run_five_day_evidence(*, live=False, key=None, client=None):
    """Actual permitted provider only with explicit live opt-in.

    Work is intentionally NOT persistent because the connected GitHub repo
    and Actions artifacts are public; runner.temp is automatically discarded.
    """
    if live is not True:
        return {
            "schema": "v3-official-krx-five-day-transient-evidence-0.1",
            "mode": "dry_run",
            "status": "real_api_not_requested",
            "planned_paired_days": len(FIVE_SESSIONS),
            "planned_market_calls_maximum": 10,
            "observed_paired_days": 0, "actual_api_calls": 0,
            "private_source_retained": False, "vendor_rows_published": False,
            "real_data_reached_v3_analysis": False, "v3_release_ready": False,
            "confirmed_buy": False,
        }
    if type(key) is not str or not key.strip():
        raise LiveEvidenceError("krx_secret_not_available")
    if date.today().isoformat() < LAST_DAY:
        raise LiveEvidenceError("requested_history_not_completed")
    try:
        with tempfile.TemporaryDirectory(prefix="v3-krx-real-evidence-") as tmp:
            private_db = Path(tmp) / "transient.sqlite3"
            history = collect_krx_history(
                FIVE_SESSIONS, live=True, key=key, client=client,
                archive_path=private_db, consent_to_private_retention=True)
            verified = audit_private_archive(
                start=FIRST_DAY, end=LAST_DAY, private_db=private_db,
                calendar_source="weekdays")
            researched = run_optional_technical_research(
                start=FIRST_DAY, end=LAST_DAY,
                private_db=private_db, targets=PRIVATE_TARGETS,
                calendar_source="weekdays")
            summary = researched["summary"]
            if (history["observed_days"] != 5
                    or verified["archive_paired_dates"] != 5
                    or not verified["internal_chain_and_source_shape_consistent"]
                    or summary["archived_paired_market_days"] != 5):
                raise LiveEvidenceError("cross_stage_real_pair_count_mismatch")
            market_counts = {
                m: verified["by_market"][m] for m in ("KOSPI", "KOSDAQ")
            }
            if any(count["source_rows"] == 0 for count in market_counts.values()):
                raise LiveEvidenceError("real_provider_market_rows_empty")
            # Never export or persist 'researched["private_results"]'.
            return {
                "schema": "v3-official-krx-five-day-transient-evidence-0.1",
                "mode": "live",
                "status": "real_api_to_v3_partial_technical_pipeline_observed",
                "source": "KRX OpenAPI KOSPI and KOSDAQ daily market",
                "first_observed_candidate_day": FIRST_DAY,
                "last_observed_candidate_day": LAST_DAY,
                "planned_paired_days": 5,
                "planned_market_calls_maximum": 10,
                "observed_paired_days": 5,
                # Returned ONLY on success where 2 markets x 5 days completed.
                "actual_api_calls": 10,
                "real_market_source_row_counts": {
                    m: market_counts[m]["source_rows"] for m in market_counts
                },
                "real_market_valid_numeric_counts": {
                    m: market_counts[m]["valid_numeric_rows"] for m in market_counts
                },
                "real_market_quarantined_row_counts": {
                    m: market_counts[m]["quarantined_rows"] for m in market_counts
                },
                "observed_valid_target_price_rows":
                    researched["summary"]["target_count"] * 0
                    + sum(x["contiguous_ending_bars"] for x in
                          researched["private_results"].values()),
                "real_target_daily_ma5_available": sum(
                    "daily_ma5" in x["available"]
                    for x in researched["private_results"].values()
                ),
                "real_target_daily_ma20_available":
                    researched["summary"]["targets_with_daily_ma20"],
                "real_target_daily_ma120_available":
                    researched["summary"]["targets_with_daily_ma120"],
                "real_data_reached_v3_analysis": True,
                "source_json_and_hash_chain_internal_consistent": True,
                "private_source_retained": False,
                "vendor_rows_published": False,
                "price_values_published": False,
                "historical_universe_verified": False,
                "issuer_pit_verified": False,
                "corporate_actions_verified": False,
                "official_regular_session_volume_verified": False,
                "independent_cross_source_verified": False,
                "v3_release_ready": False,
                "confirmed_buy": False,
            }
    except LiveEvidenceError:
        raise
    except (HistoryCollectionError, ArchiveReauditError, PartialTechnicalError):
        raise LiveEvidenceError("bounded_real_provider_or_semantic_pipeline_failed") from None
    except Exception:
        raise LiveEvidenceError("unclassified_real_pipeline_failure") from None


def main(argv=None):
    cli = argparse.ArgumentParser(
        description="Bounded 5-day real KRX-to-v3 evidence, no raw public data")
    cli.add_argument("--live", action="store_true", help="Opt-in <=10 KRX requests")
    args = cli.parse_args(argv)
    try:
        outcome = run_five_day_evidence(
            live=args.live, key=os.environ.get("KRX_API_KEY"))
        print(json.dumps(outcome, sort_keys=True))
        return 0
    except LiveEvidenceError as exc:
        print(json.dumps({
            "schema": "v3-official-krx-five-day-transient-evidence-0.1",
            "status": "blocked", "reason": str(exc),
            "real_data_reached_v3_analysis": False,
            "private_source_retained": False, "vendor_rows_published": False,
            "v3_release_ready": False, "confirmed_buy": False,
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
