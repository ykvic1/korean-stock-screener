"""All-listed missing-profit acquisition; full-observation targets go first."""
from copy import deepcopy

import v3_dart_2025_owner_profit_probe as proof
from v3_dart_bulk_history import _stable_digest
from v3_v30b_release_gate import r1d_public_reconciles, R1D_PART_LIMITS


def extend(catalog,book,priority,prior,operational,comparison,eps):
    day,_,_,retained,_=proof.plan(catalog,book,priority,prior)
    if operational.get('asof')!=day:
        return priority,prior,set(),{'status':'waiting_current_operational_snapshot','added':0}
    if r1d_public_reconciles(operational,comparison,catalog) is None:
        raise proof.ProfitProbeError('unreconciled_completion_priority')
    # EPS completeness is useful for priority, never an admission gate for
    # independently observable earnings yield. Multiple other gaps do not
    # exclude a listed issuer from source acquisition.
    reported={}
    eps_ready=(isinstance(eps,dict) and
               eps.get('source_sha256',{}).get('annual_book')==_stable_digest(book))
    if eps_ready:
        from v3_dart_reported_eps import plan as eps_plan
        _,_,_,reported,_=eps_plan(catalog,book,eps)
    annuals={r['code']:r for r in book['records']}
    eligible=set();preferred=set()
    for code,row in operational['rows'].items():
        book_row=annuals.get(code,{})
        annual=book_row.get('annual') or {}
        part=row['parts']['valuation'].get('per_eps_quality_growth')
        # 0 or EPS-only 10/3 means the earnings-yield 5/3 is missing.
        # A retained observed earnings yield (5/3 or full5) is not refetched.
        if (part is not None and part[1]>.00001 and abs(part[1]-10/3)>.00001):continue
        if (book_row.get('status')!='observed' or annual.get('year')!=2025
            or annual.get('fs_div') not in proof.PROFIT_IDS or annual.get('currency')!='KRW'
            or not proof._valid_receipt(annual.get('receipt_id'),2025,proof._day(day))
            or not isinstance(annual.get('values'),dict)
            or annual['values'].get('owners_profit') is not None):continue
        eligible.add(code)
        gaps=[(area,key) for area,parts in R1D_PART_LIMITS.items() for key,maximum in parts.items()
              if maximum-row['parts'][area].get(key,[0,0])[1]>.00001]
        source=reported.get(code,{})
        if (gaps==[('valuation','per_eps_quality_growth')]
            and source.get('status')=='reported_basic_eps'
            and source.get('metrics',{}).get('complete_three_years') is True
            and source.get('annual_receipt_id')==annual.get('receipt_id')
            and source.get('fs_div')==annual.get('fs_div')):
            preferred.add(code)
    old_codes=set(priority['official_2025_owners_profit_missing_priority_codes'])
    added=eligible-old_codes
    expanded=deepcopy(priority)
    if added:
        queue=sorted(old_codes|added)
        expanded['official_2025_owners_profit_missing_priority_codes']=queue
        expanded['official_2025_owners_profit_missing_priority_count']=len(queue)
        existing=set(priority.get('r1d_all_listed_appended_codes',[]))
        expanded['r1d_all_listed_appended_codes']=sorted(existing|added)
        expanded['r1d_completion_selection_basis']='all_listed_missing_earnings_yield_exact_annual_receipt'
        expanded['r1d_completion_source_digest']=operational['source_digest']
        expanded['source_queue_is_not_current_95weight_count']=True
    proof.plan(catalog,book,expanded,None)
    rebound=proof.snapshot(day,catalog,book,expanded,retained) if prior is not None else None
    if rebound is not None:proof.verify(rebound,catalog,book,expanded)
    return expanded,rebound,preferred,{'status':'all_listed_profit_source_queue','added':len(added),
        'eligible_missing_profit_issuers':len(eligible),'eligible_one_gap_issuers':len(preferred),
        'already_queried_same_receipt':len(eligible&set(retained)),
        'pending_all_listed_issuers':len(eligible-set(retained)),
        'pending_one_gap_issuers':len(preferred-set(retained)),
        'eps_preference_status':'current' if eps_ready else 'waiting_current_eps_receipts',
        'provider_calls':0,'points_added':0}
