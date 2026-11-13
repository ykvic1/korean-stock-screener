"""Read-only reconciliation of already published FY2025 DART income evidence.

Counts actual same-receipt/same-scope missing annual-book values that could
repair the four-year major-account research ledger. No raw amounts, issuer IDs,
provider calls, backfilled values or scoring points are written or printed.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

from v3_dart_bulk_history import _day, _valid_receipt

OUT_FIELDS=('revenue','operating_income')


class IncomeOverlapError(ValueError):
    pass


def _missing_part(row, area, name, maximum):
    parts=row.get('parts',{}).get(area,{})
    part=parts.get(name)
    if part is None:return True
    available=part[1] if type(part) is list else part.get('available') if type(part) is dict else None
    if type(available) not in (int,float) or not 0<=available<=maximum:
        raise IncomeOverlapError('invalid_source_factor_weight')
    return available < maximum-0.00001


def _valid_book(annual, fiscal, cutoff):
    return (type(annual) is dict and annual.get('year')==fiscal and
        annual.get('fs_div') in ('CFS','OFS') and
        annual.get('currency')=='KRW' and
        _valid_receipt(annual.get('receipt_id'),fiscal,cutoff) and
        type(annual.get('values')) is dict)



def _observed(years,scope,year,field,cutoff):
    item=years.get(str(year),{}).get(scope,{})
    if (type(item) is not dict or
        not _valid_receipt(item.get('receipt_id'),year,cutoff)):
        return None
    values=item.get('values',{})
    if type(values) is not dict:return None
    value=values.get(field)
    return value if (type(value) is int and abs(value)<=10**18
                     and (field!='revenue' or value>0)) else None


def _best_scope(years,required,cutoff):
    """Prefer the scope with the most real annual observations; ties CFS."""
    return min(('CFS','OFS'),key=lambda scope:-sum(
        _observed(years,scope,year,field,cutoff) is not None
        for year,field in required))


def analyze(catalog,book,history,report):
    day=catalog.get('date')
    if (type(day) is not str or history.get('asof')!=day or
        book.get('asof')!=day or report.get('asof')!=day or
        book.get('market_listed_total')!=catalog.get('listed_total') or
        history.get('listed_total')!=catalog.get('listed_total') or
        history.get('historical_PIT_source_certified') is not False or
        report.get('investment_score_certified') is not False or
        history.get('fiscal_years')!=[2022,2023,2024,2025] or
        type(history.get('records')) is not dict or
        type(book.get('records')) is not list or
        type(report.get('rows')) is not dict or
        type(catalog.get('rows')) is not list or
        len(catalog['rows'])!=catalog['listed_total'] or
        len(report['rows'])!=catalog['listed_total']):
        raise IncomeOverlapError('unaligned_saved_annual_income_sources')
    cutoff=_day(day)
    fiscal=cutoff.year-1
    members={m['code']:m for m in catalog['rows']}
    annual={}
    for row in book['records']:
        if type(row) is not dict:
            raise IncomeOverlapError('invalid_saved_annual_book_row')
        code=row.get('code')
        if code not in members or row.get('status')!='observed':
            continue
        if code in annual:
            raise IncomeOverlapError('duplicate_saved_annual_book_issuer')
        annual[code]=row.get('annual')
    counts=Counter()
    causes=Counter()
    under=Counter()
    historical=Counter()
    historical_below=Counter()
    for code,member in members.items():
        score=report['rows'][code]
        missing_margin=_missing_part(score,'financial','operating_growth_margin',3)
        missing_growth=_missing_part(score,'financial','revenue_growth',3.5)
        if not (missing_margin or missing_growth):
            continue
        counts['issuers_with_missing_income_factor']+=1
        if missing_margin:counts['missing_operating_margin_issuers']+=1
        if missing_growth:counts['missing_revenue_growth_issuers']+=1
        below=score.get('available',0)<90
        if below:under['issuers_with_missing_income_factor']+=1
        old=history['records'].get(code,{})
        years=old.get('years',{}) if type(old) is dict else {}
        # One diagnostic per genuinely missing source year/field, scoped to
        # each independently missing R1d factor, not a made-up replacement.
        required=[]
        if missing_margin:
            required.extend((year,field) for year in (2023,2024,2025)
                            for field in ('revenue','operating_income'))
        if missing_growth:
            required.extend((year,'revenue') for year in (2022,2023,2024,2025))
        scope=_best_scope(years,set(required),cutoff)
        for year,field in sorted(set(required)):
            if _observed(years,scope,year,field,cutoff) is None:
                historical[f'missing_{year}_{field}']+=1
                if below:historical_below[f'missing_{year}_{field}']+=1
        # Alternative FY2024 source: its reported FY2023 comparative may
        # fill exactly one missing earlier-year fact, IF the actual FY2024
        # current-year and other years' facts are independently present.
        if missing_margin and all(
            _observed(years,scope,year,'revenue',cutoff) is not None
            for year in (2023,2024,2025)) and all(
            _observed(years,scope,year,'operating_income',cutoff) is not None
            for year in (2024,2025)) and (
            _observed(years,scope,2023,'operating_income',cutoff) is None):
            historical['fy2024_original_2023_operating_comparative_candidate']+=1
            if below:
                historical_below['fy2024_original_2023_operating_comparative_candidate']+=1
        # The existing FY2025 current-year revenue is an exact anchor for
        # independently observed 2023/24 comparatives in that same filing.
        if missing_growth and all(
            _observed(years,scope,year,'revenue',cutoff) is not None
            for year in (2022,2025)) and any(
            _observed(years,scope,year,'revenue',cutoff) is None
            for year in (2023,2024)):
            historical['fy2025_original_2023_2024_revenue_comparative_candidate']+=1
            if below:
                historical_below['fy2025_original_2023_2024_revenue_comparative_candidate']+=1
        # The FY2024 annual current-year value can anchor its explicit
        # 2022 and 2023 reported comparatives. This is not evidence that
        # either comparative exists until the actual filing is examined.
        if missing_growth and all(
            _observed(years,scope,year,'revenue',cutoff) is not None
            for year in (2024,2025)) and any(
            _observed(years,scope,year,'revenue',cutoff) is None
            for year in (2022,2023)):
            historical['fy2024_original_2022_2023_revenue_comparative_candidate']+=1
            if below:
                historical_below['fy2024_original_2022_2023_revenue_comparative_candidate']+=1
        source=annual.get(code)
        if not _valid_book(source,fiscal,cutoff):
            causes['no_current_year_annual_book_observation']+=1
            continue
        scopes=years.get(str(fiscal),{})
        matched=scopes.get(source['fs_div'],{}) if type(scopes) is dict else {}
        if (matched.get('receipt_id')!=source['receipt_id'] or
            not _valid_receipt(matched.get('receipt_id'),fiscal,cutoff)):
            causes['missing_or_different_current_year_receipt_scope']+=1
            continue
        vals=matched.get('values')
        if type(vals) is not dict:
            causes['missing_current_year_ledger_values']+=1
            continue
        counts['same_receipt_same_scope_current_year']+=1
        ambiguous=matched.get('ambiguous',[])
        if type(ambiguous) is not list:
            raise IncomeOverlapError('malformed_current_year_ambiguity_marker')
        book_values=source['values']
        repaired={}
        conflict=False
        for name in OUT_FIELDS:
            observed=book_values.get(name)
            old_value=vals.get(name)
            if (type(observed) is not int or
                abs(observed)>10**18 or (name=='revenue' and observed<=0) or
                name in ambiguous):
                continue
            if old_value is not None and old_value!=observed:
                conflict=True
                break
            if old_value is None:
                repaired[name]=observed
        if conflict:
            causes['current_year_amount_conflict']+=1
            continue
        if not repaired:
            causes['no_new_same_receipt_income_values']+=1
            continue
        counts['new_2025_income_source_issuers']+=1
        if below:under['new_2025_income_source_issuers']+=1
        for key in repaired:
            counts['new_2025_'+key+'_issuers']+=1
            if below:under['new_2025_'+key+'_issuers']+=1
        # Count only cases where one safely sourced repair would complete
        # the existing *same-scope* annual formula; no score is credited here.
        for task, fiscal_years, fields in (
                ('three_year_operating_margin',(2023,2024,2025),OUT_FIELDS),
                ('four_year_revenue_growth',(2022,2023,2024,2025),('revenue',))):
            ready=True
            for year in fiscal_years:
                part=(years.get(str(year),{}).get(source['fs_div'],{})
                      if type(years.get(str(year),{})) is dict else {})
                observations=part.get('values',{})
                if type(observations) is not dict:
                    ready=False;break
                if not _valid_receipt(part.get('receipt_id'),year,cutoff):
                    ready=False;break
                for field in fields:
                    value=(repaired.get(field,observations.get(field)) if year==fiscal
                           else observations.get(field))
                    if type(value) is not int or abs(value)>10**18 or (
                        field=='revenue' and value<=0):
                        ready=False;break
                if not ready:break
            if ready:
                counts['newly_completable_'+task]+=1
                if below:
                    under['newly_completable_'+task]+=1
                    potential=3 if task=='three_year_operating_margin' else 3.5
                    if score['available']+potential>=90:
                        under['possible_single_factor_threshold_cross_'+task]+=1
        causes['safe_same_receipt_repair_candidate']+=1
    return {'schema':'v3-saved-annual-income-overlap-audit-v1',
            'asof':day,'listed_total':len(members),
            'counts':dict(sorted(counts.items())),
            'below90_counts':dict(sorted(under.items())),
            'source_causes':dict(sorted(causes.items())),
            'historical_missing_year_fields':dict(sorted(historical.items())),
            'below90_historical_missing_year_fields':dict(sorted(historical_below.items())),
            'provider_calls':0,'points_added':0,'score_changed':False,
            'investment_score_certified':False,
            'historical_point_in_time_certified':False,
            'strict_same_receipt_scope_krw_required':True}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docs',type=Path,default=Path('docs'))
    args=p.parse_args(argv)
    root=args.docs
    files=['v22-market-catalog.json','v3-dart-book-reference.json',
           'v3-dart-bulk-history.json','v3-operational-research-scores.json']
    result=analyze(*(json.loads((root/name).read_text()) for name in files))
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
