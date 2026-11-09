"""Read missing parent equity from original annual XBRL with independent anchors."""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import zipfile

import v3_dart_parent_equity_recovery as base
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_capex_pilot as xp
import v3_dart_xbrl_eps_recovery as eps_original
from v3_dart_eps_prior_receipt_probe import require_archive_or_explicit_absence
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,write_json
from v3_eps_parallel_collection import collect

FILE='v3-dart-parent-equity-original-reviews.json'
READER='standard_instant_cfs_parent_equity_cfo_total_v1'
X=original.X;D=original.D


class Blocked(ValueError):pass


def instant_contexts(root,bindings,identifiers):
    """Exact 2025 year-end CFS total / TOTAL parent-equity column only."""
    result={}
    for node in root.findall(X+'context'):
        key=node.get('id')
        if not key or key in result:raise Blocked('original_equity_conflict')
        result[key]=None
        if (len(node.findall(X+'entity'))!=1 or len(node.findall(X+'period'))!=1
            or len(node.findall(X+'scenario'))>1 or any(c.tag not in (X+'entity',X+'period',X+'scenario') for c in node)):continue
        entity=node.find(X+'entity');period=node.find(X+'period');ids=entity.findall(X+'identifier')
        if (len(ids)!=1 or not ids[0].get('scheme') or (ids[0].text or '').strip().replace('-','') not in identifiers
            or len(entity.findall(X+'segment'))>1 or any(c.tag not in (X+'identifier',X+'segment') for c in entity)
            or len(period)!=1 or period.findtext(X+'instant')!='2025-12-31'):continue
        parts=[p for p in (entity.find(X+'segment'),node.find(X+'scenario')) if p is not None]
        dims=[d for p in parts for d in p]
        if len(dims) not in (1,2) or any(d.tag!=D+'explicitMember' for d in dims):continue
        pairs=[(xp._safe_qname(d.get('dimension'),bindings),xp._safe_qname(d.text,bindings)) for d in dims]
        if any(a is None or m is None or not xp._ifrs_full_namespace(a[0]) or m[0]!=a[0] for a,m in pairs):continue
        if len({a[0] for a,m in pairs})!=1:continue
        shape={(a[1],m[1]) for a,m in pairs}
        scope={('ConsolidatedAndSeparateFinancialStatementsAxis','ConsolidatedMember')}
        if shape==scope:result[key]='total'
        elif len(dims)==2 and shape==scope|{('ComponentsOfEquityAxis','EquityAttributableToOwnersOfParentMember')}:result[key]='parent'
    return result


def unique(values):
    if None in values or len(set(values))>1:raise Blocked('original_equity_conflict')
    return values[0] if values else None


