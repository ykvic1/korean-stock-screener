"""Manual pilot: whole-market discovery split into independent 100-stock jobs.

plan: issuer codes only, never original price/flow rows.
scan: derived technical observations and missing reasons per separate job.
merge: require every batch, then call existing v2 scoring / reporting once.

Nothing is posted by individual batches. This does NOT certify v3 IAS,
investor flows, financials, or the regular-session KRX API.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
from threading import Lock
import time
from zoneinfo import ZoneInfo

import screener
from v22_price_error_triage import public_reason
from v3_unscored_path_observations import (
    valid_observation, valid_unavailable_reason)
from v3_ma240_cross_research import (
    valid_observation as valid_ma240_cross,
    valid_unavailable_reason as valid_ma240_unavailable_reason)
from v3_ma_slope_transition import valid_observation as valid_ma_slope
from v3_breakout_followthrough import valid as valid_breakout_followthrough
from v3_price_session_capture import capture_window
from v3_dated_market_roster import load_dated_listing, DatedRosterError

SCHEMA = "full-market-batched-100-v0.1"
CHUNK = 100
CODE = re.compile(r"[0-9A-Z]{6}\Z")
MAX_UNIVERSE = 20000
MAX_BATCHES = 256
MAX_FILE = 10_000_000
MARKET_ROSTER_ATTEMPTS = 3


class BatchError(ValueError):
    """Safe diagnostic codes; upstream HTTP text/keys are never printed."""


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_FILE:
        raise BatchError("oversized_batch_output")
    path.write_text(raw + "\n", encoding="utf-8")


def _read(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE:
        raise BatchError("missing_or_oversized_artifact")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise BatchError("invalid_artifact_json") from None


def _date(asof: str) -> str:
    if type(asof) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof):
        raise BatchError("invalid_asof")
    try:
        d = datetime.fromisoformat(asof)
    except ValueError:
        raise BatchError("invalid_asof") from None
    if d.strftime("%Y-%m-%d") != asof:
        raise BatchError("invalid_asof")
    if d.date() > datetime.now(ZoneInfo("Asia/Seoul")).date():
        raise BatchError("future_asof")
    return asof


def _digest(asof: str, codes: list[str]) -> str:
    material = json.dumps({"asof": asof, "codes": codes, "chunk": CHUNK},
                          separators=(",", ":"), sort_keys=True).encode()
    return sha256(material).hexdigest()


def validate_manifest(manifest: dict):
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise BatchError("bad_manifest_schema")
    asof = _date(manifest.get("asof"))
    codes = manifest.get("codes")
    if (type(codes) is not list or not 1 <= len(codes) <= MAX_UNIVERSE
            or any(type(c) is not str or not CODE.fullmatch(c) for c in codes)
            or len(set(codes)) != len(codes) or codes != sorted(codes)
            or manifest.get("batch_size") != CHUNK):
        raise BatchError("bad_or_duplicate_universe")
    count = math.ceil(len(codes) / CHUNK)
    if (count > MAX_BATCHES or type(manifest.get("batch_count")) is not int
            or manifest["batch_count"] != count
            or manifest.get("digest") != _digest(asof, codes)):
        raise BatchError("bad_manifest_identity")
    if manifest.get("roster_provenance") == "date_pinned_fdr_public_cache":
        if (type(manifest.get("roster_source_sha256")) is not str or
                not re.fullmatch(r"[0-9a-f]{64}", manifest["roster_source_sha256"])):
            raise BatchError("untrusted_date_pinned_roster_digest")
    return asof, codes


def market_roster_with_retries(*, fetch=None, sleep=None):
    """Retry transient listing failures without accepting an unverified fallback.

    FDR's market-wide roster can fail intermittently even while the same
    day's next lookup succeeds. A cached/stale membership list would bypass
    the strict census and same-source identity checks, so never substitute it.
    """
    fetch = screener.market_snapshot if fetch is None else fetch
    sleep = time.sleep if sleep is None else sleep
    for attempt in range(MARKET_ROSTER_ATTEMPTS):
        try:
            return fetch()
        except Exception:
            if attempt == MARKET_ROSTER_ATTEMPTS - 1:
                raise BatchError('market_roster_source_unavailable_after_retries') from None
            logging.warning('Market roster fetch unavailable; bounded attempt %s/%s',
                            attempt + 1, MARKET_ROSTER_ATTEMPTS)
            sleep(1 + attempt * 2)


def dated_market_roster_with_retries(asof: str, *, fetch=None, sleep=None):
    """Retry only transient exact-date cache unavailability, never source drift.

    The trading day is fixed before any request. A malformed, duplicate or
    changed archive must fail immediately; retrying it could silently replace
    issuer membership after a partial 100-stock scan. No latest-day fallback.
    """
    fetch = load_dated_listing if fetch is None else fetch
    sleep = time.sleep if sleep is None else sleep
    for attempt in range(3):
        try:
            return fetch(asof)
        except DatedRosterError as exc:
            if str(exc) != 'dated_roster_unavailable' or attempt == 2:
                raise
            sleep(1 + attempt * 2)


def _same_day_saved_catalog(path: Path, asof: str) -> dict:
    """An explicitly labelled, *audit-only* roster when live KRX login fails.

    A cached market catalog cannot establish today's new issues, delistings or
    a valid source close.  Only a full same-date published roster may seed a
    read-only history scan; never silently promote it into publication.
    """
    catalog = _read(path)
    rows = catalog.get('rows')
    if (catalog.get('schema') != 'v22-current-listed-catalog-0.1'
            or catalog.get('date') != asof or type(rows) is not list
            or type(catalog.get('listed_total')) is not int
            or not 20 <= len(rows) == catalog['listed_total'] <= MAX_UNIVERSE):
        raise BatchError('saved_catalog_not_same_day_full_roster')
    saved = {}
    for row in rows:
        if (not isinstance(row, dict) or type(row.get('code')) is not str
                or not CODE.fullmatch(row['code'])
                or row.get('market') not in ('KOSPI', 'KOSDAQ')
                or row['code'] in saved):
            raise BatchError('saved_catalog_invalid_or_duplicate_issuer')
        saved[row['code']] = {'code': row['code'], 'market': row['market']}
    return saved


def prioritized_part_order(codes, asof, *, priority_path=None, catalog_path=None):
    """Best-effort SOURCE-work order only; never changes membership/digest.

    A previously verified 90-point evidence gap is merely a workload hint:
    NOT an observed factor, a new point, or a provider-recoverability claim.
    Stale, mismatched, or malformed hints restore alphabetical chunk order.
    """
    parts = list(range(math.ceil(len(codes) / CHUNK)))
    if priority_path is None or catalog_path is None:
        return parts, "not_configured", 0
    try:
        priority = _read(Path(priority_path))
        catalog = _read(Path(catalog_path))
        prior_day = priority.get("asof")
        if (priority.get("schema") != "v3-r1d-observation-work-priority-v1"
                or priority.get("missing_is_not_zero") is not True
                or prior_day != catalog.get("date")
                or type(prior_day) is not str
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", prior_day)
                or not 0 <= (datetime.fromisoformat(asof) -
                             datetime.fromisoformat(prior_day)).days <= 7
                or catalog.get("schema") != "v22-current-listed-catalog-0.1"
                or type(catalog.get("rows")) is not list
                or len(catalog["rows"]) != priority.get("listed_total")
                or catalog.get("listed_total") != priority.get("listed_total")):
            return parts, "unaligned_priority_source", 0
        older = [item.get("code") if isinstance(item, dict) else None
                 for item in catalog["rows"]]
        if (len(set(older)) != len(older) or
                any(type(code) is not str or not CODE.fullmatch(code)
                    for code in older)):
            return parts, "invalid_prior_issuer_identity", 0
        coverage = priority.get("coverage_90_first")
        if (type(coverage) is not dict or coverage.get("threshold") != 90
                or coverage.get("recovery_not_a_score_or_observation") is not True
                or coverage.get("missing_data_never_zero_filled") is not True
                or coverage.get("independent_source_recoverability_not_proven") is not True
                or type(coverage.get("family_conditional_priority_codes")) is not dict):
            return parts, "invalid_priority_contract", 0
        candidates = coverage["family_conditional_priority_codes"].get(
            "price_chart_windows")
        existing = set(older)
        if (type(candidates) is not list or len(candidates) > len(older)
                or any(type(code) is not str for code in candidates)
                or len(set(candidates)) != len(candidates)
                or any(code not in existing for code in candidates)):
            return parts, "invalid_priority_codes", 0
        preferred = set(candidates)
        # Prioritize SOURCE work with more conditional sub-90 chart gaps.
        # For a tie, prioritize newly included ordinary symbols. This does
        # not claim that history exists or that it can earn 90/100 points.
        ranked = sorted(parts, key=lambda part: (
            -sum(code in preferred for code in
                 codes[part*CHUNK:(part+1)*CHUNK]),
            -sum(code not in existing for code in
                 codes[part*CHUNK:(part+1)*CHUNK]), part))
        return ranked, "conditional_source_work_only", len(preferred & set(codes))
    except (BatchError, OSError, ValueError, TypeError, OverflowError):
        return parts, "unavailable_or_invalid_priority", 0


def plan(*, asof: str, out: Path, listing=None,
         fallback_catalog: Path | None = None,
         date_pinned_roster: bool = False,
         priority_file: Path | None = None,
         priority_catalog: Path | None = None) -> dict:
    asof = _date(asof)
    provenance = 'live_market_snapshot'
    source_digest = None
    if listing is not None:
        universe = listing
    elif date_pinned_roster:
        # Exact trading-day cache, no implicit latest-date KRX endpoint.
        # Raw CSV remains only in memory, never in the public artifacts.
        try:
            source, source_digest = dated_market_roster_with_retries(asof)
            universe = screener.market_snapshot(listing=source)
            provenance = 'date_pinned_fdr_public_cache'
        except (DatedRosterError, RuntimeError, ValueError, TypeError):
            if fallback_catalog is None:
                raise BatchError('date_pinned_roster_unavailable_or_invalid') from None
            universe = _same_day_saved_catalog(fallback_catalog, asof)
            provenance = 'same_day_saved_catalog_audit_only'
    else:
        try:
            universe = market_roster_with_retries()
        except BatchError as exc:
            if (str(exc) != 'market_roster_source_unavailable_after_retries'
                    or fallback_catalog is None):
                raise
            universe = _same_day_saved_catalog(fallback_catalog, asof)
            provenance = 'same_day_saved_catalog_audit_only'
    selected, eligible = screener.select_universe(
        universe, screener.cfg_read(), datetime.fromisoformat(asof))
    codes = sorted(r["code"] for r in selected)
    if eligible != len(codes) or len(codes) < 20:
        raise BatchError("incomplete_market_listing")
    manifest = {"schema": SCHEMA, "asof": asof, "codes": codes,
                "batch_size": CHUNK, "batch_count": math.ceil(len(codes)/CHUNK),
                "digest": _digest(asof, codes), "source_raw_rows_saved": False,
                "company_size_filter": False,
                "roster_provenance": provenance}
    if source_digest is not None and provenance == 'date_pinned_fdr_public_cache':
        manifest["roster_source_sha256"] = source_digest
    validate_manifest(manifest)
    order, priority_status, targeted = prioritized_part_order(
        codes, asof, priority_path=priority_file, catalog_path=priority_catalog)
    _write(out/"manifest.json", manifest)
    _write(out/"matrix.json", {"include": [
        {"part": n} for n in order]})
    return {"status": "market_plan_ready", "eligible": len(codes),
            "chunk_size": CHUNK, "jobs": manifest["batch_count"],
            "roster_provenance": provenance,
            "priority_status": priority_status,
            "conditional_price_chart_gap_codes_in_roster": targeted}


def batch_codes(manifest: dict, part: int) -> list[str]:
    _, codes = validate_manifest(manifest)
    if type(part) is not int or not 0 <= part < manifest["batch_count"]:
        raise BatchError("part_out_of_range")
    return codes[part*CHUNK:(part+1)*CHUNK]


def validate_part(manifest: dict, receipt: dict):
    asof, _ = validate_manifest(manifest)
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise BatchError("bad_batch_schema")
    codes = batch_codes(manifest, receipt.get("part"))
    if (receipt.get("digest") != manifest["digest"] or receipt.get("asof") != asof
            or receipt.get("codes") != codes or type(receipt.get("attempted")) is not int
            or receipt["attempted"] != len(codes)
            or receipt.get("raw_ohlcv_exported") is not False
            or receipt.get("v3_score_claimed") is not False):
        raise BatchError("batch_identity_or_coverage_changed")
    rows, errors, excluded = (receipt.get("rows"),
                              receipt.get("errors"), receipt.get("excluded"))
    if not all(isinstance(x, list) for x in (rows, errors, excluded)):
        raise BatchError("invalid_record_arrays")
    recorded = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("metrics"), dict):
            raise BatchError("invalid_technical_observation")
        stamp = row['metrics'].get('price_feed_observed_at_kst')
        if stamp is not None and capture_window(stamp) == 'unknown':
            raise BatchError('invalid_price_capture_time')
        if ("v3_unscored_path_observations" in row["metrics"] and
            not valid_observation(
                row["metrics"]["v3_unscored_path_observations"],
                row["metrics"].get("price_date"))):
            raise BatchError("untrusted_unscored_path_observation")
        if "v3_unscored_path_unavailable_reason" in row["metrics"]:
            if not valid_unavailable_reason(
                    row["metrics"].get("v3_unscored_path_observations"),
                    row["metrics"]["v3_unscored_path_unavailable_reason"]):
                raise BatchError("invalid_price_path_unavailable_reason")
        ma_receipt = row["metrics"].get("v3_ma240_cross_research")
        if ma_receipt is not None and not valid_ma240_cross(
                ma_receipt, row["metrics"].get("price_date")):
            raise BatchError("untrusted_ma240_cross_observation")
        if ("v3_ma240_cross_unavailable_reason" in row["metrics"] and
                not valid_ma240_unavailable_reason(
                    ma_receipt,
                    row["metrics"]["v3_ma240_cross_unavailable_reason"])):
            raise BatchError("invalid_ma240_cross_unavailable_reason")
        slope_receipt = row["metrics"].get("v3_ma_slope_transition")
        if slope_receipt is not None and not valid_ma_slope(
                slope_receipt, row["metrics"].get("price_date")):
            raise BatchError("untrusted_ma_slope_transition")
        breakout_receipt=row["metrics"].get("v3_breakout_followthrough")
        if breakout_receipt is not None and not valid_breakout_followthrough(
                breakout_receipt,row["metrics"].get("price_date")):
            raise BatchError("untrusted_breakout_followthrough")
        recorded.append(row.get("code"))
    for group, length in ((errors, 3), (excluded, 2)):
        for item in group:
            if not isinstance(item, list) or len(item) != length:
                raise BatchError("invalid_batch_error_row")
            recorded.append(item[0])
    if len(recorded) != len(codes) or set(recorded) != set(codes):
        raise BatchError("missing_duplicate_or_unplanned_symbol")
    return True


def scan(*, manifest: dict, part: int, out: Path,
         analyzer=None, cfg=None, sleep=None, workers=1):
    asof, _ = validate_manifest(manifest)
    symbols = batch_codes(manifest, part)
    analyzer = screener.analyze_history if analyzer is None else analyzer
    cfg = screener.cfg_read() if cfg is None else cfg
    sleep = screener.time.sleep if sleep is None else sleep
    if type(workers) is not int or not 1 <= workers <= 2:
        raise BatchError("invalid_history_workers")
    interval = float(cfg.get("request_sleep_seconds", 0.18))
    if not math.isfinite(interval) or interval < 0:
        raise BatchError("invalid_request_interval")
    day = datetime.fromisoformat(asof)
    rows, errors, excluded = [], [], []
    gate = Lock()
    next_start = 0.0
    started = time.monotonic()

    def analyze(code):
        # Pace starts across BOTH workers. Overlap network waits without
        # multiplying the configured request-start rate or sharing raw bars.
        nonlocal next_start
        with gate:
            wait = max(0.0, next_start - time.monotonic())
            if wait:
                sleep(wait)
            next_start = time.monotonic() + interval
        start = time.monotonic()
        try:
            data = analyzer(code, cfg, day)
            if (type(data) is not dict or not isinstance(data.get("price_date"), str)
                    or "code" in data or "flows_confirmed" in data):
                raise BatchError("invalid_technical_result")
            if ("v3_unscored_path_observations" in data and
                not valid_observation(data["v3_unscored_path_observations"],
                                      data["price_date"])):
                raise BatchError("untrusted_unscored_path_observation")
            if (data.get("v3_ma240_cross_research") is not None and
                not valid_ma240_cross(data["v3_ma240_cross_research"],
                                      data["price_date"])):
                raise BatchError("untrusted_ma240_cross_observation")
            if ("v3_ma240_cross_unavailable_reason" in data and
                    not valid_ma240_unavailable_reason(
                        data.get("v3_ma240_cross_research"),
                        data["v3_ma240_cross_unavailable_reason"])):
                raise BatchError("invalid_ma240_cross_unavailable_reason")
            if (data.get("v3_ma_slope_transition") is not None and
                not valid_ma_slope(data["v3_ma_slope_transition"],
                                   data["price_date"])):
                raise BatchError("untrusted_ma_slope_transition")
            if (data.get("v3_breakout_followthrough") is not None and
                not valid_breakout_followthrough(data["v3_breakout_followthrough"],
                                                 data["price_date"])):
                raise BatchError("untrusted_breakout_followthrough")
            # Individual 100-stock workers finish at different points during
            # the 16:00-20:00 aftermarket.  Capture when EACH price-derived
            # observation was completed; the final publication time alone is
            # not its as-of time.  This is provenance, never session approval.
            data['price_feed_observed_at_kst'] = datetime.now(
                ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')
            return "rows", {"code": code, "metrics": data}, time.monotonic()-start
        except screener.InsufficientHistory:
            return "excluded", [code, "insufficient_valid_price_history"], time.monotonic()-start
        except Exception as exc:
            # 2026-10-19 operational failures were all logged "ValueError".
            # Preserve stable fixed cause codes without ever publishing the
            # source exception text, raw provider rows or credentials.
            label = public_reason(exc) if isinstance(exc, ValueError) else type(exc).__name__
            if not re.fullmatch(r"[A-Za-z_]{1,64}", label):
                label = "SourceError"
            return "errors", [code, "가격이력", label], time.monotonic()-start

    # Ordered map preserves the serial receipt format and deterministic
    # issuer order, even when requests complete in a different order.
    durations = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for kind, value, elapsed in pool.map(analyze, symbols):
            {"rows": rows, "errors": errors, "excluded": excluded}[kind].append(value)
            durations.append(elapsed)
    timing = {"workers": workers, "elapsed_seconds": round(time.monotonic()-started, 3),
              "analysis_seconds_sum": round(sum(durations), 3),
              "slowest_issuer_seconds": round(max(durations, default=0), 3)}
    receipt = {"schema": SCHEMA, "asof": asof, "part": part,
               "digest": manifest["digest"], "codes": symbols,
               "attempted": len(symbols), "rows": rows, "errors": errors,
               "excluded": excluded, "raw_ohlcv_exported": False,
               "v3_score_claimed": False}
    validate_part(manifest, receipt)
    _write(out/f"part-{part:03d}.json", receipt)
    return {"status": "derived_batch_saved", "part": part, "attempted": len(symbols),
            "valid": len(rows), "errors": len(errors), "excluded": len(excluded),
            "timing": timing}


def combine_receipts(manifest: dict, receipts: list[dict], *,
                     allow_missing_parts: bool = False) -> dict:
    _, codes = validate_manifest(manifest)
    parts = {}
    for rec in receipts:
        validate_part(manifest, rec)
        if rec["part"] in parts:
            raise BatchError("duplicate_job_receipt")
        parts[rec["part"]] = rec
    expected = set(range(manifest["batch_count"]))
    missing = sorted(expected - set(parts))
    if missing and not allow_missing_parts:
        raise BatchError("missing_batch_job_receipt")
    if not parts:
        raise BatchError("no_successful_batch_receipts")
    ordered = [parts[i] for i in sorted(parts)]
    missing_codes = [code for i in missing for code in batch_codes(manifest, i)]
    rows = [r for p in ordered for r in p["rows"]]
    errors = [r for p in ordered for r in p["errors"]]
    excluded = [r for p in ordered for r in p["excluded"]]
    attempted = sum(p["attempted"] for p in ordered)
    if len(rows)+len(errors)+len(excluded) != attempted or attempted+len(missing_codes)!=len(codes):
        raise BatchError("market_partial_census_mismatch")
    return {"rows": rows, "errors": errors, "excluded": excluded,
            "eligible": len(codes), "examined": attempted,
            "deferred": len(missing_codes), "missing_batch_parts": missing,
            "raw_ohlcv_exported": False, "market_scan_complete": not missing}


def load_receipts(manifest: dict, root: Path, *,
                  allow_missing_parts: bool = False) -> dict:
    files = sorted(root.glob("part-*.json"))
    if not allow_missing_parts and len(files) != manifest["batch_count"]:
        raise BatchError("missing_or_extra_job_artifact")
    if not files:
        raise BatchError("no_successful_batch_artifacts")
    return combine_receipts(
        manifest, [_read(p) for p in files],
        allow_missing_parts=allow_missing_parts)


def flow_after_merge(rows: list[dict], day: datetime, cfg: dict, errors: list):
    ordered = sorted(rows, key=lambda r: (
        (r["components"]["일봉 구조"] or 0)
        + (r["components"]["주봉·추세"] or 0)
        + (r["components"]["거래량·OBV"] or 0),
        r["market_cap_eok"]), reverse=True)
    collector = screener.FlowCollector(
        (lambda code: screener.flows(code, day, cfg))
        if os.getenv("KRX_ID") and os.getenv("KRX_PW") else None,
        lambda code: screener.flows_naver(code, day, cfg),
        budget_seconds=int(cfg.get("flow_time_budget_seconds", 420)))
    deferred = 0
    for i, row in enumerate(ordered):
        result, error = collector.collect(row["code"])
        if result is None and isinstance(error, str) and error.startswith("deferred:"):
            deferred = len(ordered)-i
            break
        if result is not None:
            row.update(result)
        else:
            errors.append([row["code"], "기관·외국인 수급", str(error)[:200]])
        if i % 10 == 9:
            logging.info("Flow research progress %s/%s", i+1, len(ordered))
    return collector.attempted, deferred, len(ordered)


def publish(*, manifest: dict, parts_dir: Path,
            listing=None, index_data=None, optional_sources=None,
            cfg=None, use_flow=True, write_report=True,
            allow_missing_parts=False):
    if manifest.get('roster_provenance') == 'same_day_saved_catalog_audit_only':
        raise BatchError('cached_market_roster_cannot_publish')
    merged = load_receipts(
        manifest, parts_dir, allow_missing_parts=allow_missing_parts)
    cfg = screener.cfg_read() if cfg is None else cfg
    if listing is None and manifest.get("roster_provenance") == 'date_pinned_fdr_public_cache':
        # Never join a different cached market-day file at publication time.
        try:
            source, digest = dated_market_roster_with_retries(manifest["asof"])
            if digest != manifest.get("roster_source_sha256"):
                raise BatchError('date_pinned_roster_changed_between_jobs')
            listing = screener.market_snapshot(listing=source)
        except DatedRosterError:
            raise BatchError('date_pinned_roster_unavailable_or_invalid') from None
    else:
        listing = market_roster_with_retries() if listing is None else listing
    if set(listing) != set(manifest["codes"]):
        raise BatchError("listing_changed_between_jobs")
    day = datetime.fromisoformat(manifest["asof"])
    optional = screener.optional_listings(listing) if optional_sources is None else optional_sources
    idx = screener.load_indexes(day, cfg) if index_data is None else index_data
    valid = []
    errors = list(merged["errors"])
    for x in merged["rows"]:
        r = listing[x["code"]].copy()
        r.update(x["metrics"])
        # The scheduled 18:30 publisher must preserve all 20/60/120
        # source-observed horizons, including unknown=missing (not zero).
        # Compare only an index close from the same actual issuer price day.
        r.update(screener.market_relative_returns(r,idx.get(r["market"])))
        r["flows_confirmed"] = False
        valid.append(r)
    if not valid:
        raise BatchError("no_valid_price_rows")
    freshest = max(r["price_date"] for r in valid)
    # A same-day, hash-pinned issuer roster must not be published under a
    # previous trading day\u0027s FChart price data. Otherwise the next R1e
    # re-calculation can mislabel lagged observations as a new close. Keep
    # the last published market and scores unchanged until a current-day
    # price receipt is genuinely available. This does NOT certify that the
    # FChart close is the KRX 15:30 regular-session closing price.
    if (manifest.get("roster_provenance") == "date_pinned_fdr_public_cache"
            and freshest != manifest["asof"]):
        raise BatchError("date_pinned_roster_price_day_mismatch")
    if (day.date()-datetime.fromisoformat(freshest).date()).days > cfg["max_data_age_days"]:
        raise BatchError("stale_market_date")
    current = []
    for r in valid:
        if r["price_date"] != freshest:
            errors.append([r["code"], "가격이력", "stale_vs_market_session"])
        else:
            screener.score_one(r, cfg)
            current.append(r)
    if len(current)<20:
        raise BatchError("fewer_than_20_current_stocks")
    attempted = deferred = planned = 0
    if use_flow:
        attempted, deferred, planned = flow_after_merge(current, day, cfg, errors)
    for r in current:
        screener.score_one(r, cfg)
    # Scheduled 18:30 uses THIS full-market merger, not build_rows() in
    # screener.py. Connect the research-only daily + completed-week technical
    # adapter here too. Otherwise new fields never reach docs/data.json and
    # every stock incorrectly remains V3-technical-unobserved.
    # Other five pillars stay explicitly null and the existing v2.2 order,
    # decision, scoring and recommendation logic are NOT replaced.
    from v3_pattern_composite_score import compute_candidates
    compute_candidates(current)
    current.sort(key=lambda r: (
        r["decision"] == "정밀 검토 우선", r["decision"] == "관찰",
        r["score"] or 0, r["coverage_pct"] or 0), reverse=True)
    meta = {"eligible": merged["eligible"], "examined": merged["examined"],
            "history_deferred": merged["deferred"],
            "history_scan_complete": merged["market_scan_complete"],
            "history_usable": len(current), "index_markets": list(idx),
            "optional_listings": optional, "excluded": merged["excluded"],
            "flow_attempted": attempted, "flow_planned": planned,
            "flow_deferred": deferred}
    if not write_report:
        return {"rows": current, "errors": errors, "metadata": meta}
    screener.LOG.mkdir(parents=True, exist_ok=True)
    excel = screener.make_report(current, errors,
                                 datetime.fromisoformat(freshest), cfg, meta)
    data = screener.web_publish(current, errors,
                                datetime.fromisoformat(freshest), cfg, meta, excel)
    # Keep ALL currently listed stocks searchable even when OHLCV unavailable.
    from v22_active_market_catalog import write_active_catalog
    write_active_catalog(data, listing, screener.SITE / "v22-market-catalog.json")
    # Separate small active-listing research: 60-159 genuine bars can yield
    # MA20/MA60 and OBV without pretending to have 120-week history or a
    # full v2.2 ranking score. Any source failure stays unavailable.
    from v22_short_history_observations import write_short_history
    write_short_history(data, screener.SITE / "v22-partial-history.json", cfg=cfg)
    return {"status": ("complete_market_report_published"
                        if merged["market_scan_complete"]
                        else "partial_market_report_published_with_missing_batch_warning"),
            "asof": freshest, "eligible": merged["eligible"],
            "attempted": merged["examined"],
            "not_attempted_due_to_missing_batch": merged["deferred"],
            "missing_batch_parts": merged["missing_batch_parts"],
            "market_scan_complete": merged["market_scan_complete"],
            "priced": len(current),
            "displayed": data["candidate_count"],
            "flow_confirmed": data["flow_confirmed_count"],
            "v3_score_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="100-stock chunk pilot; publish only after complete union")
    subs = parser.add_subparsers(dest="cmd", required=True)
    p = subs.add_parser("plan")
    p.add_argument("--asof", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--fallback-catalog", type=Path, default=None,
                   help='Use same-day saved roster only after live listing fails; audit only')
    p.add_argument("--date-pinned-roster", action="store_true",
                   help='Use exact-date FinanceData cache, no implicit latest-date resolver')
    p.add_argument("--priority-file", type=Path, default=None)
    p.add_argument("--priority-catalog", type=Path, default=None)
    p = subs.add_parser("scan")
    p.add_argument("--manifest", required=True)
    p.add_argument("--part", required=True, type=int)
    p.add_argument("--workers", type=int, choices=(1, 2), default=1)
    p.add_argument("--out", required=True)
    p = subs.add_parser("merge")
    p.add_argument("--manifest", required=True)
    p.add_argument("--parts", required=True)
    p.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "plan":
            asof = args.asof or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
            result = plan(asof=asof, out=Path(args.out),
                          fallback_catalog=args.fallback_catalog,
                          date_pinned_roster=args.date_pinned_roster,
                          priority_file=args.priority_file,
                          priority_catalog=args.priority_catalog)
        elif args.cmd == "scan":
            result = scan(manifest=_read(Path(args.manifest)),
                          part=args.part, out=Path(args.out), workers=args.workers)
        else:
            manifest = _read(Path(args.manifest))
            if not args.publish:
                # First pilot is audit-only: do not fetch again, score,
                # commit source data, or change current production.
                result = load_receipts(manifest, Path(args.parts),
                                       allow_missing_parts=True)
                result = {
                    "status": ("complete_market_batches_audited_not_published"
                               if result["market_scan_complete"] else
                               "partial_market_batches_audited_not_published"),
                    "all_codes_in_original_universe": result["eligible"],
                    "actually_attempted": result["examined"],
                    "missing_batch_part_count": len(result["missing_batch_parts"]),
                    "unattempted_due_to_missing_batch": result["deferred"],
                    "market_scan_complete": result["market_scan_complete"],
                    "priced": len(result["rows"]),
                    "price_errors": len(result["errors"]),
                    "insufficient_history": len(result["excluded"]),
                    "raw_ohlcv_exported": False,
                }
            else:
                result = publish(manifest=manifest,
                                 parts_dir=Path(args.parts),
                                 allow_missing_parts=True)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except BatchError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 1
    except Exception:
        print('{"status":"blocked","reason":"safe_unexpected_batch_error"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
