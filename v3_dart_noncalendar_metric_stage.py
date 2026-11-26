"""Stage only verified non-calendar annual metrics; no score or raw filing writes."""
from datetime import date
from collections import Counter
import argparse,hashlib,json,math,os,re,threading,time
from pathlib import Path
from v3_dart_noncalendar_history_audit import normalize
from v3_dart_noncalendar_period_audit import annual_period
from v3_dart_bulk_history import _mapping,verified_company_mapping_cache
from v3_eps_parallel_collection import collect

def metrics(years,cap):
    out={}
    latest=years.get(2025,{})
    rev=latest.get('revenue')
    if type(rev) is int and rev>0 and type(cap) in (int,float) and 0<cap<1e12:
        ratio=rev/(cap*1e8)
        if 0<ratio<1e6:out['sales_to_market_cap']=round(ratio,8)
    a,l,e=(latest.get(k) for k in ('assets','liabilities','equity'))
    if all(type(x) is int for x in (a,l,e)) and a>0 and l>=0 and e>0 and abs(a-l-e)<=max(1,a*.01):
        out['liabilities_to_assets_pct']=round(100*l/a,6)
    recent=[years.get(y,{}) for y in (2023,2024,2025)]
    if all(type(v.get('revenue')) is int and v['revenue']>0 and type(v.get('operating_income')) is int for v in recent):
        margin=sum(100*v['operating_income']/v['revenue'] for v in recent)/3
        if math.isfinite(margin) and abs(margin)<1e7:out['operating_margin_3y_pct']=round(margin,6)
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('dry-run','live'),default='dry-run')
    p.add_argument('--followup',action='store_true',help='Stage the two verified FY2025 follow-up receipts')
    args=p.parse_args()
    read=lambda n:json.loads(Path(n).read_text())
    cat=read('docs/v22-market-catalog.json');book=read('docs/v3-dart-book-reference.json')
    path='ops/v3-noncalendar-history-evidence-20261125.json'
    source_run=45778634105
    expected_count=33
    if args.followup:
        path='ops/v3-noncalendar-history-followup-20261125.json'
        source_run=45821091852
        expected_count=2
    evidence=read(path)
    day=cat['date'];cutoff=date.fromisoformat(day)
    if evidence['asof']!=day or book['asof']!=day or evidence['schema']!='v3-noncalendar-history-evidence-v1' or evidence['source_run']!=source_run:raise ValueError('unaligned_source')
    members={v['code']:v for v in cat['rows']};current={v['code']:v.get('annual') for v in book['records']}
    selected=[v for v in evidence['records'] if v['status']=='verified_period']
    if args.followup:
        selected=[v for v in selected if v['year']==2025]
        if {v['code'] for v in selected}!={'021880','032800'}:
            raise ValueError('unexpected_followup_issuer')
    if (len(selected)!=expected_count
        or len({(v['code'],v['year']) for v in selected})!=expected_count):
        raise ValueError('invalid_verified_cohort')
    for v in selected:
        code=v['code'];r=v['receipt_id'];received=date.fromisoformat(r[:4]+'-'+r[4:6]+'-'+r[6:8])
        if code not in members or received>cutoff or not annual_period(v['period_start'],v['period_end'],v['year'],int(v['period_end'][5:7]),received) or not re.fullmatch('[a-f0-9]{64}',v['archive_sha256']):raise ValueError('invalid_period_evidence')
        base=current.get(code)
        if v['year']==2025 and (not base or base['receipt_id']!=r or base['fs_div']!=v['scope'] or base['year']!=2025):raise ValueError('latest_receipt_changed')
    out={'asof':day,'selected':len(selected),'provider_calls':0,'points_added':0,'research_only':True,'records':[]}
    if args.mode=='dry-run':print(json.dumps(out));return
    import requests
    key=os.getenv('DART_API_KEY','')
    if len(key)!=40:raise ValueError('missing_key')
    lock=threading.Lock();local=threading.local();clients=[];done=[];values={}
    def request(endpoint,params):
        if not hasattr(local,'s'):
            local.s=requests.Session()
            with lock:clients.append(local.s)
        with lock:out['provider_calls']+=1
        try:
            res=local.s.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=(15,60))
            res.raise_for_status();return res
        except Exception:raise ValueError('provider_unavailable') from None
    cache=Path('.cache/v3-dart-company-codes.json')
    mapping=verified_company_mapping_cache(json.loads(cache.read_text()) if cache.is_file() else None,day,members)
    try:
        if mapping is None:
            with request('corpCode.xml',{}) as res:mapping=_mapping(res.content,set(members))
        def fetch(v):
            code=v['code'];year=v['year'];corp=mapping.get(code)
            if not corp:return v,None,'no_mapping'
            with request('fnlttSinglAcntAll.json',{'corp_code':corp,'bsns_year':str(year),'reprt_code':'11011','fs_div':v['scope']}) as res:body=res.json()
            if body.get('status')!='000':return v,None,'statement_unavailable'
            try:a=normalize(body.get('list'),corp,year,v['scope'],cutoff)
            except ValueError:return v,None,'untrusted_statement'
            if not a or a['receipt_id']!=v['receipt_id'] or sorted(a['values'])!=v['observed_fields']:return v,None,'receipt_or_fields_changed'
            if year==2025:
                base=current[code]['values']
                if any(base.get(k) is not None and base[k]!=a['values'][k] for k in ('revenue','operating_income','operating_cashflow') if k in a['values']):return v,None,'latest_amount_conflict'
            return v,a['values'],'verified_same_receipt'
        def checkpoint(item):
            v,a,status=item
            rec={'code':v['code'],'year':v['year'],'status':status}
            if a is not None:
                rec.update(receipt_id=v['receipt_id'],scope=v['scope'],period_start=v['period_start'],period_end=v['period_end'],archive_sha256=v['archive_sha256'],normalized_values_sha256=hashlib.sha256(json.dumps(a,sort_keys=True,separators=(',',':')).encode()).hexdigest())
                values.setdefault(v['code'],{})[v['year']]=a
            done.append(rec)
        for offset in range(0,len(selected),20):
            batch=selected[offset:offset+20];before=len(done)
            collect(batch,fetch,checkpoint,workers=4,max_choices=20,deadline=time.monotonic()+300)
            if len(done)-before!=len(batch):raise ValueError('incomplete_stage')
    finally:
        for s in clients:s.close()
    for code,years in sorted(values.items()):
        observed=metrics(years,members[code].get('reference_market_cap_eok'))
        if observed:
            out['records'].append({'code':code,'market':members[code]['market'],'metrics':observed,'source_years':sorted(years),'receipt_proofs':sorted((x for x in done if x['code']==code and x['status']=='verified_same_receipt'),key=lambda x:x['year'])})
    out['completed']=len(done);out['reasons']=dict(Counter(v['status'] for v in done));out['metric_counts']=dict(Counter(k for r in out['records'] for k in r['metrics']))
    print(json.dumps(out,sort_keys=True))
if __name__=='__main__':
    try:main()
    except Exception:print(json.dumps({'status':'blocked_noncalendar_metric_stage'}));raise SystemExit(1)
