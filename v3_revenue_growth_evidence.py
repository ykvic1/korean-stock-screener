"""Four actual revenue years do not require four operating-profit values."""
import math

from v3_dart_bulk_history import SCHEMA, _day, _valid_receipt, _best_income_window, _midrank


def window(record, fiscal, asof):
    """Same scope and four dated annual receipts; never shorten the horizon."""
    cutoff=_day(asof)
    if fiscal!=list(range(cutoff.year-4,cutoff.year)) or not isinstance(record,dict):return None
    years=record.get('years')
    if type(years) is not dict:return None
    # Preserve the established accounting scope whenever its existing full
    # income window is usable. The fallback only removes a profit dependency.
    full=_best_income_window(record,fiscal)
    scopes=(full['accounting_scope'],) if full else ('CFS','OFS')
    for scope in scopes:
        statements=[years.get(str(y),{}).get(scope,{}) for y in fiscal]
        revenues=[s.get('values',{}).get('revenue') for s in statements]
        if not all(type(v) is int and 0<v<=10**18 and
                   _valid_receipt(s.get('receipt_id'),y,cutoff)
                   for v,s,y in zip(revenues,statements,fiscal)):continue
        growth=100*((revenues[-1]/revenues[0])**(1/3)-1)
        if math.isfinite(growth) and abs(growth)<10**7:
            return {'growth':round(growth,6),'scope':scope,
                    'independent_of_missing_profit':full is None}
    return None


def derive(history,catalog):
    """Rebuild the same-market revenue peer cohort from saved actual values."""
    day=catalog['date']
    summary={'provider_calls':0,'eligible_revenue_issuers':0,'independent_revenue_only_issuers':0,
             'historical_PIT_certified':False,'growth_horizon_years':3,'required_annual_observations':4}
    if (history.get('schema')!=SCHEMA or history.get('asof')!=day or
        history.get('historical_PIT_source_certified') is not False):
        return {},{**summary,'status':'waiting_current_annual_revenue_source'}
    values={}
    members={r['code']:r['market'] for r in catalog['rows']}
    for code,record in history.get('records',{}).items():
        if code not in members:continue
        observation=window(record,history.get('fiscal_years'),day)
        if observation is not None:values[code]=observation
    peers={m:sorted(v['growth'] for c,v in values.items() if members[c]==m)
           for m in ('KOSPI','KOSDAQ')}
    # Match the existing 4-point source percentile -> 3.5-point R1d
    # conversion, including both six-decimal rounding steps.
    parts={c:{'points':round(round(4*_midrank(v['growth'],peers[members[c]],1),6)*3.5/4,6),
              'available':3.5,'asof':day}
           for c,v in values.items() if len(peers[members[c]])>=30}
    summary.update(status='four_observed_revenue_years_only',eligible_revenue_issuers=len(values),
        independent_revenue_only_issuers=sum(v['independent_of_missing_profit'] for v in values.values()),
        same_market_peer_counts={m:len(p) for m,p in peers.items()})
    return parts,summary
