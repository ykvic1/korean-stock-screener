"""Receipt-bound annual parent-equity supplement, separate from the DART book.

Only exact IFRS consolidated BS equity less EXPLICIT non-controlling interests
is admitted. This derived annual reference is neither official KRX PBR nor PIT
certification; original OpenDART rows never enter the public repository.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import re

import v3_dart_cashflow_enrichment as cf
import v3_dart_parent_equity_pilot as pilot
from v3_dart_bulk_history import _mapping, verified_company_mapping_cache, write_json

SCHEMA='v3-same-receipt-reported-parent-equity-v1'
CONTRACT='2025_cfs_total_equity_less_explicit_nci_v1'
MAX_CAPTURE=7


class ParentEquityRecoveryError(ValueError):pass


def _annuals(book):
    records=book.get('records')
    if type(records) is not list or len(records)>6000:
        raise ParentEquityRecoveryError('untrusted_parent_equity_book')
    found={}
    for row in records:
        if type(row) is not dict or row.get('code') in found:
            raise ParentEquityRecoveryError('duplicate_parent_equity_book_issuer')
        found[row['code']]=row.get('annual')
    return found


def verify(saved,catalog,book,cash):
    if (type(saved) is not dict or saved.get('schema')!=SCHEMA
        or saved.get('investment_score_certified') is not False
        or saved.get('historical_PIT_certified') is not False
        or saved.get('raw_provider_rows_published') is not False
        or type(saved.get('records')) is not dict or len(saved['records'])>MAX_CAPTURE):
        raise ParentEquityRecoveryError('untrusted_parent_equity_supplement')
    day=saved.get('observed_on')
    try:
        if not isinstance(day,str) or date.fromisoformat(day)>date.fromisoformat(catalog['date']):
            raise ValueError()
    except (ValueError,TypeError):
        raise ParentEquityRecoveryError('invalid_parent_equity_observed_day') from None
    book_rows=_annuals(book)
    members={r['code'] for r in catalog['rows']}
    for code,proof in saved['records'].items():
        if (type(code) is not str or not pilot.CODE.fullmatch(code) or code not in members
            or type(proof) is not dict or set(proof)!={
                'contract','receipt_id','year','scope','equity_anchor_krw',
                'explicit_nci_krw','owners_equity_krw','cfo_anchor_krw',
                'provider_sha256','observed_on','digest'}):
            raise ParentEquityRecoveryError('invalid_parent_equity_proof')
        try:
            proof_date=date.fromisoformat(proof['observed_on'])
            if not date.fromisoformat(day)<=proof_date<=date.fromisoformat(catalog['date']):
                raise ValueError()
        except (ValueError,TypeError):
            raise ParentEquityRecoveryError('invalid_parent_equity_proof_date') from None
        annual=book_rows.get(code)
        anchor=cash.get('records',{}).get(code,{}).get('years',{}).get('2025',{})
        total=proof['equity_anchor_krw'];nci=proof['explicit_nci_krw'];owner=proof['owners_equity_krw']
        if (type(annual) is not dict or annual.get('year')!=2025
            or annual.get('fs_div')!='CFS' or annual.get('currency')!='KRW'
            or annual.get('values',{}).get('owners_equity') is not None
            or proof['contract']!=CONTRACT or proof['year']!=2025
            or proof['scope']!='CFS'
            or proof['receipt_id']!=annual.get('receipt_id')
            or type(total) is not int or total!=annual.get('values',{}).get('equity')
            or type(nci) is not int or abs(nci)>10**18
            or type(owner) is not int or not 0<owner<=10**18
            or total-nci!=owner
            or anchor.get('major_annual_receipt_matched') is not True
            or anchor.get('annual_receipt_id')!=proof['receipt_id']
            or anchor.get('scope')!='CFS'
            or type(proof['cfo_anchor_krw']) is not int
            or proof['cfo_anchor_krw']!=anchor.get('operating_cashflow_krw')
            or type(proof['provider_sha256']) is not str
            or not re.fullmatch('[a-f0-9]{64}',proof['provider_sha256'])
            or proof['digest']!=cf.fingerprint({k:v for k,v in proof.items() if k!='digest'})):
            raise ParentEquityRecoveryError('untrusted_parent_equity_recovery')
    return len(saved['records'])


def blank(day):
    return {'schema':SCHEMA,'observed_on':day,'records':{},
            'investment_score_certified':False,'historical_PIT_certified':False,
            'raw_provider_rows_published':False}


def merge(saved,code,annual,cfo,payload,*,corp,day,catalog,book,cash):
    out=deepcopy(saved)
    if code in out['records']:
        raise ParentEquityRecoveryError('duplicate_parent_equity_recovery')
    value,basis=pilot.parse(payload,corp=corp,code=code,receipt=annual['receipt_id'],
                            year=2025,scope='CFS',expected_equity=annual['values']['equity'])
    if value is None or basis!='same_receipt_total_minus_explicit_nci':
        return out,basis
    total=int(annual['values']['equity'])
    proof={'contract':CONTRACT,'receipt_id':annual['receipt_id'],'year':2025,
           'scope':'CFS','equity_anchor_krw':total,
           'explicit_nci_krw':total-value,'owners_equity_krw':value,
           'cfo_anchor_krw':cfo,'provider_sha256':cf.fingerprint(payload),
           'observed_on':day}
    proof['digest']=cf.fingerprint(proof)
    out['records'][code]=proof
    verify(out,catalog,book,cash)
    return out,'verified_reported_parent_equity'


def overlay(book,catalog,saved,cash):
    """Build an in-memory audited book; the published raw book is untouched."""
    verify(saved,catalog,book,cash)
    patched=deepcopy(book)
    records={row['code']:row for row in patched['records']}
    observations={row['code']:row for row in patched['observations']}
    members={row['code']:row for row in catalog['rows']}
    for code,proof in saved['records'].items():
        annual=records[code]['annual']
        annual['values']['owners_equity']=proof['owners_equity_krw']
        cap=members[code].get('reference_market_cap_eok')
        if type(cap) not in (int,float) or not 0<cap<1e12:
            raise ParentEquityRecoveryError('missing_parent_equity_market_cap')
        pbr=round(cap*1e8/proof['owners_equity_krw'],3)
        if not 0<pbr<1000:
            raise ParentEquityRecoveryError('out_of_bounds_parent_equity_pbr')
        row=observations.get(code)
        if (row is None or row.get('receipt_id')!=proof['receipt_id']
            or row.get('year')!=2025 or row.get('fs_div')!='CFS'
            or row.get('market_cap_reference_day')!=catalog['date']
            or row.get('official_exchange_pbr_verified') is not False
            or row.get('pbr_simple_annual_reference') is not None):
            raise ParentEquityRecoveryError('unmatched_parent_equity_valuation_row')
        row['pbr_simple_annual_reference']=pbr
    return patched


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('dry-run','live','verify'),default='dry-run')
    a=p.parse_args();root=Path('docs');target=root/'v3-dart-parent-equity-supplement.json'
    if target.is_symlink():raise ParentEquityRecoveryError('unsafe_parent_equity_output')
    read=lambda name:json.loads((root/name).read_text(encoding='utf8'))
    catalog,book,cash,report,comparison=[read(n) for n in (
        'v22-market-catalog.json','v3-dart-book-reference.json','v3-dart-cashflow-reference.json',
        'v3-operational-research-scores.json','v3-operational-research-comparison.json')]
    saved=read(target.name) if target.is_file() else blank(catalog['date'])
    if a.mode=='verify':
        print(json.dumps({'verified_recoveries':verify(saved,catalog,book,cash),'provider_calls':0}));return
    selected,priority=pilot.targets(report,comparison,catalog,book,cash,MAX_CAPTURE)
    selected=[(code,annual) for code,annual in selected if code not in saved['records']]
    output={'selected':len(selected),'priority_pbr_only_issuers':priority,
            'verified_recoveries':len(saved['records']),'provider_calls':0,
            'reasons':{},'investment_score_certified':False}
    if a.mode=='dry-run' or not selected:
        print(json.dumps(output,sort_keys=True));return
    key=os.getenv('DART_API_KEY','')
    if not re.fullmatch('[A-Za-z0-9]{40}',key):raise ParentEquityRecoveryError('missing_parent_equity_key')
    import requests
    client=requests.Session();members={r['code']:r for r in catalog['rows']}
    cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,
                                           catalog['date'],members)
    def get(endpoint,params):
        try:
            r=client.get('https://opendart.fss.or.kr/api/'+endpoint,
                         params={'crtfc_key':key,**params},timeout=(20,60))
            output['provider_calls']+=1;r.raise_for_status();return r
        except Exception:raise ParentEquityRecoveryError('parent_equity_provider_unavailable') from None
    if mapping is None:mapping=_mapping(get('corpCode.xml',{}).content,set(members))
    from collections import Counter
    reasons=Counter()
    for code,annual in selected:
        if code not in mapping:
            reasons['company_mapping_missing']+=1;continue
        payload=get('fnlttSinglAcntAll.json',{'corp_code':mapping[code],
                    'bsns_year':'2025','reprt_code':'11011','fs_div':'CFS'}).json()
        cfo=cash['records'][code]['years']['2025']['operating_cashflow_krw']
        try:
            next_saved,reason=merge(saved,code,annual,cfo,payload,corp=mapping[code],
                                    day=catalog['date'],catalog=catalog,book=book,cash=cash)
        except pilot.ParentEquityError as error:
            next_saved,reason=saved,str(error)
        if next_saved!=saved:
            write_json(target,next_saved);saved=next_saved
        reasons[reason]+=1
    output['verified_recoveries']=len(saved['records'])
    output['reasons']=dict(reasons)
    print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_parent_equity_recovery',
                          'reason':'parent_equity_source_not_verified'}))
        raise SystemExit(1)
