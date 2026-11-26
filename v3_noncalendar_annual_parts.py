"""Admit receipt-bound non-December annual metrics into missing R1e factors.

The reviewed source is date-visible and scope-bound. Existing issuer factors
are not replaced. Same-market peer ranks use the existing metric formulas.
"""
from datetime import date,timedelta
import calendar,math,re
from v3_dart_bulk_history import research_parts,_midrank
from v3_dart_operating_income_comparative import margin_observations

OUTPUT='v3-noncalendar-verified-metrics.json'
MAP={'sales_to_market_cap':('valuation','sales_to_cap',5,2.5,1),
     'liabilities_to_assets_pct':('financial','balance_sheet_strength',4,3,-1),
     'operating_margin_3y_pct':('financial','operating_growth_margin',4,3,1)}

def valid_period(proof,scope,cutoff):
    try:
        year=proof['year'];s=date.fromisoformat(proof['period_start']);e=date.fromisoformat(proof['period_end'])
        r=proof['receipt_id'];received=date.fromisoformat(r[:4]+'-'+r[4:6]+'-'+r[6:8])
        return (type(year) is int and e.year==year and e.month!=12
            and e.day==calendar.monthrange(year,e.month)[1]
            and s==date(year-1,e.month,calendar.monthrange(year-1,e.month)[1])+timedelta(days=1)
            and e<received<=cutoff and re.fullmatch('[0-9]{14}',r) is not None
            and proof['scope']==scope and proof['status']=='verified_same_receipt'
            and all(re.fullmatch('[a-f0-9]{64}',proof[k]) is not None for k in ('archive_sha256','normalized_values_sha256')))
    except (ValueError,TypeError,KeyError,OverflowError):return False

def admitted(catalog,history,book,source,*,short_periods=(),operating_source=None,reviewed_margin=None):
    if source is None:return {}
    day=catalog['date'];cutoff=date.fromisoformat(day)
    if (source.get('schema')!='v3-noncalendar-verified-metric-stage-v1'
        or source.get('source_run')!=45779276676 or source.get('source_job')!=139770506229
        or source.get('research_only') is not True or source.get('score_promoted') is not False
        or source.get('asof','9999')>day or not book or book.get('asof')!=day
        or history.get('asof')!=day):return {}
    if (source.get('completed')!=33 or len(source.get('records',[]))!=14
        or source.get('reasons')!={'verified_same_receipt':33}):raise ValueError('noncalendar_source_inventory_mismatch')
    members={r['code']:r for r in catalog['rows']}
    annual={r['code']:r.get('annual') or {} for r in book['records'] if r.get('status')=='observed'}
    records=source['records']
    followup=source.get('followup')
    if followup is not None:
        extra=followup.get('records',[])
        if (followup.get('asof')!=source['asof']
            or followup.get('source_run')!=45821555730 or followup.get('source_job')!=139914819950
            or followup.get('research_only') is not True or followup.get('score_promoted') is not False
            or followup.get('completed')!=2 or followup.get('reasons')!={'verified_same_receipt':2}
            or len(extra)!=2 or {row['code'] for row in extra}!={'021880','032800'}
            or any(row['source_years']!=[2025]
                   or set(row['metrics'])!={'sales_to_market_cap','liabilities_to_assets_pct'}
                   for row in extra)):
            raise ValueError('noncalendar_followup_inventory_mismatch')
        records=records+extra
    candidates={};seen=set()
    for row in records:
        code=row['code']
        if code in seen:raise ValueError('noncalendar_duplicate_issuer')
        seen.add(code)
        if code not in members or code in short_periods:continue
        a=annual.get(code,{})
        if a.get('currency')!='KRW' or a.get('year')!=cutoff.year-1 or a.get('fs_div') not in ('CFS','OFS'):continue
        proofs=row['receipt_proofs'];years=row['source_years'];scope=a['fs_div']
        if (years!=sorted(set(years)) or years!=[p['year'] for p in proofs]
            or not years or years[-1]!=a['year'] or row['market']!=members[code]['market']
            or not all(p['code']==code and valid_period(p,scope,cutoff) for p in proofs)
            or len({p['period_end'][5:] for p in proofs})!=1
            or proofs[-1]['receipt_id']!=a['receipt_id']):continue
        values=row['metrics']
        if not isinstance(values,dict) or not set(values)<=set(MAP) or any(type(v) not in (int,float) or not math.isfinite(v) for v in values.values()):raise ValueError('noncalendar_invalid_metric')
        out={}
        rev=a.get('values',{}).get('revenue');cap=members[code].get('reference_market_cap_eok')
        if 'sales_to_market_cap' in values and type(rev) in (int,float) and 0<rev<=1e18 and type(cap) in (int,float) and 0<cap<1e12:
            ratio=round(rev/(cap*1e8),8)
            if 0<ratio<1e6 and (source['asof']!=day or abs(ratio-values['sales_to_market_cap'])<=1e-8):out['sales_to_market_cap']=ratio
        leverage=values.get('liabilities_to_assets_pct')
        liab=a.get('values',{}).get('liabilities');eq=a.get('values',{}).get('equity')
        if leverage is not None and 0<=leverage<100 and type(liab) in (int,float) and type(eq) in (int,float) and liab>=0 and eq>0:
            # Independent accounting equation cross-check; source assets
            # were checked by the original metric stage to the same 1% tolerance.
            if abs(leverage-100*liab/(liab+eq))<=1.0:out['liabilities_to_assets_pct']=leverage
        margin=values.get('operating_margin_3y_pct')
        if margin is not None and years[-3:]==list(range(a['year']-2,a['year']+1)) and abs(margin)<1e7:
            out['operating_margin_3y_pct']=margin
        if out:candidates[code]=(out,{'receipt_id':a['receipt_id'],'scope':scope,'period_end':proofs[-1]['period_end'],'source_asof':source['asof']})
    if not candidates:return {}
    baseline=research_parts(history,catalog,book,include_independent_earnings=True)['rows']
    observed={r['code']:{k:v['observed_value'] for k,v in r['unverified_components'].items()} for r in baseline}
    margins,_=margin_observations(catalog,history,operating_source,reviewed=reviewed_margin)
    for code,v in margins.items():observed.setdefault(code,{})['operating_margin_3y_pct']=v
    for code,(values,_) in candidates.items():
        for k,v in values.items():
            if observed.setdefault(code,{}).get(k) is None:observed[code][k]=v
    result={}
    for code,(values,proof) in candidates.items():
        parts={}
        for k,v in values.items():
            area,name,base,weight,direction=MAP[k]
            peers=sorted(row[k] for peer,row in observed.items() if peer in members and members[peer]['market']==members[code]['market'] and row.get(k) is not None)
            if len(peers)>=30:
                parts.setdefault(area,{})[name]={'points':round(round(base*_midrank(v,peers,direction),6)*weight/base,6),'available':weight,'asof':day}
        if parts:result[code]=(parts,proof,values.get('liabilities_to_assets_pct'))
    return result
