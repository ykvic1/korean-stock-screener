"""Receipt-, entity-, explicit-scope- and CFO-anchored original XBRL recovery.

Only existing IFRS cash-flow concepts may fill missing values. Raw archives,
context identifiers and company profiles remain ephemeral. No missing/blank/nil
fact becomes zero, and no account label or calculation sum is inferred.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import zipfile

import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

CONTRACT='original_xbrl_entity_scope_cfo_anchor_v1'
FIELDS={'operating_cashflow_krw':cf.CFO_IDS,'ppe_capex_krw':cf.PPE_IDS,
        'intangible_capex_krw':cf.INTANGIBLE_IDS}
CAPEX=set(FIELDS)-{'operating_cashflow_krw'}
X='{http://www.xbrl.org/2003/instance}'
D='{http://xbrl.org/2006/xbrldi}'
REASONS={'recovered','no_original_file','no_verified_scope_anchor','no_missing_supported_fact','unusable_original_facts'}

class RecoveryError(ValueError):pass


def company_ids(profile,corp,code):
    if (type(profile) is not dict or profile.get('status')!='000'
        or profile.get('corp_code')!=corp or profile.get('stock_code')!=code):
        raise RecoveryError('original_xbrl_company_identity_not_verified')
    ids={corp,code}
    for key,size in (('bizr_no',10),('jurir_no',13)):
        value=profile.get(key)
        if type(value) is str and re.fullmatch('[0-9-]+',value):
            compact=value.replace('-','')
            if len(compact)==size:ids.add(compact)
    return ids


def _number(node):
    if node.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1'):
        return None
    raw=(node.text or '').strip()
    if len(raw)>64 or not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',raw):return None
    value=Decimal(raw)
    return int(value) if value==value.to_integral_value() and abs(value)<=10**18 else None


def verified_contexts(data,*,year,scope,identifiers,diagnostic=None,equity_owner=False,ordinary_share=False):
    if type(equity_owner) is not bool:raise RecoveryError('invalid_original_equity_context_selector')
    if type(ordinary_share) is not bool or (ordinary_share and equity_owner):raise RecoveryError('invalid_original_ordinary_context_selector')
    diagnostic=diagnostic if diagnostic is not None else Counter()
    root=xp._safe_xbrl_xml(data)
    if root.tag!=X+'xbrl':return root,{},{}
    bindings=xp._unambiguous_namespace_bindings(data)
    contexts={};units=set()
    for ctx in root.findall(X+'context'):
        key=ctx.get('id')
        if not key or key in contexts:raise RecoveryError('duplicate_original_xbrl_context')
        contexts[key]=False;diagnostic['contexts_seen']+=1
        period=ctx.find(X+'period');entity=ctx.find(X+'entity')
        if (len(ctx.findall(X+'period'))!=1 or len(ctx.findall(X+'entity'))!=1
            or len(ctx.findall(X+'scenario'))>1
            or any(v.tag not in (X+'entity',X+'period',X+'scenario') for v in ctx)):
            continue
        if any(v.tag not in (X+'identifier',X+'segment') for v in entity) or len(entity.findall(X+'segment'))>1:continue
        identity=entity.findall(X+'identifier')
        if (len(identity)!=1 or not identity[0].get('scheme')
            or (identity[0].text or '').strip().replace('-','') not in identifiers):continue
        diagnostic['entity_matched_contexts']+=1
        if (period.findtext(X+'startDate')!=f'{year}-01-01'
            or period.findtext(X+'endDate')!=f'{year}-12-31'
            or len(period)!=2):continue
        diagnostic['exact_annual_contexts']+=1
        parts=[p for p in (entity.find(X+'segment'),ctx.find(X+'segment'),ctx.find(X+'scenario')) if p is not None]
        dims=[v for p in parts for v in p]
        if not dims:diagnostic['non_dimensional_annual_contexts']+=1
        if ordinary_share and len(dims)==2:
            # EPS only: the explicit TOTAL ordinary-share member, with no
            # entity-defined subclass, preference share or other dimension.
            if any(v.tag!=D+'explicitMember' for v in dims):continue
            pairs=[(xp._safe_qname(v.get('dimension'),bindings),xp._safe_qname(v.text,bindings)) for v in dims]
            if any(a is None or m is None or not xp._ifrs_full_namespace(a[0]) or m[0]!=a[0] for a,m in pairs):continue
            expected={('ConsolidatedAndSeparateFinancialStatementsAxis','ConsolidatedMember' if scope=='CFS' else 'SeparateMember'),
                      ('ClassesOfShareCapitalAxis','OrdinarySharesMember')}
            if len({a[0] for a,_ in pairs})==1 and {(a[1],m[1]) for a,m in pairs}==expected:
                contexts[key]=True;diagnostic['explicit_ordinary_share_contexts']+=1
            continue
        if equity_owner:
            # Only the TOTAL parent equity column. Never retained earnings,
            # NCI, an individual reserve, or a restatement/other dimension.
            if len(dims)==1 and dims[0].tag==D+'explicitMember':
                a=xp._safe_qname(dims[0].get('dimension'),bindings);m=xp._safe_qname(dims[0].text,bindings)
                if a is not None and m is not None and xp._ifrs_full_namespace(a[0]) and a[1]=='ComponentsOfEquityAxis' and m==(a[0],'EquityAttributableToOwnersOfParentMember'):
                    diagnostic['parent_equity_member_without_explicit_scope']+=1
            if len(dims)!=2 or any(v.tag!=D+'explicitMember' for v in dims):continue
            pairs=[(xp._safe_qname(v.get('dimension'),bindings),xp._safe_qname(v.text,bindings)) for v in dims]
            if any(a is None or m is None or not xp._ifrs_full_namespace(a[0]) or m[0]!=a[0] for a,m in pairs):continue
            namespaces={a[0] for a,_ in pairs}
            expected={('ConsolidatedAndSeparateFinancialStatementsAxis','ConsolidatedMember' if scope=='CFS' else 'SeparateMember'),
                      ('ComponentsOfEquityAxis','EquityAttributableToOwnersOfParentMember')}
            if len(namespaces)==1 and {(a[1],m[1]) for a,m in pairs}==expected:
                contexts[key]=True;diagnostic['explicit_owner_equity_contexts']+=1
            continue
        if len(dims)!=1 or dims[0].tag!=D+'explicitMember':continue
        axis=xp._safe_qname(dims[0].get('dimension'),bindings)
        member=xp._safe_qname(dims[0].text,bindings)
        if (axis is not None and member is not None and xp._ifrs_full_namespace(axis[0])
            and axis[1]=='ConsolidatedAndSeparateFinancialStatementsAxis'
            and member==(axis[0],'ConsolidatedMember' if scope=='CFS' else 'SeparateMember')):
            contexts[key]=True;diagnostic['explicit_scope_contexts']+=1
    return root,bindings,contexts


def instance_values(data,*,year,scope,identifiers,expected_cfo,diagnostic=None):
    root,bindings,contexts=verified_contexts(data,year=year,scope=scope,identifiers=identifiers,diagnostic=diagnostic)
    diagnostic=diagnostic if diagnostic is not None else Counter()
    units=set()
    seen=set()
    for unit in root.findall(X+'unit'):
        key=unit.get('id')
        if not key or key in seen:raise RecoveryError('duplicate_original_xbrl_unit')
        seen.add(key)
        if len(unit)==1 and unit[0].tag==X+'measure' and xp._safe_qname(unit[0].text,bindings)==('http://www.xbrl.org/2003/iso4217','KRW'):
            units.add(key)
    facts={k:{} for k in FIELDS}
    for fact in root:
        ctx=fact.get('contextRef')
        if not contexts.get(ctx) or fact.get('unitRef') not in units:continue
        uri,local=xp._qualified_name(fact.tag)
        if not xp._ifrs_full_namespace(uri):continue
        for field,ids in FIELDS.items():
            if any(local==name.split('_',1)[1] for name in ids):
                facts[field].setdefault(ctx,[]).append(_number(fact))
                diagnostic['scope_krw_standard_'+field+'_facts']+=1
    cfo=[v for values in facts['operating_cashflow_krw'].values() for v in values]
    if not cfo:return {}
    if any(v is None for v in cfo) or set(cfo)!={expected_cfo}:raise RecoveryError('unverified_original_cfo')
    anchors=set(facts['operating_cashflow_krw'])
    result={'operating_cashflow_krw':expected_cfo}
    for field in CAPEX:
        values=[v for ctx,vals in facts[field].items() if ctx in anchors for v in vals]
        if values:
            if any(v is None or v<0 for v in values) or len(set(values))!=1:raise RecoveryError('unusable_original_capex_fact')
            result[field]=values[0]
    return result


def archive_values(content,*,year,scope,identifiers,expected_cfo,diagnostic=None):
    checked=xp.inspect_binary_archive(content,year=year)
    if checked['file_absent']:return {},'no_original_file'
    found={k:[] for k in FIELDS}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for entry in z.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl')):continue
            values=instance_values(z.read(entry),year=year,scope=scope,identifiers=identifiers,expected_cfo=expected_cfo,diagnostic=diagnostic)
            for key,value in values.items():found[key].append(value)
    if not found['operating_cashflow_krw']:return {},'no_verified_scope_anchor'
    if any(len(set(values))>1 for values in found.values()):
        raise RecoveryError('conflicting_original_xbrl_facts')
    return {key:values[0] for key,values in found.items() if values},'no_missing_supported_fact'


def verify_recovery(record):
    marker=record.get('original_xbrl_review')
    if marker is None:
        if record.get('original_xbrl_proof') is not None:raise RecoveryError('original_xbrl_review_missing')
        return False
    if (type(marker) is not dict or set(marker)!={'contract','receipt_id','reason'}
        or marker['contract']!=CONTRACT or marker['receipt_id']!=record.get('annual_receipt_id')
        or marker['reason'] not in REASONS or record.get('major_annual_receipt_matched') is not True):
        raise RecoveryError('untrusted_original_xbrl_review')
    proof=record.get('original_xbrl_proof')
    if marker['reason']!='recovered':
        if proof is not None:raise RecoveryError('unexpected_original_xbrl_proof')
        return False
    keys={'contract','receipt_id','year','scope','archive_sha256','profile_sha256','cfo_anchor','fields','digest'}
    if (type(proof) is not dict or set(proof)!=keys or proof['contract']!=CONTRACT
        or proof['receipt_id']!=record.get('annual_receipt_id') or proof['year']!=record.get('year')
        or proof['scope']!=record.get('scope') or type(proof['cfo_anchor']) is not int
        or proof['cfo_anchor']!=record.get('operating_cashflow_krw')
        or type(proof['fields']) is not dict or not proof['fields'] or not set(proof['fields'])<=CAPEX
        or any(type(v) is not int or not 0<=v<=10**18 or v!=record.get(k) for k,v in proof['fields'].items())
        or any(type(proof[k]) is not str or not re.fullmatch('[a-f0-9]{64}',proof[k]) for k in ('archive_sha256','profile_sha256'))
        or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
        raise RecoveryError('untrusted_original_xbrl_recovery_proof')
    return True


def merge(old,values,*,reason,archive_hash,profile_hash):
    if (old.get('major_annual_receipt_matched') is not True or old.get('operating_cashflow_krw') is None
        or old.get('original_xbrl_review') is not None):raise RecoveryError('untrusted_original_xbrl_merge')
    if type(values) is not dict or not set(values)<=set(FIELDS):raise RecoveryError('invalid_original_xbrl_values')
    new=deepcopy(old)
    for field,value in values.items():
        if old.get(field) is not None and old[field]!=value:raise RecoveryError('original_xbrl_saved_amount_conflict')
    added={k:v for k,v in values.items() if k in CAPEX and old.get(k) is None}
    new.update(added)
    if added:
        if values.get('operating_cashflow_krw')!=old['operating_cashflow_krw']:raise RecoveryError('original_xbrl_cfo_anchor_missing')
        proof={'contract':CONTRACT,'receipt_id':old['annual_receipt_id'],'year':old['year'],'scope':old['scope'],
               'archive_sha256':archive_hash,'profile_sha256':profile_hash,'cfo_anchor':old['operating_cashflow_krw'],'fields':added}
        proof['digest']=cf.fingerprint(proof);new['original_xbrl_proof']=proof
        reason='recovered'
    new['original_xbrl_review']={'contract':CONTRACT,'receipt_id':old['annual_receipt_id'],'reason':reason}
    a,b=new.get('ppe_capex_krw'),new.get('intangible_capex_krw')
    new['total_capex_krw']=a+b if a is not None and b is not None else None
    new['free_cashflow_krw']=new['operating_cashflow_krw']-new['total_capex_krw'] if new['total_capex_krw'] is not None else None
    verify_recovery(new)
    return new


def targets(state,priority,limit,seed_years=()):
    if type(limit) is not int or not 1<=limit<=32:raise RecoveryError('invalid_original_xbrl_budget')
    queue=[]
    for code,issuer in state['records'].items():
        missing=[(year,row) for year,row in issuer['years'].items() if row.get('free_cashflow_krw') is None
                 and row.get('operating_cashflow_krw') is not None and row.get('major_annual_receipt_matched') is True
                 and row.get('original_xbrl_review') is None]
        if missing:queue.append((code not in priority,len(missing),code,missing))
    candidates=[(code,year,row) for _,_,code,missing in sorted(queue) for year,row in sorted(missing)]
    # Revisit the deterministic structural sample with this stronger verifier.
    # Sample membership never bypasses receipt/entity/scope/CFO validation.
    seeds=set(seed_years)
    return sorted(candidates,key=lambda item:(item[0],int(item[1])) not in seeds)[:limit]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('dry-run','live'),default='dry-run')
    p.add_argument('--limit',type=int,default=16)
    p.add_argument('--structural-cohort-index',type=int,
                   help='Recheck a completed original-file structural sample first (0..1000000)')
    a=p.parse_args();root=Path('docs')
    read=lambda name:json.loads((root/name).read_text())
    market,catalog,bulk,state=[read(n) for n in ('data.json','v22-market-catalog.json','v3-dart-bulk-history.json','v3-dart-cashflow-reference.json')]
    state=cf.retained_current_market_cashflow(market,catalog,bulk,state);cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True)
    priority=xp.operational_gap_priority(read('v3-operational-research-scores.json'),read('v3-operational-research-comparison.json'),catalog,bulk)
    seeds=[]
    if a.structural_cohort_index is not None:
        seeds=[(code,year) for code,year,*_ in xp.select_candidates(
            state,bulk,asof=catalog['date'],limit=xp.MAX_PROBES,
            cohort_index=a.structural_cohort_index,priority_codes=priority)]
    selected=targets(state,priority,a.limit,seeds)
    output={'selected_issuer_years':len(selected),'priority_issuers':len(priority),'provider_calls':0,'completed':0,'recovered_fields':0,'reasons':{},'scores_certified':False,'context_checks':{}}
    if a.mode=='dry-run' or not selected:print(json.dumps(output));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if len(key)!=40:raise RecoveryError('original_xbrl_key_missing')
    members={r['code']:r for r in catalog['rows']};cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,catalog['date'],members)
    session=requests.Session();profiles={};deadline=time.monotonic()+360;reasons=Counter();diagnostic=Counter()
    def request(endpoint,params,stream=False):
        try:
            reply=session.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(20,90),stream=stream)
            output['provider_calls']+=1;reply.raise_for_status();return reply
        except Exception:raise RecoveryError('original_xbrl_provider_unavailable') from None
    if mapping is None:mapping=_mapping(request('corpCode.xml',{}).content,set(members))
    for code,year,old in selected:
        if time.monotonic()+120>=deadline:break
        if code not in mapping:continue
        if code not in profiles:
            try:profile=request('company.json',{'corp_code':mapping[code]}).json()
            except Exception:raise RecoveryError('original_xbrl_profile_unavailable') from None
            profiles[code]=(company_ids(profile,mapping[code],code),cf.fingerprint(profile))
        ids,profile_hash=profiles[code]
        raw=xp._read_response_limited(request('fnlttXbrl.xml',{'rcept_no':old['annual_receipt_id'],'reprt_code':'11011'},True))
        try:values,reason=archive_values(raw,year=int(year),scope=old['scope'],identifiers=ids,expected_cfo=old['operating_cashflow_krw'],diagnostic=diagnostic)
        except RecoveryError:values,reason={},'unusable_original_facts'
        new=merge(old,values,reason=reason,archive_hash=hashlib.sha256(raw).hexdigest(),profile_hash=profile_hash)
        state['records'][code]['years'][year]=new
        state=cf.summary(catalog['date'],members,state['records'],cf.fingerprint(bulk))
        cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True)
        write_json(root/'v3-dart-cashflow-reference.json',state)
        output['completed']+=1;output['recovered_fields']+=len(new.get('original_xbrl_proof',{}).get('fields',{}));reasons[new['original_xbrl_review']['reason']]+=1
    output['reasons']=dict(reasons);output['context_checks']=dict(diagnostic);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except (RecoveryError,xp.XbrlPilotError,cf.CashflowError) as error:
        print(json.dumps({'status':'blocked_original_xbrl_recovery','reason':str(error)}));raise SystemExit(1)
    except Exception:
        print(json.dumps({'status':'blocked_original_xbrl_recovery','reason':'unexpected_original_source_error'}));raise SystemExit(1)
