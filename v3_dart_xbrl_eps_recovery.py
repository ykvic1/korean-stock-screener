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
import zipfile

import v3_dart_reported_eps as eps
import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
import v3_dart_xbrl_cashflow_recovery as original
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

CONTRACT='one_original_2025_filing_scope_cfo_reported_basic_eps_v1'
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


def extract_instance(data,*,scope,identifiers,cfo_anchor,diagnostic=None):
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
        _,_,ctx=original.verified_contexts(data,year=year,scope=scope,identifiers=identifiers)
        values=[]
        for fact in root:
            uri,local=xp._qualified_name(fact.tag)
            if (not ctx.get(fact.get('contextRef')) or not xp._ifrs_full_namespace(uri)
                or local!='BasicEarningsLossPerShare'):continue
            diagnostic['scoped_standard_basic_eps_facts']+=1
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


def extract_archive(raw,**kwargs):
    checked=xp.inspect_binary_archive(raw,year=2025)
    if checked['file_absent']:return {},'original_file_absent'
    found={};anchored=0
    diagnostic=kwargs.setdefault('diagnostic',Counter())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
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
    if (type(proof) is not dict or set(proof)!=keys or proof['contract']!=CONTRACT
        or proof['receipt_id']!=row.get('annual_receipt_id') or proof['scope']!=row.get('fs_div')
        or type(proof['cfo_anchor']) is not int or abs(proof['cfo_anchor'])>10**18
        or type(proof['values']) is not dict or not proof['values'] or not set(proof['values'])<={'2023','2024','2025'}
        or any(type(v) not in (int,float) or v!=row['reported_basic_eps_2023_2025'][int(y)-2023] for y,v in proof['values'].items())
        or any(type(proof[k]) is not str or not re.fullmatch('[a-f0-9]{64}',proof[k]) for k in ('archive_sha256','profile_sha256'))
        or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
        raise OriginalEPSError('invalid_eps_original_proof')
    eps.metrics(row['reported_basic_eps_2023_2025'])
    if row.get('status')!='reported_basic_eps':raise OriginalEPSError('invalid_eps_original_status')
    return True


def merge(row,values,*,reason,cfo_anchor,archive_hash,profile_hash):
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
        proof['digest']=cf.fingerprint(proof);out['original_xbrl_proof']=proof;reason='recovered'
        out['status']='reported_basic_eps';out['source_account_id']=out.get('source_account_id') or 'ifrs-full_BasicEarningsLossPerShare'
        out.pop('source_reason',None);out['comparative_basis']='one_annual_filing_same_scope'
    out['metrics']=eps.metrics(series)
    out['original_xbrl_review']={'contract':CONTRACT,'receipt_id':row['annual_receipt_id'],'reason':reason}
    verify_review(out);return out


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
    selected=[]
    for code,row in sorted(retained.items(),key=lambda item:(item[0] not in priority,-sum(v is not None for v in item[1]['reported_basic_eps_2023_2025']),item[0])):
        anchor=cash['records'].get(code,{}).get('years',{}).get('2025',{})
        if (not row['metrics']['complete_three_years'] and row.get('original_xbrl_review') is None
            and anchor.get('major_annual_receipt_matched') is True and anchor.get('scope')==row['fs_div']
            and anchor.get('annual_receipt_id')==row['annual_receipt_id'] and type(anchor.get('operating_cashflow_krw')) is int):selected.append((code,row,anchor['operating_cashflow_krw']))
    selected=selected[:a.limit];output={'selected':len(selected),'priority_issuers':len(priority),'completed':0,'recovered_issuers':0,'new_complete_issuers':0,'provider_calls':0,'context_checks':{},'reasons':{},'investment_score_certified':False}
    if a.mode=='dry-run' or not selected:print(json.dumps(output));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise OriginalEPSError('missing_original_eps_key')
    client=requests.Session();cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    def get(endpoint,params,stream=False):
        try:
            reply=client.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(20,90),stream=stream);output['provider_calls']+=1;reply.raise_for_status();return reply
        except Exception:raise OriginalEPSError('original_eps_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    diagnostic=Counter();reasons=Counter();deadline=time.monotonic()+360
    for code,row,cfo in selected:
        if time.monotonic()+120>=deadline:break
        if code not in mapping:continue
        try:profile=get('company.json',{'corp_code':mapping[code]}).json()
        except Exception:raise OriginalEPSError('original_eps_profile_unavailable') from None
        identifiers=original.company_ids(profile,mapping[code],code)
        raw=xp._read_response_limited(get('fnlttXbrl.xml',{'rcept_no':row['annual_receipt_id'],'reprt_code':'11011'},True))
        try:values,reason=extract_archive(raw,scope=row['fs_div'],identifiers=identifiers,cfo_anchor=cfo,diagnostic=diagnostic)
        except (OriginalEPSError,eps.EPSError):values,reason={},'unusable_original_eps'
        try:new=merge(row,values,reason=reason,cfo_anchor=cfo,archive_hash=hashlib.sha256(raw).hexdigest(),profile_hash=cf.fingerprint(profile))
        except OriginalEPSError:new=merge(row,{},reason='unusable_original_eps',cfo_anchor=cfo,archive_hash=hashlib.sha256(raw).hexdigest(),profile_hash=cf.fingerprint(profile))
        retained[code]=new;saved=eps.snapshot(day,catalog,book,expected,retained)
        eps.plan(catalog,book,saved);write_json(root/'v3-dart-reported-eps.json',saved)
        output['completed']+=1;output['recovered_issuers']+=int(verify_review(new));output['new_complete_issuers']+=int(new['metrics']['complete_three_years'] and not row['metrics']['complete_three_years']);reasons[new['original_xbrl_review']['reason']]+=1
    output['context_checks']=dict(diagnostic);output['reasons']=dict(reasons);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_original_eps_source','reason':'original_eps_source_not_verified'}));raise SystemExit(1)
