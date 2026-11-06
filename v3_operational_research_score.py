"""Early operational R1a: saved evidence only, parallel to the legacy model.

Unimplemented/new observations remain unavailable. No missing-weight inflation,
provider calls, source-collection locks, or replacement of legacy score history.
"""
import argparse
import csv
from datetime import date
from hashlib import sha256
from io import StringIO
import json
import math
from pathlib import Path

from v3_actual_money_contract import usable_records
from v3_volume_participation import observed as volume_observed
from v3_pattern_composite_score import _observed as pattern_observed, _weekly_trend
from v3_path_provisional_factors import preview as path_preview
from v3_eps_valuation import valid as valid_eps
from v3_personal_score_adjustments import profile, rounded, build as legacy_build
from v3_ma_slope_transition import valid_observation as valid_ma_slope
from v3_actual_money_structure import valid as valid_money_structure
from v3_breakout_followthrough import valid as valid_breakout_followthrough
from v3_dart_reported_eps import plan as reported_eps_plan, EPSError
from v3_dart_bulk_history import _stable_digest
from v3_revenue_growth_evidence import derive as revenue_growth_evidence
from v3_eps_valuation import evaluate as evaluate_reported_eps

VERSION = 'v3-stable-r1d-operational-research'
COMPATIBLE_PREVIOUS = ('v3-stable-r1a-operational-research',
                       'v3-stable-r1b-operational-research',
                       'v3-stable-r1c-operational-research',VERSION)
OUTPUT = 'v3-operational-research-scores.json'
SPECS = {
 'volume': {'long_horizon_growth':6,'persistence':5,'concentration_quality':2,
   'up_down_asymmetry':2,'post_spike_absorption':3,'correction_contraction':1,
   'weekly_structure_increment':2,'obv_price_divergence':4},
 'technical': {'daily_pattern':8.4,'weekly_pattern':9.6,'ma20_ma60_cross_near_ma240':6,
   'daily_rsi':1.2,'weekly_rsi':1.8,'long_ma_position':1.8,'independent_breakout_confirmation':1.2},
 'financial': {'revenue_growth':3.5,'operating_growth_margin':3,'cashflow_fcf_persistence':3,'balance_sheet_strength':3},
 'valuation': {'per_eps_quality_growth':5,'pbr_equity_quality':2.5,'fcf_yield':2.5,'sales_to_cap':2.5},
 'momentum': {'existing_momentum':15,'daily_ma_slope_turn':2,'weekly_ma_slope_turn':3}}
BUDGET = {k:round(sum(v.values()),6) for k,v in SPECS.items()}
assert BUDGET == {'volume':25,'technical':30,'financial':12.5,'valuation':12.5,'momentum':20}


def finite(x):
    return type(x) in (int,float) and math.isfinite(x)


def smooth(x):
    u=max(0,min(1,x))
    return u*u*(3-2*u)


def fiscal(record):
    years=record.get('years',{})
    keys=sorted(y for y in years if y.isdigit())[-5:]
    scope=next((s for s in ('CFS','OFS') if len(keys)>=3 and all(
        isinstance(years[y].get(s,{}).get('values'),dict) for y in keys)),None)
    return [years[y][scope]['values'] for y in keys] if scope else []


def risk_assessment(record, financial_sector=False):
    annual=fiscal(record); severity=[]; warnings=[]
    if annual:
        last=annual[-1]; assets=last.get('assets'); equity=last.get('equity')
        if finite(assets) and assets>0 and finite(equity):
            # Financial firms normally operate with lower equity/assets.
            # Unknown industry also cannot justify this generic threshold.
            if equity<=0 or financial_sector is False:
                severity.append(smooth((.22-equity/assets)/.22))
            if equity<=0:warnings.append('자기자본 0 이하 · 고위험')
        losses=[]; streak=0
        for v in annual:
            op,rev=v.get('operating_income'),v.get('revenue')
            if not finite(op) or not finite(rev) or rev<=0:break
            losses.append(max(0,-op/rev))
        if len(losses)==len(annual):
            for v in reversed(annual):
                if v['operating_income']>=0:break
                streak+=1
            severity.append(sum(v['operating_income']<0 for v in annual)/len(annual)
                *smooth(streak/3)*smooth(sum(losses)/len(losses)/.2))
            if streak>=3:warnings.append('3개년 이상 연속 영업적자')
    # Debt maturity/interest service, capital impairment and repeated CFO are
    # not fully authenticated by this saved annual subset. Never label safe.
    return {'deduction':round(5*max(severity,default=0),4),
        'status':'partial' if severity else 'unknown','warnings':warnings,
        'unverified':['자본금 대비 잠식률','업종별 부채·상환능력','반복 현금흐름 악화']}


