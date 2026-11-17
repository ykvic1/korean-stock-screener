"""Reproduce historical market coverage from the same pinned data as the delisting pilot."""

import argparse
from collections import Counter
import csv
from datetime import datetime, time
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from v3_forward_horizon_readiness import HORIZONS, _anniversary
from v3_historical_performance_pilot_audit import ENTRY, ROOT, SIGNAL, TAG, day, require
from v3_historical_pilot_daily_replay import CALENDAR_CODES, SOURCE_DIGESTS

END = day("2026-11-13")
MARKETS = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KOSDAQ GLOBAL": "KOSDAQ"}


def read_panels(paths):
    import pyarrow.parquet as pq

    columns = ["Date", "Code", "Market", "Name", "Stocks", "Open", "High", "Low", "Close", "Volume"]
    panels = {}
    for path, digest in zip(paths, SOURCE_DIGESTS, strict=True):
        require(sha256(path.read_bytes()).hexdigest() == digest, "historical_public_source_digest_mismatch")
    for path in paths[:2]:
        table = pq.read_table(path, columns=columns, filters=[
            ("Date", ">=", datetime.combine(SIGNAL, time.min)),
            ("Market", "in", list(MARKETS)),
        ])
        for batch in table.to_batches():
            for session, code, market, name, shares, o, h, l, c, v in zip(*(batch.column(key).to_pylist() for key in columns), strict=True):
                add_row(panels.setdefault(session.date(), {}), code, market, name, (shares, o, h, l, c, v))
    require(END not in panels, "duplicate_market_endpoint")
    panels[END] = {}
    with paths[2].open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["Market"] in MARKETS:
                values = tuple(int(row[key]) if row[key] else None for key in columns[4:])
                add_row(panels[END], row["Code"], row["Market"], row["Name"], values)
    return panels


def add_row(panel, code, market, name, values):
    require(code not in panel and code and name, "duplicate_or_invalid_market_security")
    panel[code] = (MARKETS[market], name, *values)


def traded(row):
    if row is None:
        return False
    o, h, l, c, v = row[3:]
    return all(x is not None for x in (o, h, l, c, v)) and 0 < l <= min(o, c) <= max(o, c) <= h and v > 0


