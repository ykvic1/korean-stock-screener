"""Latest-receipt and common-share-basis audit; never writes scored EPS."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date,datetime
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import zipfile
from zoneinfo import ZoneInfo

import v3_dart_reported_eps as eps
import v3_dart_eps_prior_receipt_probe as prior
import v3_dart_xbrl_eps_recovery as original_eps
import v3_dart_xbrl_cashflow_recovery as original
import v3_dart_xbrl_capex_pilot as xp
import v3_dart_cashflow_enrichment as cf
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,write_json
from v3_eps_explicit_labels import bind_archive,EPSLabelError
from v3_eps_parallel_collection import collect

SCHEMA='v3-three-annual-receipt-eps-basis-audit-v1'
CONTRACT='three_latest_annual_cfo_eps_and_exact_2024_weighted_ordinary_shares_v1'
FILE='v3-eps-basis-audit.json'
READER_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
X=original.X
YEARS=('2023','2024','2025')
PASSED=frozenset(('basis_checks_passed','same_filing_eps_completed'))
REASONS=frozenset(('same_filing_eps_completed','basis_checks_passed','expected_receipt_missing','expected_receipt_not_current',
    'latest_annual_source_absent','latest_receipt_changed','latest_annual_cfo_missing',
    'latest_annual_source_not_comparable',
    'latest_cfo_conflict','latest_2023_eps_conflict','latest_2023_eps_unavailable',
    'latest_current_eps_conflict',
    'original_unavailable','original_facts_conflict','original_eps_unavailable',
    'comparative_cfo_unavailable','comparative_cfo_conflict',
    'weighted_ordinary_shares_missing','weighted_ordinary_shares_conflict','original_not_usable'))


class AuditError(ValueError):pass
class SourceUnavailable(AuditError):pass
class Blocked(AuditError):pass


def share_number(raw):
    if type(raw) is not str or len(raw)>64 or not re.fullmatch(r'[+]?[0-9]+(?:\.[0-9]+)?',raw):return None
    value=Decimal(raw)
    if not 0<value<=10**18:return None
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else str(int(value))


def unique(values):
    if len(set(values))>1:raise Blocked('original_facts_conflict')
    return values[0] if values else None


def share_accuracy(node,value):
    token=node.get('decimals')
    if token=='INF' or (token is None and node.get('precision')=='INF'):return 'INF'
    if type(token) is not str or not re.fullmatch(r'[+]?[0-9]{1,2}',token):return None
    places=int(token);fraction=len(value.split('.')[1]) if '.' in value else 0
    return str(places) if fraction<=places<=64 else None


def read_filing(raw,*,filing_year,scope,identifiers,cfo_anchor):
    prior.require_archive_or_explicit_absence(raw)
    checked=xp.inspect_binary_archive(raw,year=filing_year,include_extension_evidence=False)
    if checked['file_absent']:raise Blocked('original_unavailable')
    facts={kind:{y:[] for y in YEARS if int(y)<=filing_year} for kind in ('eps','cfo','shares')}
    counts=Counter();anchored=False
    accuracy={y:[] for y in facts['shares']}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        try:tags,binding=bind_archive(archive,archive.infolist())
        except EPSLabelError:
            tags=frozenset();binding=cf.fingerprint([])
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl')):continue
            data=archive.read(entry)
            root,bindings,ctx=original.verified_contexts(data,year=filing_year,scope=scope,identifiers=identifiers)
            if root.tag!=X+'xbrl':continue
            won,per_share=original_eps.units(root,bindings)
            cfo=[original._number(f) for f in root if ctx.get(f.get('contextRef'))
                and f.get('unitRef') in won and xp._ifrs_full_namespace(xp._qualified_name(f.tag)[0])
                and xp._qualified_name(f.tag)[1] in {v.split('_',1)[1] for v in cf.CFO_IDS}]
            if not cfo:continue
            if None in cfo or set(cfo)!={cfo_anchor}:raise Blocked('latest_cfo_conflict')
            anchored=True
            shares_units={u.get('id') for u in root.findall(X+'unit') if len(u)==1 and u[0].tag==X+'measure'
                and xp._safe_qname(u[0].text,bindings)==('http://www.xbrl.org/2003/instance','shares')}
            for year in facts['eps']:
                plain=ctx if int(year)==filing_year else original.contexts_from_instance(
                    root,bindings,year=int(year),scope=scope,identifiers=identifiers)
                ordinary=original.contexts_from_instance(root,bindings,year=int(year),
                    scope=scope,identifiers=identifiers,ordinary_share=True)
                for fact in root:
                    uri,local=xp._qualified_name(fact.tag);standard=xp._ifrs_full_namespace(uri);ref=fact.get('contextRef')
                    if standard and plain.get(ref) and local in {v.split('_',1)[1] for v in cf.CFO_IDS} and fact.get('unitRef') in won:
                        value=original._number(fact)
                        if value is None:raise Blocked('original_not_usable')
                        facts['cfo'][year].append(value)
                    basic=(standard and local=='BasicEarningsLossPerShare') or fact.tag in tags
                    if basic and ordinary.get(ref) and fact.get('unitRef') in per_share:
                        if fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1'):raise Blocked('original_not_usable')
                        value=eps.amount((fact.text or '').strip())
                        if value is None:raise Blocked('original_not_usable')
                        facts['eps'][year].append(value)
                    if year=='2024' and standard and local=='WeightedAverageShares':
                        counts['standard_weighted_share_facts_seen']+=1
                        if ordinary.get(ref):
                            counts['weighted_share_contexts_matched']+=1
                            if fact.get('unitRef') in shares_units:
                                counts['weighted_share_units_matched']+=1
                                value=None if fact.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true','1') else share_number((fact.text or '').strip())
                                if value is None:raise Blocked('weighted_ordinary_shares_missing')
                                precision=share_accuracy(fact,value)
                                if precision is None:raise Blocked('weighted_ordinary_shares_missing')
                                facts['shares'][year].append(value)
                                accuracy[year].append(precision)
    if not anchored:raise Blocked('original_unavailable')
    return {**{kind:{y:unique(v) for y,v in years.items()} for kind,years in facts.items()},
        'share_accuracy':{y:('INF' if values and all(v=='INF' for v in values) else str(min(int(v) for v in values if v!='INF')) if values else None) for y,values in accuracy.items()},
        'archive_sha256':hashlib.sha256(raw).hexdigest(),'binding_sha256':binding,'counts':dict(counts)}


def latest_annual(payload,*,code,corp,year,scope,receipt,day):
    status=payload.get('status') if type(payload) is dict else None
    if status=='013':raise Blocked('latest_annual_source_absent')
    if status!='000' or type(payload.get('list')) is not list or not payload['list']:
        raise SourceUnavailable('annual_provider_unavailable')
    rows=payload['list']
    if len(rows)>18000:raise SourceUnavailable('annual_provider_unavailable')
    for row in rows:
        if (type(row) is not dict or row.get('corp_code')!=corp or str(row.get('bsns_year'))!=str(year)
            or row.get('reprt_code')!='11011' or ('stock_code' in row and row['stock_code']!=code)
            or ('fs_div' in row and row['fs_div']!=scope)):
            raise SourceUnavailable('annual_identity_not_verified')
    try:normalized=cf.normalize_year(rows,code=code,corp=corp,year=year,scope=scope,asof=day,major_receipt=receipt)
    except cf.CashflowError:raise Blocked('latest_annual_source_not_comparable') from None
    if not normalized['major_annual_receipt_matched']:raise Blocked('latest_receipt_changed')
    if type(normalized['operating_cashflow_krw']) is not int:raise Blocked('latest_annual_cfo_missing')
    standard=[];named=[]
    for row in rows:
        is_standard=row.get('account_id') in eps.EPS_IDS
        named_basic=row.get('account_id')==eps.NONSTANDARD_ID and eps.canonical_basic_eps_name(row.get('account_nm')) is not None
        if not(is_standard or named_basic) or row.get('sj_div') not in ('IS','CIS') or row.get('account_detail','-') not in ('','-'):continue
        if row.get('currency')!='KRW' or row.get('rcept_no')!=receipt:raise Blocked('latest_annual_source_not_comparable')
        try:value=eps.amount(row.get('thstrm_amount'))
        except eps.EPSError:raise Blocked('latest_annual_source_not_comparable') from None
        (standard if is_standard else named).append(value)
    selected=standard or named
    if len(set(selected))>1:raise Blocked('original_facts_conflict')
    return {'cfo':normalized['operating_cashflow_krw'],'eps':selected[0] if selected else None,
        'receipt':receipt,'sha256':cf.fingerprint(payload)}


def contexts(catalog,book,state,bulk,cash,research):
    _,_,_,retained,_=eps.plan(catalog,book,state)
    if research.get('scores_unchanged') is not True or type(research.get('records')) is not dict:raise AuditError('invalid_basis_research')
    out={}
    for code,record in research['records'].items():
        prior.verify_record(record)
        if code!=record['code'] or record['reason']!='candidate_2023_verified':continue
        row=retained.get(code)
        if (not row or row['fs_div']!=record['scope'] or row['annual_receipt_id']!=record['receipt_2025']
            or row['reported_basic_eps_2023_2025'][0] is not None
            or row['reported_basic_eps_2023_2025'][1]!=record['saved_eps_2024']
            or row['reported_basic_eps_2023_2025'][2] is None):continue
        receipts={y:bulk.get('records',{}).get(code,{}).get('years',{}).get(y,{}).get(row['fs_div'],{}).get('receipt_id') for y in YEARS}
        context={'code':code,'scope':row['fs_div'],'baseline':row,'research':record,'receipts':receipts,
            'cash':cash.get('records',{}).get(code,{}).get('years',{})}
        anchors={y:{k:context['cash'].get(y,{}).get(k) for k in ('scope','annual_receipt_id','major_annual_receipt_matched','operating_cashflow_krw')} for y in YEARS}
        context['input_digest']=cf.fingerprint({'baseline':row,'research':record,'receipts':receipts,'cash_anchors':anchors,'reader_sha256':READER_SHA})
        out[code]=context
    return out


def assess(context,*,corp,day,get,cached=None):
    code=context['code'];scope=context['scope'];receipts=context['receipts'];research=context['research'];base=context['baseline']
    def valid_receipt(y):
        return (type(receipts[y]) is str and re.fullmatch(str(int(y)+1)+r'\d{10}',receipts[y])
                and receipts[y][:8]<=day.replace('-',''))
    if not valid_receipt('2025') or receipts['2025']!=base['annual_receipt_id']:
        raise Blocked('expected_receipt_not_current')
    def annual(y):
        return latest_annual(get('fnlttSinglAcntAll.json',{'corp_code':corp,'bsns_year':y,'reprt_code':'11011','fs_div':scope}).json(),
            code=code,corp=corp,year=int(y),scope=scope,receipt=receipts[y],day=day)
    api={'2025':annual('2025')}
    if api['2025']['eps'] is not None and api['2025']['eps']!=base['reported_basic_eps_2023_2025'][2]:raise Blocked('latest_current_eps_conflict')
    old_cfo=base.get('original_xbrl_proof',{}).get('cfo_anchor')
    if type(old_cfo) is int and old_cfo!=api['2025']['cfo']:raise Blocked('latest_cfo_conflict')
    saved5=context['cash'].get('2025',{})
    if (saved5.get('major_annual_receipt_matched') is True and saved5.get('scope')==scope
        and saved5.get('annual_receipt_id')==receipts['2025'] and type(saved5.get('operating_cashflow_krw')) is int
        and saved5['operating_cashflow_krw']!=api['2025']['cfo']):raise Blocked('latest_cfo_conflict')
    previous=None
    if cached and cached.get('reason') in PASSED and cached['input_digest']==context['input_digest']:
        verify_record(cached);previous=cached['evidence']
        if cached['reason']=='same_filing_eps_completed' and previous['api_cfo']['2025']==api['2025']['cfo']:
            evidence=deepcopy(previous);evidence['api_sha256']['2025']=api['2025']['sha256']
            return evidence
    def validate_chain_api():
        if not all(valid_receipt(y) for y in ('2023','2024')):raise Blocked('expected_receipt_missing')
        if receipts['2024']!=research['receipt_2024']:raise Blocked('expected_receipt_not_current')
        api.update({y:annual(y) for y in ('2023','2024') if y not in api})
        if api['2024']['cfo']!=research['cfo_2024_krw']:raise Blocked('latest_cfo_conflict')
        if api['2024']['eps'] is not None and api['2024']['eps']!=base['reported_basic_eps_2023_2025'][1]:raise Blocked('latest_current_eps_conflict')
        for y in YEARS:
            saved=context['cash'].get(y,{})
            if (saved.get('major_annual_receipt_matched') is True and saved.get('scope')==scope
                and saved.get('annual_receipt_id')==receipts[y] and type(saved.get('operating_cashflow_krw')) is int
                and saved['operating_cashflow_krw']!=api[y]['cfo']):raise Blocked('latest_cfo_conflict')
        if api['2023']['eps'] is not None and api['2023']['eps']!=research['candidate_eps_2023']:raise Blocked('latest_2023_eps_conflict')
    if previous and cached['reason']=='basis_checks_passed':
        validate_chain_api()
        if (previous and cached['reason']=='basis_checks_passed' and previous['api_cfo']=={y:api[y]['cfo'] for y in YEARS}
            and (api['2023']['eps'] is not None or previous['latest_2023_channel']=='original')):
            evidence=deepcopy(previous);evidence['api_sha256']={y:api[y]['sha256'] for y in YEARS}
            return evidence
    profile=get('company.json',{'corp_code':corp}).json();identifiers=original.company_ids(profile,corp,code)
    filings={}
    def archive(year):
        response=get('fnlttXbrl.xml',{'rcept_no':receipts[year],'reprt_code':'11011'},True)
        try:raw=xp._read_response_limited(response)
        finally:response.close()
        # Service/auth/quota XML must interrupt before classifying an original.
        prior.require_archive_or_explicit_absence(raw)
        try:return read_filing(raw,filing_year=int(year),scope=scope,identifiers=identifiers,cfo_anchor=api[year]['cfo'])
        except (xp.XbrlPilotError,original.RecoveryError,eps.EPSError,zipfile.BadZipFile):
            raise Blocked('original_not_usable') from None
    filings['2025']=archive('2025')
    if any(filings['2025']['eps'].get(y)!=base['reported_basic_eps_2023_2025'][int(y)-2023] for y in ('2024','2025')):
        raise Blocked('original_eps_unavailable')
    direct=[filings['2025']['eps'].get(y) for y in YEARS]
    if all(v is not None for v in direct):
        return {'api_cfo':{'2025':api['2025']['cfo']},'api_sha256':{'2025':api['2025']['sha256']},
            'eps':direct,'filing_eps':{'2025':filings['2025']['eps']},'filing_cfo':{'2025':filings['2025']['cfo']},
            'archive_sha256':{'2025':filings['2025']['archive_sha256']},'profile_sha256':cf.fingerprint(profile),
            'path':'same_filing'}
    validate_chain_api()
    filings['2024']=archive('2024')
    if (filings['2024']['eps'].get('2023')!=research['candidate_eps_2023']
        or filings['2024']['eps'].get('2024')!=base['reported_basic_eps_2023_2025'][1]):raise Blocked('original_eps_unavailable')
    for filing,years in (('2024',('2023','2024')),('2025',('2024','2025'))):
        for y in years:
            observed=filings[filing]['cfo'].get(y)
            if observed is None:raise Blocked('comparative_cfo_unavailable')
            if observed!=api[y]['cfo']:raise Blocked('comparative_cfo_conflict')
    shares={y:filings[y]['shares'].get('2024') for y in ('2024','2025')}
    if any(v is None for v in shares.values()):raise Blocked('weighted_ordinary_shares_missing')
    if shares['2024']!=shares['2025']:raise Blocked('weighted_ordinary_shares_conflict')
    channel='api';latest_eps=api['2023']['eps']
    if latest_eps is None:
        filings['2023']=archive('2023');channel='original';latest_eps=filings['2023']['eps'].get('2023')
        if latest_eps!=research['candidate_eps_2023']:raise Blocked('latest_2023_eps_unavailable')
    return {'api_cfo':{y:api[y]['cfo'] for y in YEARS},'api_sha256':{y:api[y]['sha256'] for y in YEARS},
        'eps':[research['candidate_eps_2023'],*base['reported_basic_eps_2023_2025'][1:]],
        'weighted_shares_2024':shares,'filing_eps':{y:filings[y]['eps'] for y in filings},
        'weighted_share_accuracy_2024':{y:filings[y]['share_accuracy'].get('2024') for y in ('2024','2025')},
        'filing_cfo':{y:filings[y]['cfo'] for y in filings},
        'archive_sha256':{y:filings[y]['archive_sha256'] for y in filings},
        'profile_sha256':cf.fingerprint(profile),'latest_2023_channel':channel,'latest_2023_eps':latest_eps,'path':'cross_filing'}


def make_record(context,*,day,checked_on,reason,evidence=None):
    record={'contract':CONTRACT,'code':context['code'],'scope':context['scope'],'asof':day,'checked_on':checked_on,
        'input_digest':context['input_digest'],'receipts':context['receipts'],'reason':reason,
        'evidence':evidence,'scores_unchanged':True,'corporate_actions_certified':False}
    record['digest']=cf.fingerprint(record);verify_record(record);return record


def _verify_record(record):
    keys={'contract','code','scope','asof','checked_on','input_digest','receipts','reason','evidence','scores_unchanged','corporate_actions_certified','digest'}
    if (type(record) is not dict or set(record)!=keys or record['contract']!=CONTRACT
        or not re.fullmatch('[0-9A-Z]{6}',str(record['code'])) or record['scope'] not in ('CFS','OFS')
        or record['reason'] not in REASONS or record['scores_unchanged'] is not True or record['corporate_actions_certified'] is not False
        or not re.fullmatch('[a-f0-9]{64}',str(record['input_digest']))
        or type(record['receipts']) is not dict or set(record['receipts'])!=set(YEARS)
        or record['digest']!=cf.fingerprint({k:v for k,v in record.items() if k!='digest'})):raise AuditError('invalid_basis_record')
    for key in ('asof','checked_on'):
        if date.fromisoformat(record[key]).isoformat()!=record[key]:raise AuditError('invalid_basis_date')
    if record['checked_on']<record['asof']:raise AuditError('invalid_basis_date')
    if record['reason'] not in PASSED:
        if record['evidence'] is not None:raise AuditError('unexpected_blocked_basis_values')
        return False
    e=record['evidence'];common={'api_cfo','api_sha256','eps','filing_eps','filing_cfo','archive_sha256','profile_sha256','path'}
    direct=record['reason']=='same_filing_eps_completed'
    expected=common if direct else common|{'weighted_shares_2024','weighted_share_accuracy_2024','latest_2023_channel','latest_2023_eps'}
    api_years={'2025'} if direct else set(YEARS)
    if (type(e) is not dict or set(e)!=expected or e['path']!=('same_filing' if direct else 'cross_filing')
        or set(e['api_cfo'])!=api_years or any(type(v) is not int or abs(v)>10**18 for v in e['api_cfo'].values())
        or set(e['api_sha256'])!=api_years or any(not re.fullmatch('[a-f0-9]{64}',str(v)) for v in [*e['api_sha256'].values(),*e['archive_sha256'].values(),e['profile_sha256']])
        or type(e['eps']) is not list or len(e['eps'])!=3 or not eps.metrics(e['eps'])['complete_three_years']):raise AuditError('invalid_basis_evidence')
    if direct:
        if any(set(e[k])!={'2025'} for k in ('filing_eps','filing_cfo','archive_sha256')):raise AuditError('invalid_same_filing_basis')
        if type(e['filing_cfo']['2025'].get('2025')) is not int or e['filing_cfo']['2025'].get('2025')!=e['api_cfo']['2025'] or any(e['filing_eps']['2025'].get(y)!=e['eps'][int(y)-2023] for y in YEARS):raise AuditError('same_filing_eps_conflict')
        if not re.fullmatch(r'2026\d{10}',str(record['receipts']['2025'])) or record['receipts']['2025'][:8]>record['asof'].replace('-',''):raise AuditError('invalid_basis_receipts')
        return True
    if (e['latest_2023_channel'] not in ('api','original') or type(e['latest_2023_eps']) not in (int,float) or e['latest_2023_eps']!=e['eps'][0]
        or set(e['weighted_shares_2024'])!={'2024','2025'}
        or any(type(v) is not str or share_number(v) is None or share_number(v)!=v for v in e['weighted_shares_2024'].values())
        or e['weighted_shares_2024']['2024']!=e['weighted_shares_2024']['2025']):raise AuditError('invalid_cross_filing_share_basis')
    if set(e['weighted_share_accuracy_2024'])!={'2024','2025'}:raise AuditError('missing_weighted_share_accuracy')
    for y in ('2024','2025'):
        token=e['weighted_share_accuracy_2024'][y]
        if token!='INF' and (type(token) is not str or not token.isascii() or not token.isdecimal() or str(int(token))!=token
            or int(token)>64 or int(token)<(len(e['weighted_shares_2024'][y].split('.')[1]) if '.' in e['weighted_shares_2024'][y] else 0)):
            raise AuditError('inaccurate_weighted_shares')
    filings={'2024','2025'}|({'2023'} if e['latest_2023_channel']=='original' else set())
    if set(e['filing_eps'])!=filings or set(e['filing_cfo'])!=filings or set(e['archive_sha256'])!=filings:raise AuditError('missing_basis_original')
    for filing,years in (('2024',('2023','2024')),('2025',('2024','2025'))):
        for y in years:
            if type(e['filing_cfo'][filing].get(y)) is not int or e['filing_cfo'][filing].get(y)!=e['api_cfo'][y] or e['filing_eps'][filing].get(y)!=e['eps'][int(y)-2023]:raise AuditError('basis_cross_filing_conflict')
    if e['latest_2023_channel']=='original' and (e['filing_cfo']['2023'].get('2023')!=e['api_cfo']['2023'] or e['filing_eps']['2023'].get('2023')!=e['eps'][0]):raise AuditError('basis_latest_2023_conflict')
    if any(type(record['receipts'][y]) is not str or not re.fullmatch(str(int(y)+1)+r'\d{10}',record['receipts'][y]) or record['receipts'][y][:8]>record['asof'].replace('-','') for y in YEARS):raise AuditError('invalid_basis_receipts')
    return True


def verify_record(record):
    try:return _verify_record(record)
    except AuditError:raise
    except Exception:raise AuditError('invalid_basis_record') from None


def verify_for_context(record,context):
    if not verify_record(record):return False
    if (record['code']!=context['code'] or record['scope']!=context['scope']
        or record['input_digest']!=context['input_digest'] or record['receipts']!=context['receipts']
        or record['evidence']['eps'][1:]!=context['baseline']['reported_basic_eps_2023_2025'][1:]):return False
    if record['reason']=='basis_checks_passed':
        e=record['evidence'];research=context['research']
        if e['eps'][0]!=research['candidate_eps_2023'] or e['api_cfo']['2024']!=research['cfo_2024_krw']:return False
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('dry-run','live','verify'),default='dry-run');p.add_argument('--limit',type=int,default=16);a=p.parse_args()
    if not 1<=a.limit<=16:raise AuditError('invalid_basis_limit')
    root=Path('docs');read=lambda n:json.loads((root/n).read_text())
    catalog,book,state,bulk,cash,research=[read(n) for n in ('v22-market-catalog.json','v3-dart-book-reference.json','v3-dart-reported-eps.json','v3-dart-bulk-history.json','v3-dart-cashflow-reference.json','v3-eps-prior-original-receipt-research.json')]
    target=root/FILE
    if target.is_symlink():raise AuditError('unsafe_basis_output')
    old=read(FILE) if target.is_file() else {'schema':SCHEMA,'records':{},'scores_unchanged':True}
    if old.get('schema')!=SCHEMA or old.get('scores_unchanged') is not True or type(old.get('records')) is not dict:raise AuditError('invalid_basis_state')
    records=old['records']
    for code,r in records.items():
        if code!=r['code']:raise AuditError('basis_issuer_key_mismatch')
        verify_record(r)
    candidates=contexts(catalog,book,state,bulk,cash,research);today=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat();day=catalog['date']
    stable_negatives={'original_eps_unavailable','comparative_cfo_unavailable','weighted_ordinary_shares_missing','weighted_ordinary_shares_conflict','original_not_usable'}
    choices=[c for code,c in sorted(candidates.items()) if code not in records or records[code]['input_digest']!=c['input_digest'] or (records[code]['reason'] not in stable_negatives and (records[code]['checked_on']!=today or records[code]['asof']!=day))]
    output={'asof':day,'eligible':len(candidates),'remaining':len(choices),'selected':min(a.limit,len(choices)),'reviewed':0,'qualified':0,'basis_passed':0,'same_filing_completed':0,'provider_calls':0,'source_readers':4,'scores_unchanged':True,'reasons':{}}
    if a.mode!='live' or not choices:print(json.dumps(output,sort_keys=True));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise SourceUnavailable('basis_key_missing')
    _,members,_,_,_=eps.plan(catalog,book,state);cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    local=threading.local();lock=threading.Lock();sessions=[]
    def get(endpoint,params,stream=False):
        try:
            if not hasattr(local,'client'):
                local.client=requests.Session()
                with lock:sessions.append(local.client)
            response=local.client.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(20,90),stream=stream)
            with lock:output['provider_calls']+=1
            response.raise_for_status();return response
        except Exception:raise SourceUnavailable('basis_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    reasons=Counter()
    def fetch(context):
        code=context['code']
        if code not in mapping:raise SourceUnavailable('basis_company_map_unavailable')
        try:
            evidence=assess(context,corp=mapping[code],day=day,get=get,cached=records.get(code))
            reason='same_filing_eps_completed' if evidence['path']=='same_filing' else 'basis_checks_passed'
        except Blocked as error:
            if str(error) not in REASONS:raise
            evidence=None;reason=str(error)
        return make_record(context,day=day,checked_on=today,reason=reason,evidence=evidence)
    def checkpoint(record):
        records[record['code']]=record
        snapshot={'schema':SCHEMA,'asof':day,'scores_unchanged':True,'records':records,'reviewed_total':len(records),
            'qualified_total':sum(verify_record(r) for r in records.values()),
            'basis_passed_total':sum(r['reason']=='basis_checks_passed' for r in records.values()),
            'same_filing_completed_total':sum(r['reason']=='same_filing_eps_completed' for r in records.values())}
        write_json(target,snapshot);output['reviewed']+=1;output['qualified']+=int(record['reason'] in PASSED);output['basis_passed']+=int(record['reason']=='basis_checks_passed');output['same_filing_completed']+=int(record['reason']=='same_filing_eps_completed');reasons[record['reason']]+=1
    try:collect(choices[:a.limit],fetch,checkpoint,workers=4,deadline=time.monotonic()+480)
    finally:
        for client in sessions:client.close()
    output['reasons']=dict(reasons);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_eps_basis_audit','reason':'source_not_verified','scores_unchanged':True}));raise SystemExit(1)