def score_issuer(stock, saved, *, day, money=None, eps=None, reported_eps=None,
                 annual=None, financial_sector=None):
    parts={a:{k:None for k in ks} for a,ks in SPECS.items()}
    def put(area,key,points,available,asof):
        if (finite(points) and finite(available) and 0<available<=SPECS[area][key]+1e-6
            and 0<=points<=available and isinstance(asof,str) and asof<=day
            and date.fromisoformat(asof).isoformat()==asof):
            parts[area][key]={'points':round(points,6),'available':round(available,6),'asof':asof}
    fresh=stock and stock.get('price_date')==day
    if fresh:
        v=volume_observed(stock,asof=day,market=stock['market'],money=money)
        if v:
            vp=v['subpoints'];vd=money['asof'] if money else day
            structure=money.get('r1c_structure') if money else None
            if structure is not None and valid_money_structure(structure):
                put('volume','long_horizon_growth',structure['growth6'],6,vd)
                for key,score in structure['components'].items():
                    if score is not None:
                        put('volume',key,score,SPECS['volume'][key],vd)
            else:
                put('volume','long_horizon_growth',vp['sustained_volume'],6,vd)
            put('volume','obv_price_divergence',vp['obv_price_not_reflected'],4,day)
            cross=vp['ma20_ma60_cross_near_ma240']
            if cross is not None:put('technical','ma20_ma60_cross_near_ma240',cross,6,day)
        p=path_preview(stock,asof=day)
        if p:put('volume','post_spike_absorption',p['research_volume_post_spike_absorption_points']*.75,3,day)
        slope=stock.get('v3_ma_slope_transition')
        if slope is not None and valid_ma_slope(slope,day):
            for frame in ('daily','weekly'):
                pieces=slope['parts'][frame]
                if pieces is not None:
                    weight=SPECS['momentum'][frame+'_ma_slope_turn']
                    put('momentum',frame+'_ma_slope_turn',sum(pieces.values()),weight,
                        slope['weekly_asof'] if frame=='weekly' else day)
        breakout=stock.get('v3_breakout_followthrough')
        if breakout is not None and valid_breakout_followthrough(breakout,day):
            put('technical','independent_breakout_confirmation',breakout['points'],1.2,day)
        for frame,limit,rsi_max in [('daily',8.4,1.2),('weekly',9.6,1.8)]:
            evidence=pattern_observed(stock,frame)
            if not evidence:continue
            candidates=[1 if q['stage']=='breakout_observed' else .7 for q in evidence['patterns']]
            geometry=evidence['pattern_geometry']
            if any(geometry.get(k) for k in ('falling_wedge_candidate',
                    'inverse_head_shoulders_breakout_candidate','post_rise_consolidation_candidate')):
                candidates.append(.7)
            # No bearish/unknown-trend triangle preference. This is a dated
            # price-trend proxy, not a calibrated pattern win probability.
            if (geometry.get('triangle_candidate') and finite(stock.get('price60'))
                and finite(stock.get('price120')) and stock['price60']>0 and stock['price120']>0):
                candidates.append(.7)
            put('technical',frame+'_pattern',max(candidates,default=0)*limit,limit,evidence['asof'])
            put('technical',frame+'_rsi',rsi_max if evidence['rsi30_cross_recent'] or
                evidence['rsi_bullish_divergence_recent'] else 0,rsi_max,evidence['asof'])
            if frame=='weekly':
                ma=_weekly_trend(evidence)
                if ma is not None:put('technical','long_ma_position',ma*.6,1.8,evidence['asof'])
    components=saved.get('components',{})
    for new,old,maximum in [('revenue_growth','revenue_cagr_3y_pct',3.5),
            ('operating_growth_margin','operating_margin_3y_pct',3),
            ('cashflow_fcf_persistence','fcf_assets_3y',3),('balance_sheet_strength','liabilities_to_assets_pct',3)]:
        c=components.get('financial',{}).get(old)
        if c and c.get('available')==4:put('financial',new,c['points']*maximum/4,maximum,c['asof'])
    for new,old,maximum in [('pbr_equity_quality','pbr',2.5),('fcf_yield','fcf_yield',2.5),('sales_to_cap','sales_to_market_cap',2.5)]:
        c=components.get('valuation',{}).get(old)
        if c and c.get('available')==5:put('valuation',new,c['points']*maximum/5,maximum,c['asof'])
    c=components.get('valuation',{}).get('earnings_yield_pct')
    if c and c.get('available')==5:
        put('valuation','per_eps_quality_growth',c['points']/3,5/3,c['asof'])
    # The official reported three-year EPS series is independent of FCF/PBR.
    # The personal 20pt bundle requires *all four* valuation baselines; that
    # must not suppress 2/3 of the R1d PER/EPS subfactor for an otherwise
    # verified EPS receipt. Missing earnings yield remains unavailable weight.
    if reported_eps is not None:
        try:
            series=reported_eps['reported_basic_eps_2023_2025']
            eps_parts,_=evaluate_reported_eps(series,{k:0 for k in
                ('pbr','earnings_yield_pct','fcf_yield','sales_to_market_cap')})
            earned=(eps_parts['eps_growth']+eps_parts['eps_stability'])*5/12
            available=10/3
            oldest=reported_eps['observed_on']
            baseline=parts['valuation']['per_eps_quality_growth']
            if baseline is not None:
                earned+=baseline['points']
                available+=baseline['available']
                oldest=min(oldest,baseline['asof'])
            put('valuation','per_eps_quality_growth',earned,available,oldest)
        except (ValueError,KeyError,TypeError,OverflowError):
            pass
    if eps and valid_eps(eps,code=saved['code'],name=saved['name'],market=saved['market'],day=day):
        q=eps['parts'];ed=eps['source_oldest_asof']
        put('valuation','per_eps_quality_growth',sum(q[k] for k in ('earnings_yield_pct','eps_growth','eps_stability'))*5/12,5,ed)
        for new,old,m in [('pbr_equity_quality','pbr',4),('fcf_yield','fcf_yield',2),('sales_to_cap','sales_to_market_cap',2)]:
            put('valuation',new,q[old]*2.5/m,2.5,ed)
    mom=saved.get('factors',{}).get('momentum')
    if mom:put('momentum','existing_momentum',mom['points'],mom['available'],mom['asof'])
    return parts,risk_assessment(annual or {},financial_sector=financial_sector)


