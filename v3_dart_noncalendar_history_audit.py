"""Read-only four-year noncalendar source recovery; no calendar guard relaxation."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from v3_dart_noncalendar_period_audit import inspect
from v3_dart_xbrl_cashflow_recovery import company_ids
import v3_dart_xbrl_capex_pilot as xp
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,_explicit_krw_currency,_amount
from v3_eps_parallel_collection import collect

# Strict, exact standard accounts; never substitute industry-specific labels.
IDS={'ifrs-full_Revenue':'revenue','ifrs-full_CashFlowsFromUsedInOperatingActivities':'operating_cashflow',
     'dart_OperatingIncomeLoss':'operating_income','ifrs-full_Assets':'assets',
     'ifrs-full_Liabilities':'liabilities','ifrs-full_Equity':'equity'}
STMT={'revenue':('IS','CIS'),'operating_income':('IS','CIS'),'operating_cashflow':('CF',),
      'assets':('BS',),'liabilities':('BS',),'equity':('BS',)}

def normalize(rows,corp,year,scope,cutoff):
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('invalid_annual_rows')
    if not rows:return None
    receipts=set();values={};duplicates=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('corp_code')!=corp or str(row.get('bsns_year'))!=str(year) or row.get('reprt_code')!='11011':raise ValueError('annual_identity_mismatch')
        if row.get('fs_div') not in (None,scope):raise ValueError('annual_scope_mismatch')
        receipt=row.get('rcept_no','')
        if not isinstance(receipt,str) or not re.fullmatch(r'[0-9]{14}',receipt):raise ValueError('invalid_receipt')
        received=date.fromisoformat(receipt[:4]+'-'+receipt[4:6]+'-'+receipt[6:8])
        if received>cutoff:raise ValueError('future_receipt')
        receipts.add(receipt)
        field=IDS.get(row.get('account_id'))
        if field is None or row.get('sj_div') not in STMT[field] or not _explicit_krw_currency(row.get('currency')):continue
        value=_amount(row.get('thstrm_amount'))
        if field in values and values[field]!=value:duplicates.add(field)
        values[field]=value
    if len(receipts)!=1:raise ValueError('mixed_receipts')
    for field in duplicates:values.pop(field,None)
    values={k:v for k,v in values.items() if type(v) is int and abs(v)<=10**18}
    if not any(values.get(k) not in (None,0) for k in ('revenue','operating_cashflow')):return None
    if all(k in values for k in ('assets','liabilities','equity')) and abs(values['assets']-values['liabilities']-values['equity'])>max(1,abs(values['assets'])*.01):
        for k in ('assets','liabilities','equity'):values.pop(k)
    return {'year':year,'fs_div':scope,'receipt_id':next(iter(receipts)),'values':values,'currency':'KRW'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=('dry-run','live'),default='dry-run');a=p.parse_args()
    root=Path('docs');read=lambda n:json.loads((root/n).read_text())
    cat=read('v22-market-catalog.json');book=read('v3-dart-book-reference.json')
    proof=json.loads(Path('ops/v3-noncalendar-period-evidence-20261125.json').read_text())
    day=cat['date'];cutoff=date.fromisoformat(day)
    if proof['asof']!=day or book['asof']!=day:raise ValueError('noncalendar_source_day_mismatch')
    members={v['code']:v for v in cat['rows']}
    current={v['code']:v.get('annual') for v in book['records']}
    selected=[]
    for v in proof['records']:
        if v['status']!='verified_period':continue
        code=v['code'];base=current.get(code)
        if code not in members or not base or base['receipt_id']!=v['receipt_id'] or base['fs_div']!=v['scope'] or base['year']!=v['fiscal_year']:raise ValueError('noncalendar_receipt_changed')
        for year in range(v['fiscal_year']-3,v['fiscal_year']+1):
            selected.append((code,v,year))
    if len(selected)>56 or len({(c,y) for c,_,y in selected})!=len(selected):raise ValueError('invalid_bounded_cohort')
    out={'asof':day,'selected':len(selected),'verified_years':0,'provider_calls':0,'points_added':0,'research_only':True,'records':[]}
    if a.mode=='dry-run':print(json.dumps(out));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if len(key)!=40:raise ValueError('missing_key')
    lock=threading.Lock();local=threading.local();clients=[];profiles={};profile_locks={c:threading.Lock() for c,_,_ in selected}
    def request(endpoint,params,stream=False):
        if not hasattr(local,'s'):
            local.s=requests.Session()
            with lock:clients.append(local.s)
        with lock:out['provider_calls']+=1
        try:
            res=local.s.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(15,60),stream=stream)
            res.raise_for_status();return res
        except Exception:raise ValueError('provider_unavailable') from None
    cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    try:
        if mapping is None:
            with request('corpCode.xml',{}) as res:mapping=_mapping(res.content,set(members))
        def fetch(choice):
            code,v,year=choice
            rec={'code':code,'year':year,'scope':v['scope']}
            if code not in mapping:return dict(rec,status='no_mapping')
            corp=mapping[code]
            with profile_locks[code]:
                if code not in profiles:
                    with request('company.json',{'corp_code':corp}) as res:profile=res.json()
                    profiles[code]=company_ids(profile,corp,code)
            ids=profiles[code]
            with request('fnlttSinglAcntAll.json',{'corp_code':corp,'bsns_year':str(year),'reprt_code':'11011','fs_div':v['scope']}) as res:body=res.json()
            if body.get('status') in ('013','014'):return dict(rec,status='no_statement')
            if body.get('status')!='000':raise ValueError('provider_non_success')
            try:annual=normalize(body.get('list'),corp,year,v['scope'],cutoff)
            except ValueError:return dict(rec,status='untrusted_statement')
            if annual is None:return dict(rec,status='missing_standard_anchor')
            if year==v['fiscal_year'] and annual['receipt_id']!=v['receipt_id']:return dict(rec,status='latest_receipt_changed')
            received=date.fromisoformat(annual['receipt_id'][:4]+'-'+annual['receipt_id'][4:6]+'-'+annual['receipt_id'][6:8])
            month=int(v['period_end'][5:7])
            with request('fnlttXbrl.xml',{'rcept_no':annual['receipt_id'],'reprt_code':'11011'},True) as res:raw=xp._read_response_limited(res)
            try:status,period=inspect(raw,annual,ids,month,received)
            except (xp.XbrlPilotError,ValueError):return dict(rec,status='unusable_original')
            if not period:return dict(rec,status=status)
            digest=hashlib.sha256(raw).hexdigest()
            if year==v['fiscal_year'] and digest!=v['archive_sha256']:return dict(rec,status='latest_archive_changed')
            # No source amounts published in logs; only availability, receipt and exact period.
            return dict(rec,status='verified_period',receipt_id=annual['receipt_id'],period_start=period[0],period_end=period[1],archive_sha256=digest,observed_fields=sorted(annual['values']))
        def checkpoint(rec):
            out['records'].append(rec);out['verified_years']+=int(rec['status']=='verified_period')
        collect(selected,fetch,checkpoint,workers=4,max_choices=56,deadline=time.monotonic()+480)
    finally:
        for s in clients:s.close()
        out['records'].sort(key=lambda x:(x['code'],x['year']))
        out['reasons']=dict(Counter(x['status'] for x in out['records']))
        out['four_year_revenue_ready']=sum(all(any(x['code']==c and x['year']==y and x['status']=='verified_period' and 'revenue' in x.get('observed_fields',[]) for x in out['records']) for y in range(cutoff.year-4,cutoff.year)) for c in {c for c,_,_ in selected})
        print(json.dumps(out,sort_keys=True))
if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_noncalendar_historical_audit'}));raise SystemExit(1)
