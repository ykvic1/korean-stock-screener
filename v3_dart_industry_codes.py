"""Official DART current-company industry taxonomy, bounded research-only.

Company profile induty_code is a CURRENT disclosure-company classification,
not a time-travel KRX sector index, industry/15 score, or a license to fill
missing sector labels as if they were verified historical memberships.
Only public 6-digit ticker -> observed industry code is committed; the
8-digit DART corp IDs and the full company profile stay in Actions cache.
"""
from __future__ import annotations
import argparse
from datetime import date,datetime,timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from v3_dart_bulk_history import (_mapping,_stable_digest,
    verified_company_mapping_cache,make_company_mapping_cache,write_json)
from v3_dart_rolling_book_reference import _inputs

SCHEMA="v3-dart-current-listed-industry-code-reference-0.1"
MAX_PER_RUN=96
MAX_SECONDS=880
CODE=re.compile(r"[0-9]{6}\Z")
INDUSTRY=re.compile(r"[0-9]{2,8}\Z")
HASH=re.compile(r"[a-f0-9]{64}\Z")


class IndustrySourceError(ValueError):
    """Only fixed reason codes; no response text, secret or firm metadata."""


def _hash_corp(corp):
    if type(corp) is not str or not re.fullmatch(r"[0-9]{8}",corp):
        raise IndustrySourceError("bad_corporate_identity")
    return hashlib.sha256(corp.encode("ascii")).hexdigest()


def summary(asof,members,records,last_run=0):
    if type(records) is not dict or len(records)>len(members):
        raise IndustrySourceError("industry_reference_too_many_records")
    counts={"code_observed":0,"code_missing":0}
    for code,record in records.items():
        if (code not in members or type(record) is not dict or
            record.get("market") not in ("KOSPI","KOSDAQ") or
            record.get("market")!=members[code]["market"] or
            type(record.get("corporation_identity_digest")) is not str or
            not HASH.fullmatch(record["corporation_identity_digest"]) or
            record.get("origin")!="OpenDART_company_json" or
            record.get("historical_pit_verified") is not False):
            raise IndustrySourceError("industry_issuer_provenance_invalid")
        try:
            observed=date.fromisoformat(record["checked_on"])
        except (TypeError,ValueError,KeyError):
            raise IndustrySourceError("industry_observation_date_invalid") from None
        # Cached current-company profile may be retained when the new market
        # trading date rolls; never confuse it with historical PIT membership.
        if (observed<date.fromisoformat(asof)-timedelta(days=45)
                or observed>date.today()+timedelta(days=2)):
            raise IndustrySourceError("industry_observation_date_mismatch")
        value=record.get("industry_code")
        status=record.get("status")
        if value is None:
            if status!="industry_unavailable":
                raise IndustrySourceError("missing_industry_state_invalid")
            counts["code_missing"]+=1
        elif type(value) is str and INDUSTRY.fullmatch(value) and status=="observed":
            counts["code_observed"]+=1
        else:
            raise IndustrySourceError("industry_code_not_official")
    if type(last_run) is not int or not 0<=last_run<=MAX_PER_RUN:
        raise IndustrySourceError("invalid_industry_last_run")
    return {
        "schema":SCHEMA,"asof":asof,"listed_total":len(members),
        "attempted_company_profiles":len(records),
        "observed_industry_codes":counts["code_observed"],
        "missing_industry_codes":counts["code_missing"],
        "not_yet_attempted":len(members)-len(records),
        "last_run_attempted":last_run,
        "source":"OpenDART company.json induty_code (current company classification)",
        "source_records_digest":_stable_digest(records),
        "official_krx_sector_membership_verified":False,
        "past_constituent_membership_pit_verified":False,
        "industry_5433_points_authorized":False,
        "investable_v3_score_certified":False,
        "source_company_profiles_published":False,
        "raw_corp_ids_published":False,
        "records":{code:records[code] for code in sorted(records)},
    }


