"""Optional official sources. Keys are environment-only; never logged or published.
Financial data remains separate from historical similarity until PIT history validated.
"""
import json, os, time
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from datetime import datetime
from financial_metrics import metrics



def receipt_visible(row, asof):
    receipt=str(row.get('rcept_no',''))
    return len(receipt)==14 and receipt[:8].isdigit() and receipt[:8]<asof.replace('-','')


def dart_json(session, endpoint, key, **params):
    # Exceptions intentionally exclude request URL/parameters, which contain secret.
    try:
        r=session.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=30)
        r.raise_for_status(); data=r.json()
    except Exception:raise RuntimeError('OpenDART transport/JSON failure (credentials redacted)') from None
    if data.get('status') not in ('000','013'):raise RuntimeError('OpenDART status '+str(data.get('status')))
    return data if endpoint=='company.json' else data.get('list',[])


def dart_collect(stocks,asof,key,years=3):
    import requests
    session=requests.Session()
    try:
        response=session.get('https://opendart.fss.or.kr/api/corpCode.xml',params={'crtfc_key':key},timeout=40)
        response.raise_for_status()
        with ZipFile(BytesIO(response.content)) as z:root=ET.fromstring(z.read('CORPCODE.xml'))
    except Exception:raise RuntimeError('OpenDART company mapping failed (credentials redacted)') from None
    mapping={e.findtext('stock_code'):e.findtext('corp_code') for e in root.findall('list') if e.findtext('stock_code')}
    result=[];year=int(asof[:4])
    for s in stocks:
        corp=mapping.get(s['code']);reports=[];errors=[]
        if not corp:result.append({'code':s['code'],'status':'unconfirmed','reason':'corp_code unmapped'});continue
        try:
            company=dart_json(session,'company.json',key,corp_code=corp)
            industry=company.get('induty_code')
        except RuntimeError:
            industry=None
        for y in range(year-1,year-years-1,-1):
            for fs in ('CFS','OFS'):
                try:
                    rows=dart_json(session,'fnlttSinglAcntAll.json',key,corp_code=corp,bsns_year=str(y),reprt_code='11011',fs_div=fs)
                    rows=[r for r in rows if receipt_visible(r,asof)]
                    if rows:
                        reports.append({'year':y,'fs_div':fs,'accounts':rows,'metrics':metrics(rows,s.get('market_cap_eok')),'source':'OpenDART annual accounts; receipt date cutoff enforced'})
                        break
                except RuntimeError as e:errors.append(str(e));break
            time.sleep(.15)
        result.append({'code':s['code'],'status':'observed' if reports else 'unconfirmed','reports':reports,'industry_code_current':industry,'industry_pit':False,'errors':errors,'backtest_included':False})
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


def collect(source=Path('docs/data.json'),output=Path('docs/official.json')):
    d=json.loads(source.read_text());asof=d['date'];key=os.getenv('DART_API_KEY');krx=os.getenv('KRX_API_KEY')
    out={'asof':asof,'dart':{'status':'key_required','secret':'DART_API_KEY'},'krx':{'status':'key_required','secret':'KRX_API_KEY'},'backtest_included':False}
    if key:
        try:
            stocks=dart_collect(d['stocks'],asof,key)
            observed=sum(s.get('status')=='observed' for s in stocks)
            out['dart']={'status':'observed' if observed else 'unconfirmed','requested':len(stocks),'observed':observed,'stocks':stocks}
        except Exception as e:out['dart']={'status':'failed','error':str(e)}
    if krx:out['krx']=krx_indices(asof,krx)
    output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');return out

if __name__=='__main__':
    d=collect();print(json.dumps({'dart':d['dart']['status'],'krx':d['krx']['status']}))

