"""Receipt-bound FY2025 comparatives for missing 2023/24 operating income.

Use a reported 2025 operating-income anchor from the *same* 2025 filing/scope.
No source value is guessed and no FY2025 comparative is mislabeled as an
original FY2023/24 filing. Historical point-in-time certification remains false.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import time

from v3_dart_bulk_history import (_amount, _day, _explicit_krw_currency,
                                  _mapping, _valid_receipt, _midrank,
                                  _three_year_margin, verified_company_mapping_cache,
                                  write_json, _stable_digest)

SCHEMA = 'v3-dart-fy2025-reported-operating-income-comparatives-v1'
OUTPUT = 'v3-dart-operating-income-2025-comparative.json'
ACCOUNT_IDS = frozenset(('dart_OperatingIncomeLoss',
    'ifrs-full_ProfitLossFromOperatingActivities',
    'ifrs_ProfitLossFromOperatingActivities'))
NONSTANDARD = '-표준계정코드 미사용-'
ACCOUNT_NAMES = frozenset(('영업이익', '영업이익(손실)'))
COLUMNS = {'2023':'bfefrmtrm_amount', '2024':'frmtrm_amount',
           '2025':'thstrm_amount'}
LIMIT = 32


class OperatingComparativeError(ValueError):
    pass


def expected_sources(catalog, history):
    day = catalog.get('date')
    if (type(day) is not str or history.get('asof') != day or
        history.get('listed_total') != catalog.get('listed_total') or
        history.get('fiscal_years') != [2022, 2023, 2024, 2025] or
        history.get('historical_PIT_source_certified') is not False or
        type(history.get('records')) is not dict or
        catalog.get('listed_total') != len(catalog.get('rows', []))):
        raise OperatingComparativeError('unaligned_operating_comparative_sources')
    cutoff = _day(day)
    selected = {}
    for member in catalog['rows']:
        code = member.get('code')
        if type(code) is not str or re.fullmatch('[0-9A-Z]{6}', code) is None:
            raise OperatingComparativeError('invalid_operating_comparative_code')
        history_record = history['records'].get(code, {})
        years = history_record.get('years', {})
        for scope in ('CFS', 'OFS'):
            statements = {year:years.get(year, {}).get(scope, {})
                          for year in ('2023','2024','2025')}
            if any(not isinstance(row, dict) or
                   not isinstance(row.get('values'), dict) or
                   not _valid_receipt(row.get('receipt_id'), int(year), cutoff) or
                   type(row['values'].get('revenue')) is not int or
                   row['values']['revenue'] <= 0
                   for year,row in statements.items()):
                continue
            latest = statements['2025']
            latest_op = latest['values'].get('operating_income')
            if type(latest_op) is not int or abs(latest_op) > 10**18:
                continue
            missing = [y for y in ('2023','2024')
                       if statements[y]['values'].get('operating_income') is None]
            if not missing or any(
                    type(statements[y]['values'].get('operating_income')) not in (int,type(None))
                    for y in ('2023','2024')):
                continue
            selected[code] = {
                'code':code, 'scope':scope, 'source_receipt_id':latest['receipt_id'],
                'source_2025_operating_income_krw':latest_op,
                'original_receipts':{y:statements[y]['receipt_id'] for y in ('2023','2024')},
                'original_operating_income':{
                    y:statements[y]['values'].get('operating_income')
                    for y in ('2023','2024')},
                'missing_years':missing}
            break
    return selected


def verified_prior(prior, expected, day):
    if prior is None:
        return {}
    if (type(prior) is not dict or prior.get('schema') != SCHEMA or
        prior.get('missing_is_not_zero') is not True or
        prior.get('investment_score_certified') is not False or
        prior.get('historical_PIT_source_certified') is not False or
        prior.get('raw_provider_rows_published') is not False or
        prior.get('asof', '9999') > day or
        type(prior.get('records')) is not dict):
        raise OperatingComparativeError('untrusted_operating_comparative_checkpoint')
    retained = {}
    for code,row in prior['records'].items():
        base = expected.get(code)
        if base is None or type(row) is not dict:
            continue
        if any(row.get(k) != v for k,v in base.items()):
            continue  # Recheck changed original/2025 receipts, scope, or baseline.
        if (row.get('status') not in ('observed','not_observed') or
            row.get('checked_on','9999') > day or
            type(row.get('source_sha256')) is not str or
            not re.fullmatch('[a-f0-9]{64}', row['source_sha256']) or
            row.get('source_account_id') not in (None,*ACCOUNT_IDS,NONSTANDARD) or
            (row.get('source_account_id') == NONSTANDARD and
             row.get('source_account_name') not in ACCOUNT_NAMES) or
            (row.get('source_account_id') not in (None,NONSTANDARD) and
             row.get('source_account_name') is not None)):
            raise OperatingComparativeError('tampered_operating_comparative_checkpoint')
        additions = row.get('recovered_operating_income_krw')
        observed = row['status'] == 'observed'
        if (type(additions) is not dict or
            set(additions) != (set(base['missing_years']) if observed else set()) or
            any(type(amount) is not int or abs(amount)>10**18
                for amount in additions.values()) or
            (row.get('source_account_id') is None) != (not observed)):
            raise OperatingComparativeError('invalid_operating_comparative_observation')
        retained[code] = row
    return retained


def parse(payload, base, *, corp, day):
    status = payload.get('status') if type(payload) is dict else None
    rows = payload.get('list') if status == '000' else [] if status == '013' else None
    if type(rows) is not list or len(rows)>20000 or (status=='000' and not rows):
        raise OperatingComparativeError('operating_comparative_provider_unavailable')
    matched = []
    for row in rows:
        if (type(row) is not dict or row.get('corp_code') != corp or
            str(row.get('bsns_year')) != '2025' or
            row.get('reprt_code') != '11011' or
            ('stock_code' in row and row['stock_code'] != base['code']) or
            ('fs_div' in row and row['fs_div'] != base['scope'])):
            raise OperatingComparativeError('operating_comparative_identity_mismatch')
        account = row.get('account_id')
        if not (account in ACCOUNT_IDS or
                (account == NONSTANDARD and row.get('account_nm') in ACCOUNT_NAMES)):
            continue
        if row.get('sj_div') not in ('IS','CIS') or row.get('account_detail','-') not in ('','-'):
            continue
        if (row.get('rcept_no') != base['source_receipt_id'] or
            not _explicit_krw_currency(row.get('currency'))):
            raise OperatingComparativeError('operating_comparative_receipt_or_unit_mismatch')
        values = {year:_amount(row.get(col)) for year,col in COLUMNS.items()}
        matched.append((account,row.get('account_nm'),values))
    additions = {}
    chosen = None
    if matched:
        if any(v['2025'] is None or v['2025'] != base['source_2025_operating_income_krw']
               for _,_,v in matched):
            raise OperatingComparativeError('operating_comparative_2025_anchor_conflict')
        for year in ('2023','2024'):
            existing = base['original_operating_income'][year]
            proposed = {v[year] for _,_,v in matched if v[year] is not None}
            if (len(proposed)>1 or (existing is not None and proposed and
                                  existing not in proposed)):
                raise OperatingComparativeError('operating_comparative_historical_conflict')
            if year in base['missing_years'] and len(proposed)==1:
                additions[year] = proposed.pop()
        if set(additions)==set(base['missing_years']):
            chosen = sorted(matched,key=lambda item:(item[0]==NONSTANDARD,item[0]))[0]
    observed = chosen is not None
    result = dict(base)
    result.update(
        checked_on=day,status='observed' if observed else 'not_observed',
        recovered_operating_income_krw=additions if observed else {},
        source_account_id=chosen[0] if observed else None,
        source_account_name=chosen[1] if observed and chosen[0]==NONSTANDARD else None,
        source_sha256=_stable_digest(payload))
    return result


def snapshot(day, catalog, records):
    return {'schema':SCHEMA,'asof':day,'listed_total':catalog['listed_total'],
            'missing_is_not_zero':True,'investment_score_certified':False,
            'historical_PIT_source_certified':False,'raw_provider_rows_published':False,
            'provider_calls':0,'attempted_issuer_count':len(records),
            'observed_issuer_count':sum(r['status']=='observed' for r in records.values()),
            'records':dict(sorted(records.items()))}


def plan(catalog,history,prior=None,*,priority=(),limit=LIMIT):
    if type(limit) is not int or not 1<=limit<=LIMIT:
        raise OperatingComparativeError('invalid_operating_comparative_limit')
    expected=expected_sources(catalog,history)
    retained=verified_prior(prior,expected,catalog['date'])
    want=set(priority)
    selected=sorted((c for c in expected if c not in retained),
                    key=lambda c:(c not in want,c))[:limit]
    return expected,retained,selected


def margin_parts(catalog,history,supplement=None):
    """Rebuild three-year margin peer ranks from actual same-scope evidence.

    Keep the score allocation (4 personal points -> 3 R1d points) unchanged.
    A FY2025 comparison is contemporary source evidence, NOT original-year PIT.
    """
    expected=expected_sources(catalog,history)
    retained=verified_prior(supplement,expected,catalog['date'])
    by_market={r['code']:r['market'] for r in catalog['rows']}
    margins={}
    supplemented=set()
    years=('2023','2024','2025')
    for code,record in history['records'].items():
        if code not in by_market:
            continue
        direct=_three_year_margin(record,history['fiscal_years'])
        if direct is not None:
            margins[code]=direct
            continue
        supplement_row=retained.get(code)
        if not supplement_row or supplement_row['status']!='observed':
            continue
        scope=supplement_row['scope']
        originals=record['years']
        amounts=[]
        for year in years:
            item=originals[year][scope]
            revenue=item['values']['revenue']
            op=item['values'].get('operating_income')
            if op is None:
                op=supplement_row['recovered_operating_income_krw'].get(year)
            if type(op) is not int or type(revenue) is not int or revenue<=0:
                break
            amounts.append(100*op/revenue)
        if len(amounts)==3 and all(math.isfinite(x) and abs(x)<1e7 for x in amounts):
            margins[code]=round(sum(amounts)/3,6)
            supplemented.add(code)
    peers={m:sorted(v for c,v in margins.items() if by_market[c]==m)
           for m in ('KOSPI','KOSDAQ')}
    parts={code:{'points':round(round(4*_midrank(value,peers[by_market[code]],1),6)*3/4,6),
                 'available':3,'asof':catalog['date']}
           for code,value in margins.items() if len(peers[by_market[code]])>=30}
    summary={'provider_calls':0,'three_year_actual_operating_margins':len(margins),
             'verified_2025_comparative_margin_issuers':len(supplemented),
             'investment_score_certified':False,'historical_PIT_source_certified':False}
    return parts,summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docs',default='docs')
    p.add_argument('--cache',default='.cache/v3-dart-company-codes.json')
    p.add_argument('--mode',choices=('dry-run','live','verify'),default='dry-run')
    p.add_argument('--limit',type=int,default=LIMIT)
    args=p.parse_args(argv)
    root=Path(args.docs);read=lambda n:json.loads((root/n).read_text(encoding='utf-8'))
    catalog,history=read('v22-market-catalog.json'),read('v3-dart-bulk-history.json')
    path=root/OUTPUT;prior=read(OUTPUT) if path.is_file() else None
    source=root/'v3-operational-evidence-priority.json';want=set()
    if source.is_file():
        gap=read(source.name)
        if (gap.get('schema')=='v3-r1d-observation-work-priority-v1' and
            gap.get('asof')==catalog['date'] and
            gap.get('listed_total')==catalog['listed_total']):
            want=set(gap.get('coverage_90_first',{}).get(
                'family_conditional_priority_codes',{}).get('dart_annual_fundamentals',[]))
    expected,retained,selected=plan(catalog,history,prior,priority=want,limit=args.limit)
    output={'asof':catalog['date'],'eligible':len(expected),
            'remaining':len(expected)-len(retained),'selected':len(selected),
            'selected_below90_annual_candidates':sum(c in want for c in selected),
            'reviewed':0,'new_observed':0,'provider_calls':0,
            'score_points_added':0,'investment_score_certified':False}
    if args.mode=='verify':
        if prior is None:raise OperatingComparativeError('operating_comparative_checkpoint_missing')
        print(json.dumps({**output,'status':'verified','observed':len([
            r for r in retained.values() if r['status']=='observed'])}))
        return
    if args.mode=='dry-run' or not selected:
        print(json.dumps({**output,'status':'dry_run'},sort_keys=True));return
    if path.is_symlink():raise OperatingComparativeError('unsafe_operating_comparative_path')
    import requests
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):
        raise OperatingComparativeError('operating_comparative_key_unavailable')
    cache_path=Path(args.cache)
    cache=json.loads(cache_path.read_text()) if cache_path.is_file() else None
    members={r['code']:r for r in catalog['rows']}
    mapping=verified_company_mapping_cache(cache,catalog['date'],members)
    client=requests.Session()
    def request(endpoint,params):
        try:
            resp=client.get('https://opendart.fss.or.kr/api/'+endpoint,
                params={'crtfc_key':key,**params},timeout=(20,75))
            output['provider_calls']+=1
            resp.raise_for_status()
            return resp
        except Exception:
            raise OperatingComparativeError('operating_comparative_provider_unavailable') from None
    if mapping is None:
        mapping=_mapping(request('corpCode.xml',{}).content,set(members))
    deadline=time.monotonic()+420
    for code in selected:
        if time.monotonic()+85>=deadline:break
        if code not in mapping:continue
        base=expected[code]
        try:
            response=request('fnlttSinglAcntAll.json',{
                'corp_code':mapping[code],'bsns_year':'2025',
                'reprt_code':'11011','fs_div':base['scope']}).json()
            observed=parse(response,base,corp=mapping[code],day=catalog['date'])
        except OperatingComparativeError as err:
            if str(err)=='operating_comparative_provider_unavailable':raise
            observed={**base,'checked_on':catalog['date'],'status':'not_observed',
                      'recovered_operating_income_krw':{},'source_account_id':None,
                      'source_account_name':None,'source_sha256':_stable_digest(response)}
        retained[code]=observed
        write_json(path,snapshot(catalog['date'],catalog,retained))
        output['reviewed']+=1
        output['new_observed']+=int(observed['status']=='observed')
    print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'status':'blocked_operating_comparative_source',
                          'reason':'operating_comparative_not_verified'}))
        raise SystemExit(1)