def prior_records(prior,asof,members):
    if prior is None:
        return {}
    if (type(prior) is not dict or prior.get("schema")!=SCHEMA or
        prior.get("source_records_digest")!=_stable_digest(prior.get("records"))):
        raise IndustrySourceError("bad_prior_industry_checkpoint")
    try:
        old=date.fromisoformat(prior["asof"])
        day=date.fromisoformat(asof)
    except (TypeError,ValueError,KeyError):
        raise IndustrySourceError("bad_prior_industry_day") from None
    if old>day or (day-old).days>45:
        raise IndustrySourceError("stale_industry_checkpoint_requires_new_source")
    old_members={c:{"market":r["market"]} for c,r in prior["records"].items()}
    expected=summary(prior["asof"],old_members,prior["records"],prior["last_run_attempted"])
    # A saved checkpoint spans the FULL market, even if only 96 out of
    # 2497 companies have a record so far. Do not use the observed subset
    # as the previous market census denominator.
    expected["listed_total"]=prior["listed_total"]
    expected["not_yet_attempted"]=prior["listed_total"]-len(prior["records"])
    # Only attributes in the canonical immutable source state accepted.
    if (type(prior["listed_total"]) is not int
            or not len(prior["records"])<=prior["listed_total"]<=6000
            or expected!=prior):
        raise IndustrySourceError("tampered_prior_industry_checkpoint")
    return {c:r for c,r in prior["records"].items()
            if c in members and members[c]["market"]==r["market"]}


