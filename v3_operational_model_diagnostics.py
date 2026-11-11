"""Saved-source rank/composition diagnostics, never realized performance."""
import argparse
from collections import Counter
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path
import statistics

from v3_personal_score_adjustments import rounded
from v3_v30b_release_gate import r1d_public_reconciles, R1D_PART_LIMITS

OUTPUT='v3-operational-model-diagnostics.json'
BANDS=('under_1000','1000_to_5000','5000_to_50000','50000_plus','unknown')


def size_band(cap):
    if type(cap) not in (int,float) or not math.isfinite(cap) or cap<=0:return 'unknown'
    return BANDS[0] if cap<1000 else BANDS[1] if cap<5000 else BANDS[2] if cap<50000 else BANDS[3]


def composition(codes,rows,caps):
    amounts=[caps[c] for c in codes if size_band(caps.get(c))!='unknown']
    counts=Counter(size_band(caps.get(c)) for c in codes)
    return {'issuer_count':len(codes),'markets':dict(Counter(rows[c]['market'] for c in codes)),
        'capitalization_issuer_counts':{k:counts[k] for k in BANDS},
        'capitalization_unit':'KRW_100_million','capitalization_observed_count':len(amounts),
        'median_capitalization_eok':round(statistics.median(amounts),4) if amounts else None,
        'operational_mean_available_weight':round(statistics.mean(rows[c]['available'] for c in codes),4) if codes else None,
        'operational_risk_unconfirmed_count':sum(rows[c]['risk_status']=='unknown' for c in codes),
        'operational_positive_risk_deduction_count':sum(rows[c]['risk_deduction']>0 for c in codes)}


def contribution(codes,rows,area,key):
    values=[rows[c]['parts'][area][key][0] for c in codes if key in rows[c]['parts'][area]]
    return {'maximum':R1D_PART_LIMITS[area][key],'observed_count':len(values),
        'missing_count':len(codes)-len(values),'positive_count':sum(v>0 for v in values),
        'mean_observed_points':round(statistics.mean(values),6) if values else None}


def correlation_pairs(rows):
    # Restrict to fully observed component weight to avoid replacing missing
    # values by zero, or correlating differently selected pairwise cohorts.
    complete=[r for r in rows.values() if r['available']==100]
    keys=[(a,k) for a,parts in R1D_PART_LIMITS.items() for k in parts]
    pairs=[];unavailable=0
    for first,second in itertools.combinations(keys,2):
        a=[r['parts'][first[0]][first[1]][0] for r in complete]
        b=[r['parts'][second[0]][second[1]][0] for r in complete]
        if len(complete)<30 or len(set(a))<2 or len(set(b))<2:
            unavailable+=1
            continue
        value=statistics.correlation(a,b)
        pairs.append({'first':':'.join(first),'second':':'.join(second),
                      'pearson':round(value,6),'issuer_count':len(complete)})
    pairs.sort(key=lambda r:(-abs(r['pearson']),r['first'],r['second']))
    return {'cohort':'full_100_weight_observed_only','issuer_count':len(complete),
        'minimum_issuers':30,'tested_pairs':len(pairs),'unavailable_pairs':unavailable,
        'absolute_review_threshold':.85,'high_correlation_pairs':[r for r in pairs if abs(r['pearson'])>=.85],
        'largest_absolute_correlations':pairs[:10],
        'low_correlation_does_not_prove_no_double_count':True,
        'cross_sectional_correlation_is_not_predictive_validation':True}


