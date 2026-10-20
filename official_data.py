"""Optional official sources. Keys are environment-only; never logged or published.
Financial data remains separate from historical similarity until PIT history validated.
"""
import json, os, time
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from datetime import datetime


class DartStopCollectionError(Exception):
    """Stop this batch on account-limit, invalid-key, or service-wide DART errors.

    Deliberately not a RuntimeError: the per-symbol missing-data handler must
    not swallow this and continue sending additional paid/provider requests.
    """


def receipt_visible(row, asof):
    receipt=str(row.get('rcept_no',''))
    return len(receipt)==14 and receipt[:8].isdigit() and receipt[:8]<=asof.replace('-','')


def dart_json(session, endpoint, key, **params):
    # Exceptions intentionally exclude request URL/parameters, which contain secret.
    try:
        r=session.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=30)
        r.raise_for_status(); data=r.json()
    except Exception:raise RuntimeError('OpenDART transport/JSON failure (credentials redacted)') from None
    status = data.get('status')
    # HTTP 200 is not proof of an accepted call. Never loop across every issuer
    # after a provider-wide throttle/account/service failure.
    if status in ('010', '011', '012', '020', '800', '900', '901'):
        raise DartStopCollectionError('OpenDART stop status '+str(status)+' (credentials redacted)')
    if status not in ('000','013'):raise RuntimeError('OpenDART status '+str(status))
    return data.get('list',[])


def dart_collect(stocks, asof, key, years=3, budget_seconds=360):
    """Latest historical annual reported values for *bounded active candidates*.

    Keep a 6-minute optional enrichment budget so a slow DART endpoint cannot
    postpone publishing core stock/quant decisions. Never publish raw filings.
    """
    import requests
    from financial_metrics import metrics
    if not isinstance(stocks, list) or not 1 <= len(stocks) <= 20:
        raise ValueError("bounded_research_targets_required")
    # Default existing shortlist still uses 3 years, rolling weekday
    # annual reference still uses 1 year. A separate MANUAL-only bounded
    # four-year pilot may request 4 annual fiscal reports, never more.
    if not 1 <= years <= 4 or not 30 <= budget_seconds <= 900:
        raise ValueError("invalid_financial_probe_budget")
    session=requests.Session()
    started=time.monotonic()
    try:
        response=session.get('https://opendart.fss.or.kr/api/corpCode.xml',
                             params={'crtfc_key':key},timeout=40)
        response.raise_for_status()
        with ZipFile(BytesIO(response.content)) as z:
            root=ET.fromstring(z.read('CORPCODE.xml'))
    except Exception:
        raise RuntimeError('OpenDART company mapping failed (credentials redacted)') from None
    mapping={e.findtext('stock_code'):e.findtext('corp_code')
             for e in root.findall('list') if e.findtext('stock_code')}
    result=[]
    year=int(asof[:4])
    for stock in stocks:
        code=stock['code']
        if time.monotonic()-started>=budget_seconds:
            result.append({'code':code,'status':'deferred','reason':'time_budget'})
            continue
        corp=mapping.get(code)
        if not corp:
            result.append({'code':code,'status':'unconfirmed',
                           'reason':'corp_code_not_mapped'})
            continue
        records=[]
        errors=[]
        for y in range(year-1,year-years-1,-1):
            if time.monotonic()-started>=budget_seconds:
                errors.append('time_budget_exceeded')
                break
            for fs in ('CFS','OFS'):
                if time.monotonic()-started>=budget_seconds:
                    errors.append('time_budget_exceeded')
                    break
                try:
                    annual=dart_json(session,'fnlttSinglAcntAll.json',key,
                       corp_code=corp,bsns_year=str(y),reprt_code='11011',
                       fs_div=fs)
                    annual=[r for r in annual if receipt_visible(r,asof)]
                    if not annual:
                        continue
                    observed=metrics(annual,stock.get('market_cap_eok'))
                    # Full annual account rows and filing payloads remain
                    # ephemeral. Export only derived observed summary + basis.
                    values={k:v for k,v in observed['values'].items()
                            if type(v) in (int,float) or v is None}
                    receipts=sorted(set(
                        str(r.get('rcept_no')) for r in annual
                        if receipt_visible(r,asof)))
                    records.append({
                        'year':y,'fs_div':fs,'values':values,
                        'receipt_id':receipts[0] if receipts else None,
                        'source':'OpenDART fnlttSinglAcntAll annual',
                        'currency':'KRW',
                        'revision_history_verified':False,
                        'point_in_time_historical_price_verified':False,
                    })
                    break
                except RuntimeError as exc:
                    errors.append(str(exc)[:90])
                    break
            time.sleep(.08)
        result.append({
            'code':code,
            'status':'observed' if records else
                     'deferred' if 'time_budget_exceeded' in errors else
                     'unconfirmed',
            'reports':records,
            'error_codes':errors[:3],
            'backtest_included':False,
        })
    return result

