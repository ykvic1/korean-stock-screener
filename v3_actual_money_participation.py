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
import os
from pathlib import Path

from v3_krx_money_backfill import CHECKPOINT, _path, _cohort_at_recent_first, history_candidates
from v3_krx_money_public_progress import collect as history_progress
from v3_krx_money_forward import CHECKPOINT as FORWARD, _manifest, _verify_tail, forward_candidates
from v3_krx_money_5day import validate_money_blob
from v3_krx_calendar_week_money import (CHECKPOINT as WEEKLY, HISTORY_WEEKS,
    manifest, path as week_path, week_days, validate as validate_week)
from v3_krx_private_github_backfill import PrivateGitStorage
from v3_dart_bulk_history import _stable_digest

from v3_actual_money_contract import (
    SCHEMA, MODEL, OUTPUT, DAILY, WEEKLY_WINDOWS, usable_records, closed_week_end)
PRIVATE_REPO='ykvic1/korean-stock-private-data'

class ActualMoneyError(ValueError):
    """Fixed codes only, never source data."""


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


def collect(store, *, asof, catalog, source_revision, days=None, digest=None,
            forward_days=None, week_loader=week_days):
    if (store.repository!=PRIVATE_REPO or type(source_revision) is not str
        or len(source_revision)!=40 or any(c not in '0123456789abcdef' for c in source_revision)
        or catalog.get('date')!=asof or type(catalog.get('rows')) is not list):
        raise ActualMoneyError('invalid_money_score_source_identity')
    store.verify_private()
    if days is None:
        days,digest=history_candidates()
    if forward_days is None:
        forward_days=forward_candidates(asof)
    paths=(CHECKPOINT,FORWARD,WEEKLY)
    before={p:store.get_content(p) for p in paths}
    progress=history_progress(store,days,digest)
    fstate=_manifest(before[FORWARD][0],forward_days)
    _verify_tail(store,fstate,forward_days)
    if forward_days[:len(days)%5]!=days[5*(len(days)//5):]:
        raise ActualMoneyError('invalid_money_history_forward_boundary')
    fcount=fstate['next_window_index']
    windows=[forward_days[5*i:5*(i+1)] for i in range(fcount-1,max(-1,fcount-97),-1)]
    windows += [_cohort_at_recent_first(days,i) for i in range(
        min(progress['five_session_cohorts_completed'],96-len(windows)))]
    if len(windows)!=96 or windows[0][-1]!=asof:
        raise ActualMoneyError('money_daily_source_not_current_complete_480_sessions')
    daily=[]
    for window in windows:
        raw,_=store.get_content(_path(window))
        value=validate_money_blob(raw,window[0],window[-1])
        if value.get('amount_bucket_krw')!=100_000:
            raise ActualMoneyError('money_daily_amount_unit_mismatch')
        daily.append(value)
    # Verify the whole expected candidate prefix: no silent holes/overlap or
    # a forged calendar gap between two individually plausible files.
    flattened=[d for w in reversed(windows) for d in w]
    combined=days[:5*(len(days)//5)]+forward_days[:5*fcount]
    if flattened!=combined[-480:]:
        raise ActualMoneyError('money_daily_calendar_chain_gap')
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
    for i in range(HISTORY_WEEKS):
        week=(end-timedelta(weeks=i)).isoformat()
        expected=week_loader(week)
        raw,_=store.get_content(week_path(week))
        weekly.append(validate_week(raw,week,expected))
    records={}
    members=set()
    for item in catalog['rows']:
        code=item.get('code'); market=item.get('market')
        if (type(code) is not str or len(code)!=6 or not code.isdigit()
            or market not in ('KOSPI','KOSDAQ') or code in members):
            raise ActualMoneyError('invalid_money_current_issuer_catalog')
        members.add(code)
        key=market+':'+code
        d=[r['derived_issuer_money_factors'].get(key) for r in daily]
        w=[r['issuer_week_factors'].get(key) for r in weekly]
        dv=[r['total_actual_amount_100k_krw']/5 if r is not None else None for r in d]
        wv=[r['amount_100k_krw']/r['observed_sessions'] if r is not None else None for r in w]
        points=provisional_points(dv,wv)
        if points is not None:
            records[code]={'market':market,'asof':asof,'points':points,
                'model':MODEL,'weekly_session_end':weekly[0]['session_end'],
                'issuer_name':item['name']}
    if catalog.get('listed_total')!=len(members):
        raise ActualMoneyError('incomplete_money_current_issuer_catalog')
    if any(store.get_content(p)!=before[p] for p in paths):
        raise ActualMoneyError('private_money_checkpoint_changed_during_score_read')
    return {'schema':SCHEMA,'model':MODEL,'asof':asof,'points_budget':6,
        'source_catalog_sha256':_stable_digest(catalog),
        'source_private_revision':source_revision,
        'source_checkpoint_sha256':{str(i):sha256(before[p][0]).hexdigest() for i,p in enumerate(paths)},
        'daily_windows':list(DAILY),'calendar_week_windows':list(WEEKLY_WINDOWS),
        'calendar_week_end':end.isoformat(),'weekly_session_end':weekly[0]['session_end'],
        'daily_cohorts_inspected':96,'calendar_weeks_inspected':HISTORY_WEEKS,
        'listed_total':len(members),'eligible_issuers':len(records),'records':records,
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
        if not (path in (CHECKPOINT,FORWARD,WEEKLY) or path.startswith(
            ('derived/money-five-session/','derived/money-calendar-weeks/'))):
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
    prior_path=root/OUTPUT
    if prior_path.is_file():
        prior=usable_records(json.loads(prior_path.read_text()),catalog)
        retained={code:r for code,r in prior.items() if code not in report['records']}
        report['records'].update(retained)
        report['eligible_issuers']=len(report['records'])
        report['retained_prior_money_issuers']=len(retained)
    (root/OUTPUT).write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as handle: handle.write('ready=true\n')
    print(json.dumps({'actual_money_six_point_issuers':report['eligible_issuers'],
                      'asof':report['asof'],'raw_amounts_published':False}))

if __name__=='__main__':
    try:
        main()
    except Exception:
        raise SystemExit('blocked: actual_money_score_source_not_complete') from None