def ledger_rows(root, name):
    with (root / "ops" / f"v3-performance-{name}-{TAG}.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def summarize(panels, roster, trade_rows, delisting_prices, *, stable_codes=None, stable_outcomes=None):
    signal = panels[SIGNAL]
    sessions = sorted(session for session in panels if ENTRY <= session <= END)
    require(sessions and sessions[0] == ENTRY and sessions[-1] == END, "historical_market_session_range")
    require(all(traded(panels[session].get(code)) for session in sessions for code in CALENDAR_CODES),
            "market_calendar_reference_without_trade")
    valuations = {next(s for s in sessions if s >= _anniversary(SIGNAL, h)): h for h in HORIZONS}
    delisted = {row["code"] for row in roster if row["anchor_membership"] == "present"}
    anchor_status = {row["code"]: row for row in roster}
    trades = {row["code"]: row for row in trade_rows}
    require(len(anchor_status) == len(roster) and len(trades) == len(trade_rows)
            and delisted == (signal.keys() & anchor_status.keys()) == trades.keys(),
            "market_delisting_membership_mismatch")
    for code in delisted:
        require(signal[code][:2] == (anchor_status[code]["market"], anchor_status[code]["anchor_name"])
                and traded(panels[ENTRY].get(code)) == (trades[code]["next_session_status"] == "traded"),
                "market_delisting_identity_or_entry_mismatch")
    require(len({(r["code"], r["horizon_months"]) for r in delisting_prices}) == len(delisting_prices)
            and all(int(r["horizon_months"]) in HORIZONS for r in delisting_prices),
            "invalid_market_delisting_horizons")
    priced_delisted = Counter(int(row["horizon_months"]) for row in delisting_prices
                              if row["value_per_entry_share_krw"])
    require(all(row["code"] in delisted and trades[row["code"]]["next_session_status"] == "traded"
                for row in delisting_prices), "market_delisting_price_outside_roster")

    require(len(valuations) == len(HORIZONS), "overlapping_market_valuation_sessions")
    non_delisted = signal.keys() - delisted
    entry_prices = {code: panels[ENTRY][code][3] for code in non_delisted if traded(panels[ENTRY].get(code))}
    states = {}
    lows = dict(entry_prices)
    gaps = Counter()
    returns_by_horizon = {}
    for session in sessions:
        panel = panels[session]
        counts = Counter()
        returns, adverse = [], []
        for code, entry in entry_prices.items():
            anchor = signal[code]
            row = panel.get(code)
            if code not in states:
                if row is None:
                    states[code] = "missing_security_session"
                elif row[:2] != anchor[:2]:
                    states[code] = "market_or_name_changed"
                elif not anchor[2] or row[2] != anchor[2]:
                    states[code] = "listed_shares_changed_or_missing"
                elif traded(row):
                    lows[code] = min(lows[code], row[5])
                else:
                    gaps[code] += 1
            if session in valuations:
                if code in states:
                    counts[states[code]] += 1
                elif not traded(row):
                    counts["valuation_unpriced"] += 1
                else:
                    if stable_codes is not None:
                        stable_codes.setdefault(str(valuations[session]), set()).add(code)
                    counts["stable_quote_valuations"] += 1
                    counts["stable_quote_paths_with_gaps"] += code in gaps
                    change = (row[6] / entry - 1) * 100
                    drawdown = (1 - lows[code] / entry) * 100
                    returns.append(change)
                    adverse.append(drawdown)
                    if stable_outcomes is not None:
                        stable_outcomes.setdefault(str(valuations[session]), {})[code] = {
                            "valuation_session": session.isoformat(),
                            "price_change_pct": round(change, 4),
                            "observed_adverse_excursion_pct": round(drawdown, 4),
                            "unpriced_sessions": gaps[code],
                        }
        if session in valuations:
            horizon = valuations[session]
            returns_by_horizon[str(horizon)] = {
                "valuation_session": session.isoformat(),
                "non_delisted_entry_unpriced": len(non_delisted) - len(entry_prices),
                **{key: counts[key] for key in (
                    "missing_security_session", "market_or_name_changed", "listed_shares_changed_or_missing",
                    "valuation_unpriced", "stable_quote_valuations", "stable_quote_paths_with_gaps",
                )},
                "stable_quote_median_price_change_pct": round(median(returns), 2) if returns else None,
                "stable_quote_median_observed_adverse_excursion_pct": round(median(adverse), 2) if adverse else None,
                "delisted_pilot_price_valuations": priced_delisted[horizon],
                "delisted_unpriced_or_outside_pilot": len(delisted) - priced_delisted[horizon],
            }
    return {
        "signal_session": SIGNAL.isoformat(), "entry_session": ENTRY.isoformat(),
        "source_sha256": dict(zip(("2025_parquet", "2026_parquet", "2026-11-13_csv"), SOURCE_DIGESTS, strict=True)),
        "market_sessions": len(sessions), "listed_by_market": dict(Counter(row[0] for row in signal.values())),
        "listed_total": len(signal), "delisted_issuers": len(delisted),
        "delisted_with_entry_trade": sum(traded(panels[ENTRY].get(code)) for code in delisted),
        "non_delisted_with_entry_trade": len(entry_prices), "horizons": returns_by_horizon,
        "historical_security_type_verified": False, "historical_adjusted_prices_verified": False,
        "point_in_time_scores_verified": False, "performance_validated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path, metavar="SOURCE")
    parser.add_argument("--verify", type=Path, help="Compare the recomputed report with a saved JSON file.")
    args = parser.parse_args()
    report = summarize(read_panels(args.sources), ledger_rows(ROOT, "delistings"),
                       ledger_rows(ROOT, "delisting-trade-observations"), ledger_rows(ROOT, "price-replay"))
    if args.verify is not None:
        require(report == json.loads(args.verify.read_text(encoding="utf-8")), "market_coverage_report_mismatch")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
