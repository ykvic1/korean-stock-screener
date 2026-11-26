"""One-time bounded non-calendar fiscal period proof; research only, no score writes."""
from datetime import date,timedelta
import argparse
import calendar
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import zipfile

import v3_dart_xbrl_capex_pilot as xp
from v3_dart_xbrl_cashflow_recovery import X,D,company_ids,_number
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache,_valid_receipt
from v3_saved_annual_income_overlap_audit import _valid_book
from v3_eps_parallel_collection import collect

# Independently located historical annual filing periods. These only
# nominate a month for an exact-receipt original XBRL check; no score admission.
HISTORICAL_MONTHS = {
    ('021880', '20250617000184', 2025, 'CFS'): 3,
    ('032800', '20250917000341', 2025, 'CFS'): 6,
}

def historical_month(code, annual):
    return HISTORICAL_MONTHS.get((code, annual.get('receipt_id'),
                                  annual.get('year'), annual.get('fs_div')))

def annual_period(start,end,year,month,received):
    try:
        s,e=date.fromisoformat(start),date.fromisoformat(end)
        expected=date(year,month,calendar.monthrange(year,month)[1])
        previous=date(year-1,month,calendar.monthrange(year-1,month)[1])
    except (ValueError,TypeError,OverflowError):
        return False
    return month!=12 and e==expected and s==previous+timedelta(days=1) and e<received

def anchors(data,annual,ids,month,received):
    root=xp._safe_xbrl_xml(data)
    if root.tag!=X+'xbrl':return set()
    bindings=xp._unambiguous_namespace_bindings(data)
    contexts={};seen=set()
    for ctx in root.findall(X+'context'):
        key=ctx.get('id')
        if not key or key in seen:raise ValueError('duplicate_context')
        seen.add(key)
        entity=ctx.find(X+'entity');period=ctx.find(X+'period')
        if entity is None or period is None or len(period)!=2:continue
        ident=entity.findall(X+'identifier')
        if len(ident)!=1 or not ident[0].get('scheme') or (ident[0].text or '').strip().replace('-','') not in ids:continue
        start,end=period.findtext(X+'startDate'),period.findtext(X+'endDate')
        if not annual_period(start,end,annual['year'],month,received):continue
        dims=[v for part in (entity.find(X+'segment'),ctx.find(X+'scenario')) if part is not None for v in part]
        if len(dims)!=1 or dims[0].tag!=D+'explicitMember':continue
        axis=xp._safe_qname(dims[0].get('dimension'),bindings)
        member=xp._safe_qname(dims[0].text,bindings)
        if axis is None or not xp._ifrs_full_namespace(axis[0]) or axis[1]!='ConsolidatedAndSeparateFinancialStatementsAxis' or member!=(axis[0],'ConsolidatedMember' if annual['fs_div']=='CFS' else 'SeparateMember'):continue
        contexts[key]=(start,end)
    units=set();seen=set()
    for unit in root.findall(X+'unit'):
        key=unit.get('id')
        if not key or key in seen:raise ValueError('duplicate_unit')
        seen.add(key)
        if len(unit)==1 and unit[0].tag==X+'measure' and xp._safe_qname(unit[0].text,bindings)==('http://www.xbrl.org/2003/iso4217','KRW'):units.add(key)
    concepts={'Revenue':'revenue','CashFlowsFromUsedInOperatingActivities':'operating_cashflow'}
    facts={}
    for fact in root:
        ctx=fact.get('contextRef')
        if ctx not in contexts or fact.get('unitRef') not in units:continue
        uri,local=xp._qualified_name(fact.tag)
        if not xp._ifrs_full_namespace(uri) or local not in concepts:continue
        field=concepts[local]
        saved=annual['values'].get(field)
        if type(saved) not in (int,float) or int(saved)!=saved or abs(saved)>10**18 or saved==0:continue
        facts.setdefault((contexts[ctx],field),set()).add(_number(fact))
    if any(values!={annual['values'][field]} for (_,field),values in facts.items()):
        raise ValueError('conflicting_scope_anchor')
    return {period for (period,field),values in facts.items()}

