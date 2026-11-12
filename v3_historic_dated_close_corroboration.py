"""Three historic complete-market KRX vs date-pinned public close comparisons.

Research-only. Both source frames stay in memory; publish aggregate match
counts, never licensed KRX OHLCV, ticker identities or price values. Even a
perfect three-day match is NOT a 260-session adjusted/regular-price proof.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
import re

from official_probe.clients import KRXClient
from official_probe.validation import _numeric, ProbeError
from v3_krx_daily_anomaly_triage import classify_row
from v3_krx_history_collection import _snapshot_market
from v3_dated_market_roster import load_dated_listing
from v3_fdr_cache_krx_close_corroboration import compare_dated_cache

MAX_DAYS=3
SCHEMA='v3-historic-fdr-close-vs-official-krx-research-v1'


class HistoricalCloseAuditError(ValueError):
    """Safe error classes, without original price, ticker or URLs."""


def audit(days, *, client, loader, membership, validate_source=_snapshot_market):
    if (type(days) is not list or len(days)!=MAX_DAYS or
        any(type(x) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',x) for x in days)
        or len(set(days))!=len(days)):
        raise HistoricalCloseAuditError('invalid_historic_source_dates')
    try:
        parsed=[date.fromisoformat(d) for d in days]
    except ValueError:
        raise HistoricalCloseAuditError('invalid_historic_source_dates') from None
    if any(d.isoformat()!=raw for d,raw in zip(parsed,days)) or parsed!=sorted(parsed,reverse=True):
        raise HistoricalCloseAuditError('invalid_historic_source_dates')
    results=[]
    for day in days:
        frame,digest=loader(day)
        saved=membership(frame,day)
        if (type(saved) is not dict or not 1500<=len(saved)<=4000
            or any(type(code) is not str or type(row) is not dict
                   or code!=row.get('code') or row.get('market') not in ('KOSPI','KOSDAQ')
                   for code,row in saved.items())):
            raise HistoricalCloseAuditError('historic_ordinary_listing_not_verified')
        official={}
        for exchange in ('KOSPI','KOSDAQ'):
            rows,raw=client.daily_market(exchange,day)
            validate_source(rows,raw,market=exchange,asof=day)
            for row in rows:
                key=(exchange,row['ISU_CD'])
                if key in official:
                    raise HistoricalCloseAuditError('duplicate_krx_historic_identity')
                quality,_=classify_row(row)
                if quality!='valid_numeric_observation':
                    continue
                try:
                    official[key]=_numeric(row['TDD_CLSPRC'],'TDD_CLSPRC',positive=True)
                except (KeyError,ProbeError,ValueError):
                    continue
        # The existing independent join enforces source SHA/market/identity,
        # counts missing/invalid prices and emits aggregate values only.
        sample=compare_dated_cache(saved,official,frame,digest)
        results.append({'asof':day,'listed_reference_issuers':len(saved),
                        'public_source_sha256':digest,'counts':sample['counts'],
                        'full_day_close_matches':sample['regular_close_full_market_corroborated']})
    return {'schema':SCHEMA,'status':'three_prior_sessions_examined',
            'historical_sessions':results,
            'source_requests':MAX_DAYS*3, # one public cache + two KRX markets / date
            'historical_260_session_corroborated':False,
            'regular_only_full_ohlcv_verified':False,
            'corporate_actions_adjusted_verified':False,
            'krx_nxt_consolidated_volume_verified':False,
            'source_prices_or_identifiers_exported':False,
            'official_krx_originals_persisted':False,
            'investment_score_or_available_weight_changed':False}


def _members(frame,day):
    import screener
    snapshot=screener.market_snapshot(listing=frame)
    picked,eligible=screener.select_universe(
        snapshot,screener.cfg_read(),datetime.fromisoformat(day))
    if eligible!=len(picked):
        raise HistoricalCloseAuditError('historic_universe_selection_mismatch')
    return {v['code']:{'code':v['code'],'market':v['market']} for v in picked}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('dry-run','live'),default='dry-run')
    args=p.parse_args()
    days=['2026-11-10','2026-11-09','2026-11-06']
    if args.mode=='dry-run':
        print(json.dumps({'status':'dry_run','dates':days,'provider_calls':0,
                          'investment_points_added':0},sort_keys=True))
        return
    key=os.getenv('KRX_API_KEY','')
    if not key.strip():
        raise HistoricalCloseAuditError('official_krx_key_unavailable')
    report=audit(days,client=KRXClient(key),
                 loader=load_dated_listing,membership=_members)
    print(json.dumps(report,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        reason=(str(exc) if type(exc) is HistoricalCloseAuditError
                and str(exc) in ('official_krx_key_unavailable','invalid_historic_source_dates',
                                 'historic_ordinary_listing_not_verified',
                                 'historic_universe_selection_mismatch',
                                 'duplicate_krx_historic_identity')
                else 'historical_close_source_unverified')
        print(json.dumps({'status':'blocked','reason':reason,
                          'investment_points_added':0},sort_keys=True))
        raise SystemExit(1)
