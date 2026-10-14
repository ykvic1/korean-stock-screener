"""Independent Yahoo/NAVER comparison; quarantine, never fabricate OHLC."""
import json, hashlib, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
import requests
from audit_prices import FIELDS, anomalies
from quant_engine import read_ohlcv


def yahoo(code,market,asof,root):
    suffix={'KOSPI':'.KS','KOSDAQ':'.KQ'}.get(market)
    if not suffix:raise ValueError('unknown exchange: no guessed suffix')
    p=root/f'{code}_yahoo.json'
    if not p.exists():
        start=int((pd.Timestamp(asof,tz='UTC')-pd.Timedelta(days=365*9+30)).timestamp())
        end=int((pd.Timestamp(asof,tz='UTC')+pd.Timedelta(days=1)).timestamp())
        r=requests.get(f'https://query2.finance.yahoo.com/v8/finance/chart/{code}{suffix}',params={'period1':start,'period2':end,'interval':'1d','events':'splits,div','includeAdjustedClose':'true'},headers={'User-Agent':'Mozilla/5.0'},timeout=25)
        r.raise_for_status();body=r.json()
        if not body.get('chart',{}).get('result'):raise ValueError('empty yahoo')
        p.write_text(json.dumps(body))
    body=json.loads(p.read_text())['chart']['result'][0]
    if body['meta']['symbol']!=code+suffix:raise ValueError('symbol mismatch')
    idx=pd.to_datetime(body['timestamp'],unit='s',utc=True).tz_convert('Asia/Seoul').normalize().tz_localize(None)
    df=pd.DataFrame(body['indicators']['quote'][0],index=idx).rename(columns=str.title)[FIELDS].loc[:asof]
    # Adj Close is NOT substituted for Close; mixing it with OHLC is prohibited.
    return df,body.get('events',{}),hashlib.sha256(p.read_bytes()).hexdigest()


