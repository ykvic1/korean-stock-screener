"""Conservative annual statement mapping; no inferred missing accounts."""
from collections import defaultdict

def amount(value):
    try:return int(str(value).replace(',','').replace(' ',''))
    except (TypeError,ValueError):return None

def metrics(rows,market_cap_eok=None):
    ids={'revenue':['ifrs-full_Revenue','ifrs_Revenue'],
         'operating_income':['dart_OperatingIncomeLoss'],
         'operating_cashflow':['ifrs-full_CashFlowsFromUsedInOperatingActivities','ifrs_CashFlowsFromUsedInOperatingActivities'],
         'liabilities':['ifrs-full_Liabilities','ifrs_Liabilities'],
         'equity':['ifrs-full_Equity','ifrs_Equity'],
         'owners_equity':['ifrs-full_EquityAttributableToOwnersOfParent','ifrs_EquityAttributableToOwnersOfParent'],
         'owners_profit':['ifrs-full_ProfitLossAttributableToOwnersOfParent','ifrs_ProfitLossAttributableToOwnersOfParent']}
    result={};provenance={}
    for key,aliases in ids.items():
        candidates=[r for r in rows if r.get('account_id') in aliases and r.get('currency')=='KRW' and r.get('account_detail','-') in ('-','')]
        vals={amount(r.get('thstrm_amount')) for r in candidates};vals.discard(None)
        result[key]=next(iter(vals)) if len(vals)==1 else None
        provenance[key]=[{'account_id':r.get('account_id'),'receipt':r.get('rcept_no'),'currency':r.get('currency')} for r in candidates]
    def ratio(a,b,factor=1):return round(a/b*factor,4) if a is not None and b is not None and b>0 else None
    cap=market_cap_eok*100000000 if isinstance(market_cap_eok,(int,float)) else None
    result.update(operating_margin_pct=ratio(result['operating_income'],result['revenue'],100),debt_to_equity_pct=ratio(result['liabilities'],result['equity'],100),sales_to_market_cap=ratio(result['revenue'],cap),pbr_annual_reference=ratio(cap,result['owners_equity']),per_annual_reference=ratio(cap,result['owners_profit']))
    return {'values':result,'account_provenance':provenance,'valuation_basis':'current market cap / latest available annual owners equity or profit; not TTM, not historical PIT price','missing_policy':'null; ambiguous or non-KRW accounts not estimated'}
