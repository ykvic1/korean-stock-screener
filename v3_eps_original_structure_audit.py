"""Bounded anonymous diagnosis of reviewed missing EPS. Never changes a score."""
from __future__ import annotations
import argparse
from collections import Counter
import io
import json
import os
from pathlib import Path
import re
import time
import zipfile

import v3_dart_xbrl_eps_recovery as ep
import v3_dart_reported_eps as eps
import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache

X=original.X
D=original.D
L='{http://www.xbrl.org/2003/linkbase}'


def candidates(retained,cash,priority=()):
    selected=[]
    for code,row in sorted(retained.items(),key=lambda item:(item[0] not in priority,item[0])):
        anchor=cash.get('records',{}).get(code,{}).get('years',{}).get('2025',{})
        if (row.get('code')==code and row.get('fs_div') in ('CFS','OFS')
            and not row['metrics']['complete_three_years']
            and row.get('original_xbrl_review',{}).get('reason')=='no_additional_reported_basic_eps'
            and anchor.get('major_annual_receipt_matched') is True
            and anchor.get('scope')==row['fs_div']
            and anchor.get('annual_receipt_id')==row['annual_receipt_id']
            and type(anchor.get('operating_cashflow_krw')) is int):
            ep.verify_review(row)
            selected.append((code,row,anchor['operating_cashflow_krw']))
    return selected


def annual_contexts(root,bindings,identifiers,scope):
    """Research categories only; an extra dimension never authorizes an EPS."""
    output={}
    for ctx in root.findall(X+'context'):
        entity=ctx.find(X+'entity');period=ctx.find(X+'period')
        if entity is None or period is None:continue
        if (len(ctx.findall(X+'entity'))!=1 or len(ctx.findall(X+'period'))!=1
            or len(ctx.findall(X+'scenario'))>1
            or any(v.tag not in (X+'entity',X+'period',X+'scenario') for v in ctx)
            or len(entity.findall(X+'segment'))>1
            or any(v.tag not in (X+'identifier',X+'segment') for v in entity)):continue
        ids=entity.findall(X+'identifier')
        if (len(ids)!=1 or not ids[0].get('scheme')
            or (ids[0].text or '').strip().replace('-','') not in identifiers):continue
        year=next((y for y in (2023,2024,2025) if len(period)==2
            and period.findtext(X+'startDate')==f'{y}-01-01'
            and period.findtext(X+'endDate')==f'{y}-12-31'),None)
        if year is None:continue
        dims=[v for part in (entity.find(X+'segment'),ctx.find(X+'scenario')) if part is not None for v in part]
        scope_members=[];other_axes=[]
        for dim in dims:
            a=xp._safe_qname(dim.get('dimension'),bindings)
            m=xp._safe_qname(dim.text,bindings)
            if (dim.tag==D+'explicitMember' and a and m and xp._ifrs_full_namespace(a[0])
                and m[0]==a[0] and a[1]=='ConsolidatedAndSeparateFinancialStatementsAxis'):
                scope_members.append(m[1])
            else:other_axes.append(a[1] if a and xp._ifrs_full_namespace(a[0]) else 'unknown')
        expected='ConsolidatedMember' if scope=='CFS' else 'SeparateMember'
        shape=('no_explicit_scope' if not scope_members else
               'other_or_ambiguous_scope' if scope_members!=[expected] else
               'scope_only' if not other_axes else
               'scope_with_share_class_axis' if 'ClassesOfShareCapitalAxis' in other_axes else
               'scope_with_equity_axis' if 'ComponentsOfEquityAxis' in other_axes else
               'scope_with_other_dimensions')
        output[ctx.get('id')]=(year,shape)
    return output


