"""R1c private-source, non-overlapping actual-money score decomposition.

Growth is separated from persistence/concentration instead of rewarding the
same properties twice in the original participation-six product. Only bounded
point results leave the private source; amounts, window ratios and prices do
not. The intrabucket pressure is incomplete (cohort boundaries are missing),
so this remains an explicitly provisional research score.
"""
import math

SCHEMA = 'v3-actual-money-r1c-independent-structure-0.1'
COMPONENT_MAX = {'persistence':5, 'concentration_quality':2,
                 'up_down_asymmetry':2, 'correction_contraction':1,
                 'weekly_structure_increment':2}
DAILY_WINDOWS = (4,12,24,48)
WEEKLY_WINDOWS = (5,10,20,60,120)
CORRECTION_COHORT_WINDOWS = (12,24,48)  # 60, 120, 240 observed sessions


def _clip(value):
    return max(0., min(1., value))


def _score_window(values, n):
    if (len(values) < 2*n or any(type(x) not in (float,int) or
            not math.isfinite(x) or x<0 for x in values[:2*n])):
        return None
    recent,older=values[:n],values[n:2*n]
    total,baseline=sum(recent),sum(older)
    if baseline <= 0:
        return None
    ratio=total/baseline
    growth=_clip(math.log(max(1.,ratio),3))
    sustained=sum(value>baseline/n for value in recent)/n
    concentration=(_clip((max(recent)/total-1/n)/(1-1/n))
                   if total>0 and n>1 else 1.)
    return {'growth':growth,'sustained':sustained,
            'quality':(1-concentration)**5*_clip((ratio-1.02)/.15),
            'ratio':ratio}


def correction_observation(cohorts):
    """Use the shortest complete window with three up AND three down buckets.

    A one-sided 60-session market is not missing turnover history. Extend
    the actual sample to 120/240 sessions before declaring it unavailable.
    Never skip a corrupt/missing cohort, infer a price sign at boundaries,
    change the one-point formula, or count absent corrections as zero.
    """
    for size in CORRECTION_COHORT_WINDOWS:
        if len(cohorts)<size:return None,None
        up=[];down=[]
        for row in cohorts[:size]:
            if type(row) is not dict:return None,None
            move=row.get('intrabucket_price_change_bp')
            amount=row.get('total_actual_amount_100k_krw')
            if type(move) is not int or type(amount) is not int or not 0<=amount<10**15:
                return None,None
            if move>0:up.append(amount)
            elif move<0:down.append(amount)
        if len(up)>=3 and len(down)>=3 and sum(up)>0:
            return _clip(2*(1-(sum(down)/len(down))/(sum(up)/len(up)))),size
    return None,None


def derive(daily, weekly, cohorts):
    """Newest-first quantized actual-money buckets; no public source series."""
    if (type(daily) is not list or type(weekly) is not list or type(cohorts) is not list):
        return None
    d=[_score_window(daily,n) for n in DAILY_WINDOWS]
    w=[_score_window(weekly,n) for n in WEEKLY_WINDOWS]
    if any(x is None for x in d+w):
        return None
    growth=3*sum(x['growth'] for x in d)/len(d)+3*sum(x['growth'] for x in w)/len(w)
    persistence=2.5*(sum(x['sustained'] for x in d)/len(d)+
                     sum(x['sustained'] for x in w)/len(w))
    concentration=sum((d[0]['quality'],w[0]['quality']))
    # The four within-cohort intervals have directional trading-value
    # pressure, but cross-cohort price signs are absent. Do not claim exact
    # day-by-day signed turnover/OBV or investor buying.
    pressures=[]
    for row in cohorts[:12]:
        if type(row) is not dict:
            continue
        pressure=row.get('money_pressure_intrabucket_bp')
        if type(pressure) is int and -10000<=pressure<=10000:
            pressures.append(pressure)
    asymmetry=(2*_clip(sum(pressures)/len(pressures)/5000)
               if len(pressures)==12 else None)
    contraction,correction_window=correction_observation(cohorts)
    # Residual long-weekly increase OVER recent daily increase, not a
    # second award for the same daily growth ratio.
    weekly_increment=2*_clip((w[-1]['ratio']-max(1.,d[0]['ratio']))/1.0)
    values={'persistence':persistence,'concentration_quality':concentration,
            'up_down_asymmetry':asymmetry,'correction_contraction':contraction,
            'weekly_structure_increment':weekly_increment}
    result={'schema':SCHEMA,'growth6':round(growth,6),
            'correction_window_cohorts':correction_window,
            'components':{key:round(value,6) if value is not None else None
                          for key,value in values.items()},
            'derived_from_private_reported_acc_trdval':True,
            'original_amounts_or_paths_exported':False,
            'intrabucket_boundary_price_sign_known':False,
            'independent_regular_session_verified':False,
            'investment_score_certified':False}
    return result if valid(result) else None


