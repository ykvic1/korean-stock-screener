"""Private actual-money inputs -> bounded personal provisional participation6.

The nine comparisons use equal disjoint periods. Daily and exact calendar-week
families each contribute 3 points. No amount, ratio or reconstructable time
series is emitted publicly. Missing/zero-baseline evidence is unavailable,
never a zero score. Historical investment certification remains false.
"""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
import argparse
import json
import math
import re
import os
from pathlib import Path

from v3_krx_money_backfill import CHECKPOINT, _path, _cohort_at_recent_first, history_candidates
from v3_krx_money_public_progress import collect as history_progress
from v3_krx_money_forward import CHECKPOINT as FORWARD, _manifest, _verify_tail, forward_candidates
from v3_krx_money_5day import validate_money_blob
from v3_krx_calendar_week_money import (CHECKPOINT as WEEKLY, HISTORY_WEEKS,
    manifest, path as week_path, week_days, validate as validate_week)
from v3_krx_private_github_backfill import (
    PrivateGitStorage,PrivateHistoryError,storage_diagnostic)
from v3_dart_bulk_history import _stable_digest

from v3_actual_money_contract import (
    SCHEMA, MODEL, OUTPUT, DAILY, WEEKLY_WINDOWS, usable_records, closed_week_end)
from v3_actual_money_structure import derive as derive_r1c_structure
from v3_actual_money_structure import derive_components, COMPONENT_SCHEMA, COMPONENT_MAX
from v3_actual_money_contract import usable_component_records
from v3_krx_money_week_repair import (CHECKPOINT as WEEK_REPAIR,
    PREFIX as WEEK_REPAIR_PREFIX, index as repair_index, overlay as repair_overlay)
from v3_krx_money_daily_repair import (CHECKPOINT as DAILY_REPAIR,
    PREFIX as DAILY_REPAIR_PREFIX, index as daily_repair_index, overlay as daily_repair_overlay)
PRIVATE_REPO='ykvic1/korean-stock-private-data'
COVERAGE_SUMMARY='v3-actual-money-coverage-summary.json'
COVERAGE_SUMMARY_SCHEMA='v3-personal-actual-money-coverage-summary-0.1'

class ActualMoneyError(ValueError):
    """Fixed codes only, never source data."""


def safe_failure_diagnostic(issue):
    """Fixed reason enums only: never emit private paths, HTTP bodies or token."""
    if isinstance(issue,PrivateHistoryError):
        return {'kind':'private_storage','details':storage_diagnostic(issue)}
    if isinstance(issue,ActualMoneyError):
        reason=str(issue)
        allowed={'invalid_money_score_source_identity',
            'invalid_money_history_forward_boundary',
            'money_daily_source_not_current_complete_480_sessions',
            'money_daily_amount_unit_mismatch','money_daily_calendar_chain_gap',
            'money_calendar_week_history_unavailable',
            'money_calendar_week_history_incomplete','money_calendar_week_source_stale',
            'money_calendar_week_checkpoint_tail_changed',
            'invalid_money_current_issuer_catalog',
            'money_unclassified_factor_gap','incomplete_money_current_issuer_catalog',
            'private_money_checkpoint_changed_during_score_read',
            'money_public_score_self_validation_failed',
            'invalid_money_coverage_amount','money_snapshot_path_not_allowed',
            'money_snapshot_path_escape','money_snapshot_file_too_large'}
        return {'kind':'source_contract','reason':reason if reason in allowed else 'other_source_contract'}
    return {'kind':'unexpected_failure'}


def window_strength(recent, baseline):
    """0..1, independent of issuer absolute liquidity/market cap.

    Relative average increase saturates at 3x. At equal per-bucket amounts
    concentration has no penalty; one-bucket dominance has no reward.
    Complete holiday weeks compare average amount per observed session.
    """
    if (not recent or len(recent)!=len(baseline) or any(
        type(v) not in (int,float) or not math.isfinite(v) or v<0
        for v in recent+baseline)):
        return None
    reference=sum(baseline)/len(baseline)
    if reference<=0:
        return None
    total=sum(recent)
    if total<=0:
        return 0.0
    ratio=total/len(recent)/reference
    increase=max(0.0,min(1.0,math.log(max(ratio,1.0))/math.log(3)))
    persistence=sum(v>reference for v in recent)/len(recent)
    n=len(recent)
    concentration=(max(recent)/total-1/n)/(1-1/n) if n>1 else 1.0
    return increase*persistence*max(0.0,1-concentration)


