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
    pressures=[];up=[];down=[]
    for row in cohorts[:12]:
        if type(row) is not dict:
            continue
        pressure=row.get('money_pressure_intrabucket_bp')
        amount=row.get('total_actual_amount_100k_krw')
        move=row.get('intrabucket_price_change_bp')
        if type(pressure) is int and -10000<=pressure<=10000:
            pressures.append(pressure)
        if type(move) is int and type(amount) is int and 0<=amount<10**15:
            if move>0:up.append(amount)
            elif move<0:down.append(amount)
    asymmetry=(2*_clip(sum(pressures)/len(pressures)/5000)
               if len(pressures)==12 else None)
    contraction=None
    if len(up)>=3 and len(down)>=3 and sum(up)>0:
        contraction=_clip(2*(1-(sum(down)/len(down))/(sum(up)/len(up))))
    # Residual long-weekly increase OVER recent daily increase, not a
    # second award for the same daily growth ratio.
    weekly_increment=2*_clip((w[-1]['ratio']-max(1.,d[0]['ratio']))/1.0)
    values={'persistence':persistence,'concentration_quality':concentration,
            'up_down_asymmetry':asymmetry,'correction_contraction':contraction,
            'weekly_structure_increment':weekly_increment}
    result={'schema':SCHEMA,'growth6':round(growth,6),
            'components':{key:round(value,6) if value is not None else None
                          for key,value in values.items()},
            'derived_from_private_reported_acc_trdval':True,
            'original_amounts_or_paths_exported':False,
            'intrabucket_boundary_price_sign_known':False,
            'independent_regular_session_verified':False,
            'investment_score_certified':False}
    return result if valid(result) else None


def valid(source):
    if (type(source) is not dict or set(source)!=set(('schema','growth6',
        'components','derived_from_private_reported_acc_trdval',
        'original_amounts_or_paths_exported','intrabucket_boundary_price_sign_known',
        'independent_regular_session_verified','investment_score_certified'))
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
    return all(source['components'][k] is None or
               (type(source['components'][k]) in (float,int)
                and math.isfinite(source['components'][k])
                and 0<=source['components'][k]<=maximum)
               for k,maximum in COMPONENT_MAX.items())
