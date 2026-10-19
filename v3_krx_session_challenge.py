"""Manual bounded KRX-vs-XKRX candidate-day challenge, no raw data retention.

An observed KRX two-market response supports *that dated observation only*.
An empty response does NOT authenticate an official closure or its reason.
An XKRX holiday is third-party research input, never a source of market truth.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path

from official_probe.clients import KRXClient, KRXProbeError
from v3_krx_history_collection import (
    validate_dates, _snapshot_market, HistoryCollectionError, MARKETS)
from v3_krx_private_backfill_plan import calendar_weekday_candidates, BackfillPlanError
from v3_krx_session_candidates import candidate_dates, CandidateCalendarError

MAX_CHALLENGE_DATES = 5

class SessionChallengeError(ValueError):
    """Static error codes only, never provider text or credentials."""


def _status_for_market(provider, market, day):
    try:
        rows, raw = provider.daily_market(market, day)
        inspected = _snapshot_market(rows, raw, market=market, asof=day)
        return {
            "market": market, "source_state": "observed_matching_krx_day",
            "source_sha256": inspected["source_sha256"],
            "provider_rows": inspected["provider_rows_observed"],
            "unpriced_rows": inspected["quarantined_unpriced_candidates"],
        }
    except KRXProbeError as error:
        if error.reason == "empty_market_rows":
            return {"market": market, "source_state": "empty_krx_rows_unverified"}
        return {"market": market, "source_state": "krx_transport_or_schema_error",
                "reason": error.reason, "http_status": error.http_status}
    except (HistoryCollectionError, ValueError, TypeError, KeyError):
        # Refuse to treat a malformed or stale response as evidence of a
        # closure OR an open day. Even a KRX HTTP 200 can carry wrong dates.
        return {"market": market, "source_state": "krx_row_contract_rejected"}
    except Exception:
        # Provider exceptions may contain raw authorization headers or URL.
        return {"market": market, "source_state": "unclassified_provider_error"}


def _interpret(expect_xkrx_session, market_items):
    states = [x["source_state"] for x in market_items]
    if states == ["observed_matching_krx_day"] * 2:
        if expect_xkrx_session:
            return "paired_date_observed_in_krx"
        return "xkrx_excluded_day_has_two_market_observations"
    if states == ["empty_krx_rows_unverified"] * 2:
        if not expect_xkrx_session:
            return "xkrx_exclusion_and_krx_empty_unverified"
        return "xkrx_session_but_two_market_empty_unverified"
    if set(states) <= {"observed_matching_krx_day", "empty_krx_rows_unverified"}:
        return "two_markets_disagree_or_partial_day"
    return "source_error_or_unverified_response"


def inspect_days(days, *, live=False, key=None, client=None):
    try:
        valid = validate_dates(days)
    except HistoryCollectionError:
        raise SessionChallengeError("invalid_calendar_challenge_dates") from None
    if len(valid) > MAX_CHALLENGE_DATES:
        raise SessionChallengeError("too_many_calendar_challenges")
    try:
        # A holiday-only request still needs valid surrounding XKRX sessions.
        # Backward-only padding never reaches a future, unfinished day.
        padded_start = (date.fromisoformat(valid[0])-timedelta(days=10)).isoformat()
        base = calendar_weekday_candidates(padded_start, valid[-1])
        candidate = candidate_dates(base, source="xkrx")
    except (BackfillPlanError, CandidateCalendarError):
        raise SessionChallengeError("xkrx_candidate_calendar_unavailable") from None
    included = set(candidate["dates"])
    if live is not True:
        return {
            "schema":"v3-krx-calendar-challenge-0.1",
            "mode":"dry_run","dates":valid,
            "third_party_session_candidate_dates":[d for d in valid if d in included],
            "third_party_excluded_dates":[d for d in valid if d not in included],
            "max_krx_market_requests_if_live":len(valid)*2,
            "actual_krx_market_requests":0,
            "krx_day_evidence_observed":False,
            "official_calendar_whole_range_authenticated":False,
            "confirmed_scoring_allowed":False,
            "v3_release_ready":False,
        }
    if type(key) is not str or not key.strip():
        raise SessionChallengeError("krx_api_key_required_for_explicit_live")
    provider = client if client is not None else KRXClient(key)
    results=[]
    for day in valid:
        observations=[_status_for_market(provider,m,day) for m in MARKETS]
        status=_interpret(day in included,observations)
        results.append({
            "asof":day,"xkrx_candidate_session":day in included,
            "market_results":observations,"comparison":status,
            "not_entire_calendar_certified":True,
        })
    inconsistency = any(r["comparison"] not in (
        "paired_date_observed_in_krx",
        "xkrx_exclusion_and_krx_empty_unverified",
    ) for r in results)
    return {
        "schema":"v3-krx-calendar-challenge-0.1",
        "mode":"live","dates":valid,"results":results,
        "requested_krx_market_calls":2*len(valid),
        "observed_paired_date_count":sum(
            r["comparison"]=="paired_date_observed_in_krx" for r in results),
        "xkrx_excluded_dates_with_empty_market_rows":sum(
            r["comparison"]=="xkrx_exclusion_and_krx_empty_unverified" for r in results),
        "calendar_challenge_discrepancy_or_error":inconsistency,
        "official_calendar_whole_range_authenticated":False,
        "independent_krx_session_calendar_verified":False,
        "historical_250_sessions_verified":False,
        "regular_only_volume_verified":False,
        "confirmed_scoring_allowed":False,
        "v3_release_ready":False,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Private-counts-only bounded manual KRX calendar challenge")
    p.add_argument("--dates",required=True,help="1 to 5 complete weekday dates")
    p.add_argument("--live",action="store_true")
    p.add_argument("--output",help="Ignored research JSON in evidence/krx_history/ only")
    args=p.parse_args(argv)
    output = None
    try:
        if args.output:
            root=Path(__file__).resolve().parent
            safe=(root/"evidence"/"krx_history").resolve()
            requested_output=Path(args.output).resolve()
            if safe not in requested_output.parents:
                # Never assign a rejected path to output, even for error
                # diagnostics: fail-closed must not create an outside file.
                raise SessionChallengeError("calendar_summary_location_not_private")
            output=requested_output
        try:
            report=inspect_days(
                [v.strip() for v in args.dates.split(",")],
                live=args.live,key=os.environ.get("KRX_API_KEY"))
        except HistoryCollectionError:
            raise SessionChallengeError("invalid_calendar_challenge_dates") from None
        if output:
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,sort_keys=True),encoding="utf-8")
        # Never emit response bytes, issuer names or supplied API keys.
        print(json.dumps(report,sort_keys=True))
        return 1 if report.get("calendar_challenge_discrepancy_or_error") else 0
    except SessionChallengeError as err:
        msg={"status":"blocked","reason":str(err),
             "v3_release_ready":False,"confirmed_scoring_allowed":False}
        if output:
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(msg,sort_keys=True),encoding="utf-8")
        print(json.dumps(msg,sort_keys=True))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