def extract(raw,*,identifiers,cfo_anchor,equity_anchor):
    require_archive_or_explicit_absence(raw)
    inspected=xp.inspect_binary_archive(raw,year=2025,include_extension_evidence=False)
    if inspected['file_absent']:raise Blocked('original_unavailable')
    candidates=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl')):continue
            data=archive.read(entry)
            root,bindings,annual=original.verified_contexts(data,year=2025,scope='CFS',identifiers=identifiers)
            if root.tag!=X+'xbrl':continue
            won,_=eps_original.units(root,bindings)
            cfo=[];facts={k:[] for k in ('total','owner','nci','parent_column')}
            instant=instant_contexts(root,bindings,identifiers)
            for fact in root:
                uri,local=xp._qualified_name(fact.tag);ref=fact.get('contextRef')
                if not xp._ifrs_full_namespace(uri) or fact.get('unitRef') not in won:continue
                if annual.get(ref) and local in {s.split('_',1)[1] for s in base.cf.CFO_IDS}:cfo.append(original._number(fact))
                shape=instant.get(ref)
                kind=('total' if local=='Equity' else 'owner' if local=='EquityAttributableToOwnersOfParent' else 'nci' if local=='NoncontrollingInterests' else None)
                if shape=='total' and kind:facts[kind].append(original._number(fact))
                if shape=='parent' and local=='Equity':facts['parent_column'].append(original._number(fact))
            if not cfo:continue
            if unique(cfo)!=cfo_anchor:raise Blocked('original_equity_conflict')
            total=unique(facts['total'])
            if total is None:continue
            if total!=equity_anchor:raise Blocked('original_equity_conflict')
            owner=unique(facts['owner']);column=unique(facts['parent_column']);nci=unique(facts['nci'])
            if owner is not None and column is not None and owner!=column:raise Blocked('original_equity_conflict')
            basis='direct_parent_equity' if owner is not None else 'parent_equity_column'
            owner=owner if owner is not None else column
            if owner is None and nci is not None:owner=total-nci;basis='total_minus_explicit_nci'
            if owner is None:continue
            if owner<=0 or abs(owner)>10**18:raise Blocked('original_not_usable')
            if nci is not None and owner+nci!=total:raise Blocked('original_equity_conflict')
            candidates.append((owner,nci,basis))
    if not candidates:raise Blocked('original_parent_equity_not_observed')
    if len({(owner,nci) for owner,nci,basis in candidates})!=1:raise Blocked('original_equity_conflict')
    owner,nci,basis=candidates[0]
    return {'owner':owner,'nci':nci,'basis':basis}


def digest(code,annual,cash):return base.cf.fingerprint([READER,base.input_digest(code,annual,cash)])


def choices(catalog,book,cash,report,comparison,saved,api_reviews,reviews):
    base.verify(saved,catalog,book,cash);base.verify_reviews(api_reviews,catalog);base.verify_reviews(reviews,catalog)
    eligible,_=base.pilot.eligible_targets(report,comparison,catalog,book,cash,include_partial=True)
    selected=[]
    for code,annual in eligible:
        if code in saved['records']:continue
        api=api_reviews['records'].get(code)
        if not api or api['input_digest']!=base.input_digest(code,annual,cash):continue
        old=reviews['records'].get(code)
        if old and old['input_digest']==digest(code,annual,cash) and not (old['reason']=='official_corp_mapping_missing' and old['observed_on']!=catalog['date']):continue
        selected.append((code,annual))
    return selected


