"""Standard-library public point contract; no provider/client dependencies.

Source acquisition is intentionally separate from volume score consumption.
"""
from datetime import date,timedelta
import math
from v3_dart_bulk_history import _stable_digest
from v3_actual_money_structure import valid as valid_r1c_structure

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


def _validated_records(report, catalog):
    """Old/missing evidence is optional; reject an entire malformed new receipt."""
    if (not isinstance(report,dict) or type(report.get('asof')) is not str
        or report['asof']>catalog['date']):
        return None
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
            return None
        members={r['code']:r for r in catalog['rows']}
        records=report['records']
        if type(records) is not dict or report['eligible_issuers']!=len(records):
            return None
        diagnostic=report.get('coverage_gap_diagnostic')
        if diagnostic is not None:
            statuses={'complete','incomplete_periods','zero_baseline'}
            pairs={a+'__'+b for a in statuses for b in statuses
                   if a!='complete' or b!='complete'}
            if (type(diagnostic) is not dict or set(diagnostic)!={
                'full_fresh_evidence_issuers','fresh_unscored_issuers',
                'reason_pair_counts','incomplete_periods_do_not_prove_recent_listing',
                'zero_baseline_is_unavailable_not_zero_points',
                'issuer_amounts_or_coverage_timeseries_published'}
                or type(diagnostic['full_fresh_evidence_issuers']) is not int
                or type(diagnostic['fresh_unscored_issuers']) is not int
                or diagnostic['full_fresh_evidence_issuers']<0
                or diagnostic['fresh_unscored_issuers']<0
                or diagnostic['full_fresh_evidence_issuers']+diagnostic['fresh_unscored_issuers']!=report['listed_total']
                or diagnostic['full_fresh_evidence_issuers']>report['eligible_issuers']
                or diagnostic['incomplete_periods_do_not_prove_recent_listing'] is not True
                or diagnostic['zero_baseline_is_unavailable_not_zero_points'] is not True
                or diagnostic['issuer_amounts_or_coverage_timeseries_published'] is not False
                or type(diagnostic['reason_pair_counts']) is not dict
                or any(k not in pairs or type(v) is not int or v<=0
                       for k,v in diagnostic['reason_pair_counts'].items())
                or sum(diagnostic['reason_pair_counts'].values())!=diagnostic['fresh_unscored_issuers']):
                return None
        windows=report.get('comparison_window_coverage')
        if windows is not None:
            if (type(windows) is not dict or set(windows)!={
                'daily_comparison_eligible_issuers','weekly_comparison_eligible_issuers',
                'complete_window_is_not_full_six_point_eligibility',
                'issuer_window_flags_or_amounts_published'}
                or windows['complete_window_is_not_full_six_point_eligibility'] is not True
                or windows['issuer_window_flags_or_amounts_published'] is not False):
                return None
            fresh=diagnostic['full_fresh_evidence_issuers'] if diagnostic else 0
            for field,periods in [('daily_comparison_eligible_issuers',DAILY),
                                  ('weekly_comparison_eligible_issuers',WEEKLY_WINDOWS)]:
                counts=windows[field]
                if (type(counts) is not dict or set(counts)!={str(n) for n in periods}
                    or any(type(v) is not int or not fresh<=v<=report['listed_total']
                           for v in counts.values())):
                    return None
        for code,r in records.items():
            if (type(r) is not dict or type(r.get('asof')) is not str
                or r['asof']>report['asof'] or date.fromisoformat(r['asof']).isoformat()!=r['asof']
                or r.get('model')!=MODEL or type(r.get('issuer_name')) is not str
                or type(r.get('weekly_session_end')) is not str
                or date.fromisoformat(r['weekly_session_end']).isoformat()!=r['weekly_session_end']
                or r['weekly_session_end']>report['asof']
                or type(r.get('points')) not in (int,float) or not math.isfinite(r['points'])
                or not 0<=r['points']<=6):
                return None
            if ('r1c_structure' in r and
                not valid_r1c_structure(r['r1c_structure'])):
                return None
            if 'daily_asof' in r or 'computed_asof' in r:
                daily=r.get('daily_asof');computed=r.get('computed_asof')
                if (type(daily) is not str or type(computed) is not str
                    or date.fromisoformat(daily).isoformat()!=daily
                    or date.fromisoformat(computed).isoformat()!=computed
                    or max(daily,r['weekly_session_end'])>computed
                    or computed>report['asof']
                    or r['asof']!=min(daily,r['weekly_session_end'])):
                    return None
        return {code:r for code,r in records.items() if code in members
                and members[code]['market']==r['market']
                and members[code]['name']==r['issuer_name']}
    except (KeyError,TypeError,ValueError,AttributeError):
        return None



def usable_records(report, catalog):
    return _validated_records(report, catalog) or {}


def usable_component_records(report, catalog):
    from v3_actual_money_structure import COMPONENT_SCHEMA, valid_components
    if _validated_records(report, catalog) is None:
        return {}
    try:
        records = report['component_records']
        if (report['component_contract'] != COMPONENT_SCHEMA or type(records) is not dict
            or type(report['component_eligible_issuers']) is not int
            or report['component_eligible_issuers'] != len(records)
            or len(records) > report['listed_total']):
            return {}
        members = {row['code']: row for row in catalog['rows']}
        selected = {}
        keys = {'market', 'issuer_name', 'asof', 'daily_asof', 'computed_asof',
                'weekly_session_end', 'model', 'component_observations'}
        for code, row in records.items():
            if (type(row) is not dict or set(row) != keys or row['model'] != MODEL
                or not valid_components(row['component_observations'])
                or any(type(row[k]) is not str or date.fromisoformat(row[k]).isoformat() != row[k]
                       for k in ('asof', 'daily_asof', 'computed_asof', 'weekly_session_end'))
                or max(row['daily_asof'], row['weekly_session_end']) > row['computed_asof']
                or row['computed_asof'] > report['asof']
                or row['daily_asof'] != report['daily_asof']
                or row['weekly_session_end'] != report['weekly_session_end']
                or row['computed_asof'] != report['asof']
                or row['weekly_session_end'] > report['weekly_session_end']
                or row['asof'] != min(row['daily_asof'], row['weekly_session_end'])):
                return {}
            member = members.get(code)
            if member and member['name'] == row['issuer_name'] and member['market'] == row['market']:
                selected[code] = row
        return selected
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
