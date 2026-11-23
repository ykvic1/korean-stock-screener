"""Bounded true KRX intraday LOW checks *before* frozen first-touch sessions.

The older 245-session KRX archive exposes only daily CLOSE. To avoid falsely
promoting CLOSE evidence into LOW evidence, derive candidate original/successor
holder trading-days before the first replayed intraday touch, subtract separately
authenticated all-market KRX OHLC days, then verify a maximum of five new KRX
market dates (two market requests per date) in a single explicit one-shot run.

No issuer identity, raw OHLC, or holder reference lines leave the process.
A new KRX response is never substituted for official first-touch chronology
without signed provenance and all remaining earlier days independently checked.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import re
from pathlib import Path

from official_probe.clients import KRXClient
from v3_forward_entry import read_official_openings
from v3_forward_daily_prices import daily_row
from v3_historical_downside_official_ohlc_evidence import (
    EVIDENCE as BASE, evidence_sessions,
)
from v3_historical_extreme_direct_low_evidence import (
    EVIDENCE as EXTREMES, build as validate_extremes,
)
from v3_historical_prior_direct_low_evidence import (
    EVIDENCE as PRIOR, build as validate_prior,
)
from v3_historical_reference_touch_direct_low_evidence import (
    EVIDENCE as TOUCHES, _unpack, build as validate_touches,
)
from v3_historical_market_coverage import (
    ENTRY, ROOT, read_panels, traded, day,
)
from v3_historical_official_holder_daily_path import (
    plan as daily_holder_plan,
)
from v3_historical_official_holder_terminal import _amount, require
from v3_historical_pilot_official_prices import (
    SOURCE_DIGESTS, compare_market, exact_market_prices,
    exact_traded_extreme_symbols,
)

SCHEMA = "v3-historical-pre-first-low-official-gap-v1"
PROBE_SCHEMA = "v3-historical-pre-first-low-direct-krx-v1"
BATCH_LIMIT = 5
UNIT = Decimal("0.0001")
FROZEN = ROOT / "ops/v3-historical-pre-first-low-official-20261123.json"
FROZEN_RUN = (45093498230, 137428080473, 14782307230,
              "4564b572a90af2a0ca1c55367cb45d9059cd16ed3dab1b9cd95b23783543a44f")
FROZEN_INCREMENTAL_DAYS = 5
FROZEN_INCREMENTAL_HOLDER_LOWS = 47


def saved_official_ohlc_dates(root=ROOT):
    """Reauthenticate ALL saved OHLC-day sources before reusing their dates."""
    base = json.loads(BASE.read_text(encoding="utf-8"))
    extremes = json.loads(EXTREMES.read_text(encoding="utf-8"))
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    touches = json.loads(TOUCHES.read_text(encoding="utf-8"))
    baseline, _, _, _ = evidence_sessions(base)
    require(validate_extremes(extremes, base)["new_direct_low_compared_dates"] == 27
            and validate_prior(prior, base, extremes)[
                "all_85_horizon_extreme_dates_directly_checked"] == 85
            and validate_touches(touches, base, prior, extremes, root)[
                "all_reported_first_touch_security_days_with_exact_ohlc"] == 14,
            "incomplete_independent_historical_official_low_source")
    later = {
        on for batch in extremes["batches"]
        for on in batch["result"]["sessions"]
    }
    direct_touch = {
        on for batch in touches["groups"]
        for on in _unpack(batch)["sessions"]
    }
    official = {day(v) for v in set(baseline) | later | direct_touch}
    require(len(baseline) == 14 and len(later) == 27
            and len(direct_touch) == 10 and 41 <= len(official) <= 51
            and not (set(baseline) & later),
            "overlapping_or_missing_authenticated_marketwide_ohlc_days")
    return official


def choose_batch(uncovered, existing_days, limit=BATCH_LIMIT):
    """Count issuer-days, not dates; earliest high-coverage gaps first."""
    require(type(uncovered) is dict and type(existing_days) is set
            and type(limit) is int and 1 <= limit <= 5,
            "invalid_pre_first_low_priority_scope")
    candidates = [(day, targets) for day, targets in uncovered.items()
                  if day not in existing_days and targets]
    require(len({on for on, targets in candidates}) == len(candidates)
            and all(len({origin for origin, _, _, _ in targets}) == len(targets)
                    and all(type(units) is Decimal and units > 0
                            and type(line) is Decimal and line > 0
                            for origin, code, units, line in targets)
                    for on, targets in candidates),
            "invalid_pre_first_low_duplicate_or_holder_ratio")
    return [d for d, _ in sorted(candidates,
                                key=lambda pair: (-len(pair[1]), pair[0]))[:limit]]


def prepare(public_panels, confirmed):
    """Select only *traded* pre-first LOW holder dates with unverified KRX LOW."""
    sessions = sorted(public_panels)
    holder = daily_holder_plan(sessions)
    # Second independently authenticated five-date batch increases the unique
    # official OHLC coverage from 49 to 54; never synthesize earlier LOWs.
    require(41 <= len(confirmed) <= 61
            and all(d in public_panels for d in confirmed)
            and len(holder["reference"]) == 17, "invalid_saved_ohlc_day_calendar")
    holders = {on: {origin: (code, units)
                    for origin, code, units in holders_on}
               for on, holders_on in holder["by_day"].items()}
    candidates = {}
    counts = Counter()
    touched = [x for x in holder["reference"].values() if x["first_observed_touch_on"]]
    require(len(touched) == 14, "unexpected_first_low_touch_cohort")
    for ref in touched:
        origin, first, line = ref["code"], day(ref["first_observed_touch_on"]), _amount(
            ref["reference_line_krw"])
        require(ENTRY <= first <= sessions[-1], "invalid_frozen_first_touch_date")
        for on in holder["days"]:
            if on >= first:
                break
            owned = holders.get(on, {}).get(origin)
            if owned is None:
                counts["unpriced_entitlement_holder_days"] += 1
                continue
            code, units = owned
            candle = public_panels[on].get(code)
            if not traded(candle):
                counts["untraded_holder_days"] += 1
                continue
            require(Decimal(str(candle[5])) * units > line,
                    "public_low_precedes_frozen_first_low_touch")
            counts["public_traded_pre_touch_holder_days"] += 1
            if on in confirmed:
                counts["pre_touch_holder_lows_on_authenticated_marketwide_days"] += 1
            else:
                counts["pre_touch_holder_lows_missing_direct_official_marketday"] += 1
                candidates.setdefault(on, []).append((origin, code, units, line))
    chosen = choose_batch(candidates, confirmed)
    require(len(chosen) == BATCH_LIMIT and len(candidates) > BATCH_LIMIT
            and counts["public_traded_pre_touch_holder_days"] == (
                counts["pre_touch_holder_lows_on_authenticated_marketwide_days"]
                + counts["pre_touch_holder_lows_missing_direct_official_marketday"]),
            "pre_first_low_coverage_or_batch_incomplete")
    return {
        "holder": holder,
        "uncovered": candidates,
        "batch": chosen,
        "report": {
            "schema": SCHEMA,
            "original_issuers": len(holder["reference"]),
            "original_issuers_with_replayed_low_touch": 14,
            "previously_authenticated_official_marketwide_ohlc_dates": len(confirmed),
            "pre_touch_public_traded_holder_days": counts[
                "public_traded_pre_touch_holder_days"],
            "pre_touch_holder_days_on_existing_official_ohlc_dates": counts[
                "pre_touch_holder_lows_on_authenticated_marketwide_days"],
            "pre_touch_holder_days_without_existing_official_ohlc_date": counts[
                "pre_touch_holder_lows_missing_direct_official_marketday"],
            "pre_touch_untraded_holder_days": counts["untraded_holder_days"],
            "pre_touch_unpriced_entitlement_holder_days": counts[
                "unpriced_entitlement_holder_days"],
            "distinct_pre_touch_official_low_missing_dates": len(candidates),
            "bounded_next_official_request_dates": [d.isoformat() for d in chosen],
            "bounded_next_issuer_low_observations": sum(
                len(candidates[d]) for d in chosen),
            "maximum_official_krx_api_requests_if_executed": BATCH_LIMIT * 2,
            "first_touch_earliest_low_certified": False,
            "all_prior_official_lows_covered": False,
            "regular_session_volume_independently_verified": False,
            "historical_pit_scores_certified": False,
            "performance_validated": False,
            "v30b_release_ready": False,
        },
    }


def valid_summary(report):
    """Fail closed on complete but explicitly noncertifying gap inventory."""
    if type(report) is not dict or report.get("schema") != SCHEMA:
        return False
    numeric = (
        "pre_touch_public_traded_holder_days",
        "pre_touch_holder_days_on_existing_official_ohlc_dates",
        "pre_touch_holder_days_without_existing_official_ohlc_date",
        "pre_touch_untraded_holder_days",
        "pre_touch_unpriced_entitlement_holder_days",
        "distinct_pre_touch_official_low_missing_dates",
        "bounded_next_issuer_low_observations",
    )
    planned = report.get("bounded_next_official_request_dates")
    if (report.get("original_issuers") != 17
            or report.get("original_issuers_with_replayed_low_touch") != 14
            or not 41 <= report.get(
                "previously_authenticated_official_marketwide_ohlc_dates", -1) <= 61
            or any(type(report.get(key)) is not int or report[key] < 0
                   for key in numeric)
            or report["pre_touch_public_traded_holder_days"] !=
            report["pre_touch_holder_days_on_existing_official_ohlc_dates"]
            + report["pre_touch_holder_days_without_existing_official_ohlc_date"]
            or report["pre_touch_holder_days_without_existing_official_ohlc_date"] <= 0
            or report["distinct_pre_touch_official_low_missing_dates"] < 5
            or type(planned) is not list or len(planned) != 5
            or len(set(planned)) != 5
            or any(type(d) is not str or len(d) != 10 for d in planned)
            or report["bounded_next_issuer_low_observations"] < 5
            or report.get("maximum_official_krx_api_requests_if_executed") != 10
            or any(report.get(key) is not False for key in (
                "first_touch_earliest_low_certified",
                "all_prior_official_lows_covered",
                "regular_session_volume_independently_verified",
                "historical_pit_scores_certified",
                "performance_validated", "v30b_release_ready"))):
        return False
    if "independently_authenticated_new_low_dates" in report:
        common = (report.get("prior_official_pre_touch_lows") == 168
                  and report.get("pre_touch_public_traded_holder_days") == 752
                  and report.get("offline_replay_new_krx_market_api_requests") == 0)
        batch_count = report.get("independently_authenticated_new_low_dates")
        if batch_count == 5:
            consistent = (
                report.get("independently_authenticated_new_pre_touch_holder_lows") == 47
                and report.get("pre_touch_holder_days_on_existing_official_ohlc_dates") == 215
                and report.get("pre_touch_holder_days_without_existing_official_ohlc_date") == 537
                and report.get("unverified_pre_touch_holder_lows_remaining") == 537
                and report.get("previously_authenticated_official_marketwide_ohlc_dates") == 49
                and report.get("distinct_pre_touch_official_low_missing_dates") == 189)
        elif batch_count == 10:
            consistent = (
                report.get("independently_authenticated_new_pre_touch_holder_lows") == 82
                and report.get("second_batch_additional_original_successor_lows") == 35
                and report.get("pre_touch_holder_days_on_existing_official_ohlc_dates") == 250
                and report.get("pre_touch_holder_days_without_existing_official_ohlc_date") == 502
                and report.get("unverified_pre_touch_holder_lows_remaining") == 502
                and report.get("previously_authenticated_official_marketwide_ohlc_dates") == 54
                and report.get("distinct_pre_touch_official_low_missing_dates") == 184)
        else:
            consistent = False
        if not (common and consistent):
            return False
    return True


def verified_frozen_coverage(public_panels, baseline_days, root=ROOT):
    """Reproduce the newly authenticated five-day LOW evidence without API calls.

    Every five-day/47-security observation was compared against the all-market
    official OHLC in run #45093498230. This function rechecks that it matches
    the deterministic already-existing 44-day worklist and fixed public data
    BEFORE promoting those days to observed official OHLC days. No new KRX
    market calls and no individual issuer facts are ever emitted.
    """
    initial = prepare(public_panels, baseline_days)
    frozen = json.loads((root / "ops" /
                         "v3-historical-pre-first-low-official-20261123.json"
                         ).read_text(encoding="utf-8"))
    require(type(frozen) is dict
            and frozen.get("schema") == "v3-historical-pre-first-low-official-frozen-v1"
            and frozen.get("saved_on_kst") == "2026-11-23"
            and frozen.get("run_url") ==
                "https://github.com/ykvic1/korean-stock-screener/actions/runs/45093498230"
            and (frozen.get("run_id"), frozen.get("job_id"),
                 frozen.get("artifact_id"), frozen.get("artifact_sha256")) == FROZEN_RUN
            and frozen.get("claim_frozen_readonly") is True,
            "unauthenticated_frozen_pre_first_low_source")
    proof = frozen.get("result")
    require(type(proof) is dict and proof.get("schema") == PROBE_SCHEMA
            and proof.get("source_public_input_sha256") == list(SOURCE_DIGESTS)
            and proof.get("planning") == initial["report"]
            and proof.get("scope_dates") ==
                [d.isoformat() for d in initial["batch"]]
            and proof.get("official_low_holder_days_exact") == 47
            and proof.get("new_market_api_requests_max") == 10
            and all(proof.get(key) is False for key in (
                "has_verified_all_pre_first_touch_holder_lows",
                "performance_validated", "v30b_release_ready")),
            "tampered_or_stale_five_day_low_evidence")
    rows = proof.get("official")
    require(type(rows) is dict and set(rows) == set(proof["scope_dates"])
            and len(rows) == FROZEN_INCREMENTAL_DAYS,
            "incomplete_frozen_original_successor_low_dates")
    already = set()
    total = 0
    for on, item in rows.items():
        targets = initial["uncovered"][day(on)]
        hashes = item.get("official_source_sha256")
        require(type(hashes) is dict and set(hashes) == {"KOSPI", "KOSDAQ"}
                and all(type(value) is str and
                        re.fullmatch("[a-f0-9]{64}", value)
                        and value not in already for value in hashes.values())
                and hashes["KOSPI"] != hashes["KOSDAQ"],
                "invalid_new_pre_first_low_krx_source_digest")
        already.update(hashes.values())
        require(item.get("exact_original_or_successor_pre_first_lows") ==
                    len(targets)
                and type(item.get("marketwide_total_rows")) is int
                and item["marketwide_total_rows"] > 2600
                and type(item.get("marketwide_traded_exact_ohlc")) is int
                and type(item.get("marketwide_untraded_rows")) is int
                and item["marketwide_traded_exact_ohlc"] > 2000
                and item["marketwide_traded_exact_ohlc"] +
                    item["marketwide_untraded_rows"] ==
                    item["marketwide_total_rows"]
                and item.get("no_prior_official_low_breach_on_this_date") is True,
                "frozen_pre_first_low_price_or_marketwide_mismatch")
        total += len(targets)
    require(total == FROZEN_INCREMENTAL_HOLDER_LOWS,
            "unexpected_official_direct_prior_low_scope")
    extended = prepare(public_panels, baseline_days | {day(v) for v in rows})
    require(extended["report"]["pre_touch_public_traded_holder_days"] ==
            initial["report"]["pre_touch_public_traded_holder_days"]
            and extended["report"]["pre_touch_holder_days_on_existing_official_ohlc_dates"]
                == initial["report"]["pre_touch_holder_days_on_existing_official_ohlc_dates"] + 47
            and extended["report"]["pre_touch_holder_days_without_existing_official_ohlc_date"]
                == initial["report"]["pre_touch_holder_days_without_existing_official_ohlc_date"] - 47
            and len(extended["uncovered"]) == len(initial["uncovered"]) - 5,
            "new_krx_low_evidence_did_not_reduce_unverified_gap")
    extended["report"].update({
        "independently_authenticated_new_low_dates": FROZEN_INCREMENTAL_DAYS,
        "independently_authenticated_new_pre_touch_holder_lows": total,
        "prior_official_pre_touch_lows": initial["report"][
            "pre_touch_holder_days_on_existing_official_ohlc_dates"],
        "unverified_pre_touch_holder_lows_remaining":
            extended["report"]["pre_touch_holder_days_without_existing_official_ohlc_date"],
        "offline_replay_new_krx_market_api_requests": 0,
        "first_touch_earliest_low_certified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    })
    return extended


def check_official_session(public, raw, hashes, targets):
    """Anonymous exact-security LOW at original/successor holder ratio."""
    official = {symbol: daily_row(market, data)
                for symbol, (market, data) in raw.items()}
    market = compare_market(public, official, public.keys() | official.keys())
    wanted = {symbol for origin, symbol, units, line in targets}
    selected = compare_market(public, official, wanted)
    require(exact_market_prices(market)
            and selected["scope"] == len(wanted)
            and exact_traded_extreme_symbols(selected),
            "unmatched_krx_pre_first_low_ohlc_or_market_identity")
    require(type(hashes) is dict and set(hashes) == {"KOSPI", "KOSDAQ"}
            and all(type(value) is str and len(value) == 64
                    and all(c in "0123456789abcdef" for c in value)
                    for value in hashes.values())
            and hashes["KOSPI"] != hashes["KOSDAQ"],
            "untrusted_pre_first_low_official_source_digest")
    for origin, code, units, line in targets:
        krx = official[code]
        low = _amount(krx[5]) * units
        require(krx[2] == "traded"
                and low > line
                and (low.quantize(UNIT, rounding=ROUND_HALF_UP)
                     == (Decimal(str(public[code][5])) * units).quantize(
                         UNIT, rounding=ROUND_HALF_UP)),
                "official_low_breaches_pre_first_low_reference")
    return {
        "official_source_sha256": hashes,
        "marketwide_traded_exact_ohlc": market["exact_ohlc"],
        "marketwide_untraded_rows": market["no_positive_trade_on_either"],
        "marketwide_total_rows": market["scope"],
        "exact_original_or_successor_pre_first_lows": len(targets),
        "no_prior_official_low_breach_on_this_date": True,
    }


def check_bounded_batch(panels, prepared, client):
    """Maximum FIVE genuinely new dates; no provider call in offline mode."""
    days = prepared["batch"]
    require(len(days) == BATCH_LIMIT
            and all(day in prepared["uncovered"] for day in days),
            "unapproved_krx_pre_first_low_probe_batch")
    observed, all_digests = {}, set()
    for on in days:
        raw, hashes = read_official_openings(client, on.isoformat())
        result = check_official_session(
            panels[on], raw, hashes, prepared["uncovered"][on])
        require(all(digest not in all_digests for digest in hashes.values()),
                "reused_krx_pre_first_low_source_digest")
        all_digests.update(hashes.values())
        observed[on.isoformat()] = result
    return {
        "schema": PROBE_SCHEMA,
        "source_public_input_sha256": list(SOURCE_DIGESTS),
        "planning": prepared["report"],
        "scope_dates": [on.isoformat() for on in days],
        "official": observed,
        "new_market_api_requests_max": BATCH_LIMIT * 2,
        "official_low_holder_days_exact": sum(
            value["exact_original_or_successor_pre_first_lows"]
            for value in observed.values()),
        "has_verified_all_pre_first_touch_holder_lows": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        panels = read_panels(args.sources)
        known = saved_official_ohlc_dates()
        if args.probe:
            # One-shot first batch was already authenticated. Refuse replay
            # rather than unknowingly using another ten provider requests.
            require(not FROZEN.exists(),
                    "frozen_first_low_five_day_probe_already_completed")
            prepared = prepare(panels, known)
            key = os.environ.get("KRX_API_KEY", "")
            require(bool(key.strip()), "official_krx_key_unavailable")
            result = check_bounded_batch(panels, prepared, KRXClient(key))
        else:
            result = verified_frozen_coverage(panels, known)["report"]
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        # Emit only a fixed-format reason identifier. No ticker or quote.
        reason = str(exc)
        if not re.fullmatch(r"[a-z][a-z0-9_]{5,99}", reason):
            reason = "invalid_or_unexpected_input"
        parser.exit(1, "historical_pre_first_low_official_gap_audit_failed:"
                    + reason + "\\n")

if __name__ == "__main__":
    main()
