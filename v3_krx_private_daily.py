"""KRX official EOD private two-market ingestion candidate, opt-in ONLY.

One read-only KOSPI and one KOSDAQ request after service approval.
Never linked to existing v2.2/Pages/stock scores, never a scheduled workflow.
Original KRX market lists and normalized OHLC/ACC_TRDVOL stay in a private
directory outside this PUBLIC Git repository. GH runner disk is ephemeral.
This does NOT certify that accumulated volume covers KRX regular session only.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from official_probe.clients import KRXClient, ProbeError

MARKETS = ("KOSPI", "KOSDAQ")
ROOT = Path(__file__).resolve().parent
PRIVATE_DEFAULT = Path.home() / ".local" / "share" / "korean-stock-screener" / "krx"
CODE = re.compile(r"[0-9]{6}\Z")
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
NUMERIC = re.compile(r"[0-9]+(?:,[0-9]{3})*\Z")
OHLC = {"open": "TDD_OPNPRC", "high": "TDD_HGPRC",
        "low": "TDD_LWPRC", "close": "TDD_CLSPRC"}
MAX_SOURCE_ROWS = 5000
MAX_PRIVATE_FILE = 12_000_000


class IngestError(ValueError):
    """Safe internal reason labels; never reveal credential or HTTP error."""


def _date(asof: str) -> date:
    if type(asof) is not str or DAY.fullmatch(asof) is None:
        raise IngestError("bad_date")
    try:
        d = date.fromisoformat(asof)
    except ValueError:
        raise IngestError("bad_date") from None
    if d > datetime.now(ZoneInfo("Asia/Seoul")).date():
        raise IngestError("future_date")
    return d


def _whole(value: Any, field: str) -> int:
    if type(value) not in (str, int):
        raise IngestError("noninteger_" + field)
    if type(value) is int:
        if value < 0:
            raise IngestError("negative_" + field)
        n = value
    else:
        trimmed = value.strip()
        if NUMERIC.fullmatch(trimmed) is None:
            raise IngestError("noninteger_" + field)
        n = int(trimmed.replace(",", ""))
    if n > 10**15:
        raise IngestError("unreasonable_" + field)
    return n


def normalize_market(rows: Any, *, market: str, asof: str,
                     source: bytes, minimum_stocks: int = 500,
                     quarantine_invalid_prices: bool = False) -> dict:
    """Keep invalid individual quotes unavailable, never turn them into zeros.

    Default remains strict. The opt-in only saves malformed price rows in a
    private quarantine, so one halted issuer cannot discard the other market
    observations. Identity/date/duplicate and invalid volume still fail closed.
    """
    day = _date(asof)
    if market not in MARKETS or type(source) is not bytes or not source or len(source) > 20_000_000:
        raise IngestError("invalid_market_source")
    if type(minimum_stocks) is not int or minimum_stocks < 1:
        raise IngestError("invalid_minimum")
    if type(rows) is not list or not minimum_stocks <= len(rows) <= MAX_SOURCE_ROWS:
        raise IngestError("incomplete_or_oversized_market")
    result = []
    seen = set()
    skipped_nonstock = 0
    observed_nontrading = 0
    quarantined_prices = 0
    accepted_quotes = 0
    for raw in rows:
        if type(raw) is not dict:
            raise IngestError("invalid_market_row")
        code = str(raw.get("ISU_CD", "")).strip()
        if CODE.fullmatch(code) is None:
            skipped_nonstock += 1
            continue
        if code in seen:
            raise IngestError("duplicate_market_code")
        seen.add(code)
        if str(raw.get("BAS_DD", "")).replace("-", "") != day.strftime("%Y%m%d"):
            raise IngestError("wrong_market_trade_date")
        try:
            values = {k: _whole(raw.get(v), k) for k, v in OHLC.items()}
        except IngestError:
            if not quarantine_invalid_prices:
                raise
            values = None
        volume = _whole(raw.get("ACC_TRDVOL"), "volume")
        valid_ohlc = (values is not None and values["low"] > 0
                      and values["high"] >= values["low"]
                      and values["low"] <= values["open"] <= values["high"]
                      and values["low"] <= values["close"] <= values["high"])
        no_trade = (values is not None and volume == 0
                    and all(v == 0 for v in values.values()))
        if not valid_ohlc and not no_trade:
            if not quarantine_invalid_prices:
                raise IngestError("invalid_market_ohlc")
            quarantined_prices += 1
        else:
            accepted_quotes += 1
        if no_trade:
            observed_nontrading += 1
        result.append({
            "code": code, "market": market, "trade_date": day.isoformat(),
            "ohlc_observed": values if valid_ohlc else None,
            "accumulated_volume_observed": volume,
            "trading_status": ("reported_ohlc" if valid_ohlc else
                               "no_trade_or_suspended" if no_trade else
                               "quarantined_invalid_ohlc"),
            "regular_only_volume": None,
            "regular_session_verified": False,
            "adjusted_history_verified": False,
            "confirmed_scoring_allowed": False,
        })
    if accepted_quotes < minimum_stocks:
        raise IngestError("not_enough_six_digit_market_codes")
    return {
        "market": market, "trade_date": day.isoformat(),
        "rows_received": len(rows), "six_digit_rows_validated": len(result),
        "nonstock_codes_not_imported": skipped_nonstock,
        "no_trade_rows_observed": observed_nontrading,
        "quarantined_price_rows": quarantined_prices,
        "valid_or_explicit_no_trade_rows": accepted_quotes,
        "raw_source_sha256": sha256(source).hexdigest(),
        "ohlcv": result,
    }


def reconcile_listed_roster(snapshots: dict, catalog: dict, *, asof: str) -> dict:
    """Anonymous exact-code and market reconciliation; never a score gate.

    The current listed-company catalog is not a historical point-in-time
    membership certificate. Require the same date to avoid comparing today's
    constituents against an unrelated historical KRX market observation.
    """
    if (type(catalog) is not dict or catalog.get("date") != asof
            or type(catalog.get("rows")) is not list
            or type(catalog.get("listed_total")) is not int
            or len(catalog["rows"]) != catalog["listed_total"]):
        raise IngestError("unaligned_or_invalid_market_roster")
    roster = {}
    published_states = ("priced", "price_source_or_validation_error",
                        "insufficient_history", "unattempted_batch")
    for item in catalog["rows"]:
        if type(item) is not dict:
            raise IngestError("invalid_market_roster_row")
        code, market = item.get("code"), item.get("market")
        if (type(code) is not str or CODE.fullmatch(code) is None
                or market not in MARKETS or code in roster):
            raise IngestError("invalid_or_duplicate_market_roster_code")
        state = item.get("price_history_status")
        if state is not None and state not in published_states:
            raise IngestError("invalid_published_price_source_state")
        roster[code] = (market, state)
    observed = {}
    for market in MARKETS:
        for item in snapshots[market]["ohlcv"]:
            code = item["code"]
            if code in observed:
                raise IngestError("duplicate_cross_market_code")
            observed[code] = (market, item["trading_status"],
                              item["accumulated_volume_observed"])
    counts = {
        "same_day_catalog_listed": len(roster),
        "source_six_digit_market_rows": len(observed),
        "matching_market_valid_ohlc": 0,
        "matching_market_explicit_no_trade": 0,
        "matching_market_quarantined_ohlc": 0,
        "matching_market_quarantined_with_zero_reported_volume": 0,
        "catalog_code_missing_from_source": 0,
        "catalog_code_wrong_source_market": 0,
        "source_code_not_in_listed_catalog": 0,
        "public_price_status_rows_crosschecked": 0,
        "public_price_status_overlap": {s: {
            "krx_reported_ohlc": 0,
            "krx_explicit_no_trade": 0,
            "krx_quarantined_ohlc": 0,
            "krx_missing_or_wrong_market": 0,
        } for s in published_states},
    }
    for code, (expected_market, published_state) in roster.items():
        actual = observed.get(code)
        bucket = "krx_missing_or_wrong_market"
        if actual is None:
            counts["catalog_code_missing_from_source"] += 1
        elif actual[0] != expected_market:
            counts["catalog_code_wrong_source_market"] += 1
        elif actual[1] == "reported_ohlc":
            counts["matching_market_valid_ohlc"] += 1
            bucket = "krx_reported_ohlc"
        elif actual[1] == "no_trade_or_suspended":
            counts["matching_market_explicit_no_trade"] += 1
            bucket = "krx_explicit_no_trade"
        elif actual[1] == "quarantined_invalid_ohlc":
            counts["matching_market_quarantined_ohlc"] += 1
            counts["matching_market_quarantined_with_zero_reported_volume"] += int(
                actual[2] == 0)
            bucket = "krx_quarantined_ohlc"
        else:
            raise IngestError("unrecognized_private_market_status")
        if published_state is not None:
            counts["public_price_status_rows_crosschecked"] += 1
            counts["public_price_status_overlap"][published_state][bucket] += 1
    counts["source_code_not_in_listed_catalog"] = len(set(observed) - set(roster))
    counts["regular_session_verified"] = False
    counts["historical_membership_verified"] = False
    counts["confirmed_scoring_allowed"] = False
    return counts


def _private_dir(path: Path) -> Path:
    dest = path.expanduser().resolve()
    if dest == ROOT or ROOT in dest.parents:
        raise IngestError("private_storage_must_be_outside_public_repository")
    if path.is_symlink():
        raise IngestError("private_destination_symlink_forbidden")
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(dest, 0o700)
    return dest


def _atomically_write_new(destination: Path, filename: str, payload: dict) -> str:
    dest = _private_dir(destination)
    path = dest / filename
    if path.is_symlink():
        raise IngestError("private_snapshot_symlink_forbidden")
    serial = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    if len(serial) > MAX_PRIVATE_FILE:
        raise IngestError("private_snapshot_too_large")
    if path.exists():
        try:
            if not 0 < path.stat().st_size <= MAX_PRIVATE_FILE:
                raise IngestError("existing_snapshot_bad_size")
            existing = path.read_bytes()
        except OSError:
            raise IngestError("existing_snapshot_unreadable") from None
        if existing != serial:
            raise IngestError("existing_immutable_snapshot_conflict")
        return "already_recorded_unchanged"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=dest, prefix=".partial-", delete=False) as handle:
            tmp = Path(handle.name)
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            handle.write(serial)
        # Exclusive create avoids clobbering an already-existing daily snapshot.
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise IngestError("concurrent_snapshot_exists") from None
        except OSError:
            raise IngestError("snapshot_write_failed") from None
        return "new_private_snapshot_written"
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def ingest(*, asof: str, key: str, private_dir: Path,
           client_factory=KRXClient, minimum_stocks: int = 500,
           quarantine_invalid_prices: bool = False,
           roster_catalog: Path | None = None) -> dict:
    """One KRX request per market. One failure -> zero snapshot writes."""
    d = _date(asof)
    if type(key) is not str or not key.strip():
        raise IngestError("missing_KRX_API_KEY_or_service_approval")
    try:
        client = client_factory(key)
        snapshots = {}
        for market in MARKETS:
            rows, source = client.daily_market(market, d.isoformat())
            snapshots[market] = normalize_market(
                rows, market=market, asof=d.isoformat(), source=source,
                minimum_stocks=minimum_stocks,
                quarantine_invalid_prices=quarantine_invalid_prices)
    except IngestError:
        raise
    except (ProbeError, ValueError, TypeError):
        raise IngestError("official_provider_unavailable_or_invalid") from None
    except Exception:
        raise IngestError("unexpected_provider_failure") from None
    all_codes = [row["code"] for m in MARKETS for row in snapshots[m]["ohlcv"]]
    if len(all_codes) != len(set(all_codes)):
        raise IngestError("duplicate_cross_market_code")
    roster_audit = None
    if roster_catalog is not None:
        try:
            if roster_catalog.stat().st_size > 5_000_000:
                raise IngestError("oversized_market_roster")
            catalog = json.loads(roster_catalog.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise IngestError("unreadable_market_roster") from None
        roster_audit = reconcile_listed_roster(snapshots, catalog, asof=d.isoformat())
    result = {
        "schema": "private-krx-two-market-eod-observation-v0.1",
        "asof": d.isoformat(),
        "source": "KRX official free OPEN API stock by-date services",
        "coverage": {m: {k: v for k, v in snapshots[m].items() if k != "ohlcv"}
                     for m in MARKETS},
        "markets": snapshots,
        "same_day_listed_roster_audit": roster_audit,
        "source_crosscheck_required": False,
        "official_regular_session_volume_verified": False,
        "company_actions_adjustment_verified": False,
        "historical_120_250_day_continuity_verified": False,
        "investor_category_flows_included": False,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    }
    outcome = _atomically_write_new(
        private_dir, "krx_" + d.strftime("%Y%m%d") + ".json", result)
    return {
        "status": outcome, "asof": d.isoformat(),
        "kospi_rows": snapshots["KOSPI"]["six_digit_rows_validated"],
        "kosdaq_rows": snapshots["KOSDAQ"]["six_digit_rows_validated"],
        "quarantined_price_rows": sum(snapshots[m]["quarantined_price_rows"]
                                      for m in MARKETS),
        "same_day_listed_roster_audit": roster_audit,
        "source_hashes_recorded_privately": True,
        "raw_market_data_published": False,
        "investor_flow_confirmed": False,
        "confirmed_scoring_allowed": False,
        "v3_release_ready": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="KRX official daily private research ingestion, manual live only")
    ap.add_argument("--asof", required=True, help="Already-completed market date YYYY-MM-DD")
    ap.add_argument("--private-dir", default=str(PRIVATE_DEFAULT))
    ap.add_argument("--live", action="store_true",
                    help="Explicit, authorized KRX live opt-in after service approval")
    ap.add_argument("--quarantine-invalid-prices", action="store_true",
                    help="Keep invalid individual KRX quotes unscored in private source only")
    ap.add_argument("--roster-catalog", type=Path,
                    help="Optional same-day public listed catalog for anonymous exact-code audit")
    args = ap.parse_args(argv)
    if not args.live:
        print('{"status":"not_run","reason":"service_approval_and_live_required"}')
        return 0
    try:
        report = ingest(asof=args.asof, key=os.environ.get("KRX_API_KEY", ""),
                        private_dir=Path(args.private_dir),
                        quarantine_invalid_prices=args.quarantine_invalid_prices,
                        roster_catalog=args.roster_catalog)
    except IngestError as exc:
        print(json.dumps({"status":"ingestion_blocked", "reason": str(exc)}))
        return 1
    except Exception:
        print('{"status":"ingestion_blocked","reason":"unexpected_local_failure"}')
        return 1
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