def proof(code,annual,cfo,value,archive_hash,profile_hash,day):
    record={'contract':base.ORIGINAL_CONTRACT,'receipt_id':annual['receipt_id'],'year':2025,'scope':'CFS',
        'equity_anchor_krw':annual['values']['equity'],'explicit_nci_krw':value['nci'],
        'owners_equity_krw':value['owner'],'cfo_anchor_krw':cfo,'provider_sha256':archive_hash,'observed_on':day,
        'original_xbrl':{'archive_sha256':archive_hash,'profile_sha256':profile_hash,'basis':value['basis']}}
    record['digest']=base.cf.fingerprint(record);return record


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('dry-run','live','verify'),default='dry-run');a=p.parse_args()
    root=Path('docs');read=lambda n:json.loads((root/n).read_text());target=root/'v3-dart-parent-equity-supplement.json';review_target=root/FILE
    if target.is_symlink() or review_target.is_symlink():raise base.ParentEquityRecoveryError('unsafe_original_parent_output')
    catalog,book,cash,report,comparison=[read(n) for n in ('v22-market-catalog.json','v3-dart-book-reference.json',
        'v3-dart-cashflow-reference.json','v3-operational-research-scores.json','v3-operational-research-comparison.json')]
    saved=read(target.name) if target.exists() else base.blank(catalog['date'])
    empty={'schema':base.REVIEW_SCHEMA,'records':{}}
    api_reviews=read(base.REVIEW_FILE) if (root/base.REVIEW_FILE).exists() else empty
    reviews=read(FILE) if review_target.exists() else {'schema':base.REVIEW_SCHEMA,'records':{}}
    base.verify(saved,catalog,book,cash);base.verify_reviews(reviews,catalog)
    if a.mode=='verify':print(json.dumps({'verified':len(saved['records']),'source_reviews':len(reviews['records']),'provider_calls':0}));return
    queue=choices(catalog,book,cash,report,comparison,saved,api_reviews,reviews)
    output={'remaining':len(queue),'selected':min(12,len(queue)),'reviewed':0,'new_recoveries':0,'provider_calls':0,'source_readers':4,'reasons':{}}
    if a.mode!='live' or not queue:print(json.dumps(output,sort_keys=True));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise base.ParentEquityRecoveryError('original_parent_key_missing')
    members={r['code']:r for r in catalog['rows']};cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.exists() else None,catalog['date'],members)
    local=threading.local();lock=threading.Lock();sessions=[]
    def get(endpoint,params,stream=False):
        try:
            if not hasattr(local,'client'):
                local.client=requests.Session()
                with lock:sessions.append(local.client)
            response=local.client.get('https://opendart.fss.or.kr/api/'+endpoint,
                params={'crtfc_key':key,**params},timeout=(20,90),stream=stream)
            with lock:output['provider_calls']+=1
            response.raise_for_status();return response
        except Exception:raise base.ParentEquityRecoveryError('original_parent_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    def review(code,annual,reason,source_hash):
        row={'code':code,'input_digest':digest(code,annual,cash),'reason':reason,
            'provider_sha256':source_hash,'observed_on':catalog['date']};row['digest']=base.cf.fingerprint(row);return row
    def fetch(choice):
        code,annual=choice
        if code not in mapping:return code,None,review(code,annual,'official_corp_mapping_missing',base.cf.fingerprint(mapping))
        profile=get('company.json',{'corp_code':mapping[code]}).json()
        if profile.get('status')!='000':raise base.ParentEquityRecoveryError('original_parent_profile_unavailable')
        try:ids=original.company_ids(profile,mapping[code],code)
        except original.RecoveryError:return code,None,review(code,annual,'source_identity_or_receipt_rejected',base.cf.fingerprint(profile))
        response=get('fnlttXbrl.xml',{'rcept_no':annual['receipt_id'],'reprt_code':'11011'},True)
        try:raw=xp._read_response_limited(response)
        finally:response.close()
        source_hash=hashlib.sha256(raw).hexdigest();cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        require_archive_or_explicit_absence(raw)
        try:value=extract(raw,identifiers=ids,cfo_anchor=cfo,equity_anchor=annual['values']['equity'])
        except Blocked as error:return code,None,review(code,annual,str(error),source_hash)
        except (xp.XbrlPilotError,original.RecoveryError,zipfile.BadZipFile):return code,None,review(code,annual,'original_not_usable',source_hash)
        found=proof(code,annual,cfo,value,source_hash,base.cf.fingerprint(profile),catalog['date'])
        return code,found,review(code,annual,'verified_reported_parent_equity',source_hash)
    counts=Counter()
    def checkpoint(result):
        code,found,row=result
        if found:
            if code in saved['records']:raise base.ParentEquityRecoveryError('duplicate_original_parent_equity')
            saved['records'][code]=found;base.verify(saved,catalog,book,cash);write_json(target,saved);output['new_recoveries']+=1
        reviews['records'][code]=row;base.verify_reviews(reviews,catalog);write_json(review_target,reviews)
        counts[row['reason']]+=1;output['reviewed']+=1
    try:collect(queue[:12],fetch,checkpoint,workers=4,deadline=time.monotonic()+480)
    finally:
        for client in sessions:client.close()
    output['reasons']=dict(counts);output['remaining']=len(choices(catalog,book,cash,report,comparison,saved,api_reviews,reviews))
    print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        label=str(error) if re.fullmatch('[a-z_]{1,90}',str(error)) else 'original_parent_source_not_verified'
        print(json.dumps({'status':'blocked_original_parent_equity','reason':label}));raise SystemExit(1)