def inspect_archive(raw,*,scope,identifiers,cfo_anchor):
    checked=xp.inspect_binary_archive(raw,year=2025,include_extension_evidence=False)
    counts=Counter()
    if checked['file_absent']:return {'original_file_absent':1}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl','.xsd')):continue
            data=archive.read(entry);root=xp._safe_xbrl_xml(data)
            # Resource counts are hints only: no concept-label binding or EPS
            # amount is authorized or retained by this diagnostic.
            counts['explicit_basic_eps_label_resources']+=sum(
                eps.canonical_basic_eps_name(''.join(label.itertext())) is not None
                for label in root.iter(L+'label'))
            if root.tag!=X+'xbrl':continue
            diagnostic=Counter()
            try:
                ep.extract_instance(data,scope=scope,identifiers=identifiers,
                                   cfo_anchor=cfo_anchor,diagnostic=diagnostic)
            except (ep.OriginalEPSError,eps.EPSError):
                if not diagnostic['cfo_anchored_instances']:raise
                counts['unusable_standard_eps_instances']+=1
            if not diagnostic['cfo_anchored_instances']:
                counts['instances_without_verified_cfo']+=1;continue
            counts['cfo_anchored_instances']+=1
            _,bindings,_=original.verified_contexts(data,year=2025,scope=scope,identifiers=identifiers)
            _,per_share=ep.units(root,bindings)
            contexts=annual_contexts(root,bindings,identifiers,scope)
            for fact in root:
                if fact.get('contextRef') not in contexts:continue
                uri,local=xp._qualified_name(fact.tag)
                kind=('standard_basic' if xp._ifrs_full_namespace(uri) and local=='BasicEarningsLossPerShare' else
                      'standard_diluted' if xp._ifrs_full_namespace(uri) and local=='DilutedEarningsLossPerShare' else
                      'other_standard_eps_named' if xp._ifrs_full_namespace(uri) and 'pershare' in local.lower() else
                      'extension_eps_named' if not xp._ifrs_full_namespace(uri) and 'pershare' in local.lower() else None)
                if kind is None:continue
                year,shape=contexts[fact.get('contextRef')]
                counts[f'{kind}_facts']+=1
                counts[f'{kind}_year_{year}']+=1
                counts[f'{kind}_{shape}']+=1
                counts[f'{kind}_'+('per_share_unit' if fact.get('unitRef') in per_share else 'other_unit')]+=1
    return dict(sorted(counts.items()))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('dry-run','live'),default='dry-run')
    p.add_argument('--limit',type=int,default=4)
    a=p.parse_args()
    if not 1<=a.limit<=4:raise ValueError('invalid_structure_sample_limit')
    read=lambda n:json.loads((Path('docs')/n).read_text())
    market,catalog,book,state,bulk,cash,report,comparison=[read(n) for n in (
        'data.json','v22-market-catalog.json','v3-dart-book-reference.json',
        'v3-dart-reported-eps.json','v3-dart-bulk-history.json','v3-dart-cashflow-reference.json',
        'v3-operational-research-scores.json','v3-operational-research-comparison.json')]
    cash=cf.retained_current_market_cashflow(market,catalog,bulk,cash)
    day,members,_,retained,_=eps.plan(catalog,book,state)
    xp.operational_gap_priority(report,comparison,catalog,bulk)
    from v3_v30b_release_gate import R1D_PART_LIMITS
    priority={code for code,row in report['rows'].items() if report.get('asof')==day and
        {(area,k) for area,parts in R1D_PART_LIMITS.items() for k,maximum in parts.items()
         if maximum-row['parts'][area].get(k,[0,0])[1]>.00001}=={('valuation','per_eps_quality_growth')}}
    choices=candidates(retained,cash,priority)
    summary={'schema':'v3-anonymous-reviewed-eps-structure-research-v1','asof':day,
        'eligible_reviewed_issuers':len(choices),'selected':min(a.limit,len(choices)),
        'single_eps_gap_selected':sum(code in priority for code,_,_ in choices[:a.limit]),
        'completed':0,'provider_calls':0,'counts':{},'observations_added':0,'scores_unchanged':True,
        'issuer_receipts_context_ids_names_and_amounts_retained':False}
    if a.mode=='dry-run' or not choices:print(json.dumps(summary,sort_keys=True));return
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise ValueError('missing_structure_probe_key')
    import requests
    session=requests.Session()
    def get(endpoint,params,stream=False):
        response=session.get('https://opendart.fss.or.kr/api/'+endpoint,
            params={'crtfc_key':key,**params},timeout=(20,90),stream=stream)
        summary['provider_calls']+=1;response.raise_for_status();return response
    cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    counts=Counter();deadline=time.monotonic()+360
    for code,row,cfo in choices[:a.limit]:
        if time.monotonic()+120>=deadline:break
        if code not in mapping:continue
        profile=get('company.json',{'corp_code':mapping[code]}).json()
        identifiers=original.company_ids(profile,mapping[code],code)
        response=get('fnlttXbrl.xml',{'rcept_no':row['annual_receipt_id'],'reprt_code':'11011'},True)
        try:raw=xp._read_response_limited(response)
        finally:response.close()
        counts.update(inspect_archive(raw,scope=row['fs_div'],identifiers=identifiers,cfo_anchor=cfo))
        summary['completed']+=1
    summary['counts']=dict(sorted(counts.items()));print(json.dumps(summary,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_eps_structure_research','reason':'source_structure_not_verified',
            'observations_added':0,'scores_unchanged':True}));raise SystemExit(1)