def family(values, windows):
    scores=[]
    for n in windows:
        if len(values)<2*n or any(v is None for v in values[:2*n]):
            return None
        score=window_strength(values[:n],values[n:2*n])
        if score is None:
            return None
        scores.append(score)
    return 3*sum(scores)/len(scores)


def provisional_points(daily, weekly):
    d=family(daily,tuple(n//5 for n in DAILY))
    w=family(weekly,WEEKLY_WINDOWS)
    if d is None or w is None:
        return None
    return round(max(0,min(6,d+w)),4)


def family_coverage_reason(values, windows):
    """Explain unavailable source history without publishing issuer-level amounts.

    Coverage is NOT an explanation of why an issuer lacks observations: a
    newly listed stock, an exchange/source gap, or a code change may all cause
    the same missing-history classification. Never infer a listing date.
    """
    if len(values)<2*max(windows) or any(v is None for v in values[:2*max(windows)]):
        return 'incomplete_periods'
    for n in windows:
        recent,baseline=values[:n],values[n:2*n]
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0
               for v in recent+baseline):
            raise ActualMoneyError('invalid_money_coverage_amount')
        if sum(baseline)<=0:
            return 'zero_baseline'
    return 'complete'


def coverage_summary(report):
    """Tiny, privacy-safe home-page diagnostic, without any issuer receipts."""
    return {
        'schema':COVERAGE_SUMMARY_SCHEMA,
        'source_schema':SCHEMA,
        'model':MODEL,
        'asof':report['asof'],
        'listed_total':report['listed_total'],
        'eligible_issuers':report['eligible_issuers'],
        'investment_score_certified':False,
        'original_amounts_or_bars_published':False,
        'coverage_gap_diagnostic':report['coverage_gap_diagnostic'],
        'comparison_window_coverage':report['comparison_window_coverage'],
        **({'component_observation_coverage': report['component_observation_coverage']}
           if 'component_observation_coverage' in report else {}),
        **({'history_shape_diagnostic': report['history_shape_diagnostic']}
           if 'history_shape_diagnostic' in report else {}),
        **({'weekly_repair_observation': report['weekly_repair_observation']}
           if 'weekly_repair_observation' in report else {}),
        **({'daily_repair_observation': report['daily_repair_observation']}
           if 'daily_repair_observation' in report else {}),
    }


def history_shape(values):
    """Coverage pattern only: never infer listing dates or source amounts."""
    missing = [value is None for value in values]
    if not any(missing):
        return 'complete'
    if all(missing):
        return 'no_observed_history'
    if missing[0]:
        return 'recent_missing'
    first = missing.index(True)
    return 'tail_only_missing' if all(missing[first:]) else 'internal_missing'


def read_daily_history(store, *, asof, before, days=None, digest=None, forward_days=None):
    """One shared validation path for scoring and additive money recovery."""
    if days is None:
        days,digest=history_candidates()
    if forward_days is None:
        forward_days=forward_candidates(asof)
    progress=history_progress(store,days,digest)
    fstate=_manifest(before[FORWARD][0],forward_days)
    _verify_tail(store,fstate,forward_days)
    if forward_days[:len(days)%5]!=days[5*(len(days)//5):]:
        raise ActualMoneyError('invalid_money_history_forward_boundary')
    fcount=fstate['next_window_index']
    windows=[forward_days[5*i:5*(i+1)] for i in range(fcount-1,max(-1,fcount-97),-1)]
    windows += [_cohort_at_recent_first(days,i) for i in range(
        min(progress['five_session_cohorts_completed'],96-len(windows)))]
    if len(windows)!=96 or windows[0][-1]>asof:
        raise ActualMoneyError('money_daily_source_not_current_complete_480_sessions')
    daily=[]
    blobs=[]
    for window in windows:
        raw,_=store.get_content(_path(window))
        value=validate_money_blob(raw,window[0],window[-1])
        if value.get('amount_bucket_krw')!=100_000:
            raise ActualMoneyError('money_daily_amount_unit_mismatch')
        daily.append(value)
        blobs.append(raw)
    # Verify the whole expected candidate prefix: no silent holes/overlap or
    # a forged calendar gap between two individually plausible files.
    flattened=[d for w in reversed(windows) for d in w]
    combined=days[:5*(len(days)//5)]+forward_days[:5*fcount]
    if flattened!=combined[-480:]:
        raise ActualMoneyError('money_daily_calendar_chain_gap')
    return windows,daily,blobs


def collect(store, *, asof, catalog, source_revision, days=None, digest=None,
            forward_days=None, week_loader=week_days):
    if (store.repository!=PRIVATE_REPO or type(source_revision) is not str
        or len(source_revision)!=40 or any(c not in '0123456789abcdef' for c in source_revision)
        or catalog.get('date')!=asof or type(catalog.get('rows')) is not list):
        raise ActualMoneyError('invalid_money_score_source_identity')
    store.verify_private()
    paths=(CHECKPOINT,FORWARD,WEEKLY)
    before={p:store.get_content(p) for p in paths}
    repair_before=store.get_content(WEEK_REPAIR)
    repairs=repair_index(repair_before[0])
    windows,daily,daily_blobs=read_daily_history(store,asof=asof,before=before,
        days=days,digest=digest,forward_days=forward_days)
    daily_repair_before=store.get_content(DAILY_REPAIR)
    daily_repairs=daily_repair_index(daily_repair_before[0])
    daily_added=0
    for i,(window,blob,original) in enumerate(zip(windows,daily_blobs,daily)):
        daily[i],added=daily_repair_overlay(store,blob,original,window,daily_repairs)
        daily_added+=added
    if before[WEEKLY][0] is None:
        raise ActualMoneyError('money_calendar_week_history_unavailable')
    wstate=manifest(before[WEEKLY][0],asof)
    if wstate['history_completed']!=HISTORY_WEEKS:
        raise ActualMoneyError('money_calendar_week_history_incomplete')
    end=date.fromisoformat(wstate['anchor_week_end'])+timedelta(weeks=wstate['forward_completed'])
    if end.isoformat()!=closed_week_end(asof):
        raise ActualMoneyError('money_calendar_week_source_stale')
    tail,_=store.get_content(week_path(wstate['tail_week_end']))
    if tail is None or sha256(tail).hexdigest()!=wstate['tail_sha256']:
        raise ActualMoneyError('money_calendar_week_checkpoint_tail_changed')
    weekly=[]
    repaired_pairs=0
    for i in range(HISTORY_WEEKS):
        week=(end-timedelta(weeks=i)).isoformat()
        expected=week_loader(week)
        raw,_=store.get_content(week_path(week))
        observed=validate_week(raw,week,expected)
        observed, added=repair_overlay(store,raw,observed,repairs)
        weekly.append(observed)
        repaired_pairs+=added
    records={}
    component_records={}
    history_shapes={}
    gap_counts={}
    daily_window_counts={str(n):0 for n in DAILY}
    weekly_window_counts={str(n):0 for n in WEEKLY_WINDOWS}
    members=set()
    for item in catalog['rows']:
        code=item.get('code'); market=item.get('market')
        if (type(code) is not str or not re.fullmatch(r'[0-9A-Z]{6}', code)
            or market not in ('KOSPI','KOSDAQ') or code in members):
            raise ActualMoneyError('invalid_money_current_issuer_catalog')
        members.add(code)
        key=market+':'+code
        d=[r['derived_issuer_money_factors'].get(key) for r in daily]
        w=[r['issuer_week_factors'].get(key) for r in weekly]
        dv=[r['total_actual_amount_100k_krw']/5 if r is not None else None for r in d]
        wv=[r['amount_100k_krw']/r['observed_sessions'] if r is not None else None for r in w]
        shape=history_shape(dv)+'__'+history_shape(wv)
        history_shapes[shape]=history_shapes.get(shape,0)+1
        for n in DAILY:
            if family_coverage_reason(dv,(n//5,))=='complete':
                daily_window_counts[str(n)]+=1
        for n in WEEKLY_WINDOWS:
            if family_coverage_reason(wv,(n,))=='complete':
                weekly_window_counts[str(n)]+=1
        component = derive_components(dv, wv, d)
        if component is not None:
            component_records[code] = {
                'market': market, 'issuer_name': item['name'], 'model': MODEL,
                'asof': min(windows[0][-1], weekly[0]['session_end']),
                'daily_asof': windows[0][-1], 'computed_asof': asof,
                'weekly_session_end': weekly[0]['session_end'],
                'component_observations': component}
        points=provisional_points(dv,wv)
        if points is not None:
            records[code]={'market':market,'asof':min(windows[0][-1],weekly[0]['session_end']),'points':points,
                'daily_asof':windows[0][-1],'computed_asof':asof,
                'model':MODEL,'weekly_session_end':weekly[0]['session_end'],
                'issuer_name':item['name']}
            # A separate R1c decomposition leaves the original six-point
            # personal/legacy model untouched. No source amounts are emitted.
            structure=derive_r1c_structure(dv,wv,d)
            if structure is not None:
                records[code]['r1c_structure']=structure
        else:
            daily_reason=family_coverage_reason(dv,tuple(n//5 for n in DAILY))
            weekly_reason=family_coverage_reason(wv,WEEKLY_WINDOWS)
            if daily_reason=='complete' and weekly_reason=='complete':
                raise ActualMoneyError('money_unclassified_factor_gap')
            key=daily_reason+'__'+weekly_reason
            gap_counts[key]=gap_counts.get(key,0)+1
    if catalog.get('listed_total')!=len(members):
        raise ActualMoneyError('incomplete_money_current_issuer_catalog')
    if (any(store.get_content(p)!=before[p] for p in paths)
        or store.get_content(WEEK_REPAIR)!=repair_before
        or store.get_content(DAILY_REPAIR)!=daily_repair_before):
        raise ActualMoneyError('private_money_checkpoint_changed_during_score_read')
    return {'schema':SCHEMA,'model':MODEL,'asof':asof,'points_budget':6,
        'daily_asof':windows[0][-1],
        'source_dates_are_independent_completed_periods':True,
        'source_catalog_sha256':_stable_digest(catalog),
        'source_private_revision':source_revision,
        'source_weekly_repair_index_sha256':sha256(repair_before[0] or b'').hexdigest(),
        'source_daily_repair_index_sha256':sha256(daily_repair_before[0] or b'').hexdigest(),
        'daily_repair_observation':{'added_issuer_cohorts':daily_added,
            'examined_windows':len(daily_repairs['windows']),
            'original_archives_preserved':True,'prices_or_directions_repaired':False},
        'weekly_repair_observation':{'added_issuer_weeks':repaired_pairs,
            'examined_weeks':len(repairs['weeks']),
            'original_archives_preserved':True,'prices_or_directions_repaired':False},
        'source_checkpoint_sha256':{str(i):sha256(before[p][0]).hexdigest() for i,p in enumerate(paths)},
        'daily_windows':list(DAILY),'calendar_week_windows':list(WEEKLY_WINDOWS),
        'calendar_week_end':end.isoformat(),'weekly_session_end':weekly[0]['session_end'],
        'daily_cohorts_inspected':96,'calendar_weeks_inspected':HISTORY_WEEKS,
        'listed_total':len(members),'eligible_issuers':len(records),'records':records,
        'component_contract': COMPONENT_SCHEMA,
        'component_records': component_records,
        'component_eligible_issuers': len(component_records),
        'component_observation_coverage': {
            'observed_issuers': {key: sum(r['component_observations']['components'][key] is not None
                                         for r in component_records.values()) for key in COMPONENT_MAX},
            'full_weight_issuers': {key: sum(r['component_observations']['components'][key] is not None
                and r['component_observations']['components'][key][1] == maximum
                for r in component_records.values()) for key, maximum in COMPONENT_MAX.items()},
            'structure_only_issuers': len(set(component_records) - set(records)),
            'partial_weight_not_renormalized': True,
            'complete_structure_not_full_100_observation': True},
        'history_shape_diagnostic': {'pair_counts': history_shapes,
            'does_not_identify_listing_or_gap_cause': True,
            'issuer_dates_or_source_series_published': False},
        'coverage_gap_diagnostic':{
            'full_fresh_evidence_issuers':len(records),
            'fresh_unscored_issuers':len(members)-len(records),
            'reason_pair_counts':dict(sorted(gap_counts.items())),
            'incomplete_periods_do_not_prove_recent_listing':True,
            'zero_baseline_is_unavailable_not_zero_points':True,
            'issuer_amounts_or_coverage_timeseries_published':False},
        'comparison_window_coverage':{
            'daily_comparison_eligible_issuers':daily_window_counts,
            'weekly_comparison_eligible_issuers':weekly_window_counts,
            'complete_window_is_not_full_six_point_eligibility':True,
            'issuer_window_flags_or_amounts_published':False},
        'provisional_personal_use_only':True,'original_amounts_or_bars_published':False,
        'independent_regular_session_verified':False,'corporate_actions_and_pit_verified':False,
        'investment_score_certified':False,'single_day_money_component_points':0,
        'missing_or_zero_baseline_is_unavailable':True}




class LocalPrivateSnapshot:
    """Read only allowlisted paths from an immutable private checkout."""
    repository=PRIVATE_REPO
    def __init__(self, root, token):
        self.root=Path(root).resolve()
        self.remote=PrivateGitStorage(PRIVATE_REPO,token)
    def verify_private(self):
        self.remote.verify_private()
    def get_content(self, path):
        if not (path in (CHECKPOINT,FORWARD,WEEKLY,WEEK_REPAIR,DAILY_REPAIR) or path.startswith(
            ('derived/money-five-session/','derived/money-calendar-weeks/',WEEK_REPAIR_PREFIX,DAILY_REPAIR_PREFIX))):
            raise ActualMoneyError('money_snapshot_path_not_allowed')
        target=(self.root/path).resolve()
        if self.root not in target.parents or (self.root/path).is_symlink():
            raise ActualMoneyError('money_snapshot_path_escape')
        if not target.is_file():
            return None,None
        if target.stat().st_size>800_000:
            raise ActualMoneyError('money_snapshot_file_too_large')
        data=target.read_bytes()
        return data,sha256(data).hexdigest()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private-snapshot',required=True)
    p.add_argument('--private-revision',required=True)
    p.add_argument('--docs',default='docs')
    p.add_argument('--allow-incomplete',action='store_true')
    args=p.parse_args(argv)
    root=Path(args.docs)
    catalog=json.loads((root/'v22-market-catalog.json').read_text())
    store=LocalPrivateSnapshot(args.private_snapshot,os.getenv('V3_PRIVATE_ARCHIVE_TOKEN',''))
    try:
        report=collect(store,asof=catalog['date'],catalog=catalog,source_revision=args.private_revision)
    except ActualMoneyError as error:
        waiting={'money_daily_source_not_current_complete_480_sessions',
                 'money_calendar_week_history_unavailable','money_calendar_week_history_incomplete',
                 'money_calendar_week_source_stale'}
        if not args.allow_incomplete or str(error) not in waiting:
            raise
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as handle: handle.write('ready=false\n')
        print(json.dumps({'status':'waiting_for_complete_money_source',
                          'reason':str(error),'existing_scores_preserved':True}))
        return

    if len(usable_records(report,catalog))!=report['eligible_issuers']:
        raise ActualMoneyError('money_public_score_self_validation_failed')
    if len(usable_component_records(report,catalog)) != report['component_eligible_issuers']:
        raise ActualMoneyError('money_public_score_self_validation_failed')
    prior_path=root/OUTPUT
    if prior_path.is_file():
        prior=usable_records(json.loads(prior_path.read_text()),catalog)
        retained={code:r for code,r in prior.items() if code not in report['records']}
        report['records'].update(retained)
        report['eligible_issuers']=len(report['records'])
        report['retained_prior_money_issuers']=len(retained)
    (root/OUTPUT).write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    (root/COVERAGE_SUMMARY).write_text(json.dumps(coverage_summary(report),sort_keys=True,
                                                  indent=2)+'\n')
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as handle: handle.write('ready=true\n')
    print(json.dumps({'actual_money_six_point_issuers':report['eligible_issuers'],
                      'independent_component_issuers':report['component_eligible_issuers'],
                      'component_observation_coverage':report['component_observation_coverage'],
                      'history_shape_diagnostic':report['history_shape_diagnostic'],
                      'asof':report['asof'],'raw_amounts_published':False,
                      'krx_provider_calls':0}))

if __name__=='__main__':
    try:
        main()
    except Exception as issue:
        print(json.dumps({'status':'blocked_actual_money_source',
            'safe_diagnostic':safe_failure_diagnostic(issue)},sort_keys=True))
        raise SystemExit('blocked: actual_money_score_source_not_complete') from None
