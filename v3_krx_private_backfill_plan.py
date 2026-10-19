"""Read-only chronology and resume planner for a KRX two-market PRIVATE archive.

No live API, secrets, source bytes, prices or score. A generated weekday is
a calendar CANDIDATE, never an independently authenticated KRX trade date.
Resume is only append-only: no front-insertion or skipped historic segments.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from v3_local_private_store import (archived_trade_dates, DEFAULT_DB,
                                    PrivateStoreError)
from v3_krx_session_candidates import candidate_dates, CandidateCalendarError

# Long-range *planning* capacity, not a retained-price lookback.
# 6000 weekday candidates and <= 8400 calendar days allow around 20+ years;
# a caller can inspect 5-10 years and older available history without trimming.
# Provider requests STILL remain at most five dates/two markets per batch.
MAX_PLANNED_WEEKDAYS = 6000
MAX_PLANNED_CALENDAR_DAYS = 8400
MAX_DAILY_CALLS = 10
# Legacy IAS long-window context ONLY, never a full-history cap.
TARGET_KRX_PAIRED_DAYS = 250


class BackfillPlanError(ValueError):
    """Safe fixed reason, no source values, filesystem paths or tokens."""


def calendar_weekday_candidates(start, end):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if (first.isoformat()!=start or last.isoformat()!=end or first>last):
            raise ValueError()
        today = datetime.now(ZoneInfo("Asia/Seoul"))
        if last > today.date() or (
                last == today.date() and today.time() < time(15,30)):
            raise BackfillPlanError("planned_date_not_yet_finished")
    except (TypeError, ValueError):
        raise BackfillPlanError("invalid_date_range") from None
    if (last-first).days>MAX_PLANNED_CALENDAR_DAYS:
        raise BackfillPlanError("candidate_range_over_limit")
    dates=[]
    current=first
    while current<=last:
        if current.weekday()<5:
            dates.append(current.isoformat())
        current+=timedelta(days=1)
    if not 1<=len(dates)<=MAX_PLANNED_WEEKDAYS:
        raise BackfillPlanError("candidate_count_outside_bounds")
    return dates


def plan_next_batch(candidates, *, already_archived=None, batch_size=5):
    if (type(batch_size) is not int or not 1<=batch_size<=5
            or not isinstance(candidates,list)
            or not 1<=len(candidates)<=MAX_PLANNED_WEEKDAYS):
        raise BackfillPlanError("invalid_plan_size")
    if (any(type(d) is not str or len(d)!=10 for d in candidates)
            or candidates!=sorted(set(candidates))):
        raise BackfillPlanError("candidates_not_strictly_ascending")
    for d in candidates:
        try:
            v=date.fromisoformat(d)
        except ValueError:
            raise BackfillPlanError("invalid_candidate_date") from None
        if v.isoformat()!=d or v.weekday()>=5:
            raise BackfillPlanError("invalid_candidate_date")
    historical=already_archived if already_archived is not None else []
    if (not isinstance(historical,list) or
            historical!=candidates[:len(historical)]):
        # Verified append-only source pair archive cannot have a date
        # inserted or rewritten in its middle. Require a new research DB.
        raise BackfillPlanError("archive_not_prefix_of_candidate_sequence")
    i=len(historical)
    following=candidates[i:i+batch_size]
    return {
        "schema":"v3-krx-private-backfill-plan-0.1",
        "candidate_calendar_weekdays":len(candidates),
        "archived_two_market_pairs_verified_internally":i,
        "next_batch_dates":following,
        "next_batch_max_calls":2*len(following),
        "remaining_weekday_candidates":len(candidates)-i,
        "historical_exchange_calendar_verified":False,
        "official_trading_days_250_verified":False,
        "corporate_action_price_history_verified":False,
        "regular_only_volume_verified":False,
        "private_archive_independent_anchor_verified":False,
        "v3_release_ready":False,
        "confirmed_buy":False,
    }


def plan_private_history(start,end,*,db_path=DEFAULT_DB,batch_size=5,
                         calendar_source="weekdays"):
    weekdays=calendar_weekday_candidates(start,end)
    try:
        calendar=candidate_dates(weekdays,source=calendar_source)
    except CandidateCalendarError:
        raise BackfillPlanError("calendar_candidate_generation_rejected") from None
    candidates=calendar["dates"]
    path=Path(db_path).expanduser()
    try:
        # This read never creates an empty database. Preserve zero state on
        # first run, enforce permissions/hash chain on all existing stores.
        days=archived_trade_dates(database_path=path) if path.exists() else []
    except (PrivateStoreError, OSError):
        raise BackfillPlanError("private_archive_integrity_or_permissions_invalid") from None
    return {
        **plan_next_batch(candidates,already_archived=days,batch_size=batch_size),
        **{k:v for k,v in calendar.items() if k!="dates"},
        "calendar_candidate_count":len(candidates),
        "full_window_calendar_weekdays":len(weekdays),
        "historical_exchange_calendar_verified":False,
        "official_trading_days_250_verified":False,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="KRX two-market append-only history planning (zero live calls)")
    p.add_argument("--start",required=True,help="earliest candidate YYYY-MM-DD")
    p.add_argument("--end",required=True,help="latest candidate YYYY-MM-DD")
    p.add_argument("--private-db",default=str(DEFAULT_DB))
    p.add_argument("--batch-size",type=int,default=5)
    p.add_argument("--calendar-source",choices=("weekdays","xkrx"),
                   default="weekdays",help="xkrx: third-party UNVERIFIED session candidates")
    args=p.parse_args(argv)
    try:
        report=plan_private_history(args.start,args.end,
                                    db_path=args.private_db,
                                    batch_size=args.batch_size,
                                    calendar_source=args.calendar_source)
        print(json.dumps(report,sort_keys=True))
        return 0
    except BackfillPlanError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc),
                          "v3_release_ready":False,"confirmed_buy":False}))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
