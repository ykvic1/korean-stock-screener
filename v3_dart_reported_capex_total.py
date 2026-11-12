"""Receipt-bound explicitly reported PPE + intangible cash purchase totals.

Never infer either missing component as zero or sum a partial list of assets.
Only literal combined purchase labels in the annual CF statement may supply
an independently reported total; existing CFO/components remain immutable.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from collections import Counter
import json
import os
from pathlib import Path
import re
import time

import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,write_json

CONTRACT='same_receipt_reported_ppe_intangible_cash_purchase_total_v1'
NAMES=frozenset((
    '유형자산및무형자산의취득','유형자산및무형자산취득','유형자산과무형자산의취득','유형자산과무형자산취득',
    '유형및무형자산의취득','유형및무형자산취득','유무형자산의취득','유무형자산취득',
    '유·무형자산의취득','유ㆍ무형자산의취득','유형·무형자산의취득','유형ㆍ무형자산의취득',
    '유형,무형자산의취득',
    '유·무형자산취득','유ㆍ무형자산취득','유형·무형자산취득','유형ㆍ무형자산취득','유형,무형자산취득',
))
REASONS={'recovered','no_reported_total','unverified_cfo','known_amount_conflict','unusable_source'}

class ReportedTotalError(ValueError):pass


def canonical(value):
    if type(value) is not str or len(value)>120:return None
    compact=''.join(value.split())
    return compact if compact in NAMES else None


def parse(payload,old,*,code,corp,day,diagnostics=None):
    status=payload.get('status') if type(payload) is dict else None
    if status=='013':rows=[]
    elif status=='000' and isinstance(payload.get('list'),list) and payload['list']:rows=payload['list']
    else:raise ReportedTotalError('reported_total_provider_unavailable')
    normalized=cf.normalize_year(rows,code=code,corp=corp,year=old['year'],scope=old['scope'],asof=day,major_receipt=old['annual_receipt_id'])
    found=[]
    for row in rows:
        name=canonical(row.get('account_nm'))
        if (row.get('sj_div')!='CF' or row.get('account_detail','-') not in ('','-') or name is None):continue
        account=row.get('account_id')
        if diagnostics is not None:
            public=(account if type(account) is str and (account==cf.NONSTANDARD_ACCOUNT_ID or re.fullmatch(r'(?:dart|ifrs-full|ifrs)_[A-Za-z]{1,120}',account)) else 'other_unpublished_id')
            if public not in diagnostics and len(diagnostics)>=8:public='other_unpublished_id'
            diagnostics[public]+=1
        if account!=cf.NONSTANDARD_ACCOUNT_ID:continue
        if row.get('currency')!='KRW' or row.get('rcept_no')!=old['annual_receipt_id']:
            raise ReportedTotalError('unmatched_reported_total_source')
        amount=cf._amount(row.get('thstrm_amount'))
        if amount is None or amount<0:raise ReportedTotalError('invalid_reported_total_amount')
        found.append((name,amount))
    if not found:return normalized,None
    if len({v for _,v in found})!=1:raise ReportedTotalError('conflicting_reported_totals')
    proof={'contract':CONTRACT,'receipt_id':old['annual_receipt_id'],'year':old['year'],'scope':old['scope'],
           'cfo_anchor':normalized['operating_cashflow_krw'],'account_id':cf.NONSTANDARD_ACCOUNT_ID,
           'account_name':sorted(n for n,_ in found)[0],'amount_krw':found[0][1],'source_sha256':cf.fingerprint(payload)}
    proof['digest']=cf.fingerprint(proof)
    return normalized,proof


def verify(row):
    marker=row.get('reported_total_review');proof=row.get('reported_capex_total')
    if marker is None:
        if proof is not None:raise ReportedTotalError('missing_reported_total_review')
        return None
    if (type(marker) is not dict or set(marker)!={'contract','receipt_id','reason'}
        or marker['contract']!=CONTRACT or marker['receipt_id']!=row.get('annual_receipt_id')
        or marker['reason'] not in REASONS or row.get('major_annual_receipt_matched') is not True):
        raise ReportedTotalError('invalid_reported_total_review')
    if marker['reason']!='recovered':
        if proof is not None:raise ReportedTotalError('unexpected_reported_total_proof')
        return None
    keys={'contract','receipt_id','year','scope','cfo_anchor','account_id','account_name','amount_krw','source_sha256','digest'}
    if (type(proof) is not dict or set(proof)!=keys or proof['contract']!=CONTRACT
        or proof['receipt_id']!=row.get('annual_receipt_id') or proof['year']!=row.get('year') or proof['scope']!=row.get('scope')
        or type(proof['cfo_anchor']) is not int or proof['cfo_anchor']!=row.get('operating_cashflow_krw')
        or proof['account_id']!=cf.NONSTANDARD_ACCOUNT_ID or proof['account_name'] not in NAMES
        or type(proof['amount_krw']) is not int or not 0<=proof['amount_krw']<=10**18
        or proof['amount_krw']!=row.get('total_capex_krw')
        or type(proof['source_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}',proof['source_sha256'])
        or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
        raise ReportedTotalError('invalid_reported_total_proof')
    total=proof['amount_krw'];known=[row.get(k) for k in ('ppe_capex_krw','intangible_capex_krw')]
    if any(v is not None and (type(v) is not int or v<0 or v>total) for v in known):raise ReportedTotalError('reported_total_component_conflict')
    if all(v is not None for v in known) and sum(known)!=total:raise ReportedTotalError('reported_total_component_sum_conflict')
    return total


def merge(old,normalized,proof,reason='no_reported_total'):
    if old.get('free_cashflow_krw') is not None or old.get('reported_total_review') is not None:
        raise ReportedTotalError('reported_total_would_overwrite_observation')
    if old.get('major_annual_receipt_matched') is not True or type(old.get('operating_cashflow_krw')) is not int:
        raise ReportedTotalError('unverified_reported_total_anchor')
    out=deepcopy(old)
    if proof is not None:
        if (normalized is None or normalized.get('major_annual_receipt_matched') is not True
            or normalized.get('annual_receipt_id')!=old['annual_receipt_id'] or normalized.get('scope')!=old['scope']
            or normalized.get('year')!=old['year'] or normalized.get('operating_cashflow_krw')!=old['operating_cashflow_krw']):
            reason='unverified_cfo'
        elif any(old.get(k) is not None and normalized.get(k) is not None and old[k]!=normalized[k]
                 for k in ('ppe_capex_krw','intangible_capex_krw')):
            reason='known_amount_conflict'
        else:
            total=proof['amount_krw'];values=[old.get(k) for k in ('ppe_capex_krw','intangible_capex_krw')]
            if any(v is not None and (v<0 or v>total) for v in values) or all(v is not None for v in values) and sum(values)!=total:
                reason='known_amount_conflict'
            else:
                out['reported_capex_total']=proof;out['total_capex_krw']=total
                out['free_cashflow_krw']=old['operating_cashflow_krw']-total;reason='recovered'
    out['reported_total_review']={'contract':CONTRACT,'receipt_id':old['annual_receipt_id'],'reason':reason}
    verify(out);return out


def targets(state,priority,limit,completion_priority=(),threshold_priority=(),*,only_sub90=False):
    if type(limit) is not int or not 1<=limit<=40:raise ReportedTotalError('invalid_reported_total_budget')
    completion_priority=set(completion_priority)
    issuers=[]
    for code,issuer in state['records'].items():
        if only_sub90 and code not in threshold_priority:continue
        missing=[(y,r) for y,r in issuer['years'].items() if r.get('free_cashflow_krw') is None]
        rows=[(y,r) for y,r in issuer['years'].items() if r.get('free_cashflow_krw') is None
              and type(r.get('operating_cashflow_krw')) is int and r.get('major_annual_receipt_matched') is True and r.get('reported_total_review') is None]
        if not rows:continue
        started=any(r.get('reported_capex_total') is not None for r in issuer['years'].values())
        both=any(r.get('ppe_capex_krw') is None and r.get('intangible_capex_krw') is None for _,r in rows)
        # A reported TOTAL can fill missing PPE, intangible, or both. Spend
        # scarce calls first on the issuer's final missing financial factor,
        # then on whole three-year FCF completion, rather than on the number
        # of separate CAPEX components absent in one year.
        whole=(len(missing)==len(rows))
        issuers.append((code not in threshold_priority,code not in completion_priority,not whole,len(rows),
                        code not in priority,not started,not both,code,rows))
    return [(code,y,row) for *_,code,rows in sorted(issuers) for y,row in sorted(rows)][:limit]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('dry-run','live'),default='dry-run');p.add_argument('--limit',type=int,default=8);p.add_argument('--focus',choices=('all','sub90'),default='all');a=p.parse_args()
    root=Path('docs');read=lambda n:json.loads((root/n).read_text());target=root/'v3-dart-cashflow-reference.json'
    if target.is_symlink():raise ReportedTotalError('unsafe_reported_total_output')
    market,catalog,bulk,state=[read(n) for n in ('data.json','v22-market-catalog.json','v3-dart-bulk-history.json',target.name)]
    state=cf.retained_current_market_cashflow(market,catalog,bulk,state);cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True)
    operational=read('v3-operational-research-scores.json')
    priority=xp.operational_gap_priority(operational,read('v3-operational-research-comparison.json'),catalog,bulk)
    from v3_v30b_release_gate import R1D_PART_LIMITS
    threshold_priority={code for code in priority if operational['rows'][code]['available'] < 90}
    completion_only=set()
    if operational.get('asof')==catalog['date']:
        for code,row in operational['rows'].items():
            gaps={(area,key) for area,parts in R1D_PART_LIMITS.items()
                  for key,maximum in parts.items()
                  if maximum-row['parts'][area].get(key,[0,0])[1]>.00001}
            if gaps=={('financial','cashflow_fcf_persistence')}:
                completion_only.add(code)
    # Audit the *real unreviewed receipt-bound years*, not just mathematical
    # score-gap candidates. Exact issuer/year eligibility is checked again
    # by targets before any official provider call.
    actionable_90 = {
        code:sum(
            row.get('free_cashflow_krw') is None
            and type(row.get('operating_cashflow_krw')) is int
            and row.get('major_annual_receipt_matched') is True
            and row.get('reported_total_review') is None
            for row in state['records'][code]['years'].values()
        )
        for code in threshold_priority if code in state['records']
    }
    actionable_90 = {code:n for code,n in actionable_90.items() if n}
    selected=targets(state,priority,a.limit,completion_only,threshold_priority,
                     only_sub90=(a.focus=='sub90'))
    output={'selected_issuer_years':len(selected),'selected_sub90_issuers':len({code for code,_,_ in selected if code in threshold_priority}),'priority_issuers':len(priority),
            'focus':a.focus,'unreviewed_sub90_issuer_count':len(actionable_90),
            'unreviewed_sub90_company_year_count':sum(actionable_90.values()),
            'completed':0,'recovered_years':0,'provider_calls':0,'reasons':{},'candidate_account_id_counts':{},'investment_score_certified':False}
    if a.mode=='dry-run' or not selected:print(json.dumps(output));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise ReportedTotalError('missing_reported_total_key')
    members={r['code']:r for r in catalog['rows']};cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,catalog['date'],members)
    client=requests.Session()
    def get(endpoint,params):
        try:
            reply=client.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(20,90));output['provider_calls']+=1;reply.raise_for_status();return reply
        except Exception:raise ReportedTotalError('reported_total_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    deadline=time.monotonic()+300;reasons=Counter();diagnostics=Counter()
    for code,year,old in selected:
        if time.monotonic()+95>=deadline:break
        if code not in mapping:continue
        try:payload=get('fnlttSinglAcntAll.json',{'corp_code':mapping[code],'bsns_year':year,'reprt_code':'11011','fs_div':old['scope']}).json()
        except Exception:raise ReportedTotalError('reported_total_provider_unavailable') from None
        try:
            normalized,proof=parse(payload,old,code=code,corp=mapping[code],day=catalog['date'],diagnostics=diagnostics)
            new=merge(old,normalized,proof)
        except (ReportedTotalError,cf.CashflowError) as error:
            if str(error)=='reported_total_provider_unavailable':raise
            new=merge(old,None,None,'unusable_source')
        state['records'][code]['years'][year]=new
        state=cf.summary(catalog['date'],members,state['records'],cf.fingerprint(bulk));cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True);write_json(target,state)
        output['completed']+=1;output['recovered_years']+=int(new['reported_total_review']['reason']=='recovered');reasons[new['reported_total_review']['reason']]+=1
    output['reasons']=dict(reasons);output['candidate_account_id_counts']=dict(diagnostics);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_reported_capex_total','reason':'reported_total_source_not_verified'}));raise SystemExit(1)
