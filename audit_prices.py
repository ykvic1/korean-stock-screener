"""v2.2 evidence-first audit. Raw source bytes retained; no OHLC relaxation."""
import json, re, time, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd
import numpy as np
from quant_engine import read_ohlcv
from session_policy import unverified

FIELDS=['Open','High','Low','Close','Volume']
ROOT=Path('cache/v22_raw')

def anomalies(df):
    o,h,l,c,v=[df[k] for k in FIELDS]
    return {'nonpositive_price':(df[FIELDS[:4]]<=0).any(axis=1),
      'zero_ohl_zero_volume':(o.eq(0)&h.eq(0)&l.eq(0)&v.eq(0)&c.gt(0)),
      'range_error':((l>h)|(o>h)|(o<l)|(c>h)|(c<l)) & (o>0)&(h>0)&(l>0),
      'negative_volume':v.lt(0), 'missing':df[FIELDS].isna().any(axis=1),
      'large_jump_candidate':c.pct_change(fill_method=None).abs().gt(.35)}

def fetch_naver(code,asof):
    path=ROOT/f'{code}_naver.xml'
    if not path.exists():
        r=requests.get('https://fchart.stock.naver.com/sise.nhn',params={'timeframe':'day','count':6000,'requestType':0,'symbol':code},timeout=30);r.raise_for_status();path.write_bytes(r.content)
        (ROOT/f'{code}_receipt.json').write_text(json.dumps({'received_at':datetime.now(timezone.utc).isoformat(),'source':'NAVER fchart'}))
    rows=re.findall(r'<item data="(.*?)"\s*/>',path.read_bytes().decode('euc-kr',errors='replace'))
    df=pd.read_csv(StringIO('\n'.join(rows)),sep='|',names=['Date']+FIELDS,dtype={'Date':str})
    df['Date']=pd.to_datetime(df.Date,format='%Y%m%d');df=df.set_index('Date').sort_index()
    start=pd.Timestamp(asof)-pd.Timedelta(days=365*9+30)
    return df.loc[start:asof]

def audit_one(s,asof,old_missing):
    code=s['code'];row={'code':code,'name':s.get('name'),'v21_excluded':code in old_missing,'recovered':False}
    try:
        df=fetch_naver(code,asof);df.to_csv(ROOT/f'{code}_naver.csv',index_label='Date')
        receipt=ROOT/f'{code}_receipt.json'
        row['session_validation']=unverified(asof,'NAVER fchart',json.loads(receipt.read_text()).get('received_at') if receipt.exists() else None)
        flags=anomalies(df);row.update(rows=len(df),start=str(df.index.min().date()),end=str(df.index.max().date()),counts={k:int(v.sum()) for k,v in flags.items()})
        bad=flags['nonpositive_price']|flags['range_error']|flags['negative_volume']|flags['missing']
        row['examples']=[{'date':str(i.date()),**{k:float(df.loc[i,k]) for k in FIELDS}} for i in df.index[bad][:8]]
        out=Path('cache/quant_history_v22')/f'{code}.csv';df.to_csv(out,index_label='Date')
        try:
            read_ohlcv(out,asof)
            if row['end']!=asof:raise ValueError('stale')
            row['strict_valid']=True
        except Exception as e:row['strict_valid']=False;row['strict_error']=str(e)
        row['cause_status']='confirmed_numeric_pattern_only; corporate event not yet independently confirmed'
        row['raw_sha256']=hashlib.sha256((ROOT/f'{code}_naver.xml').read_bytes()).hexdigest()
    except Exception as e:row.update(strict_valid=False,error=str(e)[:200])
    print(code,row.get('rows'),row.get('counts',{}),flush=True)
    return row

def main():
    ROOT.mkdir(parents=True,exist_ok=True);Path('cache/quant_history_v22').mkdir(parents=True,exist_ok=True)
    d=json.load(open('docs/data.json'));q=json.load(open('docs/archive/quant_2026-10-14_214356.json')) if Path('docs/archive/quant_2026-10-14_214356.json').exists() else json.load(open('docs/quant.json'))
    # v2.1 missing list was capped at 50. Reconstruct all 53 from stock statuses.
    old_missing={s['code'] for s in q['stocks'] if s.get('status')=='insufficient'}
    snap=ROOT/'snapshot.json'
    if snap.exists() and json.loads(snap.read_text())['asof']!=d['date']:
        raise RuntimeError('raw cache snapshot date mismatch; use a fresh work directory')
    snap.write_text(json.dumps({'asof':d['date'],'source':'NAVER fchart'}))
    stocks=d['stocks'][:120]
    with ThreadPoolExecutor(max_workers=3) as ex: rows=list(ex.map(lambda s:audit_one(s,d['date'],old_missing),stocks))
    out={'asof':d['date'],'baseline_excluded_n':len(old_missing),'requested':len(stocks),'fetched':sum('rows' in r for r in rows),'strict_valid':sum(r['strict_valid'] for r in rows),'stocks':rows,'v3_ready':False,'regular_session_verified':0,'full_history_recovered':0}
    Path('docs/reliability.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({k:v for k,v in out.items() if k!='stocks'},ensure_ascii=False))
if __name__=='__main__':main()
