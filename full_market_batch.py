"""Manual pilot: whole-market discovery split into independent 100-stock jobs.

plan: issuer codes only, never original price/flow rows.
scan: derived technical observations and missing reasons per separate job.
merge: require every batch, then call existing v2 scoring / reporting once.

Nothing is posted by individual batches. This does NOT certify v3 IAS,
investor flows, financials, or the regular-session KRX API.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import screener
from v22_price_error_triage import public_reason
from v3_unscored_path_observations import valid_observation

SCHEMA = "full-market-batched-100-v0.1"
CHUNK = 100
CODE = re.compile(r"\d{6}\Z")
MAX_UNIVERSE = 20000
MAX_BATCHES = 256
MAX_FILE = 10_000_000


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
    return asof, codes


def plan(*, asof: str, out: Path, listing=None) -> dict:
    asof = _date(asof)
    universe = screener.market_snapshot() if listing is None else listing
    selected, eligible = screener.select_universe(
        universe, screener.cfg_read(), datetime.fromisoformat(asof))
    codes = sorted(r["code"] for r in selected)
    if eligible != len(codes) or len(codes) < 20:
        raise BatchError("incomplete_market_listing")
    manifest = {"schema": SCHEMA, "asof": asof, "codes": codes,
                "batch_size": CHUNK, "batch_count": math.ceil(len(codes)/CHUNK),
                "digest": _digest(asof, codes), "source_raw_rows_saved": False,
                "company_size_filter": False}
    validate_manifest(manifest)
    _write(out/"manifest.json", manifest)
    _write(out/"matrix.json", {"include": [
        {"part": n} for n in range(manifest["batch_count"])]})
    return {"status": "market_plan_ready", "eligible": len(codes),
            "chunk_size": CHUNK, "jobs": manifest["batch_count"]}


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
        if ("v3_unscored_path_observations" in row["metrics"] and
            not valid_observation(
                row["metrics"]["v3_unscored_path_observations"],
                row["metrics"].get("price_date"))):
            raise BatchError("untrusted_unscored_path_observation")
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
         analyzer=None, cfg=None, sleep=None):
    asof, _ = validate_manifest(manifest)
    symbols = batch_codes(manifest, part)
    analyzer = screener.analyze_history if analyzer is None else analyzer
    cfg = screener.cfg_read() if cfg is None else cfg
    sleep = screener.time.sleep if sleep is None else sleep
    day = datetime.fromisoformat(asof)
    rows, errors, excluded = [], [], []
    for i, code in enumerate(symbols):
        try:
            data = analyzer(code, cfg, day)
            if (type(data) is not dict or not isinstance(data.get("price_date"), str)
                    or "code" in data or "flows_confirmed" in data):
                raise BatchError("invalid_technical_result")
            if ("v3_unscored_path_observations" in data and
                not valid_observation(data["v3_unscored_path_observations"],
                                      data["price_date"])):
                raise BatchError("untrusted_unscored_path_observation")
            rows.append({"code": code, "metrics": data})
        except screener.InsufficientHistory:
            excluded.append([code, "insufficient_valid_price_history"])
        except Exception as exc:
            # 2026-10-19 operational failures were all logged "ValueError".
            # Preserve stable fixed cause codes without ever publishing the
            # source exception text, raw provider rows or credentials.
            label = public_reason(exc) if isinstance(exc, ValueError) else type(exc).__name__
            if not re.fullmatch(r"[A-Za-z_]{1,64}", label):
                label = "SourceError"
            errors.append([code, "가격이력", label])
        if i < len(symbols)-1:
            sleep(float(cfg.get("request_sleep_seconds", 0.18)))
    receipt = {"schema": SCHEMA, "asof": asof, "part": part,
               "digest": manifest["digest"], "codes": symbols,
               "attempted": len(symbols), "rows": rows, "errors": errors,
               "excluded": excluded, "raw_ohlcv_exported": False,
               "v3_score_claimed": False}
    validate_part(manifest, receipt)
    _write(out/f"part-{part:03d}.json", receipt)
    return {"status": "derived_batch_saved", "part": part, "attempted": len(symbols),
            "valid": len(rows), "errors": len(errors), "excluded": len(excluded)}


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
    merged = load_receipts(
        manifest, parts_dir, allow_missing_parts=allow_missing_parts)
    cfg = screener.cfg_read() if cfg is None else cfg
    listing = screener.market_snapshot() if listing is None else listing
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
        ref = idx.get(r["market"])
        if ref and r.get("price20") is not None and r.get("price60") is not None:
            r["relative20"] = screener.round_or_none(r["price20"]-ref["r20"])
            r["relative60"] = screener.round_or_none(r["price60"]-ref["r60"])
        else:
            r["relative20"] = r["relative60"] = None
        r["flows_confirmed"] = False
        valid.append(r)
    if not valid:
        raise BatchError("no_valid_price_rows")
    freshest = max(r["price_date"] for r in valid)
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
    p = subs.add_parser("scan")
    p.add_argument("--manifest", required=True)
    p.add_argument("--part", required=True, type=int)
    p.add_argument("--out", required=True)
    p = subs.add_parser("merge")
    p.add_argument("--manifest", required=True)
    p.add_argument("--parts", required=True)
    p.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "plan":
            asof = args.asof or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
            result = plan(asof=asof, out=Path(args.out))
        elif args.cmd == "scan":
            result = scan(manifest=_read(Path(args.manifest)),
                          part=args.part, out=Path(args.out))
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
