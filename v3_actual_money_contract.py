"""Standard-library public point contract; no provider/client dependencies.

Source acquisition is intentionally separate from volume score consumption.
"""
from datetime import date,timedelta
import math
from v3_dart_bulk_history import _stable_digest

SCHEMA='v3-personal-actual-money-participation-six-0.1'
MODEL='actual-money-disjoint-d4-w5-persistence-concentration-v1'
OUTPUT='v3-actual-money-participation.json'
PRIVATE_REPO='ykvic1/korean-stock-private-data'
DAILY=(20,60,120,240)
WEEKLY_WINDOWS=(5,10,20,60,120)

def closed_week_end(asof):
    d=date.fromisoformat(asof)
    if d.isoformat()!=asof:
        raise ValueError('invalid_money_contract_date')
    return (d+timedelta(days=6-d.weekday()) if d.weekday()>=4
            else d-timedelta(days=d.weekday()+1)).isoformat()


def usable_records(report, catalog):
    """Old/missing evidence is optional; reject an entire malformed new receipt."""
    if (not isinstance(report,dict) or type(report.get('asof')) is not str
        or report['asof']>catalog['date']):
        return {}
    try:
        if (report['schema']!=SCHEMA or report['model']!=MODEL or report['points_budget']!=6
            or len(report['source_catalog_sha256'])!=64
            or len(report['source_private_revision'])!=40
            or any(c not in '0123456789abcdef' for c in report['source_private_revision'])
            or set(report['source_checkpoint_sha256'])!={'0','1','2'}
            or any(type(v) is not str or len(v)!=64 for v in report['source_checkpoint_sha256'].values())
            or report['daily_windows']!=list(DAILY) or report['calendar_week_windows']!=list(WEEKLY_WINDOWS)
            or report['daily_cohorts_inspected']!=96 or report['calendar_weeks_inspected']!=240
            or report['calendar_week_end']!=closed_week_end(report['asof'])
            or date.fromisoformat(report['weekly_session_end']).isoformat()!=report['weekly_session_end']
            or report['weekly_session_end']>report['calendar_week_end']
            or report['weekly_session_end']>report['asof']
            or report['single_day_money_component_points']!=0
            or report['missing_or_zero_baseline_is_unavailable'] is not True
            or any(report[k] is not False for k in ('original_amounts_or_bars_published',
                'independent_regular_session_verified','corporate_actions_and_pit_verified','investment_score_certified'))
            or report['provisional_personal_use_only'] is not True):
            return {}
        members={r['code']:r for r in catalog['rows']}
        records=report['records']
        if type(records) is not dict or report['eligible_issuers']!=len(records):
            return {}
        for code,r in records.items():
            if (type(r) is not dict or type(r.get('asof')) is not str
                or r['asof']>report['asof'] or date.fromisoformat(r['asof']).isoformat()!=r['asof']
                or r.get('model')!=MODEL or type(r.get('issuer_name')) is not str
                or type(r.get('weekly_session_end')) is not str
                or date.fromisoformat(r['weekly_session_end']).isoformat()!=r['weekly_session_end']
                or r['weekly_session_end']>r['asof']
                or type(r.get('points')) not in (int,float) or not math.isfinite(r['points'])
                or not 0<=r['points']<=6):
                return {}
        return {code:r for code,r in records.items() if code in members
                and members[code]['market']==r['market']
                and members[code]['name']==r['issuer_name']}
    except (KeyError,TypeError,ValueError,AttributeError):
        return {}

