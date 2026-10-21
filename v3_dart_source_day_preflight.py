"""Offline market-day gate for separately scheduled DART annual collectors.

When the 18:30 KST market snapshot advances before the bounded annual DART
reference, defer provider calls rather than querying with mixed asof dates.
Only same-year, <=10-calendar-day legitimate source lag may be deferred.
Never rewrite a reference's historical date or mark a missing point as zero.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path

from v3_dart_rolling_book_reference import _inputs
from v3_dart_bulk_history import SCHEMA as BULK_SCHEMA


class SourceDayGateError(ValueError):
    """Fixed, non-sensitive source-contract errors."""


def _date(raw):
    if type(raw) is not str:
        raise SourceDayGateError("missing_source_reference_date")
    try:
        day=date.fromisoformat(raw)
    except ValueError:
        raise SourceDayGateError("invalid_source_reference_date") from None
    if day.isoformat()!=raw:
        raise SourceDayGateError("invalid_source_reference_date")
    return day


def check(market,catalog,reference,*,kind):
    """Return evidence-only readiness; an incompatible reference must error."""
    if kind not in ("annual","major"):
        raise SourceDayGateError("invalid_source_reference_kind")
    try:
        asof,members=_inputs(market,catalog)
    except (ValueError,KeyError,TypeError):
        raise SourceDayGateError("untrusted_current_market_census") from None
    if type(reference) is not dict:
        raise SourceDayGateError("untrusted_dart_reference")
    if kind=="annual":
        if (reference.get("market_listed_total")!=len(members)
            or reference.get("historical_point_in_time_verified") is not False
            or reference.get("official_exchange_pbr_verified") is not False
            or type(reference.get("observations")) is not list):
            raise SourceDayGateError("annual_reference_not_source_verified")
    else:
        expected=list(range(_date(asof).year-4,_date(asof).year))
        if (reference.get("schema")!=BULK_SCHEMA
            or reference.get("listed_total")!=len(members)
            or reference.get("fiscal_years")!=expected
            or reference.get("historical_PIT_source_certified") is not False
            or reference.get("raw_source_rows_published") is not False
            or type(reference.get("records")) is not dict):
            raise SourceDayGateError("major_reference_not_source_verified")
    market_day=_date(asof)
    reference_day=_date(reference.get("asof"))
    lag=(market_day-reference_day).days
    if (lag<0 or lag>10 or market_day.year!=reference_day.year):
        raise SourceDayGateError("invalid_or_expired_dart_source_lag")
    return {
        "market_asof":asof,"source_asof":reference_day.isoformat(),
        "source_kind":kind,"day_lag":lag,
        "ready":lag==0,
        "status":"same_market_day_source_ready" if lag==0
                 else "deferred_waiting_for_dart_same_market_day",
        "full_v3_100pt_certified":False,
        "extra_official_calls":0,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--docs",default="docs")
    p.add_argument("--kind",required=True,choices=("annual","major"))
    opt=p.parse_args(argv)
    base=Path(opt.docs)
    def read(path):
        return json.loads((base/path).read_text(encoding="utf8"))
    name=("v3-dart-book-reference.json" if opt.kind=="annual"
          else "v3-dart-bulk-history.json")
    result=check(read("data.json"),read("v22-market-catalog.json"),
                 read(name),kind=opt.kind)
    print(json.dumps(result,sort_keys=True))
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"],"a",encoding="utf8") as handle:
            handle.write("ready="+str(result["ready"]).lower()+"\n")
            handle.write("day_lag="+str(result["day_lag"])+"\n")


if __name__=="__main__":
    main()
