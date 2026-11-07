"""FSS-standard whole IAS 38 intangible purchase total, excluding goodwill.

IAS 38 excludes goodwill (IFRS 3). This reads the reported aggregate itself,
never a sum of software/development/components and never a missing zero.
https://www.ifrs.org/issued-standards/list-of-standards/ias-38-intangible-assets/
https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2020001
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import signal
import time
from contextlib import contextmanager

import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,write_json

CONTRACT='official_fss_whole_ias38_purchase_ex_goodwill_v1'
ACCOUNT='dart_PurchaseOfIntangibleAssetsOtherThanGoodwill'
SECTIONS={'CFS':{'CF1','CF3'},'OFS':{'CF2','CF4'}}
REASONS={'recovered','no_full_total','unverified_anchor','known_amount_conflict','unusable_source'}
LABELS=frozenset((
 '영업권이외의무형자산의취득','영업권이외의무형자산취득','영업권이외무형자산의취득','영업권이외무형자산취득',
 '영업권을제외한무형자산의취득','영업권을제외한무형자산취득','영업권제외무형자산의취득',
 '무형자산(영업권제외)의취득','무형자산(영업권이외)의취득','무형자산의취득','무형자산취득',
 'purchaseofintangibleassetsotherthangoodwill','acquisitionofintangibleassetsotherthangoodwill',
))

class IntangibleTotalError(ValueError):pass


@contextmanager
def request_wall_deadline(seconds):
    """Bound total elapsed transfer time, including continuously trickled bodies.

    Live collection runs on the Linux Actions main thread. Refuse unsupported
    signal environments or an existing timer instead of silently losing a cap.
    """
    if (type(seconds) not in (int, float) or not 0 < seconds <= 75
        or not all(hasattr(signal, name) for name in ('SIGALRM','ITIMER_REAL','setitimer','getitimer'))):
        raise IntangibleTotalError('full_intangible_wall_deadline_unavailable')
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise IntangibleTotalError('full_intangible_wall_deadline_unavailable')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise IntangibleTotalError('full_intangible_provider_deadline')
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def bounded_provider_response(client, endpoint, params, *, remaining):
    if type(remaining) not in (int,float) or remaining < 12:
        raise IntangibleTotalError('full_intangible_provider_deadline')
    response = None
    try:
        with request_wall_deadline(min(75, remaining)):
            response = client.get('https://opendart.fss.or.kr/api/'+endpoint,
                                  params=params, timeout=(15,15))
            response.raise_for_status()
            if len(response.content) > 25_000_000:
                raise IntangibleTotalError('full_intangible_provider_unavailable')
        return response
    except IntangibleTotalError:
        raise
    except Exception:
        raise IntangibleTotalError('full_intangible_provider_unavailable') from None
    finally:
        if response is not None:
            try:response.close()
            except Exception:pass


def label(value):
    if type(value) is not str or len(value)>180:return None
    compact=''.join(value.split()).replace(',','').lower()
    return compact if compact in LABELS else None


def certificate(payloads):
    sections={}
    if type(payloads) is not dict or set(payloads)!={'CF1','CF2','CF3','CF4'}:raise IntangibleTotalError('incomplete_fss_taxonomy_sections')
    for section,payload in payloads.items():
        if (type(payload) is not dict or payload.get('status')!='000'
            or type(payload.get('list')) is not list or len(payload['list'])>18000):raise IntangibleTotalError('official_taxonomy_unavailable')
        selected=[]
        for row in payload['list']:
            if type(row) is not dict:raise IntangibleTotalError('invalid_official_taxonomy_row')
            if row.get('account_id')!=ACCOUNT:continue
            names={label(row.get(k)) for k in ('account_nm','label_kor','label_eng')}-{None}
            if row.get('sj_div')!=section or row.get('data_tp') not in ('X','(X)') or not names:
                raise IntangibleTotalError('unverified_full_intangible_definition')
            selected.append({'label':sorted(names)[0],'data_type':row['data_tp'],'row_sha256':cf.fingerprint(row)})
        if selected:
            if len({(r['label'],r['data_type']) for r in selected})!=1:raise IntangibleTotalError('conflicting_full_intangible_definitions')
            sections[section]=sorted(selected,key=lambda r:r['row_sha256'])[0]
    out={'contract':CONTRACT,'account_id':ACCOUNT,'sections':sections}
    out['digest']=cf.fingerprint(out)
    return out


def verify_certificate(cert,scope):
    if (type(cert) is not dict or set(cert)!={'contract','account_id','sections','digest'}
        or cert['contract']!=CONTRACT or cert['account_id']!=ACCOUNT or type(cert['sections']) is not dict
        or not set(cert['sections'])<={'CF1','CF2','CF3','CF4'} or not set(cert['sections'])&SECTIONS[scope]
        or cert['digest']!=cf.fingerprint({k:v for k,v in cert.items() if k!='digest'})):
        raise IntangibleTotalError('invalid_full_intangible_certificate')
    for row in cert['sections'].values():
        if (type(row) is not dict or set(row)!={'label','data_type','row_sha256'} or row['label'] not in LABELS
            or row['data_type'] not in ('X','(X)') or type(row['row_sha256']) is not str
            or not re.fullmatch('[a-f0-9]{64}',row['row_sha256'])):raise IntangibleTotalError('invalid_full_intangible_certificate')
    return True


def parse(payload,old,*,code,corp,day,cert):
    verify_certificate(cert,old['scope'])
    status=payload.get('status') if type(payload) is dict else None
    if status=='013':rows=[]
    elif status=='000' and isinstance(payload.get('list'),list) and payload['list']:rows=payload['list']
    else:raise IntangibleTotalError('full_intangible_provider_unavailable')
    normalized=cf.normalize_year(rows,code=code,corp=corp,year=old['year'],scope=old['scope'],asof=day,major_receipt=old['annual_receipt_id'])
    values=[];names=[]
    for row in rows:
        if row.get('account_id')!=ACCOUNT or row.get('sj_div')!='CF' or row.get('account_detail','-') not in ('','-'):continue
        if (row.get('currency')!='KRW' or row.get('rcept_no')!=old['annual_receipt_id'] or label(row.get('account_nm')) is None):
            raise IntangibleTotalError('unmatched_full_intangible_source')
        value=cf._amount(row.get('thstrm_amount'))
        if value is None or value<0:raise IntangibleTotalError('unusable_full_intangible_amount')
        values.append(value)
        names.append(label(row['account_nm']))
    if not values:return normalized,None
    if len(set(values))!=1:raise IntangibleTotalError('conflicting_full_intangible_amounts')
    proof={'contract':CONTRACT,'receipt_id':old['annual_receipt_id'],'year':old['year'],'scope':old['scope'],
           'cfo_anchor':normalized['operating_cashflow_krw'],'account_id':ACCOUNT,'account_name':sorted(names)[0],'amount_krw':values[0],
           'source_sha256':cf.fingerprint(payload),'taxonomy':cert}
    proof['digest']=cf.fingerprint(proof);return normalized,proof


def verify(row):
    marker=row.get('full_intangible_review');proof=row.get('full_intangible_total')
    if marker is None:
        if proof is not None:raise IntangibleTotalError('missing_full_intangible_review')
        return False
    if (type(marker) is not dict or set(marker)!={'contract','receipt_id','reason'} or marker['contract']!=CONTRACT
        or marker['receipt_id']!=row.get('annual_receipt_id') or marker['reason'] not in REASONS
        or row.get('major_annual_receipt_matched') is not True):raise IntangibleTotalError('invalid_full_intangible_review')
    if marker['reason']!='recovered':
        if proof is not None:raise IntangibleTotalError('unexpected_full_intangible_proof')
        return False
    keys={'contract','receipt_id','year','scope','cfo_anchor','account_id','account_name','amount_krw','source_sha256','taxonomy','digest'}
    if (type(proof) is not dict or set(proof)!=keys or proof['contract']!=CONTRACT or proof['account_id']!=ACCOUNT
        or proof['receipt_id']!=row.get('annual_receipt_id') or proof['year']!=row.get('year') or proof['scope']!=row.get('scope') or proof['account_name'] not in LABELS
        or type(proof['cfo_anchor']) is not int or proof['cfo_anchor']!=row.get('operating_cashflow_krw')
        or type(proof['amount_krw']) is not int or not 0<=proof['amount_krw']<=10**18 or proof['amount_krw']!=row.get('intangible_capex_krw')
        or type(proof['source_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}',proof['source_sha256'])
        or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
        raise IntangibleTotalError('invalid_full_intangible_proof')
    return verify_certificate(proof['taxonomy'],row['scope'])


def merge(old,normalized,proof,reason='no_full_total'):
    if old.get('intangible_capex_krw') is not None or old.get('free_cashflow_krw') is not None or old.get('full_intangible_review') is not None:raise IntangibleTotalError('full_intangible_would_overwrite')
    if type(old.get('operating_cashflow_krw')) is not int or old.get('major_annual_receipt_matched') is not True:raise IntangibleTotalError('missing_full_intangible_anchor')
    out=deepcopy(old)
    if proof is not None:
        if (normalized is None or normalized.get('major_annual_receipt_matched') is not True
            or any(normalized.get(k)!=old[k] for k in ('year','scope','annual_receipt_id','operating_cashflow_krw'))):reason='unverified_anchor'
        elif (old.get('ppe_capex_krw') is not None and normalized.get('ppe_capex_krw') is not None and old['ppe_capex_krw']!=normalized['ppe_capex_krw']
              or normalized.get('intangible_capex_krw') is not None and normalized['intangible_capex_krw']!=proof['amount_krw']):reason='known_amount_conflict'
        else:
            out['full_intangible_total']=proof;out['intangible_capex_krw']=proof['amount_krw'];reason='recovered'
            ppe=out.get('ppe_capex_krw');out['total_capex_krw']=ppe+proof['amount_krw'] if ppe is not None else None
            out['free_cashflow_krw']=out['operating_cashflow_krw']-out['total_capex_krw'] if out['total_capex_krw'] is not None else None
    out['full_intangible_review']={'contract':CONTRACT,'receipt_id':old['annual_receipt_id'],'reason':reason}
    verify(out);return out


def targets(state,priority,limit):
    if type(limit) is not int or not 1<=limit<=40:raise IntangibleTotalError('invalid_full_intangible_budget')
    groups=[]
    for code,issuer in state['records'].items():
        missing=[(y,r) for y,r in issuer['years'].items() if r.get('free_cashflow_krw') is None]
        eligible=[(y,r) for y,r in missing if r.get('intangible_capex_krw') is None and type(r.get('operating_cashflow_krw')) is int
                  and r.get('major_annual_receipt_matched') is True and r.get('full_intangible_review') is None]
        if eligible:
            whole_completion=(len(missing)==len(eligible) and all(r.get('ppe_capex_krw') is not None for _,r in eligible))
            # An intangible total alone cannot produce a free-cashflow value
            # when PPE is also missing. Prefer an immediately computable FCF
            # company-year before exhausting the much larger PPE-missing queue.
            any_fcf_ready=any(r.get('ppe_capex_krw') is not None for _,r in eligible)
            started=any(r.get('full_intangible_total') is not None for r in issuer['years'].values())
            groups.append((not whole_completion,not any_fcf_ready,code not in priority,
                           not started,len(eligible),code,eligible))
    return [(code,y,r) for *_,code,eligible in sorted(groups)
            for y,r in sorted(eligible,key=lambda item:(item[1].get('ppe_capex_krw') is None,item[0]))][:limit]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=('dry-run','live'),default='dry-run');p.add_argument('--limit',type=int,default=8);a=p.parse_args()
    root=Path('docs');read=lambda n:json.loads((root/n).read_text());target=root/'v3-dart-cashflow-reference.json'
    if target.is_symlink():raise IntangibleTotalError('unsafe_full_intangible_output')
    market,catalog,bulk,state=[read(n) for n in ('data.json','v22-market-catalog.json','v3-dart-bulk-history.json',target.name)]
    state=cf.retained_current_market_cashflow(market,catalog,bulk,state);cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True)
    priority=xp.operational_gap_priority(read('v3-operational-research-scores.json'),read('v3-operational-research-comparison.json'),catalog,bulk)
    selected=targets(state,priority,a.limit);output={'selected_issuer_years':len(selected),'priority_issuers':len(priority),'completed':0,'recovered_years':0,'provider_calls':0,'taxonomy_sections_verified':0,'reasons':{},'investment_score_certified':False}
    if a.mode=='dry-run' or not selected:print(json.dumps(output));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise IntangibleTotalError('full_intangible_key_missing')
    client=requests.Session();deadline=time.monotonic()+300
    def get(endpoint,params):
        if deadline-time.monotonic()<12:raise IntangibleTotalError('full_intangible_provider_deadline')
        output['provider_calls']+=1
        return bounded_provider_response(client,endpoint,{'crtfc_key':key,**params},remaining=deadline-time.monotonic())
    tax=certificate({section:get('xbrlTaxonomy.json',{'sj_div':section}).json() for section in ('CF1','CF2','CF3','CF4')})
    output['taxonomy_sections_verified']=len(tax['sections'])
    for _,old_year,old in selected:verify_certificate(tax,old['scope'])
    members={r['code']:r for r in catalog['rows']};cache=Path('.cache/v3-dart-company-codes.json');mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,catalog['date'],members)
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    reasons=Counter()
    for code,year,old in selected:
        if time.monotonic()+95>=deadline:break
        if code not in mapping:continue
        payload=get('fnlttSinglAcntAll.json',{'corp_code':mapping[code],'bsns_year':year,'reprt_code':'11011','fs_div':old['scope']}).json()
        try:normalized,proof=parse(payload,old,code=code,corp=mapping[code],day=catalog['date'],cert=tax);new=merge(old,normalized,proof)
        except (IntangibleTotalError,cf.CashflowError) as error:
            if str(error)=='full_intangible_provider_unavailable':raise
            new=merge(old,None,None,'unusable_source')
        state['records'][code]['years'][year]=new;state=cf.summary(catalog['date'],members,state['records'],cf.fingerprint(bulk))
        cf.verify_state(state,market,catalog,bulk,strict_fingerprint=True);write_json(target,state)
        output['completed']+=1;output['recovered_years']+=int(new['full_intangible_review']['reason']=='recovered');reasons[new['full_intangible_review']['reason']]+=1
    output['reasons']=dict(reasons);print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        safe={'incomplete_fss_taxonomy_sections','official_taxonomy_unavailable','invalid_official_taxonomy_row',
              'unverified_full_intangible_definition','conflicting_full_intangible_definitions',
              'invalid_full_intangible_certificate','full_intangible_provider_unavailable','full_intangible_key_missing',
              'full_intangible_wall_deadline_unavailable','full_intangible_provider_deadline'}
        reason=str(error) if type(error) is IntangibleTotalError and str(error) in safe else 'full_intangible_total_not_verified'
        print(json.dumps({'status':'blocked_full_intangible_total','reason':reason}));raise SystemExit(1)
