"""Read-only fiscal-2023/24 CF label discovery for PPE-only missing FCF.

Only bounded official annual rows are observed in memory. No amounts, raw
statements, company API codes, or scores are written or printed. A candidate
label is a research clue, never a verified PPE purchase or implied zero.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re

import v3_dart_cashflow_enrichment as cf
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache
from v3_v30b_release_gate import R1D_PART_LIMITS

MAX_CALLS=10


class LabelDiscoveryError(ValueError):
    """Fixed source-research failure codes only."""


def targets(report,comparison,catalog,bulk,cash,limit=MAX_CALLS):
    if type(limit) is not int or not 1<=limit<=MAX_CALLS:
        raise LabelDiscoveryError('invalid_label_discovery_limit')
    xp.operational_gap_priority(report,comparison,catalog,bulk)  # strict saved-score reconciliation
    if (report.get('asof')!=catalog.get('date') or cash.get('asof')!=catalog.get('date')):
        raise LabelDiscoveryError('unaligned_label_discovery_sources')
    only=set()
    for code,record in report['rows'].items():
        gaps={(area,key) for area,parts in R1D_PART_LIMITS.items()
              for key,maximum in parts.items()
              if maximum-record['parts'][area].get(key,[0,0])[1]>.00001}
        if gaps=={('financial','cashflow_fcf_persistence')}:
            only.add(code)
    queue=[]
    for code in sorted(only):
        for year,record in sorted(cash.get('records',{}).get(code,{}).get('years',{}).items()):
            if (year not in ('2023','2024') or record.get('free_cashflow_krw') is not None
                or record.get('ppe_capex_krw') is not None
                or type(record.get('intangible_capex_krw')) is not int
                or type(record.get('operating_cashflow_krw')) is not int
                or record.get('major_annual_receipt_matched') is not True
                or (record.get('account_name_recheck') or {}).get('result')!='checked_unavailable'):
                continue
            queue.append((code,year,record))
    return queue[:limit],len(queue)


def _safe_label(name):
    if type(name) is not str or not 1<=len(name)<=100:
        return None
    compact=''.join(name.split())
    # Observe nonstandard labels without accepting partial/NET/combined cash
    # movements as PPE CAPEX. Even a matching label is not scored here.
    if ('유형자산' not in compact or not any(x in compact for x in ('취득','구입','매입','증가'))
        or any(x in compact for x in ('처분','매각','순액','순증','유형및무형','유무형','유·무형','유ㆍ무형'))):
        return None
    return compact if re.fullmatch(r'[가-힣0-9()·ㆍ,.\-+]+',compact) else None


def examine(payload,*,code,corp,record,day):
    if type(payload) is not dict or payload.get('status') not in ('000','013'):
        raise LabelDiscoveryError('provider_label_response_unavailable')
    if payload['status']=='013':return [],'no_statement',False
    rows=payload.get('list')
    if type(rows) is not list or not 1<=len(rows)<=18000:
        raise LabelDiscoveryError('provider_label_response_unavailable')
    # A real same-receipt operating cashflow anchor must independently match.
    normalized=cf.normalize_year(rows,code=code,corp=corp,year=record['year'],
              scope=record['scope'],asof=day,major_receipt=record['annual_receipt_id'])
    if (normalized.get('major_annual_receipt_matched') is not True
        or normalized.get('operating_cashflow_krw')!=record['operating_cashflow_krw']):
        return [],'cfo_anchor_not_matched',False
    # Only a real, independently parsed standard PPE purchase can complete
    # the saved FCF observation. Never use label text as a numerical proxy.
    standard_ppe_usable=(type(normalized.get('ppe_capex_krw')) is int
        and normalized['ppe_capex_krw']>=0
        and (normalized.get('intangible_capex_krw') is None
             or normalized['intangible_capex_krw']==record['intangible_capex_krw']))
    found=[]
    for row in rows:
        if row.get('sj_div')!='CF' or row.get('account_detail','-') not in ('','-'):
            continue
        # The CFO anchor checks standard CF facts only; candidate rows must
        # independently carry the same issuer, fiscal year, scope and filing.
        if (row.get('corp_code')!=corp
            or ('stock_code' in row and row['stock_code']!=code)
            or str(row.get('bsns_year'))!=str(record['year'])
            or row.get('reprt_code')!='11011'
            or ('fs_div' in row and row['fs_div']!=record['scope'])):
            continue
        name=_safe_label(row.get('account_nm'))
        if name is None:continue
        if row.get('rcept_no')!=record['annual_receipt_id'] or row.get('currency')!='KRW':
            continue
        aid=row.get('account_id')
        category=('nonstandard' if aid==cf.NONSTANDARD_ACCOUNT_ID else
                  'standard_ifrs' if aid in cf.PPE_IDS else
                  'other_standard' if type(aid) is str and aid.startswith(('dart_','ifrs_','ifrs-full_')) else
                  'unclassified')
        found.append((name,category))
    return sorted(set(found)),'reviewed',standard_ppe_usable


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('dry-run','live'),default='dry-run')
    p.add_argument('--limit',type=int,default=7)
    a=p.parse_args();root=Path('docs');read=lambda n:json.loads((root/n).read_text())
    catalog,bulk,cash,report,comp=[read(n) for n in (
        'v22-market-catalog.json','v3-dart-bulk-history.json','v3-dart-cashflow-reference.json',
        'v3-operational-research-scores.json','v3-operational-research-comparison.json')]
    chosen,total=targets(report,comp,catalog,bulk,cash,a.limit)
    output={'selected':len(chosen),'eligible_ppe_only_years':total,'provider_calls':0,
            'reviewed':0,'candidate_label_counts':{},'reasons':{},'writes':0,
            'standard_ppe_recovery_candidates':0,
            'investment_score_certified':False}
    if a.mode=='dry-run' or not chosen:
        print(json.dumps(output,sort_keys=True,ensure_ascii=False));return
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise LabelDiscoveryError('missing_label_discovery_key')
    import requests
    members={x['code']:x for x in catalog['rows']};cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,
                                           catalog['date'],members)
    client=requests.Session()
    def request(endpoint,params):
        try:
            r=client.get('https://opendart.fss.or.kr/api/'+endpoint,
                         params={'crtfc_key':key,**params},timeout=(20,60))
            output['provider_calls']+=1;r.raise_for_status();return r
        except Exception:raise LabelDiscoveryError('provider_label_transport_unavailable') from None
    if mapping is None:mapping=_mapping(request('corpCode.xml',{}).content,set(members))
    reasons=Counter();names=Counter()
    for code,year,record in chosen:
        if code not in mapping:reasons['company_mapping_unavailable']+=1;continue
        payload=request('fnlttSinglAcntAll.json',{'corp_code':mapping[code],
                        'bsns_year':year,'reprt_code':'11011','fs_div':record['scope']}).json()
        try:labels,reason,usable=examine(payload,code=code,corp=mapping[code],record=record,day=catalog['date'])
        except (LabelDiscoveryError,cf.CashflowError):labels,reason,usable=[],'unusable_statement',False
        output['reviewed']+=1;reasons[reason]+=1;names.update(labels)
        output['standard_ppe_recovery_candidates']+=int(usable)
    output['candidate_label_counts']={name+' ['+kind+']':count for (name,kind),count in sorted(names.items())}
    output['reasons']=dict(reasons)
    print(json.dumps(output,sort_keys=True,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_capex_label_research',
                          'reason':'capex_label_source_not_verified'}));raise SystemExit(1)
