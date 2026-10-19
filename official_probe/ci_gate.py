"""Post-artifact workflow check: fail the job for a failed live KRX probe.

The sampling command intentionally returns a safe JSON report even when KRX
fails. GitHub Actions must not therefore show a misleading green check.
This module reads only that report, never provider credentials or payloads.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import re


class LiveProbeGateError(ValueError):
    """Static safe reason; never echo payload/path/private keys."""


def enforce_live_krx_result(report, *, mode, provider):
    if mode not in ("dry_run", "live") or provider not in ("krx", "dart", "both"):
        raise LiveProbeGateError("invalid_probe_job_selection")
    if mode != "live" or provider == "dart":
        return "not_a_live_krx_job"
    if not isinstance(report, dict):
        raise LiveProbeGateError("missing_probe_result")
    krx = report.get("krx")
    if not isinstance(krx, dict):
        raise LiveProbeGateError("missing_krx_result")
    status = krx.get("status")
    if status != "observed_unverified":
        raise LiveProbeGateError("live_krx_not_observed")
    markets = krx.get("markets")
    if not isinstance(markets, list) or not markets:
        raise LiveProbeGateError("missing_verified_market_sample")
    for market in markets:
        if (not isinstance(market, dict)
                or market.get("market") not in ("KOSPI", "KOSDAQ")
                or type(market.get("found_symbols")) is not list
                or not market["found_symbols"]):
            raise LiveProbeGateError("empty_krx_symbol_sample")
    if report.get("investment_data_ready") is not False or (
            report.get("confirmed_scoring_allowed") is not False):
        raise LiveProbeGateError("invalid_unsafe_score_promotion")
    return "krx_http_sample_observed_not_scoring_verified"


def cli(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dry_run", "live"), required=True)
    parser.add_argument("--provider", choices=("krx", "dart", "both"), required=True)
    parser.add_argument("--asof", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.asof):
        print("::error::KRX live probe gate: invalid_asof")
        return 1
    path = Path("evidence/official_probe") / ("probe_" + args.asof.replace("-", "") + ".json")
    try:
        # A fresh as-of-specific file only, not a stale previous artifact.
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("asof") != args.asof or report.get("mode") != args.mode:
            raise LiveProbeGateError("wrong_or_stale_probe_result")
        result = enforce_live_krx_result(report, mode=args.mode, provider=args.provider)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        print("::error::KRX live probe gate: missing_invalid_or_failed_probe")
        return 1
    print("::notice::KRX live probe gate: " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