def build(report,comparison,catalog):
    audit=r1d_public_reconciles(report,comparison,catalog)
    if audit is None:raise ValueError('unreconciled_model_diagnostic_inputs')
    if catalog.get('date')!=report['asof']:
        raise ValueError('diagnostic_capitalization_date_mismatch')
    rows=report['rows'];caps={r['code']:r.get('reference_market_cap_eok') for r in catalog['rows']}
    common=[c for c,r in rows.items() if r['rank'] is not None and r['legacy_rank'] is not None]
    ranks=[rows[c]['legacy_rank'] for c in common]
    if (any(type(x) is not int or not 1<=x<=report['listed_total'] for x in ranks)
        or len(ranks)!=len(set(ranks))):raise ValueError('untrusted_legacy_comparison_ranks')
    old=sorted(common,key=lambda c:rows[c]['legacy_rank'])
    new=sorted(common,key=lambda c:rows[c]['rank'])
    if comparison.get('same_input_common_issuers')!=len(common):
        raise ValueError('comparison_common_cohort_mismatch')
    top={}
    for n in (50,100,200):
        old_top=old[:n];new_top=new[:n];old_set=set(old_top);new_set=set(new_top)
        top[str(n)]={'cohort':'same_saved_snapshot_common_ranked_issuers',
            'legacy':composition(old_top,rows,caps),'operational':composition(new_top,rows,caps),
            'overlap':len(old_set&new_set),'entered_codes':[c for c in new_top if c not in old_set],
            'exited_codes':[c for c in old_top if c not in new_set],
            'new_model_component_contributions':{
                area+':'+key:contribution(new_top,rows,area,key)
                for area,parts in R1D_PART_LIMITS.items() for key in parts}}
        declared=comparison.get('top',{}).get(str(n),{})
        if (declared.get('overlap')!=top[str(n)]['overlap'] or
            declared.get('new_kospi')!=sum(rows[c]['market']=='KOSPI' for c in new_top) or
            declared.get('old_kospi')!=sum(rows[c]['market']=='KOSPI' for c in old_top)):
            raise ValueError('comparison_top_composition_mismatch')
    eligible=[c for c,r in rows.items() if r['rank'] is not None]
    # Preserve the archived R1d counterfactual convention while R1e must
    # never restore a stop adjustment under any diagnosis or ranking.
    historical_stop=report['version']=='v3-stable-r1d-operational-research'
    without={c:rounded(max(0,min(100,rows[c]['base']-rows[c]['capital_deduction']+
                         (rows[c]['stop_adjustment'] if historical_stop else 0))))
             for c in eligible}
    ordered=sorted(eligible,key=lambda c:(-without[c],-rows[c]['available'],c))
    counter_ranks={c:i for i,c in enumerate(ordered,1)}
    changed=[{'code':c,'operational_rank':rows[c]['rank'],'without_risk_rank':counter_ranks[c],
              'risk_deduction':rows[c]['risk_deduction'],
              'rank_change_due_to_all_observed_risk_deductions':counter_ranks[c]-rows[c]['rank']}
             for c in eligible if rows[c]['risk_deduction']>0]
    changed.sort(key=lambda x:(x['rank_change_due_to_all_observed_risk_deductions'],x['code']))
    groups={band:composition([c for c in eligible if size_band(caps.get(c))==band],rows,caps)
            for band in BANDS}
    return {'schema':'v3-r1d-saved-source-model-diagnostics-v1',
        'asof':report['asof'],'version':report['version'],'source_digest':report['source_digest'],
        'listed_total':audit['listed_total'],'ranked_count':len(eligible),'common_ranked_issuers':len(common),
        'legacy_component_contributions_available':False,'top':top,'ranked_capitalization_groups':groups,
        'observed_risk_counterfactual':{'ranking_only_not_performance':True,
            'positive_deduction_issuer_count':len(changed),
            'risk_unconfirmed_issuer_count':sum(r['risk_status']=='unknown' for r in rows.values()),
            'largest_penalized_rank_changes':changed[:50],
            'all_other_adjustments_preserved':True},
        'component_correlations':correlation_pairs(rows),
        'retained_older_component_issuer_count':sum(bool(r['oldest_asof']) and r['oldest_asof']<report['asof'] for r in rows.values()),
        'performance_validation':{'status':'unvalidated_no_point_in_time_outcomes',
            'horizon_returns':{k:None for k in ('1m','2m','3m','6m','12m')},
            'mae':None,'structural_stop_first_rate':None,
            'early_rise_detection_validated':False,'volatility_bias_validated':False},
        'provider_calls':0,'points_added':0,'investment_score_certified':False,
        'small_cap_composition_is_not_volatility_or_loss_probability':True}


def write(root):
    root=Path(root)
    def read(name):return json.loads((root/name).read_text(encoding='utf8'))
    report=read('v3-operational-research-scores.json')
    comparison=read('v3-operational-research-comparison.json')
    catalog=read('v22-market-catalog.json')
    if catalog.get('date','')>report.get('asof','9999'):
        # The producer can intentionally retain yesterday's valid scores
        # during source rollover. Never join today's caps to that report.
        previous=read(OUTPUT) if (root/OUTPUT).is_file() else None
        if (r1d_public_reconciles(report,comparison) is None or
            type(previous) is not dict or
            previous.get('schema')!='v3-r1d-saved-source-model-diagnostics-v1' or
            previous.get('investment_score_certified') is not False or
            not previous.get('asof') or previous['asof']>report['asof']):
            raise ValueError('cannot_preserve_unverified_diagnostic_snapshot')
        return {'status':'waiting_same_day_capitalization_catalog',
            'asof':previous['asof'],'common_ranked_issuers':None,
            'provider_calls':0,'points_added':0}
    result=build(report,comparison,catalog)
    result['source_report_sha256']=sha256((root/'v3-operational-research-scores.json').read_bytes()).hexdigest()
    target=root/OUTPUT;tmp=target.with_suffix('.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n',encoding='utf8')
    tmp.replace(target)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs',type=Path,default=Path('docs'))
    args=parser.parse_args();result=write(args.docs)
    print(json.dumps({k:result[k] for k in ('asof','common_ranked_issuers','provider_calls','points_added')}|({'status':result['status']} if 'status' in result else {})))