def krx_indices(asof,key):
    import requests
    result={};errors=[]
    for api in ('kospi_dd_trd','kosdaq_dd_trd'):
        try:
            r=requests.get('https://data-dbg.krx.co.kr/svc/apis/idx/'+api,headers={'AUTH_KEY':key},params={'basDd':asof.replace('-','')},timeout=30)
            r.raise_for_status();body=r.json();rows=body.get('OutBlock_1')
            if not isinstance(rows,list) or not rows:raise ValueError('Empty or unexpected KRX index schema')
            result[api]=rows
        except Exception:errors.append(api+': official index request/schema failed (credentials redacted)')
    return {'status':'observed' if result else 'unconfirmed','asof':asof,'series':result,'errors':errors,'membership_status':'historical stock-sector membership not implemented'}


def collect(source=Path('docs/data.json'),output=Path('docs/official.json'),
            shortlist=Path('docs/recommendations.json'),max_stocks=20):
    """Batched source-observed references without blocking daily 2,000+ screen.

    Prioritize user's actual deep recommendations before other scored peers.
    DART findings are limited, and never used as an invented v3 score.
    """
    from quant_priority_universe import prioritized_stocks,optional_recommendations
    d=json.loads(source.read_text(encoding='utf-8'))
    if d.get('status')!='success' or d.get('version')!='2.0':
        raise ValueError('no_current_price_screen_for_optional_official_enrichment')
    if type(max_stocks) is not int or not 20<=max_stocks<=20:
        raise ValueError('official_detail_budget_20_stocks_max')
    asof=d['date']
    key=os.getenv('DART_API_KEY')
    krx=os.getenv('KRX_API_KEY')
    chosen=prioritized_stocks(d,optional_recommendations(shortlist),max_stocks)
    out={'asof':asof,
         'dart':{'status':'key_required','secret':'DART_API_KEY',
                 'requested':len(chosen),'universe':len(d['stocks'])},
         'krx':{'status':'key_required','secret':'KRX_API_KEY'},
         'backtest_included':False,
         'financial_reference_not_automatically_scored':True}
    if key:
        try:
            observed=dart_collect(chosen,asof,key,years=3,budget_seconds=360)
            valid=sum(x.get('status')=='observed' for x in observed)
            missing=sum(x.get('status')=='unconfirmed' for x in observed)
            deferred=sum(x.get('status')=='deferred' for x in observed)
            out['dart']={
                'status':'observed' if valid else 'unconfirmed',
                'universe':len(d['stocks']),'requested':len(chosen),
                'observed':valid,'unconfirmed':missing,'deferred':deferred,
                'source':'OpenDART reported annual accounts (research reference)',
                'audit_basis':'filing receipt not newer than source day; historical revisions not verified',
                'stocks':observed,
            }
        except Exception:
            out['dart']={'status':'failed','requested':len(chosen),
                         'error':'optional_DART_enrichment_failed_redacted'}
    if krx:
        out['krx']=krx_indices(asof,krx)
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix('.tmp')
    tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),
                   encoding='utf-8')
    tmp.replace(output)
    return out

if __name__=='__main__':
    d=collect();print(json.dumps({'dart':d['dart']['status'],'krx':d['krx']['status']}))
