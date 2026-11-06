"""Append receipt-verified R1d completion targets to the existing source queue."""
from copy import deepcopy

import v3_dart_2025_owner_profit_probe as proof
from v3_dart_bulk_history import _stable_digest
from v3_v30b_release_gate import r1d_public_reconciles, R1D_PART_LIMITS


def extend(catalog,book,priority,prior,operational,comparison,eps):
    # Validate every retained original record before changing its selection
    # anchor. Numeric values, account IDs and observation dates are untouched.
    day,_,_,retained,_=proof.plan(catalog,book,priority,prior)
    if operational.get('asof')!=day:
        return priority,prior,set(),{'status':'waiting_current_operational_snapshot','added':0}
    if r1d_public_reconciles(operational,comparison,catalog) is None:
        raise proof.ProfitProbeError('unreconciled_completion_priority')
    if (not isinstance(eps,dict) or eps.get('source_sha256',{}).get('annual_book')!=_stable_digest(book)):
        return priority,prior,set(),{'status':'waiting_current_eps_receipts','added':0}
    from v3_dart_reported_eps import plan as eps_plan
    _,_,_,reported,_=eps_plan(catalog,book,eps)
    annuals={r['code']:r for r in book['records']}
    eligible=set()
    for code,row in operational['rows'].items():
        gaps=[(area,key) for area,parts in R1D_PART_LIMITS.items() for key,maximum in parts.items()
              if maximum-row['parts'][area].get(key,[0,0])[1]>.00001]
        part=row['parts']['valuation'].get('per_eps_quality_growth')
        source=reported.get(code,{})
        annual=annuals.get(code,{}).get('annual') or {}
        if (gaps==[('valuation','per_eps_quality_growth')] and part and abs(part[1]-10/3)<.00001
            and source.get('status')=='reported_basic_eps'
            and source.get('metrics',{}).get('complete_three_years') is True
            and source.get('annual_receipt_id')==annual.get('receipt_id')
            and source.get('fs_div')==annual.get('fs_div')
            and annual.get('values',{}).get('owners_profit') is None):
            eligible.add(code)
    old_codes=set(priority['official_2025_owners_profit_missing_priority_codes'])
    added=eligible-old_codes
    expanded=deepcopy(priority)
    if added:
        queue=sorted(old_codes|added)
        expanded['official_2025_owners_profit_missing_priority_codes']=queue
        expanded['official_2025_owners_profit_missing_priority_count']=len(queue)
        existing=set(priority.get('r1d_completion_appended_codes',[]))
        expanded['r1d_completion_appended_codes']=sorted(existing|added)
        expanded['r1d_completion_selection_basis']='one_missing_earnings_yield_with_receipt_matched_complete_eps'
        expanded['r1d_completion_source_digest']=operational['source_digest']
        expanded['source_queue_is_not_current_95weight_count']=True
    # The original source parser independently validates newly admitted
    # issuer/receipt/scope/year identities and the missing-profit condition.
    proof.plan(catalog,book,expanded,None)
    rebound=proof.snapshot(day,catalog,book,expanded,retained) if prior is not None else None
    if rebound is not None:proof.verify(rebound,catalog,book,expanded)
    return expanded,rebound,eligible,{'status':'completion_first_source_queue','added':len(added),
        'eligible_one_gap_issuers':len(eligible),'already_queried_same_receipt':len(eligible&set(retained)),
        'pending_one_gap_issuers':len(eligible-set(retained)),'provider_calls':0,'points_added':0}
