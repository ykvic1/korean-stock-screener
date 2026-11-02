"""Per-issuer last-observed research factors; NEVER a mixed-date live score.

A market census rollover changes current membership but must not erase valid
observations for unrelated issuers. Each factor retains its own source day, availability and provenance.
The UI may carry the latest validated factor into one provisional personal
ranking, with each original as-of day visible and no investment certification.
"""
from __future__ import annotations

from datetime import date
import math
from v3_real_six_pillar_coverage_audit import _canonical_hash

SCHEMA = "v3-issuer-last-observed-factors-0.1"
BUDGET = {"volume":20,"technical":25,"financial":20,
          "valuation":20,"momentum":15}
SOURCES = {"current_close_replay","dart_bulk_snapshot"}


class IssuerFactorLedgerError(ValueError):
    pass


def _date(value):
    if not isinstance(value,str):
        return None
    try:
        day=date.fromisoformat(value)
        return day if day.isoformat()==value else None
    except ValueError:
        return None


def _factor(value, latest):
    if not isinstance(value,dict):
        return None
    available=value.get("available")
    points=value.get("points")
    asof=value.get("asof")
    source=value.get("source")
    if (type(available) is not int or available<=0 or
        type(points) not in (int,float) or not math.isfinite(points) or
        type(asof) is not str or _date(asof) is None or
        _date(asof)>_date(latest) or source not in SOURCES):
        return None
    return {"asof":asof,"available":available,
            "points":float(points),"source":source}


def _source_fiscal(parts,audit,current_day):
    """Accept old fiscal points ONLY as dated past observations, not scores."""
    if (not isinstance(parts,dict) or not isinstance(audit,dict) or
        parts.get("schema")!="v3-dart-multi-year-research-parts-0.1" or
        audit.get("schema")!="v3-real-saved-source-six-pillar-coverage-0.1" or
        parts.get("asof")!=audit.get("market_asof") or
        _date(parts.get("asof")) is None or
        _date(parts["asof"])>_date(current_day) or
        (date.fromisoformat(current_day)-date.fromisoformat(parts["asof"])).days>30 or
        parts.get("all_scores_provisional_unverified") is not True or
        parts.get("historical_PIT_certified") is not False or
        parts.get("fully_verified_six_pillar_count")!=0 or
        audit.get("investment_grade_scores_confirmed") is not False or
        audit.get("six_pillar_official_complete_100pt_issuer_count")!=0 or
        audit.get("source_sha256_canonical",{}).get("dart_partial_score")!=
            _canonical_hash(parts) or
        not isinstance(parts.get("rows"),list) or
        len(parts["rows"])>parts.get("listed_total",0) or
        parts.get("listed_total")!=audit.get("listed_total")):
        return {}
    index={}
    counts={"financial":0,"valuation":0}
    for row in parts["rows"]:
        if (not isinstance(row,dict) or row.get("code") in index or
            not isinstance(row.get("code"),str) or
            len(row["code"])!=6 or not row["code"].isdigit() or
            row.get("market") not in ("KOSPI","KOSDAQ") or
            row.get("asof")!=parts["asof"] or
            row.get("confirmed_v3_score") is not None or
            row.get("scoring_certified") is not False):
            return {}
        factors={}
        for pillar,field,weight in (
            ("financial","financial_candidate_points",16),
            ("valuation","valuation_candidate_points",15)):
            available=row.get(pillar+"_available_max")
            points=row.get(field)
            if type(available) is not int or available<0 or available>weight:
                return {}
            if available==0:
                if points is not None:
                    return {}
                continue
            entry=_factor({"asof":parts["asof"],"available":available,
                           "points":points,"source":"dart_bulk_snapshot"},
                          current_day)
            if entry is None or entry["points"]<0 or entry["points"]>available:
                return {}
            factors[pillar]=entry
            counts[pillar]+=1
        index[row["code"]]={"market":row["market"],"factors":factors}
    claimed=audit.get("six_pillar_numeric_provisional_issuer_counts",{})
    if any(claimed.get(k)!=n for k,n in counts.items()):
        return {}
    return index


