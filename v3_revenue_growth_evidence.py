"""Four actual revenue years do not require four operating-profit values."""
import math

from v3_dart_bulk_history import SCHEMA, _day, _valid_receipt, _best_income_window, _midrank
from v3_dart_revenue_2022 import expected_sources, verified_prior


def window(record, fiscal, asof, revenue_2022_source=None):
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
        receipts=[s.get('receipt_id') for s in statements]
        supplemented=False
        if (revenues[0] is None and revenue_2022_source is not None
            and revenue_2022_source.get('status')=='observed'
            and revenue_2022_source.get('scope')==scope
            and revenue_2022_source.get('receipt_id')==statements[0].get('receipt_id')
            and _valid_receipt(revenue_2022_source.get('source_receipt_id'),fiscal[0],cutoff)
            and type(revenue_2022_source.get('revenue_krw')) is int
            and 0<revenue_2022_source['revenue_krw']<=10**18):
            revenues[0]=revenue_2022_source['revenue_krw']
            receipts[0]=revenue_2022_source['source_receipt_id']
            supplemented=True
        if not all(type(v) is int and 0<v<=10**18 and
                   _valid_receipt(receipt,y,cutoff)
                   for v,receipt,y in zip(revenues,receipts,fiscal)):continue
        growth=100*((revenues[-1]/revenues[0])**(1/3)-1)
        if math.isfinite(growth) and abs(growth)<10**7:
            return {'growth':round(growth,6),'scope':scope,
                    'independent_of_missing_profit':full is None,
                    'official_2022_supplement':supplemented}
    return None


def derive(history,catalog,official_2022_revenue=None):
    """Rebuild the same-market revenue peer cohort from saved actual values."""
    day=catalog['date']
    summary={'provider_calls':0,'eligible_revenue_issuers':0,'independent_revenue_only_issuers':0,
             'historical_PIT_certified':False,'growth_horizon_years':3,'required_annual_observations':4}
    if (history.get('schema')!=SCHEMA or history.get('asof')!=day or
        history.get('historical_PIT_source_certified') is not False):
        return {},{**summary,'status':'waiting_current_annual_revenue_source'}
    source_rows={}
    if official_2022_revenue is not None:
        # Never let a saved supplement bypass the original issuer, scope,
        # fiscal-year or annual-receipt identity rules.
        source_rows=verified_prior(official_2022_revenue,
            expected_sources(catalog,history),day)
    values={}
    members={r['code']:r['market'] for r in catalog['rows']}
    for code,record in history.get('records',{}).items():
        if code not in members:continue
        observation=window(record,history.get('fiscal_years'),day,
                           source_rows.get(code))
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
        verified_2022_supplement_issuers=sum(v['official_2022_supplement'] for v in values.values()),
        same_market_peer_counts={m:len(p) for m,p in peers.items()})
    return parts,summary
