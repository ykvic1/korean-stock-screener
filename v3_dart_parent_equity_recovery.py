"""Receipt-bound annual parent-equity supplement, separate from the DART book.

Only exact IFRS consolidated BS parent equity or total equity less EXPLICIT
non-controlling interests is admitted. This derived annual reference is neither official KRX PBR nor PIT
certification; original OpenDART rows never enter the public repository.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import re

import v3_dart_cashflow_enrichment as cf
import v3_dart_parent_equity_pilot as pilot
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

SCHEMA='v3-same-receipt-reported-parent-equity-v1'
CONTRACT='2025_cfs_total_equity_less_explicit_nci_v1'
DIRECT_CONTRACT='2025_cfs_reported_parent_equity_v1'
ORIGINAL_CONTRACT='2025_cfs_original_parent_equity_cfo_anchor_v1'
MAX_CAPTURE=12
MAX_RECORDS=6000
REVIEW_FILE='v3-dart-parent-equity-reviews.json'
REVIEW_SCHEMA='v3-parent-equity-source-reviews-v1'
REVIEW_READER='standard_bs_owner_or_explicit_nci_v1'
REVIEW_REASONS=frozenset(('verified_reported_parent_equity','no_annual_statement',
    'total_equity_anchor_missing_or_conflicting','neither_owner_nor_explicit_nci','unusable_parent_equity',
    'official_corp_mapping_missing','source_identity_or_receipt_rejected','source_statement_value_rejected',
    'source_statement_conflict','original_unavailable','original_parent_equity_not_observed',
    'original_equity_conflict','original_not_usable'))


class ParentEquityRecoveryError(ValueError):pass


def _annuals(book):
    records=book.get('records')
    if type(records) is not list or len(records)>6000:
        raise ParentEquityRecoveryError('untrusted_parent_equity_book')
    found={}
    for row in records:
        if type(row) is not dict or row.get('code') in found:
            raise ParentEquityRecoveryError('duplicate_parent_equity_book_issuer')
        found[row['code']]=row.get('annual')
    return found


def verify(saved,catalog,book,cash):
    if (type(saved) is not dict or saved.get('schema')!=SCHEMA
        or saved.get('investment_score_certified') is not False
        or saved.get('historical_PIT_certified') is not False
        or saved.get('raw_provider_rows_published') is not False
        or type(saved.get('records')) is not dict or len(saved['records'])>MAX_RECORDS):
        raise ParentEquityRecoveryError('untrusted_parent_equity_supplement')
    day=saved.get('observed_on')
    try:
        if not isinstance(day,str) or date.fromisoformat(day)>date.fromisoformat(catalog['date']):
            raise ValueError()
    except (ValueError,TypeError):
        raise ParentEquityRecoveryError('invalid_parent_equity_observed_day') from None
    book_rows=_annuals(book)
    members={r['code'] for r in catalog['rows']}
    for code,proof in saved['records'].items():
        if (type(code) is not str or not pilot.CODE.fullmatch(code) or code not in members
            or type(proof) is not dict or set(proof)!={
                'contract','receipt_id','year','scope','equity_anchor_krw',
                'explicit_nci_krw','owners_equity_krw','cfo_anchor_krw',
                'provider_sha256','observed_on','digest'} |
                ({'original_xbrl'} if proof.get('contract')==ORIGINAL_CONTRACT else set())):
            raise ParentEquityRecoveryError('invalid_parent_equity_proof')
        try:
            proof_date=date.fromisoformat(proof['observed_on'])
            if not date.fromisoformat(day)<=proof_date<=date.fromisoformat(catalog['date']):
                raise ValueError()
        except (ValueError,TypeError):
            raise ParentEquityRecoveryError('invalid_parent_equity_proof_date') from None
        annual=book_rows.get(code)
        anchor=cash.get('records',{}).get(code,{}).get('years',{}).get('2025',{})
        total=proof['equity_anchor_krw'];nci=proof['explicit_nci_krw'];owner=proof['owners_equity_krw']
        if (type(annual) is not dict or annual.get('year')!=2025
            or annual.get('fs_div')!='CFS' or annual.get('currency')!='KRW'
            or annual.get('values',{}).get('owners_equity') is not None
            or proof['contract'] not in (CONTRACT,DIRECT_CONTRACT,ORIGINAL_CONTRACT) or proof['year']!=2025
            or proof['scope']!='CFS'
            or proof['receipt_id']!=annual.get('receipt_id')
            or type(total) is not int or total!=annual.get('values',{}).get('equity')
            or type(owner) is not int or not 0<owner<=10**18
            or (proof['contract']==CONTRACT and
                (type(nci) is not int or abs(nci)>10**18 or total-nci!=owner))
            or (proof['contract']==DIRECT_CONTRACT and nci is not None)
            or anchor.get('major_annual_receipt_matched') is not True
            or anchor.get('annual_receipt_id')!=proof['receipt_id']
            or anchor.get('scope')!='CFS'
            or type(proof['cfo_anchor_krw']) is not int
            or proof['cfo_anchor_krw']!=anchor.get('operating_cashflow_krw')
            or type(proof['provider_sha256']) is not str
            or not re.fullmatch('[a-f0-9]{64}',proof['provider_sha256'])
            or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
            raise ParentEquityRecoveryError('untrusted_parent_equity_recovery')
        if proof['contract']==ORIGINAL_CONTRACT:
            original=proof['original_xbrl']
            if (type(original) is not dict or set(original)!={'archive_sha256','profile_sha256','basis'}
                or original['basis'] not in ('direct_parent_equity','parent_equity_column','total_minus_explicit_nci')
                or original['archive_sha256']!=proof['provider_sha256']
                or not re.fullmatch('[a-f0-9]{64}',str(original['profile_sha256']))
                or (nci is not None and (type(nci) is not int or abs(nci)>10**18 or total-nci!=owner))
                or (original['basis']=='total_minus_explicit_nci' and nci is None)):
                raise ParentEquityRecoveryError('untrusted_original_parent_equity_proof')
    return len(saved['records'])


def blank(day):
    return {'schema':SCHEMA,'observed_on':day,'records':{},
            'investment_score_certified':False,'historical_PIT_certified':False,
            'raw_provider_rows_published':False}


def merge(saved,code,annual,cfo,payload,*,corp,day,catalog,book,cash):
    out=deepcopy(saved)
    if code in out['records']:
        raise ParentEquityRecoveryError('duplicate_parent_equity_recovery')
    value,basis=pilot.parse(payload,corp=corp,code=code,receipt=annual['receipt_id'],
                            year=2025,scope='CFS',expected_equity=annual['values']['equity'])
    if value is None:
        return out,basis
    total=int(annual['values']['equity'])
    direct=basis=='same_receipt_direct_parent_equity'
    proof={'contract':DIRECT_CONTRACT if direct else CONTRACT,'receipt_id':annual['receipt_id'],'year':2025,
           'scope':'CFS','equity_anchor_krw':total,
           'explicit_nci_krw':None if direct else total-value,'owners_equity_krw':value,
           'cfo_anchor_krw':cfo,'provider_sha256':cf.fingerprint(payload),
           'observed_on':day}
    proof['digest']=cf.fingerprint(proof)
    out['records'][code]=proof
    verify(out,catalog,book,cash)
    return out,'verified_reported_parent_equity'


def overlay(book,catalog,saved,cash):
    """Build an in-memory audited book; the published raw book is untouched."""
    verify(saved,catalog,book,cash)
    patched=deepcopy(book)
    records={row['code']:row for row in patched['records']}
    observations={row['code']:row for row in patched['observations']}
    members={row['code']:row for row in catalog['rows']}
    for code,proof in saved['records'].items():
        annual=records[code]['annual']
        annual['values']['owners_equity']=proof['owners_equity_krw']
        cap=members[code].get('reference_market_cap_eok')
        if type(cap) not in (int,float) or not 0<cap<1e12:
            raise ParentEquityRecoveryError('missing_parent_equity_market_cap')
        pbr=round(cap*1e8/proof['owners_equity_krw'],3)
        if not 0<pbr<1000:
            raise ParentEquityRecoveryError('out_of_bounds_parent_equity_pbr')
        row=observations.get(code)
        if (row is None or row.get('receipt_id')!=proof['receipt_id']
            or row.get('year')!=2025 or row.get('fs_div')!='CFS'
            or row.get('market_cap_reference_day')!=catalog['date']
            or row.get('official_exchange_pbr_verified') is not False
            or row.get('pbr_simple_annual_reference') is not None):
            raise ParentEquityRecoveryError('unmatched_parent_equity_valuation_row')
        row['pbr_simple_annual_reference']=pbr
    return patched


def input_digest(code,annual,cash):
    anchor=cash['records'][code]['years']['2025']
    return cf.fingerprint({'reader':REVIEW_READER,'code':code,
        'annual':{k:annual.get(k) for k in ('year','fs_div','currency','receipt_id')},
        'equity':annual['values']['equity'],
        'cash':{k:anchor.get(k) for k in ('year','scope','annual_receipt_id',
            'major_annual_receipt_matched','operating_cashflow_krw')}})


def verify_reviews(reviews,catalog):
    if (type(reviews) is not dict or set(reviews)!={'schema','records'} or reviews['schema']!=REVIEW_SCHEMA
        or type(reviews['records']) is not dict or len(reviews['records'])>MAX_RECORDS):
        raise ParentEquityRecoveryError('untrusted_parent_equity_reviews')
    members={r['code'] for r in catalog['rows']}
    for code,r in reviews['records'].items():
        if (code not in members or type(r) is not dict or set(r)!={'code','input_digest','reason','provider_sha256','observed_on','digest'}
            or r['code']!=code or r['reason'] not in REVIEW_REASONS
            or any(type(r[k]) is not str or not re.fullmatch('[a-f0-9]{64}',r[k]) for k in ('input_digest','provider_sha256'))
            or r['digest']!=cf.fingerprint({k:v for k,v in r.items() if k!='digest'})):
            raise ParentEquityRecoveryError('invalid_parent_equity_review')
        try:
            if date.fromisoformat(r['observed_on']).isoformat()!=r['observed_on'] or r['observed_on']>catalog['date']:raise ValueError()
        except (ValueError,TypeError):raise ParentEquityRecoveryError('invalid_parent_equity_review_date') from None
    return len(reviews['records'])


def pending(report,comparison,catalog,book,cash,saved,reviews):
    verify(saved,catalog,book,cash);verify_reviews(reviews,catalog)
    eligible,needed=pilot.eligible_targets(report,comparison,catalog,book,cash,include_partial=True)
    if any(r['reason']=='verified_reported_parent_equity' and code not in saved['records']
        for code,r in reviews['records'].items()):
        raise ParentEquityRecoveryError('parent_equity_review_without_proof')
    queue=[]
    for code,annual in eligible:
        if code in saved['records']:continue
        prior=reviews['records'].get(code)
        if (prior and prior['input_digest']==input_digest(code,annual,cash)
            and not (prior['reason'] in ('official_corp_mapping_missing','source_identity_or_receipt_rejected',
                'source_statement_value_rejected','source_statement_conflict') and prior['observed_on']!=catalog['date'])):continue
        queue.append((code,annual))
    return queue,len(eligible),needed


def rejected_source_reason(payload,error):
    """Quarantine one successful-but-inconsistent issuer; outages still interrupt."""
    if type(payload) is not dict or payload.get('status')!='000':raise error
    label=str(error)
    if label in ('conflicting_parent_equity_facts','conflicting_parent_equity_identity'):
        return 'source_statement_conflict'
    if label in ('unverified_parent_equity_currency','unverified_parent_equity_amount'):
        return 'source_statement_value_rejected'
    if label in ('parent_equity_corporation_mismatch','parent_equity_fiscal_year_mismatch',
        'parent_equity_report_kind_mismatch','parent_equity_scope_mismatch',
        'parent_equity_stock_mismatch','parent_equity_fact_receipt_mismatch'):
        return 'source_identity_or_receipt_rejected'
    raise error


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('dry-run','live','verify'),default='dry-run')
    a=p.parse_args();root=Path('docs');target=root/'v3-dart-parent-equity-supplement.json';review_target=root/REVIEW_FILE
    if target.is_symlink() or review_target.is_symlink():raise ParentEquityRecoveryError('unsafe_parent_equity_output')
    read=lambda name:json.loads((root/name).read_text(encoding='utf8'))
    catalog,book,cash,report,comparison=[read(n) for n in (
        'v22-market-catalog.json','v3-dart-book-reference.json','v3-dart-cashflow-reference.json',
        'v3-operational-research-scores.json','v3-operational-research-comparison.json')]
    saved=read(target.name) if target.is_file() else blank(catalog['date'])
    reviews=read(REVIEW_FILE) if review_target.is_file() else {'schema':REVIEW_SCHEMA,'records':{}}
    if a.mode=='verify':
        print(json.dumps({'verified_recoveries':verify(saved,catalog,book,cash),
            'source_reviews':verify_reviews(reviews,catalog),'provider_calls':0}));return
    queue,eligible,needed=pending(report,comparison,catalog,book,cash,saved,reviews)
    output={'selected':min(MAX_CAPTURE,len(queue)),'remaining':len(queue),'eligible':eligible,
        'pbr_missing_issuers':needed,'verified_recoveries':len(saved['records']),
        'reviewed':0,'new_recoveries':0,'provider_calls':0,'source_readers':4,'reasons':{},'investment_score_certified':False}
    if a.mode=='dry-run' or not queue:print(json.dumps(output,sort_keys=True));return
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise ParentEquityRecoveryError('missing_parent_equity_key')
    import requests,threading,time
    from collections import Counter
    from v3_eps_parallel_collection import collect
    local=threading.local();lock=threading.Lock();sessions=[]
    members={r['code']:r for r in catalog['rows']};cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,catalog['date'],members)
    def get(endpoint,params):
        try:
            if not hasattr(local,'client'):
                local.client=requests.Session()
                with lock:sessions.append(local.client)
            r=local.client.get('https://opendart.fss.or.kr/api/'+endpoint,
                params={'crtfc_key':key,**params},timeout=(20,60))
            with lock:output['provider_calls']+=1
            r.raise_for_status();return r
        except Exception:raise ParentEquityRecoveryError('parent_equity_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    reasons=Counter()
    def make_review(code,annual,reason,provider_hash):
        review={'code':code,'input_digest':input_digest(code,annual,cash),'reason':reason,
            'provider_sha256':provider_hash,'observed_on':catalog['date']}
        review['digest']=cf.fingerprint(review)
        return review
    def fetch(choice):
        code,annual=choice
        if code not in mapping:
            return code,None,make_review(code,annual,'official_corp_mapping_missing',cf.fingerprint(mapping))
        payload=get('fnlttSinglAcntAll.json',{'corp_code':mapping[code],'bsns_year':'2025','reprt_code':'11011','fs_div':'CFS'}).json()
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        # A provider/auth/identity error interrupts; it is never a missing-fact marker.
        try:
            single,reason=merge(blank(catalog['date']),code,annual,cfo,payload,corp=mapping[code],
                day=catalog['date'],catalog=catalog,book=book,cash=cash)
        except pilot.ParentEquityError as error:
            reason=rejected_source_reason(payload,error)
            return code,None,make_review(code,annual,reason,cf.fingerprint(payload))
        if reason not in REVIEW_REASONS:raise ParentEquityRecoveryError('untrusted_parent_equity_review_reason')
        return code,single['records'].get(code),make_review(code,annual,reason,cf.fingerprint(payload))
    def checkpoint(result):
        code,proof,review=result
        if proof is not None:
            if code in saved['records']:raise ParentEquityRecoveryError('duplicate_parent_equity_recovery')
            saved['records'][code]=proof;verify(saved,catalog,book,cash)
            write_json(target,saved);output['new_recoveries']+=1
        reviews['records'][code]=review;verify_reviews(reviews,catalog);write_json(review_target,reviews)
        reasons[review['reason']]+=1;output['reviewed']+=1
    try:collect(queue[:MAX_CAPTURE],fetch,checkpoint,workers=4,deadline=time.monotonic()+360)
    finally:
        for client in sessions:client.close()
    output['verified_recoveries']=len(saved['records']);output['reasons']=dict(reasons)
    output['remaining']=len(pending(report,comparison,catalog,book,cash,saved,reviews)[0])
    print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        reason=str(error) if isinstance(error,(ParentEquityRecoveryError,pilot.ParentEquityError)) and re.fullmatch('[a-z_]{1,90}',str(error)) else 'parent_equity_source_not_verified'
        print(json.dumps({'status':'blocked_parent_equity_recovery','reason':reason}));raise SystemExit(1)
