"""Recover FY2022/23 reported revenue from one FY2024 annual comparative.

The FY2024 current-year sales figure must match existing same-scope annual
DART evidence, and FY2025 same-scope annual sales must also exist. Comparative
FY2022/23 values are observed IN THE FY2024 filing, not attributed to older
original receipts or historical PIT. No absent value becomes zero.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time

from v3_dart_bulk_history import (
    SCHEMA as BULK_SCHEMA, _amount, _day, _explicit_krw_currency,
    _mapping, _stable_digest, _valid_receipt,
    verified_company_mapping_cache, write_json)
from v3_dart_revenue_2022 import IDS, NONSTANDARD, EXACT_NAMES

SCHEMA='v3-dart-fy2022-2023-revenue-from-fy2024-original-comparatives-v1'
OUTPUT='v3-dart-revenue-2024-comparative.json'
MAX_PER_RUN=16
YEARS=('2022','2023')
REASONS=frozenset((
    'no_reported_comparative','incomplete_comparative_years',
    'current_year_revenue_conflict','prior_year_revenue_conflict',
    'conflicting_revenue_accounts',
))


class Revenue2024Error(ValueError):
    pass


def expected_sources(catalog,history):
    day=catalog.get('date')
    if (type(day) is not str or
        history.get('schema')!=BULK_SCHEMA or history.get('asof')!=day or
        history.get('listed_total')!=catalog.get('listed_total') or
        history.get('fiscal_years')!=[2022,2023,2024,2025] or
        history.get('historical_PIT_source_certified') is not False or
        type(history.get('records')) is not dict or
        type(catalog.get('rows')) is not list or
        catalog.get('listed_total')!=len(catalog['rows'])):
        raise Revenue2024Error('unaligned_fy2024_revenue_sources')
    cutoff=_day(day)
    result={}
    for member in catalog['rows']:
        code=member['code']
        years=history['records'].get(code,{}).get('years',{})
        for scope in ('CFS','OFS'):
            latest={}
            for year in ('2024','2025'):
                row=years.get(year,{}).get(scope,{})
                values=row.get('values',{})
                value=values.get('revenue') if type(values) is dict else None
                if (type(value) is not int or not 0<value<=10**18 or
                    not _valid_receipt(row.get('receipt_id'),int(year),cutoff)):
                    break
                latest[year]=(value,row['receipt_id'])
            if len(latest)!=2:continue
            prior={}
            for year in YEARS:
                row=years.get(year,{}).get(scope,{})
                value=row.get('values',{}).get('revenue')
                if (type(value) is int and 0<value<=10**18 and
                    _valid_receipt(row.get('receipt_id'),int(year),cutoff)):
                    prior[year]=value
                else:
                    prior[year]=None
            missing=[y for y in YEARS if prior[y] is None]
            if not missing:continue
            result[code]={
                'code':code, 'scope':scope,
                'source_2024_receipt_id':latest['2024'][1],
                'source_2024_revenue_krw':latest['2024'][0],
                'source_2025_receipt_id':latest['2025'][1],
                'source_2025_revenue_krw':latest['2025'][0],
                'original_2022_revenue_krw':prior['2022'],
                'original_2023_revenue_krw':prior['2023'],
                'missing_years':missing}
            break
    return result


def verified_prior(prior,expected,day):
    if prior is None:return {}
    if (type(prior) is not dict or prior.get('schema')!=SCHEMA or
        prior.get('missing_is_not_zero') is not True or
        prior.get('investment_score_certified') is not False or
        prior.get('historical_PIT_source_certified') is not False or
        prior.get('raw_provider_rows_published') is not False or
        type(prior.get('records')) is not dict or
        type(prior.get('asof')) is not str or prior['asof']>day):
        raise Revenue2024Error('untrusted_fy2024_revenue_checkpoint')
    retained={}
    for code,row in prior['records'].items():
        base=expected.get(code)
        if base is None:continue
        if type(row) is not dict or any(row.get(key)!=value
                                        for key,value in base.items()):
            continue  # Re-query a corrected filing or changed historical source.
        observed=row.get('status')=='observed'
        recovered=row.get('recovered_revenue_krw')
        if (row.get('digest')!=_stable_digest(
                {k:v for k,v in row.items() if k!='digest'}) or
            row.get('status') not in ('observed','not_observed') or
            type(row.get('checked_on')) is not str or
            row['checked_on']>day or
            type(row.get('source_sha256')) is not str or
            re.fullmatch('[a-f0-9]{64}',row['source_sha256']) is None or
            row.get('reason') not in REASONS|{None} or
            (observed and row['reason'] is not None) or
            (not observed and row['reason'] is None) or
            type(recovered) is not dict or
            (observed != bool(recovered)) or
            not set(recovered)<=set(base['missing_years']) or
            any(type(v) is not int or not 0<v<=10**18
                for v in recovered.values()) or
            row.get('source_account_id') not in IDS|{NONSTANDARD,None} or
            (observed != (row.get('source_account_id') is not None)) or
            (row.get('source_account_id')==NONSTANDARD and
             row.get('source_account_name') not in EXACT_NAMES) or
            (row.get('source_account_id')!=NONSTANDARD and
             row.get('source_account_name') is not None) or
            row.get('source_receipt_id')!=(
                base['source_2024_receipt_id'] if observed else None)):
            raise Revenue2024Error('tampered_fy2024_revenue_checkpoint')
        retained[code]=row
    return retained


def parse(payload,base,*,corp,day):
    status=payload.get('status') if type(payload) is dict else None
    rows=payload.get('list') if status=='000' else [] if status=='013' else None
    if type(rows) is not list or len(rows)>20000 or (status=='000' and not rows):
        raise Revenue2024Error('fy2024_comparative_provider_unavailable')
    recognized=[]
    for row in rows:
        if (type(row) is not dict or row.get('corp_code')!=corp or
            str(row.get('bsns_year'))!='2024' or
            row.get('reprt_code')!='11011' or
            ('stock_code' in row and row['stock_code']!=base['code']) or
            ('fs_div' in row and row['fs_div']!=base['scope'])):
            raise Revenue2024Error('fy2024_comparative_identity_mismatch')
        account=row.get('account_id')
        if not (account in IDS or (account==NONSTANDARD and
            row.get('account_nm') in EXACT_NAMES)):continue
        if row.get('sj_div') not in ('IS','CIS') or row.get('account_detail','-') not in ('','-'):
            continue
        if (row.get('rcept_no')!=base['source_2024_receipt_id'] or
            not _explicit_krw_currency(row.get('currency'))):
            raise Revenue2024Error('fy2024_comparative_receipt_or_currency_mismatch')
        amounts={
            '2022':_amount(row.get('bfefrmtrm_amount')),
            '2023':_amount(row.get('frmtrm_amount')),
            '2024':_amount(row.get('thstrm_amount'))}
        recognized.append((account,row.get('account_nm'),amounts))
    amounts={}
    chosen=None
    reason='no_reported_comparative'
    if recognized:
        # Standard and explicitly named rows must not contradict each other.
        unique={tuple(row[2].get(year) for year in ('2022','2023','2024'))
                for row in recognized}
        if len(unique)!=1:
            reason='conflicting_revenue_accounts'
        elif any(row[2]['2024']!=base['source_2024_revenue_krw']
                 for row in recognized):
            reason='current_year_revenue_conflict'
        elif any(base['original_'+year+'_revenue_krw'] is not None and
                 row[2][year] is not None and
                 row[2][year]!=base['original_'+year+'_revenue_krw']
                 for row in recognized for year in YEARS):
            reason='prior_year_revenue_conflict'
        else:
            chosen=sorted(recognized,key=lambda item:(
                item[0]==NONSTANDARD,item[0]))[0]
            amounts={y:chosen[2][y] for y in base['missing_years']
                     if type(chosen[2][y]) is int and 0<chosen[2][y]<=10**18}
            reason=None if amounts else 'incomplete_comparative_years'
    observed=bool(amounts)
    result={**base,'checked_on':day,'status':'observed' if observed else 'not_observed',
        'recovered_revenue_krw':amounts,
        'source_account_id':chosen[0] if observed else None,
        'source_account_name':chosen[1] if observed and chosen[0]==NONSTANDARD else None,
        'source_receipt_id':base['source_2024_receipt_id'] if observed else None,
        'source_sha256':_stable_digest(payload),'reason':reason}
    result['digest']=_stable_digest(result)
    return result


def snapshot(day,catalog,records):
    return {'schema':SCHEMA,'asof':day,'listed_total':catalog['listed_total'],
        'missing_is_not_zero':True,'investment_score_certified':False,
        'historical_PIT_source_certified':False,'raw_provider_rows_published':False,
        'provider_calls':0,'attempted_issuer_count':len(records),
        'observed_issuer_count':sum(row['status']=='observed'
                                   for row in records.values()),
        'recovered_company_years':sum(len(row['recovered_revenue_krw'])
                                      for row in records.values()),
        'records':dict(sorted(records.items()))}


def plan(catalog,history,prior=None,*,priority_codes=(),limit=MAX_PER_RUN):
    if type(limit) is not int or not 1<=limit<=MAX_PER_RUN:
        raise Revenue2024Error('invalid_fy2024_comparative_limit')
    expected=expected_sources(catalog,history)
    records=verified_prior(prior,expected,catalog['date'])
    priority=set(priority_codes)
    selected=sorted((c for c in expected if c not in records),
                    key=lambda c:(c not in priority,len(expected[c]['missing_years'])==1,c))[:limit]
    return expected,records,selected


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docs',type=Path,default=Path('docs'))
    p.add_argument('--cache',type=Path,default=Path('.cache/v3-dart-company-codes.json'))
    p.add_argument('--mode',choices=('dry-run','live','verify'),default='dry-run')
    p.add_argument('--limit',type=int,default=MAX_PER_RUN)
    args=p.parse_args(argv)
    root=args.docs
    read=lambda n:json.loads((root/n).read_text(encoding='utf8'))
    catalog,history=read('v22-market-catalog.json'),read('v3-dart-bulk-history.json')
    path=root/OUTPUT;prior=read(OUTPUT) if path.is_file() else None
    priority=set()
    score_path=root/'v3-operational-research-scores.json'
    if score_path.is_file():
        report=read(score_path.name)
        if (report.get('asof')!=catalog['date'] or
            report.get('listed_total')!=catalog['listed_total'] or
            report.get('investment_score_certified') is not False or
            type(report.get('rows')) is not dict):
            raise Revenue2024Error('unaligned_fy2024_comparative_priority')
        priority={c for c,row in report['rows'].items()
                  if row.get('available',100)<90 and
                  ('revenue_growth' not in row.get('parts',{}).get('financial',{})
                   or row['parts']['financial']['revenue_growth'][1]<3.5)}
    expected,records,selected=plan(catalog,history,prior,
        priority_codes=priority,limit=args.limit)
    output={'asof':catalog['date'],'eligible':len(expected),
        'remaining':len(expected)-len(records),'selected':len(selected),
        'below90_eligible_unreviewed':sum(c in priority for c in expected
                                         if c not in records),
        'selected_below90':sum(c in priority for c in selected),
        'provider_calls':0,'reviewed':0,'new_observed_issuers':0,
        'new_observed_company_years':0,'score_points_added':0,
        'investment_score_certified':False}
    if args.mode=='verify':
        if prior is None:raise Revenue2024Error('fy2024_comparative_checkpoint_missing')
        print(json.dumps({**output,'status':'verified',
            'observed_total':sum(r['status']=='observed' for r in records.values())}))
        return
    if args.mode=='dry-run' or not selected:
        print(json.dumps({**output,'status':'dry_run'},sort_keys=True));return
    if path.is_symlink():raise Revenue2024Error('unsafe_fy2024_comparative_output')
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):
        raise Revenue2024Error('fy2024_comparative_key_unavailable')
    cache=json.loads(args.cache.read_text()) if args.cache.is_file() else None
    members={r['code']:r for r in catalog['rows']}
    mapping=verified_company_mapping_cache(cache,catalog['date'],members)
    client=requests.Session()
    deadline=time.monotonic()+500
    def request(endpoint,params):
        try:
            response=client.get('https://opendart.fss.or.kr/api/'+endpoint,
                params={'crtfc_key':key,**params},timeout=(20,60))
            output['provider_calls']+=1
            response.raise_for_status()
            return response
        except Exception:
            raise Revenue2024Error('fy2024_comparative_provider_unavailable') from None
    if mapping is None:
        mapping=_mapping(request('corpCode.xml',{}).content,set(members))
    for code in selected:
        if time.monotonic()+85>=deadline:break
        if code not in mapping:continue
        base=expected[code]
        payload=request('fnlttSinglAcntAll.json', {
            'corp_code':mapping[code],'bsns_year':'2024',
            'reprt_code':'11011','fs_div':base['scope']}).json()
        # An anomalous annual identity/revision is NOT evidence of absence.
        observed=parse(payload,base,corp=mapping[code],day=catalog['date'])
        records[code]=observed
        write_json(path,snapshot(catalog['date'],catalog,records))
        output['reviewed']+=1
        output['new_observed_issuers']+=int(observed['status']=='observed')
        output['new_observed_company_years']+=len(observed['recovered_revenue_krw'])
    print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status':'blocked_fy2024_revenue_comparative',
            'reason':'unverified_fy2024_revenue_original'}))
        raise SystemExit(1)