def valid(source):
    fields=set(('schema','growth6',
        'components','derived_from_private_reported_acc_trdval',
        'original_amounts_or_paths_exported','intrabucket_boundary_price_sign_known',
        'independent_regular_session_verified','investment_score_certified'))
    if (type(source) is not dict or set(source) not in (fields,fields|{'correction_window_cohorts'})
        or source['schema']!=SCHEMA
        or source['derived_from_private_reported_acc_trdval'] is not True
        or any(source[k] is not False for k in ('original_amounts_or_paths_exported',
            'intrabucket_boundary_price_sign_known','independent_regular_session_verified',
            'investment_score_certified'))
        or type(source['growth6']) not in (float,int)
        or not math.isfinite(source['growth6'])
        or not 0<=source['growth6']<=6
        or type(source['components']) is not dict
        or set(source['components'])!=set(COMPONENT_MAX)):
        return False
    if 'correction_window_cohorts' in source:
        size=source['correction_window_cohorts']
        value=source['components']['correction_contraction']
        if value is None:
            if size is not None:return False
        elif type(size) is not int or size not in CORRECTION_COHORT_WINDOWS:
            return False
    return all(source['components'][k] is None or
               (type(source['components'][k]) in (float,int)
                and math.isfinite(source['components'][k])
                and 0<=source['components'][k]<=maximum)
               for k,maximum in COMPONENT_MAX.items())


COMPONENT_SCHEMA = 'v3-actual-money-independent-component-observations-v1'


def derive_components(daily, weekly, cohorts):
    """Observe each formula only from its own required source windows.

    Missing persistence windows retain their fixed missing weight. Never
    renormalize a short history into the full five-point observation.
    """
    if any(type(x) is not list for x in (daily, weekly, cohorts)):
        return None
    d = [_score_window(daily, n) for n in DAILY_WINDOWS]
    w = [_score_window(weekly, n) for n in WEEKLY_WINDOWS]
    present = [(x, 2.5 / len(d)) for x in d if x is not None]
    present += [(x, 2.5 / len(w)) for x in w if x is not None]
    persistence = ([sum(x['sustained'] * weight for x, weight in present),
                    sum(weight for _, weight in present)] if present else None)
    short = [x for x in (d[0], w[0]) if x is not None]
    concentration = [sum(x['quality'] for x in short), len(short)] if short else None
    pressure = [row.get('money_pressure_intrabucket_bp') if type(row) is dict else None
                for row in cohorts[:12]]
    asymmetry = ([2 * _clip(sum(pressure) / 12 / 5000), 2]
                 if len(pressure) == 12 and all(type(x) is int and -10000 <= x <= 10000
                                               for x in pressure) else None)
    correction, size = correction_observation(cohorts)
    increment = ([2 * _clip(w[-1]['ratio'] - max(1., d[0]['ratio'])), 2]
                 if w[-1] is not None and d[0] is not None else None)
    components = {'persistence': persistence, 'concentration_quality': concentration,
                  'up_down_asymmetry': asymmetry,
                  'correction_contraction': [correction, 1] if correction is not None else None,
                  'weekly_structure_increment': increment}
    if not any(x is not None for x in components.values()):
        return None
    growth = (3 * sum(x['growth'] for x in d) / len(d) +
              3 * sum(x['growth'] for x in w) / len(w)
              if all(x is not None for x in d + w) else None)
    result = {'schema': COMPONENT_SCHEMA,
              'growth6': round(growth, 6) if growth is not None else None,
              'components': {k: [round(x[0], 6), round(x[1], 6)] if x is not None else None
                             for k, x in components.items()},
              'correction_window_cohorts': size,
              'missing_weight_not_renormalized': True,
              'derived_from_private_reported_acc_trdval': True,
              'original_amounts_or_paths_exported': False,
              'intrabucket_boundary_price_sign_known': False,
              'independent_regular_session_verified': False,
              'investment_score_certified': False}
    return result if valid_components(result) else None


def valid_components(source):
    if (type(source) is not dict or set(source) != {
        'schema', 'growth6', 'components', 'correction_window_cohorts',
        'missing_weight_not_renormalized', 'derived_from_private_reported_acc_trdval',
        'original_amounts_or_paths_exported', 'intrabucket_boundary_price_sign_known',
        'independent_regular_session_verified', 'investment_score_certified'}
        or source['schema'] != COMPONENT_SCHEMA
        or source['missing_weight_not_renormalized'] is not True
        or source['derived_from_private_reported_acc_trdval'] is not True
        or any(source[k] is not False for k in ('original_amounts_or_paths_exported',
            'intrabucket_boundary_price_sign_known', 'independent_regular_session_verified',
            'investment_score_certified'))
        or type(source['components']) is not dict or set(source['components']) != set(COMPONENT_MAX)):
        return False
    if source['growth6'] is not None and (type(source['growth6']) not in (float, int)
        or not math.isfinite(source['growth6']) or not 0 <= source['growth6'] <= 6):
        return False
    for key, maximum in COMPONENT_MAX.items():
        part = source['components'][key]
        if part is not None and (type(part) is not list or len(part) != 2
            or any(type(x) not in (float, int) or not math.isfinite(x) for x in part)
            or not 0 <= part[0] <= part[1] <= maximum or part[1] <= 0):
            return False
        if part is not None:
            allowed = ({round(a * .625 + b * .5, 6) for a in range(5) for b in range(6)
                        if a or b} if key == 'persistence' else
                       {1, 2} if key == 'concentration_quality' else {maximum})
            if part[1] not in allowed:
                return False
    correction = source['components']['correction_contraction']
    size = source['correction_window_cohorts']
    if correction is None:
        if size is not None:
            return False
    elif type(size) is not int or size not in CORRECTION_COHORT_WINDOWS:
        return False
    return any(x is not None for x in source['components'].values())
