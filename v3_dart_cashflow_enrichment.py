"""Bounded official DART CASH FLOW / CAPEX research evidence (NOT a V3 score).

fnlttMultiAcnt provides BS/IS. This independently probes annual CF rows
through fnlttSinglAcntAll, using only exact known IFRS account IDs.
All source account rows and credentials remain ephemeral. No missing CAPEX is
assumed zero; no cross-filing fiscal/issuer mix, PIT or investment certification.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from v3_dart_bulk_history import (
    _day, _amount, _inputs, _mapping, _stable_digest, _valid_receipt,
    verified_company_mapping_cache, make_company_mapping_cache, write_json,
)

SCHEMA="v3-observed-annual-dart-cashflow-capex-0.1"
MAX_ISSUERS=40
# Forty issuers x three fiscal years; allow one extra corpCode.xml request
# if the verified listing-mapping cache is unavailable. Partial progress is
# checkpointed after every year when the wall-clock budget is reached.
MAX_SOURCE_CALLS=121
# The workflow runs three serial batches under a 30-minute job limit.
# Stop each batch early enough to publish its source-verified checkpoints
# before the runner can kill the whole job (including publication steps).
MAX_SECONDS=300
YEARS=(2023,2024,2025)
CFO_IDS={
    "ifrs-full_CashFlowsFromUsedInOperatingActivities",
    "ifrs_CashFlowsFromUsedInOperatingActivities",
}
PPE_IDS={
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
    "ifrs_PurchaseOfPropertyPlantAndEquipment",
}
INTANGIBLE_IDS={
    "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
    "ifrs-full_PurchaseOfIntangibleAssets",
    "ifrs_PurchaseOfIntangibleAssets",
}
# OpenDART documents this literal marker for an issuer-specific XBRL account.
# Match the exact label characters after whitespace removal only; never
# substitute synonyms, a non-CF row, or an arbitrary
# extension account ID: two identical Korean labels are NOT necessarily one
# verified standard concept. Use this only in a bounded missing-fact recheck.
NONSTANDARD_ACCOUNT_ID="-표준계정코드 미사용-"
NONSTANDARD_CAPEX_NAMES={
    "ppe":"유형자산의 취득",
    "intangible":"무형자산의 취득",
}
LEGACY_NAME_RECHECK_VERSION="exact_nonstandard_cf_name_v1"
NAME_RECHECK_VERSION="whitespace_exact_nonstandard_cf_name_v2"
NAME_RECHECK_VERSIONS={LEGACY_NAME_RECHECK_VERSION,NAME_RECHECK_VERSION}
SOURCE_IFRS="OpenDART fnlttSinglAcntAll exact IFRS CF account IDs"
SOURCE_WITH_NAMES=("OpenDART fnlttSinglAcntAll exact IFRS CF IDs or "
                   "receipt-bound exact nonstandard CF names")

class CashflowError(ValueError):
    """Fixed, non-sensitive failure codes."""


def fingerprint(value):
    return _stable_digest(value)


def asof_members(market,catalog,bulk):
    asof,members=_inputs(market,catalog)
    # Never reuse FY2023-25 when FY2026 becomes the latest available annual
    # cohort; explicitly update the capture contract at fiscal rollover.
    if tuple(range(_day(asof).year-3,_day(asof).year))!=YEARS:
        raise CashflowError("cashflow_fiscal_rollover_requires_review")
    if (type(bulk) is not dict or bulk.get("asof")!=asof
        or bulk.get("listed_total")!=len(members)
        or bulk.get("fiscal_years")!=list(range(_day(asof).year-4,_day(asof).year))
        or bulk.get("historical_PIT_source_certified") is not False
        or type(bulk.get("records")) is not dict):
        raise CashflowError("incompatible_major_annual_source")
    return asof,members


def source_receipt_years(bulk_record, scope, *, asof=None):
    """Only fiscal years with real, as-of-valid annual receipts in one scope."""
    if scope not in ("CFS","OFS"):
        return ()
    years=bulk_record.get("years",{}) if type(bulk_record) is dict else {}
    cutoff=_day(asof) if asof is not None else date.max
    return tuple(year for year in YEARS if _valid_receipt(
        years.get(str(year),{}).get(scope,{}).get("receipt_id"),
        year,cutoff))


def matched_scope(bulk_record, *, asof=None, preferred_scope=None):
    """Require a verified latest-year filing; retain any earlier real years.

    A latest-year CFO/CAPEX can legitimately support a current FCF yield even
    when older annual receipts are absent. Three-year FCF/assets remains
    unavailable until ALL three source years are independently observed.
    Never merge CFS and OFS; prefer the scope with more dated receipts, and
    preserve a previously source-matched scope across subsequent runs.
    """
    qualifying={}
    for scope in ("CFS","OFS"):
        years=source_receipt_years(bulk_record,scope,asof=asof)
        if YEARS[-1] in years:
            qualifying[scope]=years
    if preferred_scope in qualifying:
        return preferred_scope
    if not qualifying:
        return None
    return max(qualifying,key=lambda scope:(
        len(qualifying[scope]),scope=="CFS"))


def retained_current_market_cashflow(market,catalog,bulk,prior):
    """Retain genuine source receipts, isolating revised fiscal statements.

    An updated major-account archive may replace one exact annual receipt.
    Quarantine ONLY that stale issuer/year observation, while retaining
    other receipt-matched fiscal years and unrelated issuers. The missing
    year becomes eligible for a fresh official provider query in plan().
    No old amount is silently relabeled with a newly received receipt.
    Neither a source mismatch nor a removed issuer can survive validation.
    """
    asof,members=asof_members(market,catalog,bulk)
    if prior is None:
        return None
    if type(prior) is not dict:
        raise CashflowError("untrusted_previous_cashflow_source")
    previous=prior.get("asof")
    try:
        previous_day=_day(previous)
        current_day=_day(asof)
    except (ValueError,TypeError):
        raise CashflowError("untrusted_previous_cashflow_date") from None
    if (previous_day>current_day or previous_day.year!=current_day.year
        or (current_day-previous_day).days>45
        or type(prior.get("listed_total")) is not int
        or not 1<=prior["listed_total"]<=6000
        or type(prior.get("records")) is not dict
        or len(prior["records"])>prior["listed_total"]):
        raise CashflowError("unsafe_cashflow_market_day_rollover")
    if (previous_day==current_day
        and prior.get("source_major_history_sha256")==fingerprint(bulk)):
        # Fast path retains the preexisting strict provenance check.
        verify_state(prior,market,catalog,bulk,strict_fingerprint=True)
        return prior
    old_records=prior["records"]
    if any(type(c) is not str or not re.fullmatch(r"[0-9A-Z]{6}",c)
           for c in old_records):
        raise CashflowError("invalid_prior_company_identity")
    # Validate the entire immutable old snapshot BEFORE source reconciliation.
    # Yesterday's listed universe cannot be reconstructed from today's one.
    reconstructed=summary(previous,old_records,old_records,
                          prior.get("source_major_history_sha256"))
    reconstructed["listed_total"]=prior["listed_total"]
    if reconstructed!=prior:
        raise CashflowError("untrusted_previous_cashflow_source")
    # If the byte-for-byte major archive did NOT change, a previously
    # matched receipt cannot spontaneously differ. That indicates a
    # tampered/invalid checkpoint, NOT a legitimate filing revision.
    archive_unchanged=(
        prior.get("source_major_history_sha256")==fingerprint(bulk))
    current_records={}
    for code,issuer in old_records.items():
        if code not in members:
            continue
        scope=issuer["scope"]
        major_company=bulk["records"].get(code,{})
        major_years=(major_company.get("years",{})
                     if type(major_company) is dict else {})
        unchanged={}
        for year,observation in issuer["years"].items():
            if observation.get("major_annual_receipt_matched") is True:
                fiscal=major_years.get(year,{})
                major=(fiscal.get(scope,{}) if type(fiscal) is dict else {})
                receipt=(major.get("receipt_id")
                         if type(major) is dict else None)
                if (not _valid_receipt(receipt,int(year),current_day)
                    or receipt!=observation.get("annual_receipt_id")):
                    if archive_unchanged:
                        raise CashflowError("cashflow_major_receipt_changed")
                    # Revised/withdrawn or no longer date-valid annual source.
                    # The old numeric FCF must never be rebased onto it.
                    continue
            # This year's CFO remains receipt-matched, but an independently
            # sourced later-filing CAPEX comparative must be revoked if that
            # later FY2025 receipt is corrected or withdrawn. Preserve the
            # original year's other independently verified observations.
            if observation.get('later_original_comparative_proof') is not None:
                from v3_dart_fcf_comparative_promotion import restore_if_later_receipt_changed
                source_year=observation['later_original_comparative_proof'].get('source_fiscal_year',2025)
                later=major_years.get(str(source_year),{}).get(scope,{})
                later_receipt=later.get('receipt_id') if type(later) is dict else None
                if observation['later_original_comparative_proof']['later_receipt']!=later_receipt:
                    if archive_unchanged:
                        raise CashflowError('comparative_source_changed_without_bulk_revision')
                    observation=restore_if_later_receipt_changed(
                        observation,actual_later_receipt=later_receipt)
            if observation.get('reviewed_disaggregated_capex') is not None:
                from v3_dart_reviewed_capex import restore
                latest = major_years.get('2025', {}).get(scope, {})
                if observation['reviewed_disaggregated_capex']['source_receipt'] != latest.get('receipt_id'):
                    observation = restore(observation)
            unchanged[year]=observation
        if unchanged:
            current_records[code]={"scope":scope,"years":unchanged}
    rebound=summary(asof,members,current_records,fingerprint(bulk))
    verify_state(rebound,market,catalog,bulk,strict_fingerprint=True)
    return rebound


def plan(market,catalog,bulk,prior=None,limit=MAX_ISSUERS,
         recover_missing_names=False,priority_codes=()):
    asof,members=asof_members(market,catalog,bulk)
    if type(limit) is not int or not 1<=limit<=MAX_ISSUERS:
        raise CashflowError("invalid_cashflow_max_issuers")
    if (type(priority_codes) not in (list,tuple,set,frozenset)
        or len(priority_codes)>6000 or
        any(type(code) is not str or code not in members for code in priority_codes)):
        raise CashflowError("untrusted_cashflow_recovery_priority")
    priority_codes=set(priority_codes)
    prior=retained_current_market_cashflow(market,catalog,bulk,prior)
    retained=prior["records"] if prior is not None else {}
    queues={"KOSPI":[],"KOSDAQ":[]}
    for code,row in members.items():
        record=bulk["records"].get(code)
        priorrow=retained.get(code,{})
        scope=matched_scope(record,asof=asof,
                            preferred_scope=priorrow.get("scope"))
        if scope is None:
            continue
        if priorrow and priorrow.get("scope")!=scope:
            # Source revisions cannot silently combine CFS and OFS observations.
            continue
        observed=priorrow.get("years",{})
        # Query only years backed by actual, same-scope major annual receipts.
        # Missing older filings remain absent, not zero-valued observations.
        available=source_receipt_years(record,scope,asof=asof)
        pending=[y for y in available if str(y) not in observed]
        if pending:
            # Finish already-partial real cash-flow chains before starting new.
            queues[row["market"]].append((len(pending),code,scope,pending))
    for m in queues:
        queues[m].sort()
    chosen=[]
    for market_name in ("KOSPI","KOSDAQ"):
        chosen+=queues[market_name][:limit//2]
    rest=sorted(queues["KOSPI"][limit//2:]+queues["KOSDAQ"][limit//2:])
    chosen+=rest[:limit-len(chosen)]
    if recover_missing_names and len(chosen)<limit:
        # The original exact-IFRS collection is already complete for most
        # listed issuers. Retry ONLY genuinely missing CFO/CAPEX account years
        # once per unchanged receipt. This is not a wholesale refetch and
        # a negative result never turns missing CAPEX into numeric zero.
        selected={code for _,code,_,_ in chosen}
        retries=[]
        for code,member in members.items():
            if code in selected or code not in retained:
                continue
            priorrow=retained[code]
            scope=priorrow["scope"]
            record=bulk["records"].get(code,{})
            missing=[]
            for year in source_receipt_years(record,scope,asof=asof):
                observed=priorrow["years"].get(str(year))
                if (observed and observed.get("major_annual_receipt_matched") is True
                    and observed.get('free_cashflow_krw') is None
                    and any(observed.get(k) is None for k in
                            ("operating_cashflow_krw","ppe_capex_krw",
                             "intangible_capex_krw"))
                    and (observed.get("account_name_recheck") or {}).get("version") != NAME_RECHECK_VERSION):
                    missing.append(year)
            if missing:
                retries.append((len(missing),code,scope,missing,member["market"]))
        # Independent R1d one-missing-FCF priority is a COLLECTION order,
        # never a score or a reason to skip receipt checks. If those saved
        # reports are stale, the caller passes an empty priority set.
        retries.sort(key=lambda item:(item[1] not in priority_codes,
                                      item[0],item[4]!="KOSPI",item[1]))
        chosen.extend(item[:4] for item in retries[:limit-len(chosen)])
    return asof,members,retained,chosen


def _matching_amount(rows, identifiers):
    amounts=[]
    for row in rows:
        if (row.get("sj_div")!="CF" or
            row.get("account_id") not in identifiers or
            row.get("account_detail","-") not in ("","-")):
            continue
        val=_amount(row.get("thstrm_amount"))
        amounts.append(val)
    if not amounts or any(v is None for v in amounts):
        return None
    if len(set(amounts))!=1:
        return None
    return amounts[0]


def _compact_account_name(value):
    # Whitespace is the only permitted change: no punctuation stripping,
    # Unicode compatibility conversion, substring search or synonym matching.
    return "".join(value.split()) if type(value) is str and len(value)<=120 else None


def _matching_exact_nonstandard_name(rows, name):
    candidates=[r for r in rows if r.get("account_id")==NONSTANDARD_ACCOUNT_ID
                and _compact_account_name(r.get("account_nm"))==_compact_account_name(name)]
    if not candidates:
        return None
    values=[_amount(r.get("thstrm_amount")) for r in candidates]
    if any(v is None for v in values) or len(set(values))!=1:
        return None
    return values[0]


def normalize_year(rows,*,code,corp,year,scope,asof,major_receipt,
                   allow_nonstandard_names=False):
    """A single request is specific to one company, year and CF scope."""
    if type(rows) is not list or len(rows)>18000:
        raise CashflowError("bad_single_full_statement_row_count")
    selected=[]
    receipts=set()
    for row in rows:
        if type(row) is not dict:
            raise CashflowError("invalid_single_full_statement_row")
        if (row.get("corp_code")!=corp or
            ("stock_code" in row and row["stock_code"]!=code) or
            str(row.get("bsns_year"))!=str(year) or
            row.get("reprt_code")!="11011" or
            ("fs_div" in row and row["fs_div"]!=scope)):
            raise CashflowError("single_full_statement_issuer_or_fiscal_mismatch")
        if row.get("sj_div")!="CF":
            continue
        is_standard=row.get("account_id") in CFO_IDS|PPE_IDS|INTANGIBLE_IDS
        is_exact_name=(allow_nonstandard_names and
            row.get("account_id")==NONSTANDARD_ACCOUNT_ID and
            _compact_account_name(row.get("account_nm")) in
            {_compact_account_name(v) for v in NONSTANDARD_CAPEX_NAMES.values()})
        if not (is_standard or is_exact_name):
            continue
        if row.get("account_detail","-") not in ("-",""):
            continue
        if row.get("currency")!="KRW":
            if is_exact_name:
                # Quarantine only the optional nonstandard candidate. It
                # cannot stand in for KRW, but must not discard already
                # validated exact-IFRS facts from the same annual receipt.
                continue
            raise CashflowError("non_krw_standard_cashflow")
        receipt=row.get("rcept_no")
        if not _valid_receipt(receipt,year,_day(asof)):
            raise CashflowError("invalid_cashflow_receipt_or_lookahead")
        receipts.add(receipt)
        selected.append(row)
    if len(receipts)>1:
        # Mixed disclosures cannot be one three-statement fiscal year.
        raise CashflowError("mixed_cashflow_receipt_revisions")
    receipt=next(iter(receipts),None)
    cfo=_matching_amount(selected,CFO_IDS)
    ppe=_matching_amount(selected,PPE_IDS)
    intangible=_matching_amount(selected,INTANGIBLE_IDS)
    name_fallback=[]
    if allow_nonstandard_names:
        # Never resolve conflicting/malformed IFRS rows through a looser
        # name match. A fallback is allowed only when the standard ID is
        # completely absent in the same annual CF statement.
        if ppe is None and not any(r.get("account_id") in PPE_IDS for r in selected):
            ppe=_matching_exact_nonstandard_name(selected,NONSTANDARD_CAPEX_NAMES["ppe"])
            if ppe is not None and ppe>=0:name_fallback.append("ppe")
        if (intangible is None and
            not any(r.get("account_id") in INTANGIBLE_IDS for r in selected)):
            intangible=_matching_exact_nonstandard_name(
                selected,NONSTANDARD_CAPEX_NAMES["intangible"])
            if intangible is not None and intangible>=0:name_fallback.append("intangible")
    # Standard IFRS "purchase" cash outflows are stored as nonnegative
    # expense amounts here; negative or missing signs require human review.
    if ppe is not None and ppe<0: ppe=None
    if intangible is not None and intangible<0: intangible=None
    capex=ppe+intangible if ppe is not None and intangible is not None else None
    fcf=cfo-capex if cfo is not None and capex is not None else None
    result={
        "year":year,"scope":scope,"annual_receipt_id":receipt,
        "major_annual_receipt_matched":(receipt is not None
                                         and receipt==major_receipt),
        "operating_cashflow_krw":cfo,
        "ppe_capex_krw":ppe,
        "intangible_capex_krw":intangible,
        "total_capex_krw":capex,
        "free_cashflow_krw":fcf,
        "raw_source_account_rows_published":False,
        "historical_PIT_certified":False,
    }
    if name_fallback:
        result["source_account_name_fallback"]=sorted(name_fallback)
    return result


def summary(asof,members,records,major_source_hash):
    count={"attempted_company_years":0,"valid_cashflow_company_years":0,
           "complete_capex_pair_company_years":0,
           "fcf_company_years":0,"receipt_matched_fcf_company_years":0,
           "three_year_fcf_receipt_matched_issuers":0,
           "most_recent_year_receipt_matched_fcf_issuers":0}
    original_reviews=0
    original_recovered=0
    reported_total_reviews=0
    reported_total_years=0
    full_intangible_reviews=0
    full_intangible_years=0
    name_probe_years=0
    name_probe_enriched_years=0
    name_fallback_years=0
    name_fallback_components=0
    later_original_comparative_years=0
    reviewed_disaggregated_years=0
    for code,r in records.items():
        if code not in members or type(r) is not dict or r.get("scope") not in ("CFS","OFS"):
            raise CashflowError("invalid_cashflow_checkpoint_issuer")
        years=r.get("years")
        if type(years) is not dict or not set(years)<=set(map(str,YEARS)):
            raise CashflowError("invalid_cashflow_checkpoint_year")
        three=[]
        for y,z in years.items():
            if (type(z) is not dict or z.get("year")!=int(y)
                or z.get("scope")!=r["scope"]
                or z.get("raw_source_account_rows_published") is not False
                or z.get("historical_PIT_certified") is not False):
                raise CashflowError("invalid_cashflow_checkpoint_provenance")
            for n in ("operating_cashflow_krw","ppe_capex_krw",
                      "intangible_capex_krw","total_capex_krw","free_cashflow_krw"):
                v=z.get(n)
                if v is not None and (type(v) is not int or abs(v)>10**18):
                    raise CashflowError("invalid_cashflow_checkpoint_amount")
            cfo=z.get("operating_cashflow_krw")
            ppe=z.get("ppe_capex_krw")
            intangible=z.get("intangible_capex_krw")
            capex=z.get("total_capex_krw")
            fcf=z.get("free_cashflow_krw")
            if z.get('full_intangible_review') is not None or z.get('full_intangible_total') is not None:
                from v3_dart_full_intangible_total import verify as verify_full_intangible,IntangibleTotalError
                try:full_intangible_years+=int(verify_full_intangible(z))
                except (IntangibleTotalError,ValueError,KeyError,TypeError):
                    raise CashflowError('untrusted_official_full_intangible_total') from None
                if not _valid_receipt(z.get('annual_receipt_id'),int(y),_day(asof)):
                    raise CashflowError('invalid_full_intangible_receipt')
                full_intangible_reviews+=1
            reported_total=None
            if z.get('reported_total_review') is not None or z.get('reported_capex_total') is not None:
                from v3_dart_reported_capex_total import verify,ReportedTotalError
                try:reported_total=verify(z)
                except (ReportedTotalError,KeyError,TypeError,ValueError):
                    raise CashflowError('untrusted_reported_capex_total') from None
                if not _valid_receipt(z.get('annual_receipt_id'),int(y),_day(asof)):
                    raise CashflowError('invalid_reported_capex_total_receipt')
                reported_total_reviews+=1;reported_total_years+=int(reported_total is not None)
            names=z.get("source_account_name_fallback")
            if names is not None:
                if (type(names) is not list or not names
                    or names!=sorted(set(names))
                    or not set(names)<=set(NONSTANDARD_CAPEX_NAMES)
                    or ("ppe" in names and ppe is None)
                    or ("intangible" in names and intangible is None)
                    or z.get("major_annual_receipt_matched") is not True):
                    raise CashflowError("untrusted_nonstandard_capex_provenance")
                name_fallback_years+=1
                name_fallback_components+=len(names)
            if z.get("original_xbrl_review") is not None or z.get("original_xbrl_proof") is not None:
                from v3_dart_xbrl_cashflow_recovery import verify_recovery
                if not _valid_receipt(z.get("annual_receipt_id"),int(y),_day(asof)):
                    raise CashflowError("invalid_original_xbrl_receipt")
                original_recovered+=int(verify_recovery(z));original_reviews+=1
            if z.get('later_original_comparative_proof') is not None:
                from v3_dart_fcf_comparative_promotion import (
                    verify_promoted_year,ComparativePromotionError)
                try:
                    verify_promoted_year(z,years,code,y)
                except (ComparativePromotionError,KeyError,ValueError,TypeError):
                    raise CashflowError('untrusted_cross_receipt_capex_proof') from None
                later_original_comparative_years+=1
            if z.get('reviewed_disaggregated_capex') is not None:
                from v3_dart_reviewed_capex import verify
                try:
                    verify(code,y,years,_day(asof))
                except (KeyError,ValueError,TypeError):
                    raise CashflowError('untrusted_reviewed_capex_statement') from None
                reviewed_disaggregated_years+=1
            marker=z.get("account_name_recheck")
            if marker is not None:
                if (type(marker) is not dict or set(marker)!={
                        "version","receipt_id","result"}
                    or type(marker.get("version")) is not str
                    or marker.get("version") not in NAME_RECHECK_VERSIONS
                    or marker.get("result") not in ("enriched","checked_unavailable")
                    or marker.get("receipt_id")!=z.get("annual_receipt_id")
                    or z.get("major_annual_receipt_matched") is not True
                    or not _valid_receipt(marker.get("receipt_id"),int(y),_day(asof))):
                    raise CashflowError("untrusted_cashflow_name_recheck")
                name_probe_years+=1
                name_probe_enriched_years+=int(marker["result"]=="enriched")
            expected_capex=reported_total if reported_total is not None else ppe+intangible if ppe is not None and intangible is not None else None
            if capex!=expected_capex:
                raise CashflowError("untrusted_cashflow_capex_sum")
            if fcf!=(cfo-capex if cfo is not None and capex is not None else None):
                raise CashflowError("untrusted_cashflow_fcf_formula")
            if (z.get("major_annual_receipt_matched") is True and
                not isinstance(z.get("annual_receipt_id"),str)):
                raise CashflowError("cashflow_missing_receipt")
            count["attempted_company_years"]+=1
            count["valid_cashflow_company_years"]+=int(cfo is not None)
            count["complete_capex_pair_company_years"]+=int(ppe is not None and intangible is not None)
            count["fcf_company_years"]+=int(fcf is not None)
            matched=fcf is not None and z.get("major_annual_receipt_matched") is True
            count["receipt_matched_fcf_company_years"]+=int(matched)
            if y=="2025" and matched:
                count["most_recent_year_receipt_matched_fcf_issuers"]+=1
            three.append((int(y),matched))
        if len(three)==3 and all(v for _,v in three):
            count["three_year_fcf_receipt_matched_issuers"]+=1
    if original_reviews:
        count.update(original_xbrl_reviewed_years=original_reviews,original_xbrl_recovered_years=original_recovered)
    if reported_total_reviews:
        count.update(reported_capex_total_reviewed_years=reported_total_reviews,
                     reported_combined_capex_company_years=reported_total_years)
    if full_intangible_reviews:
        count.update(full_intangible_reviewed_years=full_intangible_reviews,
                     official_full_intangible_total_company_years=full_intangible_years)
    if name_probe_years or name_fallback_years:
        count.update(name_recheck_company_years=name_probe_years,
            name_recheck_enriched_company_years=name_probe_enriched_years,
            nonstandard_capex_name_company_years=name_fallback_years,
            nonstandard_capex_name_components=name_fallback_components)
    source=("OpenDART API plus receipt/entity/scope/CFO-anchored original IFRS XBRL" if original_recovered else SOURCE_WITH_NAMES if name_fallback_years else SOURCE_IFRS)
    if reported_total_years:source+=' plus receipt-matched reported PPE+intangible purchase totals'
    if full_intangible_years:source+=' plus verified FSS whole IAS38 intangible purchase totals'
    if later_original_comparative_years:
        source+=' plus CFO-matched later-original annual CAPEX comparatives'
        count['later_original_comparative_years']=later_original_comparative_years
    if reviewed_disaggregated_years:
        source+=' plus reviewed DART investing cash-flow purchase subtotals'
        count['reviewed_disaggregated_capex_years']=reviewed_disaggregated_years
    return {"schema":SCHEMA,"asof":asof,"listed_total":len(members),
            "source_major_history_sha256":major_source_hash,
            "source_fiscal_years":list(YEARS),
            "source":source,
            "cashflow_accounting_consolidated_with_major_source":False,
            "three_year_fcf_financial15_certified":False,
            "current_year_fcf_valuation15_certified":False,
            "official_six_pillar_100pt_certified":False,
            "historical_PIT_certified":False,
            "raw_dart_rows_published":False,
            "metrics":count,
            "records":dict(sorted(records.items()))}


def verify_state(state,market,catalog,bulk,strict_fingerprint=True):
    asof,members=asof_members(market,catalog,bulk)
    if (type(state) is not dict or state.get("schema")!=SCHEMA or
        state.get("asof")!=asof or state.get("listed_total")!=len(members) or
        state.get("historical_PIT_certified") is not False or
        state.get("raw_dart_rows_published") is not False or
        state.get("official_six_pillar_100pt_certified") is not False or
        type(state.get("records")) is not dict):
        raise CashflowError("invalid_cashflow_snapshot_identity")
    if (strict_fingerprint and
        state.get("source_major_history_sha256")!=fingerprint(bulk)):
        raise CashflowError("cashflow_source_changed_after_rebase")
    # A newer bulk archive may append unrelated filings. Permit that
    # without a needless refetch, but NEVER carry a "receipt matched" FCF
    # across a changed or missing major-account filing for the same issuer.
    for code,issuer in state["records"].items():
        if type(issuer) is not dict:
            raise CashflowError("invalid_cashflow_checkpoint_issuer")
        scope=issuer.get("scope")
        current=bulk.get("records",{}).get(code,{}).get("years",{})
        for year,record in issuer.get("years",{}).items():
            if (type(record) is dict and
                record.get("major_annual_receipt_matched") is True):
                major=current.get(year,{}).get(scope,{})
                if (type(major) is not dict or
                    major.get("receipt_id")!=record.get("annual_receipt_id")):
                    raise CashflowError("cashflow_major_receipt_changed")
    calculated=summary(asof,members,state["records"],
                       state.get("source_major_history_sha256"))
    if state!=calculated:
        raise CashflowError("cashflow_summary_or_snapshot_tampered")
    return True


def merge_account_name_recheck(prior,observed):
    """Add only independently re-observed missing facts from one receipt.

    Existing non-null CFO/CAPEX can never be overwritten, including by a
    different value under the same receipt. A response with no recognized CF
    rows leaves every previously observed amount untouched and is marked
    checked to avoid an hourly blind-refetch loop.
    """
    if (type(prior) is not dict or type(observed) is not dict or
        prior.get("major_annual_receipt_matched") is not True or
        (prior.get("account_name_recheck") is not None and
         (type(prior["account_name_recheck"]) is not dict or
          prior["account_name_recheck"].get("version") != LEGACY_NAME_RECHECK_VERSION or
          prior["account_name_recheck"].get("receipt_id") != prior.get("annual_receipt_id"))) or
        prior.get("year")!=observed.get("year") or
        prior.get("scope")!=observed.get("scope") or
        (observed.get("annual_receipt_id") is not None and
         observed.get("annual_receipt_id")!=prior.get("annual_receipt_id"))):
        raise CashflowError("cashflow_name_recheck_receipt_or_scope_mismatch")
    updated=dict(prior)
    added=[]
    columns=(("operating_cashflow_krw",None),
             ("ppe_capex_krw","ppe"),
             ("intangible_capex_krw","intangible"))
    used=set(prior.get("source_account_name_fallback",[]))
    observed_names=set(observed.get("source_account_name_fallback",[]))
    for field,name in columns:
        old,new=prior.get(field),observed.get(field)
        if old is not None and new is not None and old!=new:
            raise CashflowError("cashflow_name_recheck_original_amount_conflict")
        if old is None and new is not None:
            updated[field]=new
            added.append(field)
            if name in observed_names:
                used.add(name)
    if used:updated["source_account_name_fallback"]=sorted(used)
    ppe=updated["ppe_capex_krw"]
    intangible=updated["intangible_capex_krw"]
    capex=ppe+intangible if ppe is not None and intangible is not None else None
    if prior.get('reported_capex_total') is not None:
        from v3_dart_reported_capex_total import verify,ReportedTotalError
        try:capex=verify(prior)
        except ReportedTotalError:raise CashflowError('untrusted_reported_capex_total') from None
    updated["total_capex_krw"]=capex
    cfo=updated["operating_cashflow_krw"]
    updated["free_cashflow_krw"]=cfo-capex if cfo is not None and capex is not None else None
    updated["account_name_recheck"]={
        "version":NAME_RECHECK_VERSION,
        "receipt_id":prior["annual_receipt_id"],
        "result":"enriched" if added else "checked_unavailable"}
    if updated.get('reported_capex_total') is not None:
        try:verify(updated)
        except ReportedTotalError:raise CashflowError('reported_total_recheck_conflict') from None
    return updated


def collect(market,catalog,bulk,prior,*,key,session,corp_cache=None,
            issuer_limit=MAX_ISSUERS,on_progress=None,
            recover_missing_names=False,priority_codes=()):
    asof,members,retained,chosen=plan(
        market,catalog,bulk,prior=prior,limit=issuer_limit,
        recover_missing_names=recover_missing_names,
        priority_codes=priority_codes)
    state=dict(retained)
    if not chosen:
        return summary(asof,members,state,fingerprint(bulk)),0
    if type(key) is not str or len(key)<20:
        raise CashflowError("missing_cashflow_api_key")
    mapping=verified_company_mapping_cache(corp_cache,asof,members)
    calls=0
    if mapping is None:
        try:
            resp=session.get("https://opendart.fss.or.kr/api/corpCode.xml",
                             params={"crtfc_key":key},timeout=(20,95))
            calls+=1
            resp.raise_for_status()
            mapping=_mapping(resp.content,set(members))
        except Exception:
            raise CashflowError("cashflow_corp_mapping_unavailable") from None
    cutoff=time.monotonic()+MAX_SECONDS
    for _,code,scope,pending in chosen:
        if code not in mapping:
            # Unmapped code not confused with observed zero-CF: try again in
            # later scheduled census, never persist an invented company record.
            continue
        years=dict(state.get(code,{"scope":scope,"years":{}})["years"])
        for year in pending:
            if calls>=MAX_SOURCE_CALLS or time.monotonic()>=cutoff:
                raise CashflowError("cashflow_safe_call_or_time_budget")
            try:
                resp=session.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                    params={"crtfc_key":key,"corp_code":mapping[code],
                            "bsns_year":str(year),"reprt_code":"11011",
                            "fs_div":scope},timeout=32)
                calls+=1
                resp.raise_for_status()
                payload=resp.json()
            except Exception:
                raise CashflowError("cashflow_provider_http_or_json_failure") from None
            if type(payload) is not dict:
                raise CashflowError("cashflow_unexpected_provider_payload")
            status=payload.get("status")
            if status=="013":
                rows=[]
            elif status=="000":
                rows=payload.get("list")
                if type(rows) is not list or not rows:
                    raise CashflowError("cashflow_bad_success_response")
            else:
                raise CashflowError("cashflow_provider_stop")
            major=bulk["records"][code]["years"][str(year)][scope]
            recheck=(str(year) in years)
            result=normalize_year(rows,code=code,corp=mapping[code],year=year,
                                  scope=scope,asof=asof,
                                  major_receipt=major["receipt_id"],
                                  allow_nonstandard_names=recheck and recover_missing_names)
            if recheck:
                if not recover_missing_names:
                    raise CashflowError("unexpected_cashflow_account_name_recheck")
                result=merge_account_name_recheck(years[str(year)],result)
            years[str(year)]=result
            state[code]={"scope":scope,"years":years}
            snap=summary(asof,members,state,fingerprint(bulk))
            if on_progress:
                on_progress(snap)
    return summary(asof,members,state,fingerprint(bulk)),calls


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market",default="docs/data.json")
    p.add_argument("--catalog",default="docs/v22-market-catalog.json")
    p.add_argument("--major",default="docs/v3-dart-bulk-history.json")
    p.add_argument("--state",default="docs/v3-dart-cashflow-reference.json")
    p.add_argument("--cache",default=".cache/v3-dart-company-codes.json")
    p.add_argument("--issuer-limit",type=int,default=MAX_ISSUERS)
    p.add_argument("--mode",choices=("dry-run","live","recover-name-fallback",
                                    "verify","rebind"),default="dry-run")
    a=p.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))
    market=read(a.market)
    catalog=read(a.catalog)
    bulk=read(a.major)
    state_path=Path(a.state)
    prior=read(a.state) if state_path.is_file() else None
    priority=set()
    if a.mode=="recover-name-fallback":
        # Calculate this from the independently reconciled actual R1d report,
        # not from a potentially stale or hand-edited UI rank field.
        from v3_dart_xbrl_capex_pilot import operational_gap_priority
        root=Path(a.catalog).parent
        score_file=root/"v3-operational-research-scores.json"
        compare_file=root/"v3-operational-research-comparison.json"
        if score_file.is_file() and compare_file.is_file():
            priority=operational_gap_priority(
                read(score_file),read(compare_file),catalog,bulk)
    asof,members,_,chosen=plan(market,catalog,bulk,prior=prior,limit=a.issuer_limit,
        recover_missing_names=a.mode=="recover-name-fallback",
        priority_codes=priority)
    if a.mode=="dry-run":
        print(json.dumps({"status":"dry_run_no_api","asof":asof,
             "eligible_next_company_count":len(chosen),"source_calls":0,
             "cashflow_scores_certified":False},sort_keys=True))
        return
    if a.mode=="rebind":
        # Only after a git rebase: no network, no account substitution.
        # verify_state checks each previously receipt-matched statement
        # against the current major-history issuer/year/scope/receipt.
        if prior is None:
            raise CashflowError("cashflow_verified_state_missing")
        refreshed=retained_current_market_cashflow(
            market,catalog,bulk,prior)
        refreshed=summary(asof,members,refreshed["records"],fingerprint(bulk))
        removed_years=(prior["metrics"]["attempted_company_years"]
                       -refreshed["metrics"]["attempted_company_years"])
        write_json(state_path,refreshed)
        print(json.dumps({"status":"receipt_rebound_revised_years_quarantined",
             "source_calls":0,"quarantined_stale_company_years":removed_years,
             "persisted_issuer_years":
             refreshed["metrics"]["attempted_company_years"],
             "full_v3_ready":False},sort_keys=True))
        return
    if a.mode=="verify":
        if prior is None:
            raise CashflowError("cashflow_verified_state_missing")
        verify_state(prior,market,catalog,bulk)
        print("verified_source_only_dart_cashflow_checkpoint")
        return
    import requests
    key=os.getenv("DART_API_KEY","")
    cache=read(a.cache) if Path(a.cache).is_file() else None
    saved=0
    def checkpoint(state):
        nonlocal saved
        write_json(state_path,state)
        saved+=1
    try:
        finished,calls=collect(market,catalog,bulk,prior,key=key,
                 session=requests.Session(),corp_cache=cache,
                 issuer_limit=a.issuer_limit,on_progress=checkpoint,
                 recover_missing_names=a.mode=="recover-name-fallback",
                 priority_codes=priority)
    except CashflowError as exc:
        if not saved:
            raise
        print(json.dumps({"status":"partial_observed_cashflow_saved",
             "source_year_checkpoints":saved,
             "halt_cause":str(exc),"full_v3_ready":False},sort_keys=True))
        return
    write_json(state_path,finished)
    print(json.dumps({"status":"source_derived_cashflow_research_only",
        "actual_fiscal_source_calls":calls,
        "persisted_issuer_years":finished["metrics"]["attempted_company_years"],
        "observed_cfo_years":finished["metrics"]["valid_cashflow_company_years"],
        "observed_fcf_years":finished["metrics"]["fcf_company_years"],
        "fully_matched_three_fcf_issuers":
        finished["metrics"]["three_year_fcf_receipt_matched_issuers"],
        "account_name_recheck_company_years":
        finished["metrics"].get("name_recheck_company_years",0),
        "account_name_recheck_enriched_years":
        finished["metrics"].get("name_recheck_enriched_company_years",0),
        "full_v3_ready":False},sort_keys=True))


if __name__=="__main__":
    main()