def verified_reported_eps(catalog, annual_book, source):
    """Reuse only issuer/receipt-checked annual EPS; no provider or price calls."""
    if (not isinstance(annual_book,dict) or not isinstance(source,dict)
        or not isinstance(source.get('source_sha256'),dict)
        or source['source_sha256'].get('annual_book')!=_stable_digest(annual_book)):
        return {}
    try:
        _,_,_,retained,_=reported_eps_plan(catalog,annual_book,source)
    except (EPSError,ValueError,KeyError,TypeError):
        return {}
    return {code:entry for code,entry in retained.items()
            if entry.get('status')=='reported_basic_eps'
            and entry.get('metrics',{}).get('complete_three_years') is True}


def build(market,catalog,ledger,history,money_source,personal,industry=None,
          annual_book=None,reported_eps_source=None,previous=None,
          revenue_2022_source=None,revenue_2023_comparative=None):
    day=catalog['date']
    if (market['date']!=day or ledger['market_asof']!=day or ledger.get('missing_is_not_zero') is not True
        or ledger.get('investment_score_certified') is not False or history.get('asof',day)>day):raise ValueError('unaligned_operational_sources')
    stocks={r['code']:r for r in market['stocks']}
    money=usable_records(money_source,catalog)
    raw_eps=verified_reported_eps(catalog,annual_book,reported_eps_source)
    revenue_parts,revenue_summary=revenue_growth_evidence(
        history,catalog,revenue_2022_source,revenue_2023_comparative)
    old,_=legacy_build(market,catalog,ledger,valuation_overrides=personal.get('valuation_overrides'))
    legacy={r['code']:r for r in old};rows={}
    previous=previous if previous and previous.get('version') in COMPATIBLE_PREVIOUS and previous.get('asof','9999')<=day else {}
    prior_version=(previous.get('prior_operational_version') if previous.get('version')==VERSION
                   else previous.get('version'))
    if previous.get('format')=='compact-parts-v1':
        previous=json.loads(json.dumps(previous))
        for r in previous.get('rows',{}).values():
            r['parts']={a:{k:{'points':p[0],'available':p[1],'asof':p[2]} for k,p in ps.items()} for a,ps in r['parts'].items()}
    for member in catalog['rows']:
        code=member['code'];saved=ledger['rows'].get(code);stock=stocks.get(code)
        if not saved or any(saved.get(k)!=member.get(k) for k in ('code','name','market')):
            raise ValueError('operational_issuer_identity_mismatch')
        sector=(industry or {}).get('records',{}).get(code,{})
        sector_code=sector.get('industry_code')
        sector_valid=(sector.get('status')=='observed' and sector.get('origin')=='OpenDART_company_json'
            and sector.get('market')==member['market'] and sector.get('checked_on','9999')<=day
            and isinstance(sector_code,str) and sector_code.isdigit() and len(sector_code)>=2)
        financial_sector=sector_code[:2] in ('64','65','66') if sector_valid else None
        parts,risk=score_issuer(stock,saved,day=day,money=money.get(code),financial_sector=financial_sector,
            eps=personal.get('valuation_overrides',{}).get(code),reported_eps=raw_eps.get(code),
            annual=history.get('records',{}).get(code))
        if code in revenue_parts:
            parts['financial']['revenue_growth']=revenue_parts[code]
        prior=previous.get('rows',{}).get(code)
        if prior and prior['name']==member['name'] and prior['market']==member['market']:
            for area in parts:
                for key,p in parts[area].items():
                    older=prior.get('parts',{}).get(area,{}).get(key)
                    if (older and finite(older.get('points')) and finite(older.get('available'))
                        and 0<=older['points']<=older['available']<=SPECS[area][key]+1e-6
                        and older['asof']<=day and (p is None or older['asof']>p['asof'])
                        # A recent 1/3 earnings-yield receipt must not hide
                        # older but separately verified 2/3 reported EPS.
                        and (p is None or key!='per_eps_quality_growth'
                             or older['available']+1e-6>=p['available'])):
                        parts[area][key]=older
        if prior and risk['status']=='unknown' and prior.get('risk_status')=='partial':
            risk['deduction']=prior['risk_deduction'];risk['status']='partial'
            risk['warnings']=prior.get('risk_warnings',[])
        areas={a:{'points':round(sum(p['points'] for p in ps.values() if p),6),
                  'available':round(sum(p['available'] for p in ps.values() if p),6)} for a,ps in parts.items()}
        available=round(sum(v['available'] for v in areas.values()),6)
        base=round(sum(v['points'] for v in areas.values()),6) if available else None
        adj=profile(stock,base)
        score=rounded(max(0,min(100,base-adj['capital_deduction']+adj['stop_adjustment']-risk['deduction']))) if base is not None else None
        previous_score=(prior.get('prior_operational_score') if previous.get('version')==VERSION
                        else prior.get('score')) if prior else None
        previous_rank=(prior.get('prior_operational_rank') if previous.get('version')==VERSION
                       else prior.get('rank')) if prior else None
        rows[code]={'name':member['name'],'market':member['market'],'score':score,'base':base,
            'available':available,'capital_deduction':adj['capital_deduction'],'stop_adjustment':adj['stop_adjustment'],
            'missing_adjustment_sources':adj['missing_adjustment_sources'],
            'risk_asof':history.get('asof') if history.get('records',{}).get(code) else (prior or {}).get('risk_asof'),
            'risk_deduction':risk['deduction'],'risk_status':risk['status'],'risk_warnings':risk['warnings'],
            'asof':max((p['asof'] for ps in parts.values() for p in ps.values() if p),default=day),
            'oldest_asof':min((p['asof'] for ps in parts.values() for p in ps.values() if p),default=None),
            'legacy_score':legacy[code]['adjusted_score'],'legacy_rank':legacy[code]['personal_rank'],
            'prior_operational_score':previous_score,'prior_operational_rank':previous_rank,
            'parts':parts,'areas':areas,'rank':None}
    eligible=sorted((k for k,r in rows.items() if r['score'] is not None),
        key=lambda k:(-rows[k]['score'],-rows[k]['available'],k))
    for rank,k in enumerate(eligible,1):rows[k]['rank']=rank
    return {'version':VERSION,'asof':day,'budgets':BUDGET,'status':'provisional_operational',
        'prior_operational_version':prior_version,
        'investment_score_certified':False,'performance_validated':False,'provider_calls':0,
        'missing_is_not_zero':True,'unobserved_weight_not_normalized':True,'listed_total':len(rows),
        'risk_scope':'observed_lower_bound_not_complete_solvency_review',
        'revenue_growth_source_summary':revenue_summary,
        'pending':['거래 세부구조: 기간 미충족 또는 부호 근거 부족 시 종목별 미관측',
                   '독립 돌파확인: 가격 경로 부족 종목 미관측',
                   'MA 전환: 가격 이력 부족 종목 미관측',
                   '공식 보고 EPS: 주식수 변동·기업행위 독립 보정은 미검증',
                   '부채상환·현금흐름 위험 검증'],
        'rows':rows}


