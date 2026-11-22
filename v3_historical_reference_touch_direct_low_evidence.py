"""Read-only audit of 11 first-touch LOW holder-value comparisons vs KRX.

Archived official aggregates are compressed only to preserve the two complete
10KB authenticated responses while keeping Git history compact; no private
security or price rows are exposed. Source-digest/replay checks and immutable
Git blob hashes constrain this proof to the exact frozen historical sample.
A quote on a reported first-touch date does NOT prove prior-day noncontact.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import zlib

from v3_historical_downside_official_ohlc_evidence import (
    EVIDENCE as BASELINE_PATH, evidence_sessions, verified_market_session,
)
from v3_historical_prior_direct_low_evidence import (
    EVIDENCE as PRIOR_PATH, build as prior_proof,
)
from v3_historical_extreme_direct_low_evidence import (
    EVIDENCE as LATER_PATH, build as later_proof,
)
from v3_historical_reference_touch_direct_probe import pending_first_touch_targets
from v3_historical_pilot_official_prices import exact_traded_extreme_symbols
from v3_historical_pilot_daily_replay import SOURCE_DIGESTS

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "ops/v3-historical-first-touch-official-low-20261122.json"
SCHEMA = "v3-historical-first-touch-official-low-frozen-v1"
REPORT_SCHEMA = "v3-historical-reference-touch-official-low-v1"
HEX = re.compile(r"[a-f0-9]{64}\Z")
HEX_SHA1 = re.compile(r"[a-f0-9]{40}\Z")
PROVENANCE = {
    0: (45020053953, 137200734955, 14753193394,
        "ed650ee8c8f388271f88000dd93ccd01e096cadc896ca9badc4929d65dfed277",
        "37d4b994d2444d04ba277add41863c96a934e02911fcf9d5628854f081d15181"),
    1: (45020053953, 137200734777, 14752964248,
        "efd332812399c344671eb32d5feca5d125fd18c091fda28a4d4245b12626c0f2",
        "088d723c3893600c1cf9bd5388af10d46815d80487494007d13fa96c4b78fe27"),
}
SOURCE_LEDGERS = (
    "ops/v3-performance-downside-reference-20251113.csv",
    "ops/v3-performance-delisting-trade-observations-20251113.csv",
    "ops/v3-performance-delisting-settlement-20251113.csv",
    "ops/v3-performance-downside-horizons-20251113.csv",
)


class ReferenceTouchEvidenceError(ValueError):
    """Only sanitized fixed error codes, never issuer-level raw content."""


def require(condition, reason):
    if not condition:
        raise ReferenceTouchEvidenceError(reason)


def checked_digest(raw):
    require(type(raw) is str and HEX.fullmatch(raw), "invalid_source_digest")
    return raw


def _git_blob_sha1(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _unpack(group):
    payload = group.get("data")
    require(type(payload) is str and 0 < len(payload) <= 15000
            and re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", payload),
            "invalid_frozen_touch_payload")
    try:
        zipped = base64.b64decode(payload, validate=True)
        require(len(zipped) <= 15000, "oversized_touch_payload")
        inflate = zlib.decompressobj(wbits=31)
        content = inflate.decompress(zipped, 90000)
        require(len(content) <= 90000 and inflate.eof
                and not inflate.unused_data and not inflate.unconsumed_tail,
                "invalid_compressed_touch_source")
        data = json.loads(content)
    except (ValueError, TypeError, zlib.error, binascii.Error, UnicodeError):
        raise ReferenceTouchEvidenceError("invalid_compressed_touch_source") from None
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    require(hashlib.sha256(canonical).hexdigest()
                == group.get("canonical_report_sha256"),
            "stored_touch_report_hash_changed")
    return data


def _historical_sources(baseline, prior, later):
    baseline_dates, _, _, _ = evidence_sessions(baseline)
    require(prior_proof(prior, baseline, later)[
                "all_85_horizon_extreme_dates_directly_checked"] == 85,
            "not_all_extreme_date_lows_verified")
    require(later_proof(later, baseline)[
                "new_direct_low_compared_dates"] == 27,
            "later_direct_low_proof_missing")
    original = {}
    for batch in baseline["batches"]:
        for day, row in batch["sessions"].items():
            source = row["official_source_sha256"]
            require(day not in original, "duplicated_baseline_official_day")
            original[day] = source
    require(set(original) == set(baseline_dates), "incomplete_baseline_source_days")
    for evidence in (prior, later):
        for batch in evidence["batches"]:
            for day, row in batch["result"]["sessions"].items():
                source = row["official_source_sha256"]
                require(day not in original or original[day] == source,
                        "changed_official_same_day_source")
                original[day] = source
    require(len(original) >= 41, "incomplete_prior_source_roster")
    return original


def build(evidence, baseline, prior, later, root=ROOT):
    require(type(evidence) is dict and evidence.get("schema") == SCHEMA
            and evidence.get("saved_on_kst") == "2026-11-22"
            and evidence.get("audit_scope")
                == "eleven_previously_unverified_specific_touch_security_days_only"
            and evidence.get("prior_35_extreme_low_evidence_required") is True
            and evidence.get("archive_content_encoding")
                == "gzip_base64_canonical_json"
            and evidence.get("official_run_url")
                == "https://github.com/ykvic1/korean-stock-screener/actions/runs/45020053953"
            and evidence.get("market_wide_numeric_equivalence_is_not_0900_1530_session_certification")
                is True
            and all(evidence.get(key) is False for key in (
                "first_touch_earliest_session_proven", "full_adjusted_path_verified",
                "historical_point_in_time_scores_certified",
                "performance_validated", "v30b_release_ready")),
            "unauthorized_first_touch_promotion")
    sources = evidence.get("source_git_blob_sha1")
    require(type(sources) is dict and set(sources) == set(SOURCE_LEDGERS),
            "missing_frozen_reference_ledger_provenance")
    for file in SOURCE_LEDGERS:
        expected = sources[file]
        require(type(expected) is str and HEX_SHA1.fullmatch(expected)
                and _git_blob_sha1((root / file).read_bytes()) == expected,
                "historical_reference_or_action_ledger_changed")
    official_by_day = _historical_sources(baseline, prior, later)
    target = pending_first_touch_targets(root)
    expected_dates = sorted(target)
    require(len(expected_dates) == 10
            and sum(len(t) for t in target.values()) == 11,
            "incomplete_frozen_first_touch_selection")
    frozen = evidence.get("groups")
    require(type(frozen) is list and len(frozen) == 2
            and {g.get("group") for g in frozen if type(g) is dict} == {0, 1},
            "invalid_frozen_touch_group_roster")
    used_official_sources = {
        digest: day for day, fingerprints in official_by_day.items()
        for digest in fingerprints.values()
    }
    require(len(used_official_sources) >= 82, "reused_earlier_market_fingerprint")
    verified_days, matched = set(), 0
    market_rows = market_exact = market_zero = 0
    for group in frozen:
        group_id = group["group"]
        require((group.get("run_id"), group.get("job_id"),
                 group.get("artifact_id"), group.get("artifact_zip_sha256"),
                 group.get("canonical_report_sha256")) == PROVENANCE[group_id],
                "unknown_authenticated_touch_artifact")
        proof = _unpack(group)
        expected = expected_dates[:5] if group_id == 0 else expected_dates[5:]
        require(type(proof) is dict and proof.get("schema") == REPORT_SCHEMA
                and proof.get("requested_sessions") == expected
                and proof.get("maximum_official_api_requests") == 2 * len(expected)
                and proof.get("target_security_days")
                     == sum(len(target[d]) for d in expected)
                and proof.get("verified_security_days") == proof["target_security_days"]
                and all(proof.get(flag) is False for flag in (
                    "first_touch_is_earliest_trade_proven",
                    "pre_touch_complete_adjusted_path_verified",
                    "regular_only_volume_verified",
                    "performance_validated", "v30b_release_ready")),
                "unverified_or_overclaimed_touch_batch")
        sessions = proof.get("sessions")
        require(type(sessions) is dict and set(sessions) == set(expected),
                "missing_first_touch_official_session")
        for day, row in sessions.items():
            require(day not in verified_days and type(row) is dict,
                    "replayed_reference_touch_date")
            verified_days.add(day)
            require(row.get("schema") == REPORT_SCHEMA
                    and row.get("reported_touch_security_days") == len(target[day])
                    and row.get("touch_holder_open_low_exact") == len(target[day])
                    and all(row.get(flag) is False for flag in (
                        "first_touch_is_earliest_trade_proven",
                        "pre_touch_complete_adjusted_path_verified",
                        "regular_only_volume_verified",
                        "performance_validated", "v30b_release_ready")),
                    "reference_touch_holder_value_not_verified")
            values = row.get("official_source_sha256")
            require(type(values) is dict and set(values) == {"KOSPI", "KOSDAQ"}
                    and all(type(val) is str and HEX.fullmatch(val)
                            for val in values.values()),
                    "missing_official_touch_market_digest")
            if day in official_by_day:
                require(values == official_by_day[day],
                        "existing_official_touch_day_digest_changed")
            for digest in values.values():
                require(digest not in used_official_sources or
                        used_official_sources[digest] == day,
                        "different_date_reused_official_touch_digest")
                used_official_sources[digest] = day
            market = row.get("market_wide")
            scope, exact, zero = verified_market_session(market)
            touch = row.get("actual_touch_security_ohlc")
            require(type(touch) is dict and touch.get("scope") == len(target[day])
                    and exact_traded_extreme_symbols(touch),
                    "different_security_ohlc_or_touch_low")
            turnover = row.get("turnover")
            require(type(turnover) is dict and turnover.get("scope") == scope
                    and turnover.get("identity_mismatch") == 0
                    and turnover.get("numeric_equivalence_is_not_session_certification") is True,
                    "inconsistent_touch_turnover_scope")
            for field in ("volume", "amount"):
                part = turnover.get("fields", {}).get(field, {})
                require(type(part) is dict
                        and part.get("compared") == scope
                        and part.get("matched") == scope
                        and part.get("mismatched") == 0
                        and part.get("public_unavailable") == 0
                        and part.get("official_unavailable") == 0,
                        "unmatched_official_touch_turnover")
            market_rows += scope
            market_exact += exact
            market_zero += zero
            matched += len(target[day])
    require(set(expected_dates) == verified_days
            and matched == 11 and market_rows == 27707
            and market_exact == 26660 and market_zero == 1047,
            "historical_reference_touch_scope_regressed")
    return {
        "schema": "v3-historical-reference-touch-direct-low-coverage-v1",
        "frozen_first_touch_records": 14,
        "prior_first_touch_security_days_on_direct_extreme_low_dates": 3,
        "newly_verified_first_touch_security_days": matched,
        "unique_new_official_first_touch_dates": len(expected_dates),
        "all_reported_first_touch_dates_with_exact_security_ohlc": 13,
        "all_reported_first_touch_security_days_with_exact_ohlc": 14,
        "new_official_marketwide_issuer_day_pairs": market_rows,
        "new_official_exact_traded_ohlc": market_exact,
        "new_official_no_positive_trade": market_zero,
        "new_provider_calls_in_readonly_replay": 0,
        "all_prior_sessions_prove_first_touch_was_earliest": False,
        "first_touch_is_earliest_trade_proven": False,
        "pre_touch_complete_adjusted_path_verified": False,
        "regular_only_volume_verified": False,
        "historical_point_in_time_scores_certified": False,
        "performance_validated": False,
        "v30b_release_ready": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args(argv)
    try:
        proof = json.loads(args.evidence.read_text(encoding="utf-8"))
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        prior = json.loads(PRIOR_PATH.read_text(encoding="utf-8"))
        later = json.loads(LATER_PATH.read_text(encoding="utf-8"))
        report = build(proof, baseline, prior, later)
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        parser.exit(1, "historical_reference_touch_direct_low_evidence_invalid\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
