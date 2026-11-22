"""Compare pinned historical closes to saved official KRX close observations."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path

from v3_forward_share_actions import normalized_name
from v3_historical_market_coverage import (
    END, ENTRY, ROOT, SIGNAL, day, ledger_rows, read_panels, require, traded,
)
from v3_historical_performance_pilot_audit import PilotAuditError
from v3_historical_pilot_daily_replay import CALENDAR_CODES, SOURCE_DIGESTS
from v3_krx_private_github_backfill import PrivateGitStorage, PrivateHistoryError, storage_diagnostic
from v3_regular_close_indicators import CloseHistoryError, PRIVATE_REPO, archive_path, read_day

SCHEMA = "v3-historical-close-archive-audit-v1"
ARCHIVE_READERS = 5
COMPARISON_STATES = (
    "public_row_missing", "official_row_missing", "both_rows_missing",
    "identity_mismatch", "public_close_unavailable", "close_mismatch", "exact_close",
)
OHLC_BOUNDS = ("traded_public_bars", "official_close_inside_public_ohlc", "invalid_public_ohlc")


def pilot_windows(root):
    references = ledger_rows(root, "downside-reference")
    codes = {row["code"] for row in references}
    require(len(references) == len(codes) > 0, "invalid_pilot_close_reference_roster")
    trades = {row["code"]: row for row in ledger_rows(root, "delisting-trade-observations")
              if row["code"] in codes}
    settlements = {row["code"]: row for row in ledger_rows(root, "delisting-settlement")
                   if row["code"] in codes}
    require(codes == trades.keys() == settlements.keys(), "incomplete_pilot_close_roster")
    windows = []
    for code in sorted(codes):
        last = day(trades[code]["last_positive_volume_candidate"])
        require(trades[code]["next_session_status"] == "traded" and ENTRY <= last <= END,
                "invalid_pilot_close_trade_window")
        windows.append((code, SIGNAL, last))
        successor = settlements[code]["successor_code"]
        if successor:
            start = day(settlements[code]["settlement_or_share_availability_on"])
            require(last < start <= END, "invalid_pilot_close_successor_window")
            windows.append((successor, start, END))
    return len(codes), windows


def compare(public, official, codes):
    states = Counter(dict.fromkeys(COMPARISON_STATES, 0))
    equity = Counter(exact_listed_shares=0, mismatch_listed_shares=0, public_shares_unavailable=0)
    status = Counter(same_trade_status=0, trade_status_mismatch=0, trade_status_unavailable=0)
    bounds = Counter(dict.fromkeys(OHLC_BOUNDS, 0))
    for code in codes:
        source = public.get(code)
        saved = official.get(code)
        if source is None:
            states["public_row_missing" if saved is not None else "both_rows_missing"] += 1
        elif saved is None:
            states["official_row_missing"] += 1
        elif (source[0] != saved[0] or not normalized_name(source[1])
              or normalized_name(source[1]) != normalized_name(saved[1])):
            states["identity_mismatch"] += 1
        else:
            # Validate only actually traded public bars, never synthesize a
            # candle for a suspended/unpriced security. Official CLOSE within
            # public candle is a consistency check, not official LOW proof.
            if saved[5] is True and type(source[7]) in (int, float) and source[7] > 0:
                bounds["traded_public_bars"] += 1
                o, h, l, c = source[3:7]
                valid = (all(type(x) in (int, float) and math.isfinite(x)
                             for x in (o, h, l, c))
                         and 0 < l <= min(o, c, saved[2])
                         <= max(o, c, saved[2]) <= h)
                bounds["official_close_inside_public_ohlc" if valid
                       else "invalid_public_ohlc"] += 1
            close = source[6]
            if close is None or close <= 0:
                states["public_close_unavailable"] += 1
            else:
                states["exact_close" if close == saved[2] else "close_mismatch"] += 1
            shares = source[2]
            if shares is None or shares <= 0:
                equity["public_shares_unavailable"] += 1
            else:
                equity["exact_listed_shares" if shares == saved[3] else "mismatch_listed_shares"] += 1
            volume = source[7]
            if volume is None or volume < 0 or saved[5] is None:
                status["trade_status_unavailable"] += 1
            else:
                status["same_trade_status" if (volume > 0) == saved[5]
                       else "trade_status_mismatch"] += 1
    return {"rows": sum(states.values()), **states, **equity, **status, **bounds}


def build(panels, storage, root=ROOT, *, extreme_rows=None):
    sessions = sorted(session for session in panels if SIGNAL <= session <= END)
    require(sessions and sessions[0] == SIGNAL and sessions[-1] == END
            and len(sessions) > 1 and sessions[1] == ENTRY
            and all(traded(panels[session].get(code))
                    for session in sessions for code in CALENDAR_CODES),
            "invalid_historical_close_source_sessions")
    source_count, windows = pilot_windows(root)
    extreme_rows = (ledger_rows(root, "downside-horizons") if extreme_rows is None
                    else extreme_rows)
    extreme_keys = {(row["code"], row["horizon_months"]) for row in extreme_rows}
    extreme_days = {day(row["adverse_extreme_on"]) for row in extreme_rows}
    require(len(extreme_rows) == len(extreme_keys) == source_count * 5
            and len({code for code, _ in extreme_keys}) == source_count
            and {h for _, h in extreme_keys} == {"1", "2", "3", "6", "12"}
            and extreme_days <= set(sessions),
            "invalid_historical_adverse_extreme_ledger")

    def check(session):
        date_key = session.isoformat()
        blob, _ = storage.get_content(archive_path(date_key))
        archived = read_day(blob, date_key)
        if archived is None:
            return date_key, None
        public, official = panels[session], archived["rows"]
        pilot_codes = {code for code, start, end in windows if start <= session <= end}
        return date_key, {
            "official_source_sha256": archived["source_sha256"],
            "market": compare(public, official, public.keys() | official.keys()),
            "pilot": compare(public, official, pilot_codes),
        }

    results = {}
    with ThreadPoolExecutor(max_workers=ARCHIVE_READERS) as readers:
        # A transport/authentication fault stops the remaining window.
        for offset in range(0, len(sessions), ARCHIVE_READERS):
            results.update(readers.map(check, sessions[offset:offset + ARCHIVE_READERS]))
    missing = [key for key, result in results.items() if result is None]
    by_session = {key: result for key, result in results.items() if result is not None}
    totals = {key: Counter(compare({}, {}, ())) for key in ("market", "pilot")}
    for result in by_session.values():
        for key, total in totals.items():
            total.update(result[key])
    corroborated = sum(
        session.isoformat() in by_session
        and by_session[session.isoformat()]["market"]["exact_close"] > 0
        and by_session[session.isoformat()]["pilot"]["exact_close"] > 0
        and by_session[session.isoformat()]["market"]["close_mismatch"] == 0
        and by_session[session.isoformat()]["pilot"]["close_mismatch"] == 0
        for session in extreme_days)
    return {
        "extreme_horizon_rows": len(extreme_rows),
        "distinct_adverse_extreme_dates": len(extreme_days),
        "official_close_corroborated_extreme_dates": corroborated,
        "official_intraday_low_extremes_verified": False,
        "public_ohlc_bounds_are_not_independent_low_price_proof": True,
        "schema": SCHEMA,
        "from": SIGNAL.isoformat(),
        "through": END.isoformat(),
        "market_sessions_expected": len(sessions),
        "official_sessions_compared": len(by_session),
        "official_sessions_missing": missing,
        "pilot_originating_issues": source_count,
        "public_inputs_sha256": list(SOURCE_DIGESTS),
        "totals": {key: dict(total) for key, total in totals.items()},
        "by_session": by_session,
        "new_market_api_calls": 0,
        "adjusted_price_history_verified": False,
        "intraday_ohlc_path_verified": False,
        "point_in_time_scores_verified": False,
        "performance_validated": False,
    }



# Full matching is a publication precondition, not adjusted-price certification.
# A completed archive read must not be mistaken for a successful audit when
# even one date/issuer has an inconsistent close, identity, share count or
# trading status.
def close_reconciliation_complete(report):
    if type(report) is not dict:
        return False
    expected = report.get("market_sessions_expected")
    sessions = report.get("by_session")
    totals = report.get("totals")
    if (type(expected) is not int or expected < 1
            or report.get("official_sessions_compared") != expected
            or report.get("official_sessions_missing") != []
            or type(sessions) is not dict or len(sessions) != expected
            or type(totals) is not dict
            or report.get("new_market_api_calls") != 0):
        return False

    fieldnames = (("rows",) + COMPARISON_STATES
                  + ("exact_listed_shares", "mismatch_listed_shares",
                     "public_shares_unavailable", "same_trade_status",
                     "trade_status_mismatch", "trade_status_unavailable")
                  + OHLC_BOUNDS)
    for scope in ("market", "pilot"):
        aggregate = totals.get(scope)
        entries = [aggregate]
        for session in sessions.values():
            if type(session) is not dict:
                return False
            entries.append(session.get(scope))
        for counts in entries:
            if (type(counts) is not dict
                    or any(type(counts.get(key)) is not int or counts[key] < 0
                           for key in fieldnames)):
                return False
            rows = counts["rows"]
            if (counts["official_close_inside_public_ohlc"] +
                    counts["invalid_public_ohlc"] != counts["traded_public_bars"]
                    or counts["invalid_public_ohlc"] != 0
                    or counts["traded_public_bars"] > rows):
                return False
            if (sum(counts[key] for key in COMPARISON_STATES) != rows
                    or counts["exact_close"] != rows
                    or counts["exact_listed_shares"] != rows
                    or counts["same_trade_status"] != rows
                    or counts["exact_listed_shares"] + counts["mismatch_listed_shares"]
                       + counts["public_shares_unavailable"] != rows
                    or counts["same_trade_status"] + counts["trade_status_mismatch"]
                       + counts["trade_status_unavailable"] != rows):
                return False
        if (scope == "market" and aggregate["rows"] <= 0
                or scope == "pilot" and aggregate["rows"] <= 0
                or any(sum(part[key] for part in entries[1:]) != aggregate[key]
                       for key in fieldnames)):
            return False
    if (type(report.get("extreme_horizon_rows")) is not int
            or report["extreme_horizon_rows"] < 1
            or type(report.get("distinct_adverse_extreme_dates")) is not int
            or not 1 <= report["distinct_adverse_extreme_dates"] <= expected
            or report.get("official_close_corroborated_extreme_dates")
               != report["distinct_adverse_extreme_dates"]
            or report.get("official_intraday_low_extremes_verified") is not False
            or report.get("public_ohlc_bounds_are_not_independent_low_price_proof") is not True):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    args = parser.parse_args()
    if os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO:
        raise SystemExit("private_regular_close_destination_unavailable")
    try:
        panels = read_panels(args.sources)
        storage = PrivateGitStorage(PRIVATE_REPO, os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", ""))
        storage.verify_private()
        result = build(panels, storage)
    except PrivateHistoryError as error:
        raise SystemExit(json.dumps(storage_diagnostic(error) or
                                    {"storage_reason": "private_archive_unavailable"})) from None
    except (CloseHistoryError, PilotAuditError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, sort_keys=True))
    if result["official_sessions_missing"]:
        raise SystemExit("incomplete_private_regular_close_window")
    if not close_reconciliation_complete(result):
        raise SystemExit("historical_private_archive_reconciliation_mismatch")


if __name__ == "__main__":
    main()
