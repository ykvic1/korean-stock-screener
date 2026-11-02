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
FISCAL_COMPONENTS = {
    "financial": {"revenue_cagr_3y_pct":4,"operating_margin_3y_pct":4,
                  "liabilities_to_assets_pct":4,"operating_roa_3y_pct":4},
    "valuation": {"pbr":5,"earnings_yield_pct":5,"sales_to_market_cap":5},
}
FCF_COMPONENTS = {
    "financial": {"fcf_assets_3y":("financial_fcf_assets",4)},
    "valuation": {"fcf_yield":("valuation_fcf_yield",5)},
}
COMPONENT_SOURCES = {"dart_fiscal_subfactor","dart_fcf_subfactor"}
HISTORY_LIMIT = 3  # bounded prior observation snapshots per issuer and subfactor


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


def _component(entry,day,maximum):
    if not isinstance(entry,dict):
        return None
    source=entry.get("source")
    weight=entry.get("available")
    points=entry.get("points")
    asof=entry.get("asof")
    if (source not in COMPONENT_SOURCES or type(weight) is not int or
        weight!=maximum or type(points) not in (int,float) or
        not math.isfinite(points) or not 0<=points<=maximum or
        _date(asof) is None or _date(asof)>_date(day)):
        return None
    return {"asof":asof,"available":weight,"points":float(points),"source":source}


def _fiscal_components(row,day):
    if not isinstance(row,dict):
        return {}
    raw=row.get("unverified_components")
    if raw is None:
        return {}
    if not isinstance(raw,dict) or _date(row.get("asof")) is None or (
            _date(row["asof"])>_date(day)):
        raise IssuerFactorLedgerError("invalid_fiscal_component_container")
    result={}
    for group,components in FISCAL_COMPONENTS.items():
        for name,maximum in components.items():
            source=raw.get(name)
            if source is None:
                continue  # older source contracts genuinely lacked this part
            if type(source) is not dict or type(source.get("max_points")) is not int:
                raise IssuerFactorLedgerError("invalid_fiscal_component")
            weight=source["max_points"]
            points=source.get("candidate_points")
            if weight==0 and points is None:
                continue
            if weight!=maximum:
                raise IssuerFactorLedgerError("invalid_fiscal_component_weight")
            entry=_component({"asof":row["asof"],"available":maximum,
                "points":points,"source":"dart_fiscal_subfactor"},day,maximum)
            if entry is None:
                raise IssuerFactorLedgerError("invalid_fiscal_component_points")
            result.setdefault(group,{})[name]=entry
    return result


def _fcf_components(row,day):
    if not isinstance(row,dict) or _date(row.get("asof")) is None or (
            _date(row["asof"])>_date(day)):
        raise IssuerFactorLedgerError("invalid_fcf_component_container")
    if (row.get("strict_receipt_scope_matched") is not True or
        row.get("scoring_certified") is not False or
        row.get("historical_PIT_certified") is not False):
        raise IssuerFactorLedgerError("unverified_fcf_component_claim")
    result={}
    for group,components in FCF_COMPONENTS.items():
        for name,(prefix,maximum) in components.items():
            weight=row.get(prefix+"_available_max")
            points=row.get(prefix+"_candidate_points")
            if weight==0 and points is None:
                continue
            if weight!=maximum:
                raise IssuerFactorLedgerError("invalid_fcf_component_weight")
            entry=_component({"asof":row["asof"],"available":maximum,
                "points":points,"source":"dart_fcf_subfactor"},day,maximum)
            if entry is None:
                raise IssuerFactorLedgerError("invalid_fcf_component_points")
            result.setdefault(group,{})[name]=entry
    return result


def _prior_component_history(raw,day,maximum):
    if not isinstance(raw,list) or len(raw)>HISTORY_LIMIT:
        return []
    retained=[]
    for item in raw:
        if (not isinstance(item,dict) or
            _date(item.get("asof")) is None or _date(item["asof"])>_date(day) or
            type(item.get("points")) not in (int,float) or
            not math.isfinite(item["points"]) or not 0<=item["points"]<=maximum or
            (retained and item["asof"]<retained[-1]["asof"])):
            return []
        retained.append({"asof":item["asof"],"points":float(item["points"])})
    return retained


def _keep_components(target,candidate):
    for group,items in candidate.items():
        for name,entry in items.items():
            previous=target["components"].setdefault(group,{}).get(name)
            if previous is not None and entry["asof"]<previous["asof"]:
                continue
            if previous==entry:
                continue  # replay of same source must never create a fake update
            if previous is not None:
                audit={"asof":previous["asof"],"points":previous["points"]}
                events=target["component_history"].setdefault(group,{}).setdefault(name,[])
                if not events or events[-1]!=audit:
                    events.append(audit)
                    del events[:-HISTORY_LIMIT]
            target["components"][group][name]=entry


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
        index[row["code"]]={"market":row["market"],"factors":factors,
                            "components":_fiscal_components(row,current_day)}
    claimed=audit.get("six_pillar_numeric_provisional_issuer_counts",{})
    if any(claimed.get(k)!=n for k,n in counts.items()):
        return {}
    return index


def build(catalog,current_rows,prior=None,*,parts=None,audit=None,
          fiscal_rows=None,fcf_rows=None):
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
                      "active":True,"factors":{},"components":{},"component_history":{}}
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
                          "active":False,"factors":{},"components":{},"component_history":{}}
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
        prior_components=old.get("components")
        if not isinstance(prior_components,dict):
            prior_components={}
        for group,items in prior_components.items():
            if group not in FISCAL_COMPONENTS or not isinstance(items,dict):
                continue
            for name,value in items.items():
                maximum=FISCAL_COMPONENTS[group].get(name)
                if maximum is None:
                    maximum=(FCF_COMPONENTS.get(group,{}).get(name) or (None,None))[1]
                if maximum is None:
                    continue
                entry=_component(value,day,maximum)
                required=("dart_fcf_subfactor" if name in FCF_COMPONENTS.get(group,{})
                          else "dart_fiscal_subfactor")
                if entry is not None and entry["source"]==required:
                    target["components"].setdefault(group,{})[name]=entry
                    past=(old.get("component_history") or {})
                    if isinstance(past,dict):
                        group_past=past.get(group)
                        if isinstance(group_past,dict):
                            retained=_prior_component_history(group_past.get(name),day,maximum)
                            if retained and retained[-1]["asof"]<=entry["asof"]:
                                target["component_history"].setdefault(group,{})[name]=retained
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
        _keep_components(target,source.get("components",{}))
    for source_rows,mapper in ((fiscal_rows,_fiscal_components),
                               (fcf_rows,_fcf_components)):
        if source_rows is None:
            continue
        if not isinstance(source_rows,dict):
            raise IssuerFactorLedgerError("invalid_component_source_index")
        for code,source_row in source_rows.items():
            target=listed.get(code)
            if (not target or not target["active"] or
                not isinstance(source_row,dict) or
                source_row.get("code")!=code or
                source_row.get("market")!=target["market"] or
                source_row.get("asof")!=day):
                raise IssuerFactorLedgerError("component_issuer_identity_mismatch")
            _keep_components(target,mapper(source_row,day))
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
        "component_ledger_research_only":True,
        "missing_is_not_zero":True,
        "rows":dict(sorted(listed.items()))
    }
