"""Read-only V3 per-issuer observation/history integrity and continuity watch.

Never alter a stock score or treat provisional research as an investment grade.
A missing source update is NOT a zero or a reason to erase prior observations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import v3_issuer_factor_ledger as identity_ledger

ROOT = Path(__file__).resolve().parent
PILLARS = {"volume":20,"technical":25,"financial":20,"valuation":20,"momentum":15}
COMPONENTS = {
    "financial":{"revenue_cagr_3y_pct":4,"operating_margin_3y_pct":4,
                 "liabilities_to_assets_pct":4,"operating_roa_3y_pct":4,
                 "fcf_assets_3y":4},
    "valuation":{"pbr":5,"earnings_yield_pct":5,"sales_to_market_cap":5,
                 "fcf_yield":5},
}
SCHEMA = "v3-issuer-last-observed-factors-0.1"


def _date(value):
    if not isinstance(value,str):
        return None
    try:
        day=date.fromisoformat(value)
        return day if day.isoformat()==value else None
    except ValueError:
        return None


def _score(entry,maximum,day,*,component=False):
    if not isinstance(entry,dict):
        return False
    available=entry.get("available")
    points=entry.get("points")
    stamp=entry.get("asof")
    if (type(available) is not int or available<1 or available>maximum or
        (component and available!=maximum) or
        type(points) not in (float,int) or
        not 0<=points<=available or
        not isinstance(stamp,str) or _date(stamp) is None or stamp>day):
        return False
    if component:
        return entry.get("source") in ("dart_fiscal_subfactor","dart_fcf_subfactor")
    return entry.get("source") in ("current_close_replay","dart_bulk_snapshot")


def _check_continuity(old, new, maximum, day, issues, *, component=False):
    """Retain coverage first; a broader older source is an allowed recovery."""
    if not _score(old, maximum, day, component=component):
        issues.append("invalid_prior_continuity_observation")
        return
    if not _score(new, maximum, day, component=component):
        return  # The ordinary current-ledger audit reports malformed records.
    if new["available"] < old["available"]:
        issues.append("narrowed_existing_observation_coverage")
    elif new["available"] == old["available"] and new["asof"] < old["asof"]:
        issues.append("regressed_existing_observation_date")


def inspect(current,previous=None):
    """Return an anonymous aggregate. No raw issuer names, values or IDs."""
    issues=[]
    if (not isinstance(current,dict) or current.get("schema")!=SCHEMA or
        current.get("investment_score_certified") is not False or
        current.get("component_ledger_research_only") is not True or
        current.get("missing_is_not_zero") is not True or
        _date(current.get("market_asof")) is None or
        not isinstance(current.get("rows"),dict)):
        raise ValueError("untrusted_current_ledger")
    day=current["market_asof"]
    rows=current["rows"]
    if len(rows)>15000:
        raise ValueError("unbounded_ledger")
    counts=Counter()
    groups=Counter()
    archive = {}
    try:
        archive = identity_ledger._verified_identity_archive(current, day)
    except identity_ledger.IssuerFactorLedgerError:
        issues.append("invalid_retired_issuer_identity_archive")
    if archive and current.get("identity_history_unverified_not_ranked") is not True:
        issues.append("retired_history_misrepresented_as_current")
    counts["retired_identity_snapshots"] = len(archive)
    counts["retired_area_observations"] = sum(
        len(row["factors"]) for row in archive.values())
    counts["retired_component_observations"] = sum(
        sum(len(v) for v in row["components"].values())
        for row in archive.values())
    for code,row in rows.items():
        if (not isinstance(row,dict) or code!=row.get("code") or
            not isinstance(code,str) or len(code)!=6 or
            not all(ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for ch in code) or
            row.get("market") not in ("KOSPI","KOSDAQ") or
            not isinstance(row.get("name"),str) or
            type(row.get("active")) is not bool or
            not isinstance(row.get("factors"),dict) or
            not isinstance(row.get("components"),dict) or
            not isinstance(row.get("component_history",{}),dict)):
            issues.append("invalid_issuer_identity_or_ledger_contract")
            continue
        if row["active"]:
            counts["active"]+=1
        else:
            counts["inactive"]+=1
        if row["components"]:
            counts["issuers_with_components"]+=1
        for name,e in row["factors"].items():
            if name not in PILLARS or not _score(e,PILLARS.get(name,0),day):
                issues.append("invalid_area_observation")
                continue
            counts["area_observations"]+=1
            if e["asof"]<day:
                counts["retained_area_observations"]+=1
        for group,items in row["components"].items():
            if group not in COMPONENTS or not isinstance(items,dict):
                issues.append("invalid_component_group")
                continue
            for name,e in items.items():
                budget=COMPONENTS[group].get(name)
                if budget is None or not _score(e,budget,day,component=True):
                    issues.append("invalid_component_observation")
                    continue
                correct_source=("dart_fcf_subfactor" if name in (
                    "fcf_assets_3y","fcf_yield") else "dart_fiscal_subfactor")
                if e["source"]!=correct_source:
                    issues.append("incorrect_component_provenance")
                counts["component_observations"]+=1
                groups[group+"."+name]+=1
                if e["asof"]<day:
                    counts["retained_component_observations"]+=1
                if e["points"]==0:
                    counts["valid_observed_zero_points"]+=1
        for group,items in row.get("component_history",{}).items():
            if group not in COMPONENTS or not isinstance(items,dict):
                issues.append("invalid_component_history_group")
                continue
            for name,events in items.items():
                present=row["components"].get(group,{})
                current_entry=present.get(name) if isinstance(present,dict) else None
                budget=COMPONENTS[group].get(name)
                if (budget is None or not isinstance(current_entry,dict) or
                    not isinstance(events,list) or not 1<=len(events)<=3):
                    issues.append("orphan_or_unbounded_component_history")
                    continue
                previous_date=None
                for item in events:
                    if (not isinstance(item,dict) or
                        _date(item.get("asof")) is None or
                        item["asof"]>current_entry["asof"] or
                        (previous_date and item["asof"]<previous_date) or
                        type(item.get("points")) not in (int,float) or
                        not 0<=item["points"]<=budget):
                        issues.append("invalid_prior_observation")
                        break
                    previous_date=item["asof"]
                counts["component_history_slots"]+=1
                counts["prior_component_events"]+=len(events)
    if (counts["active"]!=current.get("listed_total") or
        counts["active"]!=current.get("active_issuer_count") or
        counts["inactive"]!=current.get("inactive_issuer_count")):
        issues.append("market_census_mismatch")
    if previous is not None:
        if (not isinstance(previous,dict) or previous.get("schema")!=SCHEMA or
            not isinstance(previous.get("rows"),dict) or
            _date(previous.get("market_asof")) is None or
            previous["market_asof"]>day):
            issues.append("invalid_prior_ledger")
        else:
            old_archive = previous.get("identity_review_archive", {})
            if not isinstance(old_archive, dict):
                issues.append("invalid_prior_retired_identity_archive")
            else:
                for key, item in old_archive.items():
                    if key not in archive:
                        issues.append("lost_retired_issuer_identity_history")
                    elif archive[key] != item:
                        issues.append("mutated_retired_issuer_identity_history")
            for code,old in previous["rows"].items():
                latest=rows.get(code)
                if not isinstance(old,dict):
                    issues.append("invalid_prior_issuer_record")
                    continue
                # Historical rows are deliberately retained as inactive after
                # delisting. Even if the active market census still matches,
                # dropping an entire prior issuer row destroys its last-known
                # observations and must not silently pass the audit.
                if not isinstance(latest,dict):
                    issues.append("lost_existing_issuer_record")
                    continue
                if (old.get("name")!=latest.get("name") or
                    old.get("market")!=latest.get("market")):
                    # A name change may be an ordinary rename or a recycled
                    # ticker. Preserve old research, do not infer continuity.
                    key = identity_ledger._archive_key(
                        code, old.get("name"), old.get("market"),
                        previous["market_asof"])
                    retired = archive.get(key)
                    if retired is None:
                        issues.append("unarchived_issuer_identity_transition")
                    else:
                        try:
                            expected = identity_ledger._archived_identity(
                                old, code, previous["market_asof"], day)
                            # Only the archive's *recorded_on* can differ if a
                            # previous same-day transition was replayed.
                            if retired != expected:
                                issues.append("retired_issuer_history_source_mismatch")
                        except identity_ledger.IssuerFactorLedgerError:
                            issues.append("untrusted_prior_issuer_at_transition")
                    if (any(x.get("source") == "dart_bulk_snapshot"
                            for x in latest.get("factors", {}).values()
                            if isinstance(x,dict)) or
                        any(latest.get("components", {}).values())):
                        issues.append("unverified_fiscal_join_across_identity_change")
                    counts["identity_transitions_review_only"]+=1
                    continue
                counts["same_identity_comparisons"]+=1
                for field,issue in (("factors","lost_existing_area_observation"),
                                    ("components","lost_existing_component_observation")):
                    historic=old.get(field,{})
                    now=latest.get(field,{})
                    if not isinstance(historic,dict) or not isinstance(now,dict):
                        continue
                    for key,value in historic.items():
                        if field=="factors":
                            if key not in now and key in PILLARS:
                                issues.append(issue)
                            elif key in now and key in PILLARS:
                                _check_continuity(value,now[key],PILLARS[key],day,issues)
                        elif (isinstance(value,dict) and
                              isinstance(now.get(key),dict)):
                            for sub in value:
                                if sub in COMPONENTS.get(key,{}) and sub not in now[key]:
                                    issues.append(issue)
                                elif sub in COMPONENTS.get(key,{}):
                                    _check_continuity(value[sub],now[key][sub],
                                        COMPONENTS[key][sub],day,issues,component=True)
                        elif isinstance(value,dict) and any(
                                sub in COMPONENTS.get(key,{}) for sub in value):
                            issues.append(issue)
    return {
        "schema":"v3-issuer-observation-history-watch-0.1",
        "asof":day,
        "status":"error" if issues else "ok",
        "score_mutations_authorized":False,
        "missing_source_is_not_zero":True,
        "history_window_max_previous_events":3,
        "counts":dict(sorted(counts.items())),
        "per_component_observed_issuer_counts":dict(sorted(groups.items())),
        "issues":sorted(set(issues)),
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current",type=Path,
        default=ROOT/"docs/v3-issuer-last-observed-factors.json")
    parser.add_argument("--prior",type=Path)
    parser.add_argument("--assert-safe",action="store_true")
    args=parser.parse_args(argv)
    def read(path):
        if path.stat().st_size>8_000_000:
            raise ValueError("unbounded_input")
        return json.loads(path.read_text(encoding="utf8"))
    report=inspect(read(args.current),read(args.prior) if args.prior else None)
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    if args.assert_safe and report["status"]!="ok":
        raise SystemExit(2)
    return report


if __name__=="__main__":
    main()
