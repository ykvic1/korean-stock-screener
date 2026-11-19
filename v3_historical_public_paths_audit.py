"""Reconcile the published historical price-path feed with saved research ledgers."""
from collections import Counter
from hashlib import sha256
from io import StringIO
import csv
import json
import math
from pathlib import Path
import re

from v3_historical_performance_pilot_audit import ENTRY, HORIZONS, ROOT, SIGNAL, TAG

PERIODS = tuple(map(str, HORIZONS))
COLUMNS = ["price_pct", "downside_pct", "unpriced_sessions", "basis", "unlisted_entitlement"]
BASES = (None, "reviewed_share_action", "delisting_case_replay")
SCHEMA = "v3-historical-public-price-paths-audit-1"


class PublicPathAuditError(ValueError):
    """Fixed diagnostics for aggregate-only workflow output."""


def require(condition, reason):
    if not condition:
        raise PublicPathAuditError(reason)


def unique_fields(pairs):
    values = dict(pairs)
    require(len(values) == len(pairs), "duplicate_historical_json_key")
    return values


def numeric_or_missing(value):
    return value is None or (type(value) in (int, float) and math.isfinite(value))


def audit(root=ROOT):
    hashes = {}

    def read(directory, name, extension="json"):
        raw = (root / directory / f"{name}-{TAG}.{extension}").read_bytes()
        hashes[name] = sha256(raw).hexdigest()
        return raw.decode("utf-8")

    def delisting_ledger(name):
        rows = {}
        for row in csv.DictReader(StringIO(read("ops", f"v3-performance-{name}", "csv")), strict=True):
            key = row["code"], row["horizon_months"]
            require(key not in rows, "duplicate_delisting_path")
            rows[key] = row
        return rows

    def read_json(directory, name):
        return json.loads(read(directory, name), object_pairs_hook=unique_fields)

    public = read_json("docs", "v3-historical-price-paths")
    scope = read_json("ops", "v3-performance-operating-scope")["scope_counts"]["reproduced_operating_scope"]
    market = read_json("ops", "v3-performance-market-coverage")
    financial = read_json("ops", "v3-performance-financial-factor-replay")
    prices, downside = delisting_ledger("price-replay"), delisting_ledger("downside-horizons")
    require(prices.keys() == downside.keys(), "delisting_ledger_path_keys_mismatch")
    require(public["schema"] == "v3-historical-price-paths-1" and public["path_columns"] == COLUMNS
            and financial["schema"] == "v3-historical-financial-factor-replay-v1"
            and all(doc["performance_validated"] is False and doc["signal_session"] == SIGNAL.isoformat()
                    and doc["entry_session"] == ENTRY.isoformat() for doc in (public, market, financial)),
            "historical_publication_scope_mismatch")
    require(public["valuations"] == {h: market["horizons"][h]["valuation_session"] for h in PERIODS},
            "historical_valuation_dates_mismatch")

    rows = public["rows"]
    require(len(rows) == scope["listed"] == financial["historical_scope_count"],
            "historical_roster_count_mismatch")
    markets = Counter()
    entry_count = 0
    valued = Counter()
    downside_observed = Counter()
    valued_path_with_gaps = Counter()
    delisted = set()
    financial_rows = financial["rows"]
    for code, row in rows.items():
        require(re.fullmatch(r"[A-Z0-9]{6}", code) and row["market"] in scope["by_market"]
                and isinstance(row["name"], str) and row["name"].strip()
                and type(row["entry_observed"]) is bool and isinstance(row["paths"], dict)
                and (row["entry_observed"] or not row["paths"]),
                "invalid_historical_public_row")
        markets[row["market"]] += 1
        entry_count += row["entry_observed"]
        for h, path in row["paths"].items():
            require(h in PERIODS and isinstance(path, list) and len(path) == len(COLUMNS),
                    "invalid_historical_public_path_shape")
            value, adverse, gaps, basis, entitlement = path
            require(numeric_or_missing(value) and numeric_or_missing(adverse)
                    and (adverse is None or adverse >= 0)
                    and type(gaps) is int and gaps >= 0 and basis in BASES and type(entitlement) is bool,
                    "invalid_historical_public_path_values")
            valued[h] += value is not None
            downside_observed[h] += adverse is not None
            valued_path_with_gaps[h] += value is not None and gaps > 0
            key = code, h
            if basis == "delisting_case_replay":
                p, d = prices.get(key), downside.get(key)
                require(p and d and row["name"] == p["name"] == d["name"]
                        and public["valuations"][h] == p["valuation_session"] == d["valuation_session"]
                        and value == (float(p["price_change_pct"]) if p["price_change_pct"] else None)
                        and adverse == float(d["maximum_adverse_excursion_observed_pct"])
                        and gaps == int(d["unpriced_sessions"]),
                        "published_delisting_path_differs_from_ledger")
                delisted.add(key)
        if code in financial_rows:
            saved = financial_rows[code]
            require((row["name"], row["market"]) == (saved["name"], saved["market"])
                    and row["paths"].keys() == saved["price_paths"].keys(),
                    "published_financial_detail_identity_mismatch")
            for h, path in saved["price_paths"].items():
                expected = [path["price_change_pct"], path["observed_adverse_excursion_pct"],
                            path["unpriced_sessions"], path.get("basis"),
                            bool(path.get("valuation_includes_unlisted_entitlement"))]
                require(row["paths"][h] == expected, "published_financial_detail_path_mismatch")
    require(financial_rows.keys() <= rows.keys() and delisted == prices.keys(),
            "historical_research_rows_missing_from_publication")
    require(markets == scope["by_market"] and entry_count == scope["next_open_observed"],
            "historical_market_or_entry_count_mismatch")
    subset = financial["outcome_coverage"]["complete_three_parts"]
    require(set(public["priced"]) == set(subset) == set(PERIODS)
            and public["cohort_size"] == financial["complete_three_part_issuers"]
            and all(public["cohort_size"] == subset[h]["cohort_size"]
                    and public["priced"][h] == subset[h]["priced"] <= valued[h] for h in PERIODS),
            "historical_financial_subset_summary_mismatch")
    return {
        "schema": SCHEMA, "signal_session": public["signal_session"], "entry_session": public["entry_session"],
        "operating_cohort_count": len(rows), "observed_entry_count": entry_count,
        "unobserved_entry_count": len(rows) - entry_count,
        "delisting_paths_reconciled": len(delisted), "financial_detail_issuers_reconciled": len(financial_rows),
        "horizons": {h: {"priced": valued[h], "entered_but_unpriced": entry_count - valued[h],
                         "downside_observed": downside_observed[h],
                         "priced_paths_with_gaps": valued_path_with_gaps[h]} for h in PERIODS},
        "input_sha256": hashes, "performance_validated": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(audit(), sort_keys=True, allow_nan=False))
    except (OSError, TypeError, ValueError, OverflowError, KeyError, csv.Error) as exc:
        reason = str(exc) if isinstance(exc, PublicPathAuditError) else "invalid_audit_inputs"
        raise SystemExit(f"Historical public path audit failed: {reason}") from None
