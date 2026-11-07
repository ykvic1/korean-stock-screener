"""Original annual owners' profit, with independently matched receipt/scope/CFO.

A standard ProfitLoss fact in the TOTAL parent-equity member is reported
parent profit, not total group profit or comprehensive income. Member:
https://www.esma.europa.eu/sites/default/files/2024-05/ESMA32-2009130576-3011_Final_Report_amending_RTS_on_ESEF_-_2024.pdf
No absent NCI or profit is inferred as zero. Raw originals remain ephemeral.
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

import v3_dart_2025_owner_profit_probe as profit
import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_xbrl_eps_recovery import units
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,write_json

CONTRACT='original_2025_profit_receipt_entity_scope_cfo_v1'
DIMENSIONAL_ID='original-xbrl:ProfitLoss[EquityAttributableToOwnersOfParentMember]'
BASES={'direct_owner_profit','parent_equity_profit_loss'}
REASONS={'recovered','original_file_absent','no_verified_cfo_scope','no_verified_owner_profit','unusable_original_profit','unverified_company_identity'}
REJECTED_ARCHIVE_REASONS={
    'ambiguous_extension_linkage_locator','ambiguous_local_calculation_locator',
    'ambiguous_local_presentation_locator','ambiguous_local_taxonomy_element',
    'duplicate_xbrl_context_reference','unparseable_xbrl_archive_member',
    'unparseable_xbrl_instance','unsafe_xbrl_archive_member','unsafe_xbrl_xml_entities',
    'xbrl_binary_response_out_of_bounds','xbrl_zip_decompression_bounds_exceeded',
    'xbrl_zip_member_count_exceeded','xbrl_zip_unreadable'}
X=original.X

class OriginalProfitError(ValueError):pass


def extract_instance(data,*,scope,identifiers,cfo_anchor,diagnostic=None):
    diagnostic=diagnostic if diagnostic is not None else Counter()
    root,bindings,contexts=original.verified_contexts(data,year=2025,scope=scope,identifiers=identifiers,diagnostic=diagnostic)
    if root.tag!=X+'xbrl':return None,[]
    won,_=units(root,bindings);anchors=[]
    for fact in root:
        uri,local=xp._qualified_name(fact.tag)
        if (contexts.get(fact.get('contextRef')) and fact.get('unitRef') in won
            and xp._ifrs_full_namespace(uri) and local in {n.split('_',1)[1] for n in cf.CFO_IDS}):anchors.append(original._number(fact))
    if not anchors:return None,[]
    if any(v is None for v in anchors) or set(anchors)!={cfo_anchor}:raise OriginalProfitError('original_profit_cfo_conflict')
    diagnostic['cfo_anchored_instances']+=1
    _,_,parent_contexts=original.verified_contexts(data,year=2025,scope=scope,identifiers=identifiers,diagnostic=diagnostic,equity_owner=True)
    found=[];bases=set()
    direct={n.split('_',1)[1] for n in profit.PROFIT_IDS[scope]}
    for fact in root:
        uri,local=xp._qualified_name(fact.tag);ctx=fact.get('contextRef')
        if not xp._ifrs_full_namespace(uri) or fact.get('unitRef') not in won:continue
        basis=('direct_owner_profit' if contexts.get(ctx) and local in direct else
               'parent_equity_profit_loss' if scope=='CFS' and parent_contexts.get(ctx) and local=='ProfitLoss' else None)
        if basis is None:continue
        value=original._number(fact)
        if value is None:raise OriginalProfitError('unusable_original_owner_profit')
        found.append(value);bases.add(basis);diagnostic[basis+'_facts']+=1
    if len(set(found))>1:raise OriginalProfitError('conflicting_original_owner_profit')
    return (found[0],sorted(bases)) if found else (None,[])


def extract_archive(raw,**kwargs):
    # Standard IFRS facts use no extension labels or local role guesses.
    # Keep all ZIP/XML bounds, but avoid unrelated extension research gates.
    checked=xp.inspect_binary_archive(raw,year=2025,include_extension_evidence=False)
    if checked['file_absent']:return None,[],'original_file_absent'
    diagnostic=kwargs.setdefault('diagnostic',Counter());values=[];bases=set();anchored=0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl')):continue
            before=diagnostic['cfo_anchored_instances'];value,b=extract_instance(archive.read(entry),**kwargs)
            anchored+=diagnostic['cfo_anchored_instances']-before
            if value is not None:values.append(value);bases.update(b)
    if len(set(values))>1:raise OriginalProfitError('conflicting_original_profit_instances')
    return (values[0] if values else None),sorted(bases),'no_verified_owner_profit' if anchored else 'no_verified_cfo_scope'


def verify_review(row):
    marker=row.get('original_xbrl_review');proof=row.get('original_xbrl_proof')
    if marker is None:
        if proof is not None:raise OriginalProfitError('missing_original_profit_review')
        return False
    if (type(marker) is not dict or set(marker)!={'contract','receipt_id','reason'}
        or marker['contract']!=CONTRACT or marker['receipt_id']!=row.get('annual_receipt_id')
        or marker['reason'] not in REASONS):raise OriginalProfitError('invalid_original_profit_review')
    if marker['reason']!='recovered':
        if proof is not None or row.get('source_account_id')==DIMENSIONAL_ID:raise OriginalProfitError('unexpected_original_profit_proof')
        return False
    keys={'contract','receipt_id','scope','cfo_anchor','owners_profit_krw','basis','archive_sha256','profile_sha256','digest'}
    if (type(proof) is not dict or set(proof) not in (keys,keys|{'cfo_response_sha256'}) or proof['contract']!=CONTRACT
        or proof['receipt_id']!=row.get('annual_receipt_id') or proof['scope']!=row.get('fs_div')
        or type(proof['cfo_anchor']) is not int or abs(proof['cfo_anchor'])>10**18
        or type(proof['owners_profit_krw']) is not int or abs(proof['owners_profit_krw'])>10**18
        or proof['owners_profit_krw']!=row.get('owners_profit_krw')
        or type(proof['basis']) is not list or not proof['basis'] or proof['basis']!=sorted(set(proof['basis']))
        or not set(proof['basis'])<=BASES
        or ('parent_equity_profit_loss' in proof['basis'] and proof['scope']!='CFS')
        or row.get('status')!='observed_owner_profit'
        or any(type(proof[k]) is not str or not re.fullmatch('[a-f0-9]{64}',proof[k]) for k in ('archive_sha256','profile_sha256','cfo_response_sha256') if k in proof)
        or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
        raise OriginalProfitError('invalid_original_profit_proof')
    expected=('ifrs-full_ProfitLossAttributableToOwnersOfParent' if proof['scope']=='CFS' else 'ifrs-full_ProfitLoss') if 'direct_owner_profit' in proof['basis'] else DIMENSIONAL_ID
    if row.get('source_account_id')!=expected:raise OriginalProfitError('original_profit_basis_mismatch')
    return True


def merge(row,value,bases,*,reason,cfo_anchor,archive_hash,profile_hash,cfo_response_hash=None,asof=None):
    if row.get('owners_profit_krw') is not None or row.get('original_xbrl_review') is not None:raise OriginalProfitError('original_profit_would_overwrite_existing')
    out=deepcopy(row)
    if value is not None:
        proof={'contract':CONTRACT,'receipt_id':row['annual_receipt_id'],'scope':row['fs_div'],'cfo_anchor':cfo_anchor,
               'owners_profit_krw':value,'basis':bases,'archive_sha256':archive_hash,'profile_sha256':profile_hash}
        if cfo_response_hash is not None:proof['cfo_response_sha256']=cfo_response_hash
        proof['digest']=cf.fingerprint(proof);out['original_xbrl_proof']=proof;reason='recovered'
        out['status']='observed_owner_profit';out['owners_profit_krw']=value
        out['source_account_id']=(('ifrs-full_ProfitLossAttributableToOwnersOfParent' if row['fs_div']=='CFS' else 'ifrs-full_ProfitLoss') if 'direct_owner_profit' in bases else DIMENSIONAL_ID)
    out['original_xbrl_review']={'contract':CONTRACT,'receipt_id':row['annual_receipt_id'],'reason':reason}
    verify_review(out);profit._validated_record(out,asof or row['observed_on']);return out


def official_cfo_anchor(payload,*,code,corp,scope,receipt,day):
    """An independent exact-scope CF API observation, not a scope substitution."""
    status=payload.get('status') if type(payload) is dict else None
    if status=='013':return None,None
    if status!='000' or not isinstance(payload.get('list'),list) or not payload['list']:
        raise OriginalProfitError('official_cfo_anchor_provider_unavailable')
    row=cf.normalize_year(payload['list'],code=code,corp=corp,year=2025,
                          scope=scope,asof=day,major_receipt=receipt)
    if row['major_annual_receipt_matched'] is not True or row['operating_cashflow_krw'] is None:return None,None
    return row['operating_cashflow_krw'],cf.fingerprint(payload)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('dry-run','live'),default='dry-run');p.add_argument('--limit',type=int,default=8);a=p.parse_args()
    if not 1<=a.limit<=16:raise OriginalProfitError('invalid_original_profit_limit')
    root=Path('docs');read=lambda n:json.loads((root/n).read_text());target=root/'v3-dart-owner-profit-supplement.json'
    if target.is_symlink():raise OriginalProfitError('unsafe_original_profit_output')
    market,catalog,bulk,cash,book,priority,state,report,comparison,eps=[read(n) for n in ('data.json','v22-market-catalog.json','v3-dart-bulk-history.json','v3-dart-cashflow-reference.json','v3-dart-book-reference.json','v3-near-100-evidence-priority.json',target.name,'v3-operational-research-scores.json','v3-operational-research-comparison.json','v3-dart-reported-eps.json')]
    cash=cf.retained_current_market_cashflow(market,catalog,bulk,cash)
    from v3_owner_profit_completion import extend
    priority,state,preferred,_=extend(catalog,book,priority,state,report,comparison,eps)
    day,members,expected,retained,_=profit.plan(catalog,book,priority,state)
    selected=[]
    for code in sorted(preferred,key=lambda c:(retained.get(c,{}).get('status')=='quarantined_source_account',c)):
        row=retained.get(code,{});anchor=cash['records'].get(code,{}).get('years',{}).get('2025',{})
        if not row or row.get('owners_profit_krw') is not None or row.get('original_xbrl_review') is not None:continue
        if (anchor.get('major_annual_receipt_matched') is True and anchor.get('scope')==row['fs_div']
            and anchor.get('annual_receipt_id')==row['annual_receipt_id'] and type(anchor.get('operating_cashflow_krw')) is int):
            selected.append((code,row,anchor['operating_cashflow_krw']))
        elif bulk['records'].get(code,{}).get('years',{}).get('2025',{}).get(row['fs_div'],{}).get('receipt_id')==row['annual_receipt_id']:
            selected.append((code,row,None))
    output={'eligible_completion_issuers':len(selected),'independent_cfo_needed':sum(cfo is None for _,_,cfo in selected),'selected':min(a.limit,len(selected)),'completed':0,'recovered_issuers':0,'provider_calls':0,'context_checks':{},'reasons':{},'investment_score_certified':False}
    if a.mode=='dry-run' or not selected:print(json.dumps(output));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise OriginalProfitError('original_profit_key_missing')
    client=requests.Session();cache=Path('.cache/v3-dart-company-codes.json');mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    def get(endpoint,params,stream=False):
        try:
            reply=client.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(20,90),stream=stream);output['provider_calls']+=1;reply.raise_for_status();return reply
        except Exception:raise OriginalProfitError('original_profit_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    write_json(root/'v3-near-100-evidence-priority.json',priority)
    diagnostic=Counter();reasons=Counter();deadline=time.monotonic()+360
    for code,row,cfo in selected[:a.limit]:
        if time.monotonic()+120>=deadline:break
        if code not in mapping:continue
        cfo_response_hash=None
        if cfo is None:
            payload=get('fnlttSinglAcntAll.json',{'corp_code':mapping[code],'bsns_year':'2025','reprt_code':'11011','fs_div':row['fs_div']}).json()
            cfo,cfo_response_hash=official_cfo_anchor(payload,code=code,corp=mapping[code],scope=row['fs_div'],receipt=row['annual_receipt_id'],day=day)
        if cfo is None:
            new=merge(row,None,[],reason='no_verified_cfo_scope',cfo_anchor=None,archive_hash=None,profile_hash=None,asof=day)
            # Store only the negative review, without any numeric fact/proof.
            retained[code]=new;saved=profit.snapshot(day,catalog,book,priority,retained);profit.verify(saved,catalog,book,priority);write_json(target,saved)
            output['completed']+=1;reasons['no_verified_cfo_scope']+=1
            continue
        try:profile=get('company.json',{'corp_code':mapping[code]}).json()
        except Exception:raise OriginalProfitError('original_profit_profile_unavailable') from None
        if type(profile) is not dict or profile.get('status')!='000':
            raise OriginalProfitError('original_profit_profile_status_unavailable')
        try:identifiers=original.company_ids(profile,mapping[code],code)
        except original.RecoveryError:
            new=merge(row,None,[],reason='unverified_company_identity',cfo_anchor=None,archive_hash=None,profile_hash=None,asof=day)
            retained[code]=new;saved=profit.snapshot(day,catalog,book,priority,retained);profit.verify(saved,catalog,book,priority);write_json(target,saved)
            output['completed']+=1;reasons['unverified_company_identity']+=1
            continue
        raw=xp._read_response_limited(get('fnlttXbrl.xml',{'rcept_no':row['annual_receipt_id'],'reprt_code':'11011'},True))
        try:value,bases,reason=extract_archive(raw,scope=row['fs_div'],identifiers=identifiers,cfo_anchor=cfo,diagnostic=diagnostic)
        except OriginalProfitError:value,bases,reason=None,[],'unusable_original_profit'
        except xp.XbrlPilotError as error:
            if str(error) not in REJECTED_ARCHIVE_REASONS:raise
            value,bases,reason=None,[],'unusable_original_profit'
        new=merge(row,value,bases,reason=reason,cfo_anchor=cfo,archive_hash=hashlib.sha256(raw).hexdigest(),profile_hash=cf.fingerprint(profile),cfo_response_hash=cfo_response_hash,asof=day)
        retained[code]=new;saved=profit.snapshot(day,catalog,book,priority,retained);profit.verify(saved,catalog,book,priority);write_json(target,saved)
        output['completed']+=1;output['recovered_issuers']+=int(verify_review(new));reasons[new['original_xbrl_review']['reason']]+=1
    output['context_checks']=dict(diagnostic);output['reasons']=dict(reasons);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        safe={'original_profit_provider_unavailable','original_profit_profile_unavailable',
              'original_profit_profile_status_unavailable','official_cfo_anchor_provider_unavailable',
              'unexpected_xbrl_non_zip_provider_response','xbrl_response_too_large',
              'invalid_xbrl_response_chunk','original_profit_key_missing',
              'invalid_profit_recheck_marker','invalid_profit_evidence_record',
              'invalid_profit_evidence_value','invalid_profit_source_quarantine_reason'}
        reason=str(error) if type(error) in (OriginalProfitError,xp.XbrlPilotError,profit.ProfitProbeError) and str(error) in safe else 'original_owner_profit_source_not_verified'
        kinds={OriginalProfitError:'original_profit',xp.XbrlPilotError:'original_archive',profit.ProfitProbeError:'saved_profit',cf.CashflowError:'cashflow_anchor',original.RecoveryError:'company_identity'}
        print(json.dumps({'status':'blocked_original_owner_profit','reason':reason,'failure_kind':kinds.get(type(error),'unexpected_processing')}));raise SystemExit(1)
