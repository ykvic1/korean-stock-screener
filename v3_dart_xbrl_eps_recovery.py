"""Recover reported basic EPS from one original annual filing, never calculate EPS.

Original issuer identity, explicit accounting scope and the saved same-receipt
2025 CFO must match. All EPS comparatives remain from that one 2025 filing.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import threading
import zipfile

import v3_dart_reported_eps as eps
import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
import v3_dart_xbrl_cashflow_recovery as original
from v3_eps_explicit_labels import EPSLabelError
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

CONTRACT='one_original_2025_filing_scope_cfo_reported_basic_eps_v1'
CONTEXT_CONTRACT='one_original_2025_explicit_ordinary_and_literal_basic_eps_v2'
CONTEXT_REVIEW='original_xbrl_context_recheck'
REASONS={'recovered','original_file_absent','no_verified_cfo_scope','no_additional_reported_basic_eps','unusable_original_eps'}
X=original.X

class OriginalEPSError(ValueError):pass


def units(root,bindings):
    won=set();per_share=set();seen=set()
    for node in root.findall(X+'unit'):
        key=node.get('id')
        if not key or key in seen:raise OriginalEPSError('duplicate_eps_original_unit')
        seen.add(key)
        if len(node)==1 and node[0].tag==X+'measure' and xp._safe_qname(node[0].text,bindings)==('http://www.xbrl.org/2003/iso4217','KRW'):
            won.add(key)
        div=node.find(X+'divide')
        if div is not None and len(node)==1 and len(div)==2:
            num=div.find(X+'unitNumerator');den=div.find(X+'unitDenominator')
            if (num is not None and den is not None and len(num)==len(den)==1
                and num[0].tag==den[0].tag==X+'measure'
                and xp._safe_qname(num[0].text,bindings)==('http://www.xbrl.org/2003/iso4217','KRW')
                and xp._safe_qname(den[0].text,bindings)==('http://www.xbrl.org/2003/instance','shares')):
                per_share.add(key)
    return won,per_share


def extract_instance(data,*,scope,identifiers,cfo_anchor,diagnostic=None,ordinary_shares=False,extension_tags=()):
    diagnostic=diagnostic if diagnostic is not None else Counter()
    root,bindings,contexts=original.verified_contexts(data,year=2025,scope=scope,identifiers=identifiers,diagnostic=diagnostic)
    if root.tag!=X+'xbrl':return {}
    won,per_share=units(root,bindings)
    anchors=[]
    for fact in root:
        uri,local=xp._qualified_name(fact.tag)
        if (contexts.get(fact.get('contextRef')) and fact.get('unitRef') in won
            and xp._ifrs_full_namespace(uri) and local in {v.split('_',1)[1] for v in cf.CFO_IDS}):
            anchors.append(original._number(fact))
    if not anchors:return {}
    if any(v is None for v in anchors) or set(anchors)!={cfo_anchor}:raise OriginalEPSError('original_eps_cfo_scope_conflict')
    diagnostic['cfo_anchored_instances']+=1
    output={}
    for year in (2023,2024,2025):
        _,_,ctx=original.verified_contexts(data,year=year,scope=scope,identifiers=identifiers,ordinary_share=ordinary_shares,diagnostic=diagnostic)
        values=[]
        for fact in root:
            uri,local=xp._qualified_name(fact.tag)
            standard=xp._ifrs_full_namespace(uri) and local=='BasicEarningsLossPerShare'
            if not ctx.get(fact.get('contextRef')) or not (standard or fact.tag in extension_tags):continue
            diagnostic['scoped_reported_basic_eps_facts']+=1
            diagnostic['scoped_standard_basic_eps_facts' if standard else 'literal_extension_basic_eps_facts']+=1
            if fact.get('unitRef') not in per_share:
                diagnostic['eps_unit_unverified']+=1;continue
            if fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1'):
                raise OriginalEPSError('nil_original_basic_eps')
            value=eps.amount((fact.text or '').strip())
            if value is None:raise OriginalEPSError('blank_original_basic_eps')
            values.append(value)
        if values:
            if len(set(values))!=1:raise OriginalEPSError('conflicting_original_basic_eps')
            output[str(year)]=values[0]
    return output


def extract_archive(raw,*,extended=False,binding_result=None,**kwargs):
    # EPS uses standard IFRS facts only; unrelated extension-label research
    # must not reject them. Mandatory archive/XML safety checks still run.
    checked=xp.inspect_binary_archive(raw,year=2025,include_extension_evidence=False)
    if checked['file_absent']:return {},'original_file_absent'
    found={};anchored=0
    diagnostic=kwargs.setdefault('diagnostic',Counter())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if extended:
            from v3_eps_explicit_labels import bind_archive
            tags,digest=bind_archive(archive,archive.infolist())
            kwargs.update(ordinary_shares=True,extension_tags=tags)
            if binding_result is not None:binding_result['sha256']=digest
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl')):continue
            before=diagnostic['cfo_anchored_instances']
            values=extract_instance(archive.read(entry),**kwargs)
            anchored+=diagnostic['cfo_anchored_instances']-before
            for year,value in values.items():
                if year in found and found[year]!=value:raise OriginalEPSError('conflicting_eps_original_instances')
                found[year]=value
    return found,'no_additional_reported_basic_eps' if anchored else 'no_verified_cfo_scope'


def verify_review(row):
    marker=row.get('original_xbrl_review');proof=row.get('original_xbrl_proof')
    recheck=row.get(CONTEXT_REVIEW)
    if recheck is not None:
        if (type(recheck) is not dict or set(recheck)!={'contract','receipt_id','previous_review'}
            or recheck['contract']!=CONTEXT_CONTRACT or recheck['receipt_id']!=row.get('annual_receipt_id')
            or marker is None):raise OriginalEPSError('invalid_eps_context_recheck')
        prior=recheck['previous_review']
        if prior is not None and (type(prior) is not dict or set(prior)!={'contract','receipt_id','reason'}
            or prior['contract']!=CONTRACT or prior['receipt_id']!=row.get('annual_receipt_id')
            or prior['reason']!='no_additional_reported_basic_eps'):
            raise OriginalEPSError('invalid_eps_previous_context_review')
    if marker is None:
        if proof is not None:raise OriginalEPSError('missing_eps_original_review')
        return False
    if (type(marker) is not dict or set(marker)!={'contract','receipt_id','reason'}
        or marker['contract']!=CONTRACT or marker['receipt_id']!=row.get('annual_receipt_id')
        or marker['reason'] not in REASONS):raise OriginalEPSError('invalid_eps_original_review')
    if marker['reason']!='recovered':
        if proof is not None:raise OriginalEPSError('unexpected_eps_original_proof')
        return False
    keys={'contract','receipt_id','scope','cfo_anchor','archive_sha256','profile_sha256','values','digest'}
    if recheck is not None:keys|={'context_contract','label_binding_sha256'}
    if (type(proof) is not dict or set(proof) not in (keys,keys|{'cfo_response_sha256'}) or proof['contract']!=CONTRACT
        or proof['receipt_id']!=row.get('annual_receipt_id') or proof['scope']!=row.get('fs_div')
        or type(proof['cfo_anchor']) is not int or abs(proof['cfo_anchor'])>10**18
        or (recheck is not None and (proof['context_contract']!=CONTEXT_CONTRACT
            or type(proof['label_binding_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}',proof['label_binding_sha256'])))
        or type(proof['values']) is not dict or not proof['values'] or not set(proof['values'])<={'2023','2024','2025'}
        or any(type(v) not in (int,float) or v!=row['reported_basic_eps_2023_2025'][int(y)-2023] for y,v in proof['values'].items())
        or any(type(proof[k]) is not str or not re.fullmatch('[a-f0-9]{64}',proof[k]) for k in ('archive_sha256','profile_sha256','cfo_response_sha256') if k in proof)
        or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
        raise OriginalEPSError('invalid_eps_original_proof')
    eps.metrics(row['reported_basic_eps_2023_2025'])
    if row.get('status')!='reported_basic_eps':raise OriginalEPSError('invalid_eps_original_status')
    return True


def merge(row,values,*,reason,cfo_anchor,archive_hash,profile_hash,cfo_response_hash=None,binding_hash=None):
    if row.get('original_xbrl_review') is not None:raise OriginalEPSError('eps_original_already_reviewed')
    if type(values) is not dict or not set(values)<={'2023','2024','2025'}:raise OriginalEPSError('invalid_eps_original_values')
    out=deepcopy(row);series=out['reported_basic_eps_2023_2025'];added={}
    for year,value in values.items():
        i=int(year)-2023
        if series[i] is not None and series[i]!=value:raise OriginalEPSError('saved_original_eps_amount_conflict')
        if series[i] is None:series[i]=value;added[year]=value
    if added:
        proof={'contract':CONTRACT,'receipt_id':row['annual_receipt_id'],'scope':row['fs_div'],
               'cfo_anchor':cfo_anchor,'archive_sha256':archive_hash,'profile_sha256':profile_hash,'values':added}
        if cfo_response_hash is not None:proof['cfo_response_sha256']=cfo_response_hash
        if row.get(CONTEXT_REVIEW) is not None:
            proof.update(context_contract=CONTEXT_CONTRACT,label_binding_sha256=binding_hash)
        proof['digest']=cf.fingerprint(proof);out['original_xbrl_proof']=proof;reason='recovered'
        out['status']='reported_basic_eps';out['source_account_id']=out.get('source_account_id') or (
            eps.ORIGINAL_REPORTED_BASIC_EPS_ID if row.get(CONTEXT_REVIEW) is not None else 'ifrs-full_BasicEarningsLossPerShare')
        out.pop('source_reason',None);out['comparative_basis']='one_annual_filing_same_scope'
    out['metrics']=eps.metrics(series)
    out['original_xbrl_review']={'contract':CONTRACT,'receipt_id':row['annual_receipt_id'],'reason':reason}
    verify_review(out);return out


def prepare_context_recheck(row):
    verify_review(row)
    previous=row.get('original_xbrl_review')
    if (row.get(CONTEXT_REVIEW) is not None or row.get('original_xbrl_proof') is not None
        or (previous is not None and previous['reason']!='no_additional_reported_basic_eps')):
        raise OriginalEPSError('eps_context_recheck_would_overwrite')
    out=deepcopy(row);out.pop('original_xbrl_review',None)
    out[CONTEXT_REVIEW]={'contract':CONTEXT_CONTRACT,'receipt_id':row['annual_receipt_id'],'previous_review':deepcopy(previous)}
    return out


def recovery_candidates(retained,cash,bulk,priority=()):
    """Same-scope annual CF can be fetched separately; never borrow other scope."""
    selected=[]
    for code,row in sorted(retained.items(),key=lambda item:(item[0] not in priority,
            -sum(v is not None for v in item[1]['reported_basic_eps_2023_2025']),item[0])):
        if (row.get('code')!=code or row.get('fs_div') not in ('CFS','OFS')
            or row['metrics']['complete_three_years'] or row.get(CONTEXT_REVIEW) is not None
            or (row.get('original_xbrl_review') is not None and
                (row['original_xbrl_review']['reason']!='no_additional_reported_basic_eps'
                 or row.get('original_xbrl_proof') is not None))):
            continue
        anchor=cash.get('records',{}).get(code,{}).get('years',{}).get('2025',{})
        if (anchor.get('major_annual_receipt_matched') is True and anchor.get('scope')==row['fs_div']
            and anchor.get('annual_receipt_id')==row['annual_receipt_id']
            and type(anchor.get('operating_cashflow_krw')) is int):
            selected.append((code,row,anchor['operating_cashflow_krw']))
        elif (bulk.get('records',{}).get(code,{}).get('years',{}).get('2025',{})
                .get(row['fs_div'],{}).get('receipt_id')==row['annual_receipt_id']):
            selected.append((code,row,None))
    return selected


def official_cfo_anchor(payload,*,code,corp,scope,receipt,day):
    status=payload.get('status') if type(payload) is dict else None
    if status=='013':return None,None
    if status!='000' or not isinstance(payload.get('list'),list) or not payload['list']:
        raise OriginalEPSError('official_eps_cfo_anchor_provider_unavailable')
    row=cf.normalize_year(payload['list'],code=code,corp=corp,year=2025,
                          scope=scope,asof=day,major_receipt=receipt)
    if row['major_annual_receipt_matched'] is not True or row['operating_cashflow_krw'] is None:return None,None
    return row['operating_cashflow_krw'],cf.fingerprint(payload)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--mode',choices=('dry-run','live'),default='dry-run');parser.add_argument('--limit',type=int,default=8);a=parser.parse_args()
    if not 1<=a.limit<=16:raise OriginalEPSError('invalid_original_eps_limit')
    root=Path('docs');read=lambda n:json.loads((root/n).read_text())
    if (root/'v3-dart-reported-eps.json').is_symlink():raise OriginalEPSError('unsafe_original_eps_output')
    market,catalog,bulk,cash,book,state=[read(n) for n in ('data.json','v22-market-catalog.json','v3-dart-bulk-history.json','v3-dart-cashflow-reference.json','v3-dart-book-reference.json','v3-dart-reported-eps.json')]
    cash=cf.retained_current_market_cashflow(market,catalog,bulk,cash)
    day,members,expected,retained,_=eps.plan(catalog,book,state)
    report,comparison=read('v3-operational-research-scores.json'),read('v3-operational-research-comparison.json')
    xp.operational_gap_priority(report,comparison,catalog,bulk)  # Independently reconcile the source-work ledger.
    from v3_v30b_release_gate import R1D_PART_LIMITS
    priority=set()
    if report.get('asof')==day:
        for code,row in report['rows'].items():
            gaps={(area,key) for area,parts in R1D_PART_LIMITS.items() for key,maximum in parts.items() if maximum-row['parts'][area].get(key,[0,0])[1]>.00001}
            if gaps=={('valuation','per_eps_quality_growth')}:priority.add(code)
    selected=recovery_candidates(retained,cash,bulk,priority)
    output={'source_readers':4,'eligible_missing_eps_issuers':len(selected),'independent_cfo_needed':sum(cfo is None for _,_,cfo in selected),
        'selected':min(a.limit,len(selected)),'priority_issuers':len(priority),'completed':0,'recovered_issuers':0,'new_complete_issuers':0,'provider_calls':0,'context_checks':{},'reasons':{},'investment_score_certified':False}
    selected=selected[:a.limit]
    if a.mode=='dry-run' or not selected:print(json.dumps(output));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise OriginalEPSError('missing_original_eps_key')
    local=threading.local();clients=[];api_lock=threading.Lock()
    cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    def get(endpoint,params,stream=False):
        try:
            if not hasattr(local,'client'):
                local.client=requests.Session()
                with api_lock:clients.append(local.client)
            reply=local.client.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(20,90),stream=stream)
            with api_lock:output['provider_calls']+=1
            reply.raise_for_status();return reply
        except Exception:raise OriginalEPSError('original_eps_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    diagnostic=Counter();reasons=Counter();deadline=time.monotonic()+360
    def fetch(choice):
        code,row,cfo=choice
        if code not in mapping:return None
        checks=Counter()
        base=prepare_context_recheck(row)
        cfo_response_hash=None
        if cfo is None:
            payload=get('fnlttSinglAcntAll.json',{'corp_code':mapping[code],'bsns_year':'2025','reprt_code':'11011','fs_div':row['fs_div']}).json()
            cfo,cfo_response_hash=official_cfo_anchor(payload,code=code,corp=mapping[code],scope=row['fs_div'],receipt=row['annual_receipt_id'],day=day)
        if cfo is None:
            new=merge(base,{},reason='no_verified_cfo_scope',cfo_anchor=None,archive_hash=None,profile_hash=None)
            return code,row,new,checks
        try:profile=get('company.json',{'corp_code':mapping[code]}).json()
        except Exception:raise OriginalEPSError('original_eps_profile_unavailable') from None
        identifiers=original.company_ids(profile,mapping[code],code)
        response=get('fnlttXbrl.xml',{'rcept_no':row['annual_receipt_id'],'reprt_code':'11011'},True)
        try:raw=xp._read_response_limited(response)
        finally:response.close()
        bindings={}
        try:values,reason=extract_archive(raw,scope=row['fs_div'],identifiers=identifiers,cfo_anchor=cfo,diagnostic=checks,extended=True,binding_result=bindings)
        except (OriginalEPSError,eps.EPSError,EPSLabelError):values,reason={},'unusable_original_eps'
        try:new=merge(base,values,reason=reason,cfo_anchor=cfo,archive_hash=hashlib.sha256(raw).hexdigest(),profile_hash=cf.fingerprint(profile),cfo_response_hash=cfo_response_hash,binding_hash=bindings.get('sha256'))
        except OriginalEPSError:new=merge(base,{},reason='unusable_original_eps',cfo_anchor=cfo,archive_hash=hashlib.sha256(raw).hexdigest(),profile_hash=cf.fingerprint(profile))
        return code,row,new,checks
    def checkpoint(result):
        code,row,new,checks=result
        retained[code]=new;saved=eps.snapshot(day,catalog,book,expected,retained)
        eps.plan(catalog,book,saved);write_json(root/'v3-dart-reported-eps.json',saved)
        diagnostic.update(checks)
        output['completed']+=1;output['recovered_issuers']+=int(verify_review(new));output['new_complete_issuers']+=int(new['metrics']['complete_three_years'] and not row['metrics']['complete_three_years']);reasons[new['original_xbrl_review']['reason']]+=1
    from v3_eps_parallel_collection import collect
    try:collect(selected,fetch,checkpoint,workers=4,deadline=deadline)
    finally:
        for client in clients:client.close()
    output['context_checks']=dict(diagnostic);output['reasons']=dict(reasons);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_original_eps_source','reason':'original_eps_source_not_verified'}));raise SystemExit(1)
