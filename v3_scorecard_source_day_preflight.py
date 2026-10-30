"""Gate mixed-date V3 all-listed CSV until annual and weekly ledgers catch up.

Independent volume/OBV current-market source continues immediately, even
when the DART annual and rotating-technical sources lag after 18:30 close.
A defer is NOT a source certification or an empty 100-point investment score.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
import os
from pathlib import Path

from v3_dart_bulk_history import SCHEMA as BULK, SCORE_SCHEMA as PARTS
from v3_weekly_rotation import SCHEMA as WEEKLY
from v3_dart_rolling_book_reference import _inputs
from v3_real_six_pillar_coverage_audit import coverage


class ScorecardReadinessError(ValueError):
    """Fail-closed status, not an exception that leaks source rows."""


def _day(value):
    if type(value) is not str:
        raise ScorecardReadinessError("missing_source_day")
    try:
        val=date.fromisoformat(value)
    except ValueError:
        raise ScorecardReadinessError("invalid_source_day") from None
    if val.isoformat()!=value:
        raise ScorecardReadinessError("invalid_source_day")
    return val


def preflight_day(day,history,parts,weekly,*,permit_weekly_only_lag=False):
    """Only a real, bounded market-day SOURCE LAG may return deferred."""
    current=_day(day)
    sources=(
        ("dart_annual",history,"asof",BULK),
        ("dart_factors",parts,"asof",PARTS),
        ("rotating_technical",weekly,"market_asof",WEEKLY),
    )
    lagged=[]
    for name,src,field,schema in sources:
        if not isinstance(src,dict) or src.get("schema")!=schema:
            raise ScorecardReadinessError("untrusted_scorecard_source_schema")
        if name=="dart_annual":
            if (src.get("historical_PIT_source_certified") is not False or
                src.get("raw_source_rows_published") is not False or
                src.get("fiscal_years")!=list(range(current.year-4,current.year))):
                raise ScorecardReadinessError("untrusted_major_source_claim")
        elif name=="dart_factors":
            if (src.get("all_scores_provisional_unverified") is not True or
                src.get("fully_verified_six_pillar_count")!=0):
                raise ScorecardReadinessError("untrusted_dart_factor_claim")
        else:
            if src.get("six_pillar_full_score_confirmed") is True:
                raise ScorecardReadinessError("untrusted_rotating_technical_claim")
        age=(current-_day(src.get(field))).days
        if age<0 or age>10:
            raise ScorecardReadinessError("invalid_or_expired_scorecard_source_day")
        if age>0:
            lagged.append(name)
    # Unlike the combined six-area audit, today's five-factor RESEARCH CSV
    # can safely omit an older weekly cohort. This is only possible with both
    # current-day financial sources; any stale annual/partial input still waits.
    weekly_omitted=(permit_weekly_only_lag and
                    lagged==["rotating_technical"])
    ready=not lagged or weekly_omitted
    return {"ready":ready,"market_asof":day,"lagging_sources":lagged,
            "technical_weekly_excluded":weekly_omitted,
            "status":("same_day_source_ready" if not lagged
                else "current_day_financial_with_prior_weekly_excluded"
                  if weekly_omitted
                else "deferred_waiting_for_saved_annual_and_weekly_source"),
            "official_100_point_scores_certified":False,
            "new_provider_calls":0}


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--docs",default="docs")
    opt=a.parse_args(argv)
    root=Path(opt.docs)
    def read(name):
        return json.loads((root/name).read_text(encoding="utf8"))
    market=read("data.json")
    catalog=read("v22-market-catalog.json")
    try:
        day,_=_inputs(market,catalog)
    except (ValueError,KeyError,TypeError):
        raise ScorecardReadinessError("untrusted_current_market_snapshot") from None
    history=read("v3-dart-bulk-history.json")
    parts=read("v3-dart-bulk-provisional.json")
    weekly=read("v3-weekly-rotation.json")
    status=preflight_day(day,history,parts,weekly,
                         permit_weekly_only_lag=True)
    if status["ready"]:
        # Same date alone is not proof of correctness: audit the entire
        # saved ledger contract before authorizing CSV regeneration. An older
        # technical source is a true omission, not re-dated to today's close.
        coverage(catalog,history,parts,weekly,allow_lagged_weekly=True)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"],"a",encoding="utf8") as fp:
            fp.write("ready="+str(status["ready"]).lower()+"\n")
            fp.write("market_asof="+day+"\n")
    print(json.dumps(status,sort_keys=True))


if __name__=="__main__":
    main()