def process(row,market,asof):
    row=dict(row);code=row['code'];root=Path('cache/v22_raw');out=Path('cache/quant_history_v22')/f'{code}.csv'
    if 'rows' not in row:return row
    df=pd.read_csv(root/f'{code}_naver.csv',index_col='Date',parse_dates=True)
    flags=anomalies(df);bad=flags['nonpositive_price']|flags['range_error']|flags['negative_volume']|flags['missing']
    row['original_strict_valid']=row.get('original_strict_valid',row['strict_valid']);row['quarantined_rows']=0
    if bad.any():
        last=int(np.flatnonzero(bad.to_numpy())[-1]);segment=df.iloc[last+1:].copy()
        row['quarantined_rows']=last+1;row['last_bad_date']=str(df.index[last].date())
    else:segment=df.copy()
    row['usable_segment_rows']=len(segment)
    row['repair_method']='retain only contiguous strict-valid suffix; no rows filled/clamped or stitched'
    row['cause_categories']=[]
    if len(df)<650:row['cause_categories'].append('short_history')
    if flags['zero_ohl_zero_volume'].any():row['cause_categories'].append('zero_OHL_zero_volume_no_trade_candidate')
    if flags['range_error'].any():
        delta=np.maximum(df.Close-df.High,df.Low-df.Close)[flags['range_error']]
        row['range_error_max_won']=float(delta.max());row['cause_categories'].append('OHLC_rounding_candidate' if delta.max()<=1 else 'OHLC_scale_or_provider_error')
    if flags['large_jump_candidate'].any():row['cause_categories'].append('corporate_action_or_price_jump_candidate')
    row['cross_validation']={'status':'unconfirmed'}
    try:
        yd,events,sha=yahoo(code,market,asof,root)
        row['yahoo_events']=events;row['yahoo_raw_sha256']=sha
        shared=segment.index.intersection(yd.index);a=segment.loc[shared];b=yd.loc[shared]
        finite=b[FIELDS].notna().all(axis=1)&(b[FIELDS[:4]]>0).all(axis=1)
        recent=shared[-252:];ar=a.loc[recent];br=b.loc[recent]
        # Small currency/float rounding tolerance only for source COMPARISON,
        # never for accepting invalid OHLC or changing source observations.
        tol=np.maximum(1.,ar[FIELDS[:4]].to_numpy()*.001)
        agreement=np.all(np.abs(ar[FIELDS[:4]].to_numpy()-br[FIELDS[:4]].to_numpy())<=tol,axis=1)
        row['cross_validation']={'status':'matched_recent' if len(recent)>=200 and agreement.all() else 'mismatch_or_insufficient','compared_sessions':len(recent),'matched_sessions':int(agreement.sum()),'price_tolerance':'max(1 KRW,0.1%) for comparison only','missing_naver_sessions_vs_yahoo':int(len(yd.loc[segment.index.min():].index.difference(segment.index))) if len(segment) else None,'full_overlap_sessions':len(shared),'yahoo_null_sessions':int((~finite).sum()),'recent_volume_equal_sessions':int(np.isclose(ar.Volume,br.Volume,rtol=0,atol=0).sum())}
        row['cross_validation']['mismatch_examples']=[{'date':str(dt.date()),'naver':{k:float(ar.loc[dt,k]) for k in FIELDS},'yahoo':{k:(float(br.loc[dt,k]) if pd.notna(br.loc[dt,k]) else None) for k in FIELDS}} for dt in recent[~agreement][:5]]
        row['cross_validation']['field_mismatch_n']={k:int((~np.isclose(ar[k],br[k],atol=1.,rtol=.001,equal_nan=False)).sum()) for k in FIELDS[:4]}
        # Count price-level adjustment discrepancies, without rescaling either source.
        ratio=(a.Close/b.Close).replace([np.inf,-np.inf],np.nan).dropna()
        row['cross_validation']['close_ratio_quantiles']=[round(float(x),6) for x in ratio.quantile([.1,.5,.9])] if len(ratio) else []
        nontrade_dates=df.index[flags['zero_ohl_zero_volume']].intersection(yd.index)
        row['zero_volume_corroborated_sessions']=int(yd.loc[nontrade_dates,'Volume'].eq(0).sum())
        row['corporate_action_status']='provider event metadata available; merger/suspension official notices still unverified'
    except Exception as e:row['cross_validation']={'status':'fetch_failed','error':str(e).split('?')[0][:150]}
    segment.to_csv(out,index_label='Date')
    try:
        valid=read_ohlcv(out,asof)
        if str(valid.index[-1].date())!=asof:raise ValueError('stale')
        row['strict_valid']=True;row.pop('strict_error',None)
    except Exception as e:row['strict_valid']=False;row['strict_error']=str(e)
    row['recovered']=bool(row['v21_excluded'] and row['strict_valid'])
    row['independently_matched']=bool(row['strict_valid'] and row['cross_validation']['status']=='matched_recent')
    print(code,row['usable_segment_rows'],row['cross_validation']['status'],flush=True)
    return row


def main():
    p=Path('docs/reliability.json');audit=json.loads(p.read_text());d=json.load(open('docs/data.json'));markets={s['code']:s.get('market') for s in d['stocks']}
    with ThreadPoolExecutor(max_workers=3) as ex:rows=list(ex.map(lambda r:process(r,markets[r['code']],d['date']),audit['stocks']))
    audit.update(stocks=rows,strict_valid=sum(r['strict_valid'] for r in rows),recovered=sum(r['recovered'] for r in rows),independently_matched=sum(r.get('independently_matched',False) for r in rows),original_strict_valid=sum(r.get('original_strict_valid',False) for r in rows))
    audit['v3_ready']=False;audit['v3_blockers']=['survivorship/delistings unresolved','corporate action and suspension official history incomplete','point-in-time finance/industry incomplete','out-of-sample predictive validation incomplete']
    p.write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False))
    print({k:v for k,v in audit.items() if k!='stocks'})
if __name__=='__main__':main()
