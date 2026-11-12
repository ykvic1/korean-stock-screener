"""Append-only KRX actual-money derived tail after the frozen 10-year source end.

The historical 490-cohort archive deliberately has 2 unused session candidates.
This module bridges those into the FIRST forward cohort without changing the
recent-first 2016-2026 historical checkpoint. A growing trading-day candidate
calendar cannot rewrite any already committed window: prefix hash is pinned.
Only nonreconstructive private five-session factors, never raw daily prices or
trading values. KRX EOD remains UNVERIFIED regular-session-only.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import os
import time as retry_clock
from zoneinfo import ZoneInfo

from official_probe.clients import KRXClient, KRXProbeError
from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_session_candidates import candidate_dates
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_krx_money_backfill import (
    END as HIST_END, history_candidates, MAX_DERIVED_BYTES, _canonical,
    _path, _safe_source_failure_diagnostics, _safe_private_failure_diagnostics,
)
from v3_krx_money_5day import store_money_five_days, validate_money_blob

SCHEMA = "v3-krx-private-actual-money-forward-checkpoint-v1"
CHECKPOINT = "derived/money-forward-checkpoint.json"
PRIVATE_REPO = "ykvic1/korean-stock-private-data"
MAX_WINDOWS_PER_RUN = 8


class ForwardMoneyError(ValueError):
    """Static private archive or source failure code only."""


def forward_candidates(asof):
    if type(asof) is not str:
        raise ForwardMoneyError("invalid_forward_cutoff")
    try:
        day = date.fromisoformat(asof)
    except ValueError:
        raise ForwardMoneyError("invalid_forward_cutoff") from None
    if (day.isoformat() != asof or day <= date.fromisoformat(HIST_END)
        or day > datetime.now(ZoneInfo("Asia/Seoul")).date()):
        raise ForwardMoneyError("invalid_forward_cutoff")
    historical, _ = history_candidates()
    if len(historical) < 5 or historical[-1] != HIST_END:
        raise ForwardMoneyError("unexpected_historical_anchor")
    residue = historical[5 * (len(historical) // 5):]
    if not 0 < len(residue) < 5:
        raise ForwardMoneyError("historical_tail_residue_not_validated")
    beginning = (date.fromisoformat(HIST_END) + timedelta(days=1)).isoformat()
    weekdays = calendar_weekday_candidates(beginning, asof)
    follow = candidate_dates(weekdays, source="xkrx")["dates"]
    dates = residue + follow
    if dates != sorted(set(dates)) or len(dates) > 6000:
        raise ForwardMoneyError("forward_calendar_not_append_only")
    return dates


def _prefix(dates, index):
    return sha256(_canonical(dates[:index * 5])).hexdigest()


def _empty():
    return {
        "schema": SCHEMA,
        "historical_anchor_end": HIST_END,
        "next_window_index": 0,
        "completed_candidate_prefix_sha256": _prefix([], 0),
        "issuer_cohort_records": 0,
        "compressed_factor_bytes": 0,
        "last_window_file_sha256": None,
        "raw_source_file_count": 0,
    }


def _manifest(raw, dates):
    if raw is None:
        return _empty()
    try:
        state = json.loads(raw)
    except Exception:
        raise ForwardMoneyError("invalid_forward_checkpoint_json") from None
    index = state.get("next_window_index")
    if (state.get("schema") != SCHEMA
        or state.get("historical_anchor_end") != HIST_END
        or type(index) is not int or not 0 <= index <= len(dates) // 5
        or state.get("completed_candidate_prefix_sha256") != _prefix(dates, index)
        or type(state.get("issuer_cohort_records")) is not int
        or not 0 <= state["issuer_cohort_records"] <= 25000 * index
        or type(state.get("compressed_factor_bytes")) is not int
        or not 0 <= state["compressed_factor_bytes"] <= MAX_DERIVED_BYTES
        or state.get("raw_source_file_count") != 0
        or (index == 0 and state.get("last_window_file_sha256") is not None)
        or (index > 0 and
            (type(state.get("last_window_file_sha256")) is not str
             or len(state["last_window_file_sha256"]) != 64))):
        raise ForwardMoneyError("invalid_forward_checkpoint")
    return state


def _verify_tail(store, state, dates):
    count = state["next_window_index"]
    if count:
        window = dates[5 * (count-1):5 * count]
        blob, _ = store.get_content(_path(window))
        if (type(blob) is not bytes or
            sha256(blob).hexdigest() != state["last_window_file_sha256"]):
            raise ForwardMoneyError("forward_private_tail_missing_or_changed")
        validate_money_blob(blob, window[0], window[-1])


def _commit_checkpoint(store, updated, previous_sha):
    payload = _canonical(updated)
    if len(payload) > 1200:
        raise ForwardMoneyError("forward_checkpoint_too_large")
    if previous_sha is None:
        store.put_new(CHECKPOINT, payload, "Create private actual-money forward checkpoint")
    else:
        store.transport("PUT", "contents/" + CHECKPOINT, {
            "message": "Advance private actual-money forward checkpoint",
            "content": base64.b64encode(payload).decode("ascii"),
            "sha": previous_sha,
        })
    for attempt in range(4):
        try:
            observed, verified_sha = store.get_content(CHECKPOINT)
        except Exception:
            observed, verified_sha = None, None
        if observed == payload and isinstance(verified_sha, str) and verified_sha:
            return verified_sha
        if attempt < 3:
            retry_clock.sleep(.7 * (attempt+1))
    raise ForwardMoneyError("forward_checkpoint_readback_failed")


def collect(store, client, dates, windows=MAX_WINDOWS_PER_RUN):
    if (store.repository != PRIVATE_REPO
        or type(dates) is not list or dates != sorted(set(dates))
        or len(dates) < 2 or len(dates) > 6000
        or type(windows) is not int or not 1 <= windows <= MAX_WINDOWS_PER_RUN):
        raise ForwardMoneyError("invalid_forward_collector_arguments")
    store.verify_private()
    saved, version = store.get_content(CHECKPOINT)
    state = _manifest(saved, dates)
    _verify_tail(store, state, dates)
    begin = state["next_window_index"]
    calls, adopted = 0, 0
    for index in range(begin, min(begin + windows, len(dates)//5)):
        window = dates[5*index:5*(index+1)]
        blob, _ = store.get_content(_path(window))
        if blob is None:
            result = store_money_five_days(store, client, window)
            if result.get("status") != "persisted_and_verified_money":
                raise ForwardMoneyError("forward_new_cohort_not_verified")
            calls += result["source_api_calls"]
            blob, _ = store.get_content(_path(window))
            if not isinstance(blob, bytes):
                raise ForwardMoneyError("forward_new_cohort_missing_after_write")
        else:
            adopted += 1
        factors = validate_money_blob(blob, window[0], window[-1])
        new_bytes = state["compressed_factor_bytes"] + len(blob)
        if new_bytes > MAX_DERIVED_BYTES:
            raise ForwardMoneyError("forward_private_archive_size_budget")
        updated = {
            "schema": SCHEMA,
            "historical_anchor_end": HIST_END,
            "next_window_index": index + 1,
            "completed_candidate_prefix_sha256": _prefix(dates, index+1),
            "issuer_cohort_records": (state["issuer_cohort_records"] +
                                      factors["complete_five_session_issuer_market_pairs"]),
            "compressed_factor_bytes": new_bytes,
            "last_window_file_sha256": sha256(blob).hexdigest(),
            "raw_source_file_count": 0,
        }
        version = _commit_checkpoint(store, updated, version)
        state = updated
    return {
        "status": "private_money_forward_checkpoint_verified",
        "completed_forward_five_session_cohorts": state["next_window_index"],
        "forward_candidate_sessions_observed": len(dates),
        "forward_candidate_complete_cohorts": len(dates)//5,
        "forward_open_tail_candidate_sessions": len(dates)%5,
        "current_run_api_calls": calls,
        "current_run_immutable_reused": adopted,
        "complete_issuer_cohort_factors": state["issuer_cohort_records"],
        "raw_vendor_source_files": 0,
        "regular_session_only_verified": False,
        "issuer_historical_identity_verified": False,
        "corporate_action_price_adjustments_verified": False,
        "new_money_in_current_investment_scores": False,
        "full_v3_score_certified": False,
    }


class RetryingMoneyClient:
    """Bounded transient GET/empty-data retry; no auth/quota/schema retries.

    Some KRX responses are HTTP 200 but contain an empty OutBlock_1.
    Retry only a bounded number of times: a persistently empty market/day
    still fails closed without committing an immutable money cohort.
    """
    def __init__(self, client):
        self.client=client
        self.requests=0
    def daily_market(self, market, day):
        for attempt in range(3):
            if self.requests>=240:
                raise ForwardMoneyError('forward_source_request_budget_exceeded')
            self.requests+=1
            try:
                return self.client.daily_market(market,day)
            except KRXProbeError as error:
                if attempt==2 or error.reason not in ('empty_body','empty_market_rows',
                                                         'transport_failure','http_server_error'):
                    raise
                retry_clock.sleep(attempt+1)


def main():
    if (not os.getenv("KRX_API_KEY", "").strip()
        or os.getenv("V3_PRIVATE_ARCHIVE_REPO") != PRIVATE_REPO
        or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN", "").strip()):
        print('{"status":"blocked","category":"private_money_credentials_missing"}')
        return 1
    stage = "cutoff"
    try:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        cutoff = now.date() if now.time() >= time(18, 0) else now.date()-timedelta(days=1)
        dates = forward_candidates(cutoff.isoformat())
        stage = "private_forward_collection"
        stored = PrivateGitStorage(PRIVATE_REPO, os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        client=RetryingMoneyClient(KRXClient(os.environ["KRX_API_KEY"]))
        result = collect(stored, client, dates,
                         windows=int(os.getenv("V3_MONEY_FORWARD_WINDOWS", "8")))
        result["current_run_source_requests_including_retries"]=client.requests
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        known = isinstance(exc, ForwardMoneyError)
        safe_reason=exc.reason if isinstance(exc,KRXProbeError) else None
        print(json.dumps({
            "status": "blocked",
            "category": str(exc) if known else "untrusted_forward_source_error",
            "krx_allowlisted_failure_reason":safe_reason,
            "http_status":exc.http_status if isinstance(exc,KRXProbeError) else None,
            "stage": stage,
            "source_payload_logged": False,
            **_safe_source_failure_diagnostics(exc),
            **_safe_private_failure_diagnostics(exc),
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