def build(catalog,current_rows,prior=None,*,parts=None,audit=None):
    """Merge each ticker/factor separately; preserve inactive issuer history."""
    day=catalog.get("date")
    members=catalog.get("rows")
    if (_date(day) is None or not isinstance(members,list) or
        catalog.get("listed_total")!=len(members) or
        not isinstance(current_rows,list) or len(current_rows)!=len(members)):
        raise IssuerFactorLedgerError("invalid_market_census")
    listed={}
    for row in members:
        code=row.get("code") if isinstance(row,dict) else None
        if (not isinstance(code,str) or len(code)!=6 or not code.isdigit() or
            code in listed or row.get("market") not in ("KOSPI","KOSDAQ") or
            not isinstance(row.get("name"),str) or not row["name"].strip()):
            raise IssuerFactorLedgerError("untrusted_market_identity")
        listed[code]={"code":code,"name":row["name"],"market":row["market"],
                      "active":True,"factors":{}}
    if prior is not None and (not isinstance(prior,dict) or
        prior.get("schema")!=SCHEMA or
        prior.get("investment_score_certified") is not False or
        type(prior.get("mixed_date_sum_permitted")) is not bool or
        _date(prior.get("market_asof")) is None or
        _date(prior["market_asof"])>_date(day) or
        not isinstance(prior.get("rows"),dict) or
        len(prior["rows"])>15000):
        raise IssuerFactorLedgerError("untrusted_prior_factor_ledger")
    previous=prior["rows"] if prior else {}
    for code,old in previous.items():
        if not isinstance(old,dict) or code!=old.get("code"):
            continue
        if code not in listed:
            if old.get("market") not in ("KOSPI","KOSDAQ") or not isinstance(old.get("name"),str):
                continue
            listed[code]={"code":code,"name":old["name"],"market":old["market"],
                          "active":False,"factors":{}}
        target=listed[code]
        # Recycled tickers, market transfers and renamed issuers require a
        # fresh identity audit; never copy their old score automatically.
        if old.get("name")!=target["name"] or old.get("market")!=target["market"]:
            continue
        for key,value in (old.get("factors") or {}).items():
            if key not in BUDGET:
                continue
            f=_factor(value,day)
            if (f and 0<=f["points"]<=f["available"]<=BUDGET[key]):
                target["factors"][key]=f
    archived=_source_fiscal(parts,audit,day)
    for code,source in archived.items():
        target=listed.get(code)
        if not target or not target["active"] or target["market"]!=source["market"]:
            continue
        for key,entry in source["factors"].items():
            old=target["factors"].get(key)
            if (old is None or entry["available"]>old["available"] or
                (entry["available"]==old["available"] and
                 entry["asof"]>old["asof"])):
                target["factors"][key]=entry
    seen=set()
    for row in current_rows:
        code=row.get("code") if isinstance(row,dict) else None
        target=listed.get(code)
        if (not target or not target["active"] or code in seen or
            row.get("name")!=target["name"] or
            row.get("market")!=target["market"] or row.get("asof")!=day):
            raise IssuerFactorLedgerError("current_issuer_identity_mismatch")
        seen.add(code)
        for key,limit in BUDGET.items():
            value=(row.get("factors") or {}).get(key)
            available=(row.get("factor_available") or {}).get(key)
            if type(available) is not int or not 0<=available<=limit:
                raise IssuerFactorLedgerError("invalid_current_factor_weight")
            if available==0:
                if value is not None:
                    raise IssuerFactorLedgerError("unacquired_factor_has_points")
                continue
            if (type(value) not in (int,float) or not math.isfinite(value)
                or not 0<=value<=available):
                raise IssuerFactorLedgerError("invalid_current_factor_points")
            old=target["factors"].get(key)
            # A new but narrower partial source must not erase a larger
            # previously observed budget. Replace on equal/better coverage.
            if old is None or available>=old["available"]:
                target["factors"][key]={
                    "asof":day,"available":available,
                    "points":float(value),"source":"current_close_replay"}
    return {
        "schema":SCHEMA,"market_asof":day,"listed_total":len(members),
        "active_issuer_count":len(members),
        "inactive_issuer_count":len(listed)-len(members),
        "investment_score_certified":False,
        "mixed_date_sum_permitted":True,
        "mixed_date_score_research_only":True,
        "missing_is_not_zero":True,
        "rows":dict(sorted(listed.items()))
    }
