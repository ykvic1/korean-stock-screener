"""Receipt-bound IAS 1.81B profit allocation; never infer absent NCI as zero.

See https://www.ifrs.org/content/dam/ifrs/publications/pdf-standards/english/
2024/issued/part-a/ias-1-presentation-of-financial-statements.pdf
Only explicit annual IS/CIS profit and NCI-profit facts in one statement may
support the difference. Comprehensive income and balance-sheet NCI are not
interchangeable with profit attributable to non-controlling interests.
"""

CONTRACT='same_receipt_annual_profit_minus_nci_v1'
DERIVED_ID='derived:ifrs-profit-loss-minus-nci-profit'
TOTAL_IDS=frozenset(('ifrs-full_ProfitLoss','ifrs_ProfitLoss'))
NCI_IDS=frozenset(('ifrs-full_ProfitLossAttributableToNoncontrollingInterests',
                   'ifrs_ProfitLossAttributableToNoncontrollingInterests'))


class ReconciliationError(ValueError):
    """Fixed source error labels only."""


def verify(proof, value):
    if (type(proof) is not dict or set(proof)!={'statement','total_profit','noncontrolling_profit'}
        or proof['statement'] not in ('IS','CIS') or type(value) is not int or abs(value)>10**18):
        raise ReconciliationError('invalid_profit_reconciliation_proof')
    for name,ids in (('total_profit',TOTAL_IDS),('noncontrolling_profit',NCI_IDS)):
        fact=proof[name]
        if (type(fact) is not dict or set(fact)!={'account_id','krw'}
            or fact['account_id'] not in ids or type(fact['krw']) is not int
            or abs(fact['krw'])>10**18):
            raise ReconciliationError('invalid_profit_reconciliation_proof')
    if value!=proof['total_profit']['krw']-proof['noncontrolling_profit']['krw']:
        raise ReconciliationError('invalid_profit_reconciliation_difference')
    return True


def derive(rows, *, base, corp, amount):
    if base['fs_div']!='CFS':return None
    groups={key:{'total_profit':[],'noncontrolling_profit':[]} for key in ('IS','CIS')}
    for row in rows:
        section=row.get('sj_div');account=row.get('account_id')
        if (section not in groups or account not in TOTAL_IDS|NCI_IDS
            or row.get('account_detail','-') not in ('','-')):continue
        if (row.get('corp_code')!=corp or
            ('stock_code' in row and row['stock_code']!=base['code']) or
            str(row.get('bsns_year'))!='2025' or row.get('reprt_code')!='11011' or
            ('fs_div' in row and row['fs_div']!='CFS')):
            raise ReconciliationError('annual_profit_source_identity_mismatch')
        if row.get('currency')!='KRW':
            raise ReconciliationError('annual_profit_currency_not_krw')
        if row.get('rcept_no')!=base['annual_receipt_id']:
            raise ReconciliationError('annual_profit_receipt_not_matched')
        value=amount(row.get('thstrm_amount'))
        if value is None:raise ReconciliationError('annual_profit_amount_not_integer_krw')
        key='total_profit' if account in TOTAL_IDS else 'noncontrolling_profit'
        groups[section][key].append((account,value))
    complete=[]
    for section,facts in groups.items():
        if any(len({v for _,v in values})>1 for values in facts.values()):
            raise ReconciliationError('conflicting_official_owner_profit_accounts')
        if not all(facts.values()):continue
        proof={'statement':section,**{key:{'account_id':sorted(values)[0][0],
            'krw':values[0][1]} for key,values in facts.items()}}
        value=proof['total_profit']['krw']-proof['noncontrolling_profit']['krw']
        try:verify(proof,value)
        except ReconciliationError:
            raise ReconciliationError('annual_profit_amount_not_integer_krw') from None
        complete.append((value,proof))
    if len({(p['total_profit']['krw'],p['noncontrolling_profit']['krw']) for _,p in complete})>1:
        raise ReconciliationError('conflicting_official_owner_profit_accounts')
    return complete[0] if complete else None


def pending(expected, retained):
    return {code for code in expected if code not in retained or
        (retained[code]['status']=='missing_standard_account' and
         retained[code]['fs_div']=='CFS' and
         retained[code].get('profit_recheck_contract')!=CONTRACT)}


def checkpoint_count(source):
    records=source.get('records',{})
    return len(records)+sum(r.get('profit_recheck_contract')==CONTRACT for r in records.values())


def should_resume(summary, before, after):
    """Queue only finite, error-free source progress, never a quota retry loop."""
    calls=summary.get('provider_calls')
    remaining=summary.get('reconciliation_pending_issuers')
    return (summary.get('status')=='official_owner_profit_source_only' and
        type(calls) is int and calls>0 and type(remaining) is int and remaining>0 and
        type(before) is int and type(after) is int and 0<=before<after)