def inspect(raw,annual,ids,month,received):
    if xp.inspect_binary_archive(raw,include_extension_evidence=False)['file_absent']:return 'original_absent',None
    found=set()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for entry in z.infolist():
            if not entry.is_dir() and entry.filename.lower().endswith(('.xml','.xbrl')):
                found.update(anchors(z.read(entry),annual,ids,month,received))
    if len(found)!=1:return 'no_unique_explicit_scope_period_anchor',None
    return 'verified_period',next(iter(found))

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=('dry-run','live'),default='dry-run');p.add_argument('--historical-month-recheck',action='store_true');args=p.parse_args()
    root=Path('docs')
    read=lambda n:json.loads((root/n).read_text())
    catalog=read('v22-market-catalog.json');book=read('v3-dart-book-reference.json');priority=read('v3-operational-evidence-priority.json')
    day=catalog['date'];cutoff=date.fromisoformat(day);year=cutoff.year-1
    if book['asof']!=day or priority['asof']!=day:raise ValueError('source_date_mismatch')
    members={row['code']:row for row in catalog['rows']}
    codes=set(priority['coverage_90_first']['family_conditional_priority_codes']['dart_annual_fundamentals'])
    selected=[]
    for row in book['records']:
        a=row.get('annual')
        if row['code'] not in codes or row.get('status')!='observed' or not isinstance(a,dict):continue
        if a.get('year')!=year or a.get('currency')!='KRW' or a.get('fs_div') not in ('CFS','OFS') or _valid_book(a,year,cutoff):continue
        receipt=a.get('receipt_id','')
        if not re.fullmatch(r'[0-9]{14}',receipt):continue
        received=date.fromisoformat(receipt[:4]+'-'+receipt[4:6]+'-'+receipt[6:8])
        if received<=date(year,12,31) and received<=cutoff:selected.append((row['code'],a,received))
    if args.historical_month_recheck:
        selected=[v for v in selected if historical_month(v[0],v[1]) is not None]
    if len(selected)>25 or len({v[0] for v in selected})!=len(selected):raise ValueError('cohort_not_bounded')
    result={'asof':day,'selected':len(selected),'verified':0,'provider_calls':0,'points_added':0,'research_only':True,'records':[]}
    if args.mode=='dry-run' or not selected:print(json.dumps(result));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if len(key)!=40:raise ValueError('missing_dart_key')
    import threading,time
    lock=threading.Lock();local=threading.local();clients=[]
    def request(endpoint,params,stream=False):
        if not hasattr(local,'session'):
            local.session=requests.Session()
            with lock:clients.append(local.session)
        with lock:result['provider_calls']+=1
        try:
            response=local.session.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(15,60),stream=stream)
            response.raise_for_status();return response
        except Exception:raise ValueError('provider_unavailable') from None
    cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    try:
        if mapping is None:
            with request('corpCode.xml',{}) as res:mapping=_mapping(res.content,set(members))
        def fetch(choice):
            code,a,received=choice
            record={'code':code,'fiscal_year':a['year'],'scope':a['fs_div'],'receipt_id':a['receipt_id']}
            if code not in mapping:return dict(record,status='no_company_mapping')
            with request('company.json',{'corp_code':mapping[code]}) as res:profile=res.json()
            ids=company_ids(profile,mapping[code],code)
            raw_month=profile.get('acc_mt','')
            old_month=historical_month(code,a) if args.historical_month_recheck else None
            if old_month is not None:
                month=old_month
                record['historical_month_candidate']=month
                record['month_basis']='receipt_bound_historical_period_candidate'
            else:
                if not isinstance(raw_month,str) or not re.fullmatch(r'0[1-9]|1[012]',raw_month) or raw_month=='12':
                    return dict(record,status='no_non_december_profile_month')
                month=int(raw_month)
            with request('fnlttXbrl.xml',{'rcept_no':a['receipt_id'],'reprt_code':'11011'},True) as res:raw=xp._read_response_limited(res)
            try:status,period=inspect(raw,a,ids,month,received)
            except (xp.XbrlPilotError,ValueError):return dict(record,status='unusable_or_ambiguous_original')
            record.update(status=status)
            if period:
                record.update(period_start=period[0],period_end=period[1],archive_sha256=hashlib.sha256(raw).hexdigest())
            return record
        def checkpoint(record):
            result['records'].append(record)
            result['verified']+=int(record['status']=='verified_period')
        collect(selected,fetch,checkpoint,workers=4,max_choices=25,deadline=time.monotonic()+360)
    finally:
        for client in clients:client.close()
        result['records'].sort(key=lambda r:r['code'])
        result['reasons']=dict(Counter(r['status'] for r in result['records']))
        print(json.dumps(result,sort_keys=True))
if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'blocked_noncalendar_period_audit'}));raise SystemExit(1)
