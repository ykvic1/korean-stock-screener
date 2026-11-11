"""Block premature full-market publication and declare session-era targets.

This is a clock/target policy, NOT a certificate that a vendor's FChart daily
prices are KRX regular closes or its share volumes aggregate both exchanges.
NXT traded from 2025-03-04, KRX joined 16:00-20:00 after-market in 2026-09.
Before NXT, legacy KRX time-off-session trading also existed; a daily total
cannot be relabelled regular-only without source-level evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
NXT_START = date(2025, 3, 4)
KRX_AFTER_START = date(2026, 9, 14)
READY_AFTER = time(21, 0)  # one hour after KRX and NXT's 20:00 close


class AftermarketCutoffError(ValueError):
    """Bounded static reason codes; no stock or raw price data."""


def target_policy(asof: str, *, now: datetime | None = None) -> dict:
    try:
        day = date.fromisoformat(asof)
    except (TypeError, ValueError):
        raise AftermarketCutoffError("invalid_trade_date") from None
    if day.isoformat() != asof:
        raise AftermarketCutoffError("invalid_trade_date")
    now = datetime.now(KST) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise AftermarketCutoffError("timezone_aware_now_required")
    local = now.astimezone(KST)
    if day > local.date():
        raise AftermarketCutoffError("future_market_date")
    era = ("krx_nxt_and_krx_aftermarket" if day >= KRX_AFTER_START
           else "nxt_aftermarket_before_krx_aftermarket" if day >= NXT_START
           else "pre_nxt_krx_only")
    required_volume = ("KRX_NXT_ALL_SESSION_VOLUME_TO_2000"
                       if day >= NXT_START else "KRX_REGULAR_SESSION_VOLUME")
    # A historic backfill is final by time, but NOT automatically complete
    # or correctly scoped. The pre-NXT era had other KRX after-hours trades.
    earliest = datetime.combine(day, READY_AFTER if day >= NXT_START
                                else time(18, 30), tzinfo=KST)
    final_by_clock = local >= earliest
    return {
        "asof": asof,
        "era": era,
        "requested_price_scope": "KRX_REGULAR_CLOSE_1530",
        "requested_volume_scope": required_volume,
        "required_final_by_kst": earliest.strftime("%Y-%m-%d %H:%M KST"),
        "complete_session_by_clock": final_by_clock,
        "source_regular_close_verified": False,
        "source_required_volume_scope_verified": False,
        "day_total_is_not_proof_of_session_scope": True,
        "complete_price_volume_source_certified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Post-20:00 market batch publication cutoff")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if (type(manifest) is not dict or manifest.get("schema") !=
                "full-market-batched-100-v0.1"):
            raise AftermarketCutoffError("invalid_batch_manifest")
        policy = target_policy(manifest.get("asof"))
        if not policy["complete_session_by_clock"]:
            raise AftermarketCutoffError("aftermarket_session_not_yet_final")
        print(json.dumps({"status": "eligible_after_cutoff_only", **policy},
                         sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        reason = (str(exc) if isinstance(exc, AftermarketCutoffError)
                  else "unavailable_or_invalid_manifest")
        print(json.dumps({"status": "blocked", "reason": reason,
                          "source_price_volume_verified": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
