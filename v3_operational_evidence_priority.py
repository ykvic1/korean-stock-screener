"""R1d availability work queue; never adds points or calls providers."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from v3_v30b_release_gate import r1d_public_reconciles, R1D_PART_LIMITS

OUTPUT='v3-operational-evidence-priority.json'

# These are WORKSTREAM labels, not interchangeable valuation inputs.  The
# counterfactual counts issuers whose *entire* missing R1d weight lies inside
# one workstream. It is an upper bound on possible 100-weight observations if
# every required, independently checked fact were subsequently recovered.
# No unobserved amount or nominal score is inferred by this grouping.
SOURCE_FAMILIES={
    'dart_cashflow_capex': frozenset((
        ('financial','cashflow_fcf_persistence'),('valuation','fcf_yield'))),
    'dart_annual_fundamentals': frozenset((
        ('financial','revenue_growth'),('financial','operating_growth_margin'),
        ('financial','balance_sheet_strength'),('valuation','pbr_equity_quality'),
        ('valuation','sales_to_cap'))),
    'reported_eps_earnings_yield': frozenset((
        ('valuation','per_eps_quality_growth'),)),
    'exchange_actual_money_windows': frozenset((
        ('volume','long_horizon_growth'),('volume','persistence'),
        ('volume','concentration_quality'),('volume','up_down_asymmetry'),
        ('volume','correction_contraction'),('volume','weekly_structure_increment'))),
    'price_chart_windows': frozenset((
        ('volume','obv_price_divergence'),('volume','post_spike_absorption'),
        ('technical','daily_pattern'),('technical','weekly_pattern'),
        ('technical','ma20_ma60_cross_near_ma240'),('technical','daily_rsi'),
        ('technical','weekly_rsi'),('technical','long_ma_position'),
        ('technical','independent_breakout_confirmation'),
        ('momentum','existing_momentum'),('momentum','daily_ma_slope_turn'),
        ('momentum','weekly_ma_slope_turn'))),
}

OBSERVATION_BANDS=(('100',100,101),('95_to_100',95,100),
    ('90_to_95',90,95),('80_to_90',80,90),('70_to_80',70,80),
    ('60_to_70',60,70),('below_60',0,60))


def coverage_90_first(queue, *, listed_total, full_100_count):
    """Prioritize real missing inputs, never claim conditional points as found.

    Recovery of *every* absent component in a family may be enough to cross
    90. This is only a mathematical upper bound: provider availability,
    accounting scope and source dates must still be independently validated.
    Family candidate lists are work-order hints, not observations or scores.
    """
    bands={name:0 for name,_,_ in OBSERVATION_BANDS}
    bands['100']=full_100_count
    by_family={name:[] for name in SOURCE_FAMILIES}
    below=[];one_family_possible=set()
    for row in queue:
        available=row['available']
        for name,low,high in OBSERVATION_BANDS[1:]:
            if low-1e-6<=available<high-1e-6:
                bands[name]+=1
                break
        if available>=90-1e-6:continue
        below.append(row['code'])
        for family,components in SOURCE_FAMILIES.items():
            potential=sum(g['missing_weight'] for g in row['gaps']
                          if (g['area'],g['component']) in components)
            if available+potential>=90-1e-6:
                by_family[family].append((row['code'],available,
                    len(row['gaps']),round(90-available,6)))
                one_family_possible.add(row['code'])
    if sum(bands.values())!=listed_total or len(below)!=sum(
        bands[name] for name in ('80_to_90','70_to_80','60_to_70','below_60')):
        raise ValueError('operational_90_band_reconciliation_failed')
    # Existing source family collections intentionally overlap. Never sum
    # their upper bounds as though they represented unique restored issuers.
    ordered={family:[code for code,_,_,_ in sorted(entries,
        key=lambda x:(x[3],x[2],x[0]))] for family,entries in by_family.items()}
    return {'threshold':90,'available_weight_bands':bands,
        'already_90_or_more':listed_total-len(below),
        'below_90_issuers':len(below),
        'one_source_family_upper_bound_count':len(one_family_possible),
        'multiple_source_families_needed_at_minimum':len(below)-len(one_family_possible),
        'family_upper_bound_counts':{key:len(value) for key,value in ordered.items()},
        'family_conditional_priority_codes':ordered,
        'independent_source_recoverability_not_proven':True,
        'recovery_not_a_score_or_observation':True,
        'missing_data_never_zero_filled':True}


def source_family_completion_upper_bounds(queue):
    """Exact missing-factor set containment; never simulate missing points."""
    counts={name:0 for name in SOURCE_FAMILIES}
    for row in queue:
        missing={(gap['area'],gap['component']) for gap in row['gaps']}
        if not missing:
            raise ValueError('empty_source_recovery_gap')
        for name,components in SOURCE_FAMILIES.items():
            if missing<=components:
                counts[name]+=1
    return {
        'conditional_only_not_provider_recoverability':True,
        'full_100_if_all_missing_family_facts_became_observed':counts,
        'maximum_missing_issuers_in_one_family':max(counts.values(),default=0),
        'no_issuer_added_to_full_100_observed':True,
        'no_missing_factor_assumed_zero_or_observed':True,
        'source_eligibility_not_inferred_from_gap_shape':True,
    }


def category(area,key,status,queried_capex):
    if key in ('cashflow_fcf_persistence','fcf_yield'):
        return ('original_filing_review_no_blind_refetch' if queried_capex
                else 'cashflow_period_scope_capex')
    if area=='financial':return 'annual_financial_period_scope'
    if area=='valuation':return 'reported_eps_or_valuation_source'
    if status=='insufficient_history':return 'catalog_confirmed_insufficient_history'
    if status=='price_source_or_validation_error':return 'price_source_validation'
    if area=='volume' and key not in ('obv_price_divergence','post_spike_absorption'):
        return 'money_window_or_directional_evidence'
    return 'chart_window_or_adjustment_review'


def build(report,comparison,catalog,legacy_priority=None):
    audited=r1d_public_reconciles(report,comparison,catalog)
    if audited is None:raise ValueError('operational_priority_inputs_not_reconciled')
    members={r['code']:r for r in catalog['rows']}
    queried=set()
    if (isinstance(legacy_priority,dict) and legacy_priority.get('asof')==report['asof']
        and legacy_priority.get('missing_capex_is_not_zero') is True):
        raw=legacy_priority.get('fcf_three_year_previously_queried_missing_account_codes',[])
        if type(raw) is list and all(type(c) is str and c in members for c in raw):queried=set(raw)
    counts=Counter();reasons=Counter();shapes=Counter();queue=[]
    for code,row in report['rows'].items():
        gaps=[]
        for area,limits in R1D_PART_LIMITS.items():
            for key,maximum in limits.items():
                part=row['parts'][area].get(key)
                missing=round(maximum-(part[1] if part else 0),6)
                if missing<=.00001:continue
                reason=category(area,key,members[code].get('price_history_status'),code in queried)
                gaps.append({'area':area,'component':key,'missing_weight':missing,'reason':reason})
                counts[area+':'+key]+=1;reasons[reason]+=1
        if not gaps:continue
        missing=round(sum(x['missing_weight'] for x in gaps),6)
        if abs(missing-(100-row['available']))>.00002:
            raise ValueError('operational_priority_gap_weight_mismatch')
        shape='+'.join(sorted(x['area']+':'+x['component'] for x in gaps));shapes[shape]+=1
        queue.append({'code':code,'name':row['name'],'market':row['market'],
            'available':row['available'],'missing_weight':missing,'missing_components':len(gaps),
            'operational_rank':row['rank'],'gaps':gaps})
    queue.sort(key=lambda r:(r['missing_components'],r['missing_weight'],r['operational_rank'] or 100000,r['code']))
    shape_names=sorted(shapes)
    shape_index={shape:i for i,shape in enumerate(shape_names)}
    multiple=[]
    for row in queue:
        if row['missing_components']==1:continue
        shape='+'.join(sorted(x['area']+':'+x['component'] for x in row['gaps']))
        multiple.append([row['code'],row['available'],row['missing_weight'],
                         shape_index[shape],members[row['code']].get('price_history_status')])
    return {'schema':'v3-r1d-observation-work-priority-v1','version':report['version'],
        'asof':report['asof'],'operational_source_digest':report['source_digest'],
        'listed_total':audited['listed_total'],'available_100_count':audited['available_100_count'],
        'incomplete_count':len(queue),'one_missing_component_count':sum(r['missing_components']==1 for r in queue),
        'component_missing_issuer_counts':dict(sorted(counts.items())),
        'reason_component_counts':dict(sorted(reasons.items())),
        'gap_shapes':dict(sorted(shapes.items(),key=lambda x:(-x[1],x[0]))),
        'single_component_queue':[r for r in queue if r['missing_components']==1],
        'coverage_90_first':coverage_90_first(queue,listed_total=audited['listed_total'],
                                               full_100_count=audited['available_100_count']),
        'source_family_completion_upper_bounds':source_family_completion_upper_bounds(queue),
        'multiple_missing_index_columns':['code','available','missing_weight','shape_index','catalog_price_status'],
        'multiple_missing_index':multiple,'shape_names':shape_names,
        'source_status_issuer_counts':dict(Counter(members[r['code']].get('price_history_status','unknown') for r in queue)),
        'provider_calls':0,'points_added':0,'investment_score_certified':False,
        'missing_is_not_zero':True,'incomplete_windows_do_not_prove_recent_listing':True,
        'priority_is_source_work_not_investment_rank':True}


def write_priority(root):
    root=Path(root)
    def read(n):return json.loads((root/n).read_text(encoding='utf8'))
    old=root/'v3-near-100-evidence-priority.json'
    result=build(read('v3-operational-research-scores.json'),
        read('v3-operational-research-comparison.json'),read('v22-market-catalog.json'),
        read(old.name) if old.is_file() else None)
    raw=(root/'v3-operational-research-scores.json').read_bytes()
    result['source_report_sha256']=sha256(raw).hexdigest()
    target=root/OUTPUT
    tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf8')
    tmp.replace(target)
    return result


if __name__=='__main__':
    result=write_priority(Path(__file__).resolve().parent/'docs')
    summary={k:result[k] for k in ('asof','available_100_count','incomplete_count',
        'one_missing_component_count','provider_calls','points_added')}
    first=result['coverage_90_first']
    summary.update({'available_90_count':first['already_90_or_more'],
        'below_90_count':first['below_90_issuers'],
        'coverage_bands':first['available_weight_bands'],
        'conditional_90_family_upper_bounds':first['family_upper_bound_counts']})
    print(json.dumps(summary,sort_keys=True))