def can_hold_previous_during_temporal_lag(market,catalog,ledger,history,previous):
    """Keep an already published dated ranking while upstream files catch up.

    This must never authorize mixing different trading days in a new score,
    or bypass tampered/malformed source flags. No report is rewritten on hold.
    """
    if (ledger.get('missing_is_not_zero') is not True or
        ledger.get('investment_score_certified') is not False):
        return False
    day=catalog['date']
    mismatch=(market['date']!=day or ledger['market_asof']!=day or
              history.get('asof',day)>day)
    return (mismatch and isinstance(previous,dict) and
        previous.get('version') in COMPATIBLE_PREVIOUS and
        previous.get('format')=='compact-parts-v1' and
        previous.get('investment_score_certified') is False and
        previous.get('missing_is_not_zero') is True and
        previous.get('status')=='provisional_operational' and
        isinstance(previous.get('rows'),dict) and
        len(previous['rows'])==previous.get('listed_total') and
        previous.get('asof','9999')<=max(day,market['date']))


def to_csv(report):
    out=StringIO(newline='');w=csv.writer(out,lineterminator='\n')
    w.writerow(['운영순위','종목코드','종목명','시장','모델버전','잠정운영점수','관측합계','확보배점','기준일','가장오래된기준일',
        '시총감점','손절보정','재무위험감점','재무위험상태','기존점수','기존순위','직전운영모델점수','직전운영모델순위']+
        [a+'_'+k for a in BUDGET for k in ('점수','확보배점')])
    for code,r in sorted(report['rows'].items(),key=lambda x:x[1]['rank'] or 100000):
        w.writerow([r['rank'],code,r['name'],r['market'],VERSION,r['score'],r['base'],r['available'],r['asof'],r['oldest_asof'],
            r['capital_deduction'],r['stop_adjustment'],r['risk_deduction'],r['risk_status'],r['legacy_score'],r['legacy_rank'],
            r['prior_operational_score'],r['prior_operational_rank']]+
            [r['areas'][a][k] for a in BUDGET for k in ('points','available')])
    return out.getvalue()