def choose(members,retained,mapping,limit=MAX_PER_RUN):
    if type(limit) is not int or not 1<=limit<=MAX_PER_RUN:
        raise IndustrySourceError("unsafe_company_profile_request_limit")
    pools={"KOSPI":[],"KOSDAQ":[]}
    for code,row in members.items():
        corp=mapping.get(code)
        if corp is None:
            continue
        old=retained.get(code)
        if old is None or old["corporation_identity_digest"]!=_hash_corp(corp):
            pools[row["market"]].append(code)
    for market in pools: pools[market].sort()
    chosen=[]
    for market in ("KOSPI","KOSDAQ"):
        chosen+=pools[market][:limit//2]
        pools[market]=pools[market][limit//2:]
    for market in ("KOSPI","KOSDAQ"):
        room=limit-len(chosen)
        chosen+=pools[market][:room]
    return chosen


def classify(response,code,corp,market,checked_on):
    if type(response) is not dict or response.get("status")!="000":
        raise IndustrySourceError("official_company_profile_not_verified")
    if (response.get("stock_code")!=code or
        response.get("corp_cls")!=("Y" if market=="KOSPI" else "K")):
        raise IndustrySourceError("company_profile_identity_or_market_mismatch")
    raw=response.get("induty_code")
    if raw in (None,""):
        value=None
    elif type(raw) is str and INDUSTRY.fullmatch(raw):
        value=raw
    else:
        raise IndustrySourceError("unrecognized_industry_code_format")
    return {"market":market,"industry_code":value,
            "status":"observed" if value is not None else "industry_unavailable",
            "corporation_identity_digest":_hash_corp(corp),
            "origin":"OpenDART_company_json",
            "checked_on":checked_on,"historical_pit_verified":False}


def collect(market,catalog,prior,*,key,session,cache=None,limit=MAX_PER_RUN,
            checked_on=None,on_cache=None,on_progress=None):
    asof,members=_inputs(market,catalog)
    retained=prior_records(prior,asof,members)
    if type(key) is not str or len(key)<20:
        raise IndustrySourceError("missing_industry_dart_key")
    if checked_on is None:
        checked_on=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    try:
        checked=date.fromisoformat(checked_on)
    except (ValueError,TypeError):
        raise IndustrySourceError("invalid_source_check_date") from None
    if checked<date.fromisoformat(asof):
        raise IndustrySourceError("company_profile_before_market_close_date")
    mapping=verified_company_mapping_cache(cache,asof,members)
    calls=0
    if mapping is None:
        try:
            response=session.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key":key},timeout=(20,95))
            calls+=1
            response.raise_for_status()
            mapping=_mapping(response.content,set(members))
        except Exception:
            raise IndustrySourceError("company_profile_mapping_unavailable") from None
        if on_cache:
            on_cache(make_company_mapping_cache(mapping,asof,members))
    selected=choose(members,retained,mapping,limit=limit)
    deadline=time.monotonic()+MAX_SECONDS
    for index,code in enumerate(selected,1):
        if time.monotonic()>deadline:
            raise IndustrySourceError("profile_safe_time_budget_reached")
        try:
            response=session.get(
                "https://opendart.fss.or.kr/api/company.json",
                params={"crtfc_key":key,"corp_code":mapping[code]},
                timeout=35)
            calls+=1
            response.raise_for_status()
            payload=response.json()
        except Exception:
            raise IndustrySourceError("company_profile_transport_unavailable") from None
        # Status 020/quota, missing info, unknown schema fail closed without
        # pretending a research score exists.
        record=classify(payload,code,mapping[code],members[code]["market"],checked_on)
        retained[code]=record
        if on_progress and index%16==0:
            on_progress(summary(asof,members,retained,index))
    return summary(asof,members,retained,len(selected)),calls


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market",default="docs/data.json")
    p.add_argument("--catalog",default="docs/v22-market-catalog.json")
    p.add_argument("--state",default="docs/v3-dart-industry-classification.json")
    p.add_argument("--cache",default=".cache/v3-dart-company-codes.json")
    p.add_argument("--limit",type=int,default=MAX_PER_RUN)
    p.add_argument("--mode",choices=("dry-run","live","verify","rebind"),
                   default="dry-run")
    a=p.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))
    market=read(a.market);catalog=read(a.catalog)
    asof,members=_inputs(market,catalog)
    file=Path(a.state)
    prior=read(file) if file.is_file() else None
    if a.mode=="dry-run":
        retained=prior_records(prior,asof,members)
        print(json.dumps({"status":"no_network_industry_profile_plan",
            "listed":len(members),"previous_attempts":len(retained),
            "requested_max":a.limit,"industry_score_certified":False},
            sort_keys=True))
        return
    if a.mode in ("verify","rebind"):
        if prior is None:
            raise IndustrySourceError("industry_source_checkpoint_absent")
        retained=prior_records(prior,asof,members)
        if a.mode=="verify":
            if prior!=summary(asof,members,retained,prior["last_run_attempted"]):
                raise IndustrySourceError("profile_snapshot_not_current_asof")
        else:
            write_json(file,summary(asof,members,retained,prior["last_run_attempted"]))
        print(json.dumps({"status":"industry_profile_"+a.mode,
            "attempted":len(retained),"raw_calls":0},sort_keys=True))
        return
    import requests
    cache=read(a.cache) if Path(a.cache).is_file() else None
    def save_cache(value): write_json(a.cache,value)
    saved=0
    def checkpoint(value):
        nonlocal saved
        write_json(file,value)
        saved+=1
    try:
        finished,calls=collect(market,catalog,prior,
            key=os.getenv("DART_API_KEY",""),session=requests.Session(),
            cache=cache,limit=a.limit,on_cache=save_cache,on_progress=checkpoint)
    except IndustrySourceError as exc:
        if saved==0:
            raise
        print(json.dumps({"status":"partial_validated_industry_profiles",
            "checkpoints":saved,"halt_reason":str(exc),"score_certified":False},
            sort_keys=True))
        return
    write_json(file,finished)
    print(json.dumps({"status":"official_company_industry_code_only",
        "asof":asof,"calls":calls,"mapped":finished["observed_industry_codes"],
        "attempted":finished["attempted_company_profiles"],
        "listed":finished["listed_total"],
        "verified_historical_sector_scoring":False},sort_keys=True))


if __name__=="__main__":
    main()