def coverage_summary(report, source_digest):
    """Small public R1d observation meter; not a performance or source audit."""
    rows=list(report['rows'].values())
    return {'status':'provisional_operational',
        'source_digest':source_digest,'listed_total':report['listed_total'],
        'ranked_count':sum(r['rank'] is not None for r in rows),
        'available_100_count':sum(r['available']>=100 for r in rows),
        'available_95_count':sum(r['available']>=95 for r in rows),
        'available_90_count':sum(r['available']>=90 for r in rows),
        'budgets':BUDGET,'provider_calls':0,
        'missing_is_not_zero':True,'investment_score_certified':False,
        'performance_validated':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--docs',type=Path,default=Path('docs'));args=parser.parse_args()
    root=args.docs
    def read(name):return json.loads((root/name).read_text())
    names=['data.json','v22-market-catalog.json','v3-issuer-last-observed-factors.json','v3-dart-bulk-history.json',
        'v3-actual-money-participation.json','v3-personal-adjusted-score-summary.json','v3-dart-industry-classification.json',
        'v3-dart-book-reference.json','v3-dart-reported-eps.json']
    inputs=[read(n) for n in names]
    revenue_2022_file='v3-dart-revenue-2022-supplement.json'
    revenue_2022_source=read(revenue_2022_file) if (root/revenue_2022_file).exists() else None
    comparative_file='v3-dart-revenue-2023-comparative.json'
    comparative_source=read(comparative_file) if (root/comparative_file).exists() else None
    previous=read(OUTPUT) if (root/OUTPUT).exists() else None
    if can_hold_previous_during_temporal_lag(inputs[0],inputs[1],inputs[2],inputs[3],previous):
        print(json.dumps({'status':'waiting_for_aligned_daily_sources',
            'previous_operational_score_preserved':True,
            'previous_score_asof':previous['asof'],'provider_calls':0}))
        return
    report=build(*inputs,previous=previous,revenue_2022_source=revenue_2022_source,
                 revenue_2023_comparative=comparative_source)
    report['source_hashes']={n:sha256((root/n).read_bytes()).hexdigest() for n in names}
    if revenue_2022_source is not None:
        report['source_hashes'][revenue_2022_file]=sha256((root/revenue_2022_file).read_bytes()).hexdigest()
    if comparative_source is not None:
        report['source_hashes'][comparative_file]=sha256((root/comparative_file).read_bytes()).hexdigest()
    report['implementation_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
    report['implementation_dependency_sha256']={name:sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('v3_revenue_growth_evidence.py','v3_dart_bulk_history.py',
                     'v3_dart_revenue_2022.py','v3_dart_revenue_2023_comparative.py')}
    digest=sha256(json.dumps([VERSION,report['implementation_sha256'],report['source_hashes'],
        report['implementation_dependency_sha256']],sort_keys=True).encode()).hexdigest()
    report['source_digest']=digest
    compact=json.loads(json.dumps(report));compact['format']='compact-parts-v1'
    for r in compact['rows'].values():
        r['parts']={a:{k:[p['points'],p['available'],p['asof']] for k,p in ps.items() if p} for a,ps in r['parts'].items()}
    payload=json.dumps(compact,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n'
    (root/OUTPUT).write_text(payload)
    (root/'v3-operational-research-scores.csv').write_text(to_csv(report),encoding='utf-8-sig')
    common=[(k,r) for k,r in report['rows'].items() if r['rank'] is not None and r['legacy_rank'] is not None]
    new=sorted(common,key=lambda x:x[1]['rank']);old=sorted(common,key=lambda x:x[1]['legacy_rank'])
    comparison={'asof':report['asof'],'version':VERSION,'same_input_common_issuers':len(common),
        'performance_validated':False,'top':{str(n):{'overlap':len({k for k,_ in new[:n]} & {k for k,_ in old[:n]}),
        'new_kospi':sum(r['market']=='KOSPI' for _,r in new[:n]),'old_kospi':sum(r['market']=='KOSPI' for _,r in old[:n])}
        for n in (50,100,200)},
        'operational_coverage':coverage_summary(report,digest)}
    prior=[(k,r) for k,r in report['rows'].items() if r['rank'] and r['prior_operational_rank']]
    if prior:
        comparison['prior_operational_version']=report['prior_operational_version']
        comparison['same_input_prior_ranked_issuers']=len(prior)
        comparison['prior_top_overlap']={str(n):len(
            {k for k,_ in sorted(prior,key=lambda x:x[1]['rank'])[:n]} &
            {k for k,_ in sorted(prior,key=lambda x:x[1]['prior_operational_rank'])[:n]})
            for n in (50,100,200)}
    (root/'v3-operational-research-comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
    archive=root/'score-history'/VERSION;archive.mkdir(parents=True,exist_ok=True)
    snapshot={'version':VERSION,'asof':report['asof'],'source_digest':digest,'rows':{
        k:[r['name'],r['market'],r['score'],r['available'],r['legacy_score'],r['risk_deduction']] for k,r in report['rows'].items()}}
    (archive/(report['asof']+'-'+digest[:12]+'.json')).write_text(json.dumps(snapshot,ensure_ascii=False,separators=(',',':'))+'\n')
    print(json.dumps({'version':VERSION,'listed':report['listed_total'],'ranked':sum(r['rank'] is not None for r in report['rows'].values()),'provider_calls':0}))


if __name__=='__main__':main()
