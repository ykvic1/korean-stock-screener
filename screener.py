"""Korean stock multi-factor screening v2.0 — research only, NOT an order system.
Provides evidence/coverage per factor and explicitly labels unverified sources.
"""
import argparse
import json
import logging
import math
import os
import shutil
import sys
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import numpy as np
import FinanceDataReader as fdr
from pykrx import stock
import requests
from bs4 import BeautifulSoup
import atexit
from flow_collection import FlowCollector, parse_naver_flow_table

_NAVER_BROWSER = None

def naver_rendered_table(url, pages=3):
    global _NAVER_BROWSER
    if _NAVER_BROWSER is None:
        from playwright.sync_api import sync_playwright
        runtime=sync_playwright().start()
        browser=runtime.chromium.launch(headless=True)
        page=browser.new_page()
        _NAVER_BROWSER=(runtime,browser,page)
        def close_browser():
            browser.close();runtime.stop()
        atexit.register(close_browser)
    page=_NAVER_BROWSER[2]
    page.goto(url,wait_until="domcontentloaded",timeout=30000)
    # The investor table moved from /price to /tradingTrend. It is paged10rows.
    from playwright.sync_api import expect
    toggle=page.locator('[class*="TraderTrend_trader-trend-toolbar-exchange"]')
    toggle.wait_for(state='visible',timeout=20000)
    for _ in range(3):
        if toggle.get_by_text('KRX',exact=True).count():break
        toggle.click()
    expect(toggle.get_by_text('KRX',exact=True)).to_be_visible()
    table=page.locator('table').filter(has=page.get_by_role('columnheader',name='외국인',exact=True))
    table.locator('tbody tr').first.wait_for(state='visible',timeout=20000)
    chunks=[]
    for number in range(1,pages+1):
        if number>1:
            previous=table.locator('tbody tr').first.inner_text()
            button=page.get_by_role('button',name=str(number),exact=True)
            if not button.count() or not button.is_enabled():break
            button.click()
            expect(table.locator('tbody tr').first).not_to_have_text(previous,timeout=20000)
        expect(toggle.get_by_text('KRX',exact=True)).to_be_visible()
        chunks.append(table.evaluate('element => element.outerHTML'))
    return ''.join(chunks)



class InsufficientHistory(ValueError):
    pass

ROOT = Path(__file__).resolve().parent
SITE, OUT, LOG = ROOT / 'docs', ROOT / 'reports', ROOT / 'logs'
DEFAULT = {
    'min_history_bars':160, 'historical_days':1100,
    'request_sleep_seconds':0.18,
    'flow_days':20, 'history_time_budget_seconds':2100,
    'flow_time_budget_seconds':420,
    'max_data_age_days':6, 'preferred_pbr_max':1.5,
    'max_stop_distance_pct':15,
    'enable_optional_company_listing':True
}
WEIGHTS = {'거래량·OBV':20, '일봉 구조':20, '주봉·추세':15,
           '수급':20, '시장 대비 강도':10, 'PBR':5, '실적·현금흐름':5, '위험·손익비':5}


def cfg_read():
    file = ROOT/'config.json'
    if not file.exists(): return DEFAULT.copy()
    return {**DEFAULT, **json.loads(file.read_text(encoding='utf-8'))}


def finite(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError): return None


def pct(a,b):
    return (100*(a/b-1)) if a is not None and b is not None and b>0 else None


def round_or_none(x,n=1):
    v=finite(x)
    return round(v,n) if v is not None else None


def market_snapshot():
    listing = fdr.StockListing('KRX-MARCAP')
    needed={'Code','Name','Market','Close','Volume','Amount','Marcap'}
    if listing is None or len(listing)<1000 or not needed.issubset(set(listing.columns)):
        raise RuntimeError('KRX-MARCAP 전체 시장 조회 실패/데이터 구조 변경')
    found={}
    for _,r in listing.iterrows():
        code=str(r['Code']).zfill(6)
        name=str(r['Name']).strip()
        market=str(r['Market']).upper()
        if not (code.isdigit() and len(code)==6 and market in ('KOSPI','KOSDAQ')):continue
        if any(t in name.upper() for t in ('ETF','ETN','SPAC','스팩','인버스','레버리지')) or name.endswith(('우','우B')):continue
        vals=[finite(r[t]) for t in ('Close','Volume','Amount','Marcap')]
        if any(v is None for v in vals):continue
        px,volume,amount,marcap=vals
        # No market-cap or trading-value cutoff: thinly traded and small-cap
        # shares stay observable, with liquidity/financial risks flagged later.
        # Exclude only invalid quotes/identifiers, not genuine zero-turnover days.
        if px<=0 or marcap<=0 or volume<0 or amount<0:continue
        found[code]={'code':code,'name':name,'market':market,'price':px,'snapshot_volume':volume,
                     'trading_eok':amount/1e8,'market_cap_eok':marcap/1e8,
                     'sector':None,'pbr':None,'valuation_source':None}
    if len(found)<800:raise RuntimeError(f'정상 주식 수가 비정상적으로 적음: {len(found)}')
    return found


def optional_listings(rows):
    """Supplement if FDR exposes validated sector/PBR columns; never invent."""
    successes=[]
    for name in ('KRX','KRX-DESC'):
        try:
            df=fdr.StockListing(name)
            if df is not None and 'Symbol' in df.columns and 'Code' not in df.columns:df=df.rename(columns={'Symbol':'Code'})
            if df is None or df.empty or 'Code' not in df.columns:continue
            sect_col=next((c for c in ('Sector','Industry','업종','업종명') if c in df.columns),None)
            pbr_col=next((c for c in ('PBR','Pbr','pbr') if c in df.columns),None)
            for _,t in df.iterrows():
                code=str(t['Code']).zfill(6)
                if code not in rows:continue
                if sect_col and not rows[code]['sector']:
                    s=str(t[sect_col]).strip()
                    if s and s.lower() not in ('nan','none','-') and not s.endswith('기업부'):rows[code]['sector']=s
                if pbr_col and rows[code]['pbr'] is None:
                    p=finite(t[pbr_col])
                    if p is not None and 0<p<100:
                        rows[code]['pbr']=p;rows[code]['valuation_source']=f'FDR {name} PBR (추가 검증 권장)'
            successes.append(name)
        except Exception as e:
            logging.warning('선택적 기업정보 %s 조회 실패: %s',name,str(e)[:200])
    return successes


def select_universe(rows,cfg,day):
    """Scan ALL listed ordinary shares; day-rotate history work without cap.

    The stock listing is fetched market-wide once. Historical OHLCV remains
    expensive (one request per symbol), so build_rows processes this entire
    rotating queue only while its elapsed-time safety budget permits.

    Market capitalization, liquidity and PBR are ANALYSIS fields, not
    permission to be screened. No stock is removed for being small/illiquid.
    Neither a 210-symbol limit nor a fixed publication list exists.
    """
    valid=[r for r in rows.values()
           if r.get('code') and r.get('market') in ('KOSPI','KOSDAQ')]
    valid.sort(key=lambda r:r['code'])
    if not valid:return valid,0

    trade_day=day.date() if isinstance(day,datetime) else day
    elapsed=(trade_day-datetime(2000,1,3).date()).days
    weeks,weekday=divmod(elapsed,7)
    business_day_index=weeks*5+min(weekday,4)
    # Coprime stride ensures the start position eventually visits every
    # symbol even if the number of currently listed equities is unchanged.
    # No promise that every symbol is deeply assessed in any single run.
    stride=997
    while math.gcd(stride,len(valid))!=1:
        stride+=1
    origin=(business_day_index*stride)%len(valid)
    rotated=valid[origin:]+valid[:origin]
    return rotated,len(valid)

def ma_last(arr,period):return float(np.mean(arr[-period:])) if len(arr)>=period else None


def last_series(series, code, cfg,day):
    start=(day-timedelta(days=int(cfg['historical_days']))).strftime('%Y-%m-%d')
    df=fdr.DataReader(code,start,(day+timedelta(days=1)).strftime('%Y-%m-%d'))
    if df is None or df.empty:raise ValueError('가격 이력 없음')
    df=df.loc[df.index<=day.strftime('%Y-%m-%d')].sort_index()
    if len(df)<cfg['min_history_bars']:raise InsufficientHistory(f'유효 일봉 부족: {len(df)}/{cfg["min_history_bars"]}; 지표 계산 불가')
    cols=('Open','High','Low','Close','Volume')
    if any(c not in df for c in cols):raise ValueError('필수 OHLCV 누락')
    for c in cols:df[c]=df[c].astype(float)
    if df[list(cols)].isnull().any().any():raise ValueError('OHLCV 결측 데이터')
    if df['Close'].iloc[-1]<=0:raise ValueError('현재 종가 0이하')
    if df['Volume'].iloc[-1]<=0:raise ValueError('현재 거래량 0이하')
    last=df.index[-1].date()
    if (day.date()-last).days>cfg['max_data_age_days']:raise ValueError('가격 이력 최신성 오류')
    return df,last


def linear_slope(arr):
    a=np.asarray(arr,dtype=float)
    if len(a)<3:return 0.
    x=np.arange(len(a),dtype=float)
    return float(np.polyfit(x,a,1)[0])


def pivots(values,kind='low',span=3):
    a=np.asarray(values,dtype=float)
    out=[]
    for i in range(span,len(a)-span):
        win=a[i-span:i+span+1]
        if kind=='low' and a[i]==min(win) and np.argmin(win)==span:out.append((i,a[i]))
        if kind=='high' and a[i]==max(win) and np.argmax(win)==span:out.append((i,a[i]))
    return out


def daily_patterns(df):
    """Heuristic flags are candidates, NOT confirmed chart patterns."""
    close=df['Close'].to_numpy(dtype=float);hi=df['High'].to_numpy(dtype=float);lo=df['Low'].to_numpy(dtype=float)
    vol=df['Volume'].to_numpy(dtype=float)
    tags=[]
    # Range compression, measured from non-overlapping 20-day blocks
    range_old=(max(hi[-45:-25])-min(lo[-45:-25])) / max(1e-9, np.mean(close[-45:-25]))
    range_new=(max(hi[-20:])-min(lo[-20:])) / max(1e-9,np.mean(close[-20:]))
    squeeze=range_old>0 and range_new<0.75*range_old and range_new<0.23
    # Prior multi-month rise and subsequent 20~30 day compressed sideways pause
    base20=close[-21] if len(close)>61 else close[-1]
    previous_rise=pct(base20,close[-61]) or 0
    price20=pct(close[-1],base20) or 0
    if previous_rise>=12 and abs(price20)<=10 and squeeze:
        tags.append('상승 후 횡보·수렴 후보')
    # Falling wedge: both lines falling, upper line falls faster, spread contracting
    wh=hi[-45:];wl=lo[-45:]
    hs=linear_slope(wh)/max(1.,np.mean(wh));ls=linear_slope(wl)/max(1.,np.mean(wl))
    width_first=float(np.mean(wh[:10]-wl[:10]))
    width_last=float(np.mean(wh[-10:]-wl[-10:]))
    wedge=(hs<-.0005 and ls<-.0002 and hs<ls-.0001 and width_last<width_first*.90)
    if wedge:tags.append('하락쐐기 근사 후보')
    # Triangle: descending high trend with flat/slightly ascending support
    triangle= hs<-.00035 and ls>=-.0003 and width_last<width_first*.85
    if triangle:tags.append('삼각수렴 근사 후보')
    # IHS: local swing troughs, middle trough deeper than two shoulders, shoulders comparable
    lows=pivots(lo[-90:],'low',span=3)
    ihs=False
    if len(lows)>=3:
        for a,b,c in zip(lows[:-2],lows[1:-1],lows[2:]):
            if c[0]-a[0]<18 or c[0]-a[0]>72:continue
            shoulder_diff=abs(a[1]-c[1])/max(a[1],c[1],1)
            if b[1]<min(a[1],c[1])*.955 and shoulder_diff<.09 and close[-1]>c[1]*1.025:
                ihs=True;break
    if ihs:tags.append('역헤드앤숄더 근사 후보')
    if squeeze and not any('수렴' in s for s in tags):tags.append('변동폭 수축')
    avg20=float(np.mean(vol[-21:-1]))
    prior_high=float(max(hi[-61:-1]));prior20=float(max(hi[-21:-1]))
    breakout=(close[-1]>prior20 and vol[-1]>=1.5*avg20)
    if breakout:tags.append('20일 고점·거래량 돌파')
    bullish_candle=(close[-1]>df['Open'].iloc[-1]*1.04 and (close[-1]-lo[-1])/max(1,hi[-1]-lo[-1])>=.8 and vol[-1]>=1.7*avg20)
    if bullish_candle:tags.append('거래량 동반 장대양봉')
    return {'patterns':tags,'compression':bool(squeeze),'is_wedge':bool(wedge),
       'is_ihs':bool(ihs),'is_triangle':bool(triangle),'is_breakout':bool(breakout),
       'bullish_candle':bool(bullish_candle),'prior20_high':prior20,'prior60_high':prior_high}


def analyze_history(code,cfg,day):
    df,last=last_series(None,code,cfg,day)
    close=df['Close'].to_numpy(dtype=float);volume=df['Volume'].to_numpy(dtype=float)
    lo=df['Low'].to_numpy(dtype=float);hi=df['High'].to_numpy(dtype=float)
    p=float(close[-1]);m20=ma_last(close,20);m60=ma_last(close,60);m120=ma_last(close,120)
    vol20=float(np.mean(volume[-20:]));volprev=float(np.mean(volume[-60:-20]))
    weekly=df['Close'].resample('W-FRI').last().dropna().to_numpy(dtype=float)
    w20=ma_last(weekly,20);w60=ma_last(weekly,60);w120=ma_last(weekly,120)
    was_below=bool(len(weekly)>=130 and w120 is not None and weekly[-11]<np.mean(weekly[-130:-10]))
    weekly_turn=bool(was_below and p>=w120) if w120 else False
    signed=np.sign(np.diff(close,prepend=close[0]))*volume
    obv=np.cumsum(signed)
    obslope=linear_slope(obv[-60:])/max(1.,vol20)
    price60=pct(p,close[-61]);price20=pct(p,close[-21]);price120=pct(p,close[-121])
    obv_rising=obslope>0
    divergence=bool(obv_rising and -12<=price60<=10)
    weekly_aligned=bool(w20 and w60 and w20>=w60 and p>w20)
    sma_up=bool(m20 and m60 and m120 and m20>m60>m120)
    goldencross=bool(m20 and m60 and close[-6:-1].mean()<=close[-65:-5].mean() and m20>m60)
    high52=float(max(hi[-252:])) if len(hi)>=252 else float(max(hi))
    low52=float(min(lo[-252:])) if len(lo)>=252 else float(min(lo))
    # stop line = structural 20d/45d swing support, must be below price
    support20=float(min(lo[-20:]));support45=float(min(lo[-45:]))
    stop=support20 if support20<p*.98 else support45
    if stop>=p:stop=None
    stop_pct=pct(stop,p) if stop is not None else None
    reward_pct=pct(high52,p) if high52>p else None
    rr=(reward_pct / abs(stop_pct)) if reward_pct is not None and stop_pct is not None and stop_pct<0 else None
    pat=daily_patterns(df)
    return {
      'price_date':last.isoformat(),'price_latest':p,'price20':round_or_none(price20),
      'price60':round_or_none(price60),'price120':round_or_none(price120),
      'volume_ratio':round(float(vol20/max(1,volprev)),2),
      'one_day_volume_ratio':round(float(volume[-1]/max(1,np.mean(volume[-21:-1]))),2),
      'obv_rising':bool(obv_rising),'obv_divergence':divergence,
      'obv_slope':round(float(obslope),4),'ma20':m20,'ma60':m60,'ma120':m120,
      'ma20_above_ma60':bool(m20>m60),'sma_aligned':sma_up,'goldencross_candidate':goldencross,
      'ma120w':w120,'ma20w':w20,'ma60w':w60,
      'weekly_aligned':weekly_aligned,'weekly_turn':weekly_turn,
      'ma120w_gap':round_or_none(pct(p,w120)),'high52_gap':round_or_none(pct(p,high52)),
      'low52_gap':round_or_none(pct(p,low52)),'stop_line':round_or_none(stop,0),
      'stop_pct':round_or_none(stop_pct),'reference_resistance':round_or_none(high52,0),
      'reference_target_pct':round_or_none(reward_pct),'reward_risk':round_or_none(rr,2),
      'close_vs_ma60_pct':round_or_none(pct(p,m60)),**pat}


def load_indexes(day,cfg):
    result={}
    start=(day-timedelta(days=200)).strftime('%Y-%m-%d')
    for code,market in (('KS11','KOSPI'),('KQ11','KOSDAQ')):
        try:
            df=fdr.DataReader(code,start,(day+timedelta(days=1)).strftime('%Y-%m-%d'))
            if df is None or len(df)<65:raise ValueError('지수 자료 부족')
            df=df.loc[df.index<=day.strftime('%Y-%m-%d')].sort_index()
            last=df.index[-1].date()
            if (day.date()-last).days>5:raise ValueError('지수 데이터 갱신 지연 — 비교 불가')
            close=df['Close'].astype(float).to_numpy()
            result[market]={'r20':pct(close[-1],close[-21]),'r60':pct(close[-1],close[-61]),'date':last.isoformat()}
        except Exception as e:logging.warning('%s 시장 상대강도 제외: %s',market,str(e)[:120])
    return result


def flows(code,day,cfg):
    if not os.getenv('KRX_ID') or not os.getenv('KRX_PW'):
        raise ValueError('KRX 로그인 정보 없음: 네이버 금융 대체 조회 사용')
    a=(day-timedelta(days=2*int(cfg['flow_days'])+12)).strftime('%Y%m%d')
    b=day.strftime('%Y%m%d')
    f=stock.get_market_trading_volume_by_date(a,b,code)
    if f is None or len(f)<max(10,cfg['flow_days']//2):raise ValueError('수급자료 없음 또는 거래일 부족')
    f=f.tail(cfg['flow_days'])
    for key in ('외국인합계','기관합계'):
        if key not in f.columns:raise ValueError(f'{key} 데이터 누락')
    x=f['외국인합계'].astype(float);y=f['기관합계'].astype(float)
    if not np.isfinite(x.to_numpy()).all() or not np.isfinite(y.to_numpy()).all():
        raise ValueError('기관·외국인 수급 결측/비정상 값: 미확인')
    if len(f)<int(cfg['flow_days']):raise ValueError('요청 거래일 수급 자료 부족')
    if (day.date()-f.index[-1].date()).days>cfg['max_data_age_days']:
        raise ValueError('수급 데이터 최신성 부족')
    return {'foreign_net_shares':int(x.sum()),'inst_net_shares':int(y.sum()),
            'foreign_positive_days':int((x>0).sum()),'inst_positive_days':int((y>0).sum()),
            'flow_observations':len(f),'flows_confirmed':True,
            'flow_start_date':f.index[0].date().isoformat(),'flow_end_date':f.index[-1].date().isoformat(),
            'flow_source':'pykrx (KRX 투자자별 순매매)'}



def flows_naver(code, day, cfg):
    # Legacy finance.naver.com now redirects to Npay; read the current public table.
    url=f'https://stock.naver.com/domestic/stock/{code}/tradingTrend'
    # Server HTML cannot establish the table exchange selection: render explicitly.
    rows=parse_naver_flow_table(naver_rendered_table(url))
    valid=sorted(((d,ins,forg) for d,(ins,forg) in rows.items()
                  if d<=day.strftime('%Y-%m-%d')),reverse=True)
    need=int(cfg['flow_days'])
    if len(valid)<need:raise ValueError(f'Npay 공개 수급 표 거래일 부족/서버 HTML 미제공: {len(valid)}/{need}')
    freshest=datetime.fromisoformat(valid[0][0]).date()
    if (day.date()-freshest).days>cfg['max_data_age_days']:raise ValueError('Npay 수급 최신성 부족')
    selected=valid[:need];foreign=[x[2] for x in selected];inst=[x[1] for x in selected]
    return {'foreign_net_shares':sum(foreign),'inst_net_shares':sum(inst),
            'foreign_positive_days':sum(x>0 for x in foreign),'inst_positive_days':sum(x>0 for x in inst),
            'flow_observations':len(selected),'flows_confirmed':True,
            'flow_start_date':selected[-1][0],'flow_end_date':selected[0][0],
            'flow_source':'Npay 증권 KRX 선택 기관/외국인 순매매(공개 표)',
            'flow_session_status':'regular_session_unverified'}

def score_one(r,cfg):
    """Add every dimension's observed score and coverage, without normalizing away missing factors."""
    p20,p60=r['price20'],r['price60'];vol=r['volume_ratio'];tags=r['patterns']
    turnover=20
    turnover_score=(5 if 1.1<=vol<=3 else 2 if vol>1 else 0)
    turnover_score+=5 if r['one_day_volume_ratio']>=1.7 else 2 if r['one_day_volume_ratio']>=1.2 else 0
    turnover_score+=5 if r['obv_divergence'] else 2 if r['obv_rising'] else 0
    turnover_score+=5 if (r['is_breakout'] or r['bullish_candle']) else 2 if r['compression'] else 0
    daily_score=(5 if r['sma_aligned'] else 2 if r['ma20_above_ma60'] else 0)
    daily_score+=(5 if r['is_breakout'] else 0)
    daily_score+=(5 if r['is_wedge'] or r['is_ihs'] else 3 if r['is_triangle'] or '상승 후 횡보·수렴 후보' in tags else 0)
    daily_score+=(5 if -7<=p20<=12 and r['close_vs_ma60_pct']>-5 else 2 if p20<18 else 0)
    weekly_score=(5 if r['weekly_aligned'] else 0)
    weekly_score+=(5 if r['weekly_turn'] else 2 if r['ma120w_gap'] is not None and -7<=r['ma120w_gap']<=15 else 0)
    weekly_score+=(5 if r['ma120w_gap'] is not None and r['ma120w_gap']>=0 else 2 if r['ma120w_gap'] is not None and r['ma120w_gap']>=-8 else 0)
    weekly_available=15 if r['ma120w_gap'] is not None else 0
    fl_score=None
    if r.get('flows_confirmed'):
        obs=max(1,r['flow_observations'])
        fl_score=(7 if r['foreign_net_shares']>0 else 0)+(7 if r['inst_net_shares']>0 else 0)
        fl_score+=round(6*(r['foreign_positive_days']+r['inst_positive_days'])/(2*obs))
    relscore=None
    if r.get('relative60') is not None:
        relscore=6 if r['relative60']>=10 else 4 if r['relative60']>=4 else 2 if r['relative60']>=0 else 0
        relscore+=4 if r.get('relative20') is not None and r['relative20']>=2 else 2 if r.get('relative20') is not None and r['relative20']>=0 else 0
    val=None
    if r.get('pbr') is not None:
        pbr=r['pbr'];val=5 if pbr<=cfg['preferred_pbr_max'] else 2 if pbr<=3 else 0
    stop_pct=r['stop_pct'];rr=r['reward_risk']
    risk=0 if stop_pct is None else (3 if -cfg['max_stop_distance_pct']<=stop_pct<=-3 else 1 if -23<=stop_pct<-cfg['max_stop_distance_pct'] else 0)
    risk+=(2 if rr is not None and rr>=2 else 1 if rr is not None and rr>=1 else 0)
    parts={'거래량·OBV':turnover_score,'일봉 구조':daily_score,
           '주봉·추세':weekly_score if weekly_available else None,
           '수급':fl_score,'시장 대비 강도':relscore,'PBR':val,
           '실적·현금흐름':None,'위험·손익비':risk}
    observed=sum(v for v in parts.values() if v is not None)
    available=sum(WEIGHTS[k] for k,v in parts.items() if v is not None)
    r['components']=parts;r['raw_score']=int(observed);r['available_max']=available
    r['score']=round_or_none(100*observed/available) if available else None
    r['coverage_pct']=round_or_none(100*available/sum(WEIGHTS.values()))
    r['pbr_confirmed']=val is not None
    r['financial_confirmed']=False  # 실적/현금흐름 데이터를 아직 확인하지 못함
    risk_flags=[]
    if stop_pct is None:risk_flags.append('구조적 손절선 미산출')
    elif stop_pct<-cfg['max_stop_distance_pct']:risk_flags.append('예상 손절폭 과대')
    elif stop_pct>-3:risk_flags.append('손절선 과도하게 근접')
    if r['high52_gap'] is not None and r['high52_gap']>-7:risk_flags.append('52주 고점권 — 추격 주의')
    if r['price60']>=40:risk_flags.append('단기 급등 — 추격 주의')
    if r['ma120w_gap'] is not None and r['ma120w_gap']<-15:risk_flags.append('장기 추세 취약')
    if r['one_day_volume_ratio']>8:risk_flags.append('급격한 거래량 집중 확인 필요')
    # Low liquidity does NOT remove an otherwise promising small-cap stock.
    # It is an execution/slippage risk, never a proxy for weak financials.
    if r.get('trading_eok') is not None and r['trading_eok']<2:
        risk_flags.append('거래대금 부족 — 환금성·호가 스프레드 점검 필요')
    if not r.get('flows_confirmed'):risk_flags.append('외국인·기관 수급 미확인')
    if not r['financial_confirmed']:risk_flags.append('실적·현금흐름 검증 필요')
    if not r['pbr_confirmed']:risk_flags.append('PBR 미확인')
    r['risk_flags']=risk_flags
    r['signals']=(['거래량 동반 초기 돌파'] if r['is_breakout'] else [])+tags+(['주봉 120주선 회복'] if r['weekly_turn'] else [])
    r['phase']=('초기 돌파 확인 중' if r['is_breakout'] and p60<25 else
      '상승 후 수렴' if '상승 후 횡보·수렴 후보' in tags else
      '장기 추세 전환 관찰' if r['weekly_turn'] else
      '추세 상승 중' if r['sma_aligned'] and p60>10 else
      '바닥·반등 확인 중' if r['high52_gap']<-20 and p60<10 else '중립/기타')
    has_risk=any(s in risk_flags for s in ['예상 손절폭 과대','장기 추세 취약','단기 급등 — 추격 주의'])
    # Never mark buy/priority when funds/financials missing or support is unsafe
    if has_risk:r['decision']='위험 우선 점검'
    elif not r.get('flows_confirmed') or not r['financial_confirmed']:
        r['decision']='기술적 상위 후보·검증 필요' if (r['score'] or 0)>=73 and (r['coverage_pct'] or 0)>=50 else '추가 검증 필요'
    elif (r['score'] or 0)>=75 and (r['coverage_pct'] or 0)>=90 and r['patterns']:r['decision']='정밀 검토 우선'
    elif (r['score'] or 0)>=60:r['decision']='관찰'
    else:r['decision']='제외/대기'
    r['forecast']='통계 검증 전 — 1·2·3·6·12개월 예측 수익률 미제시'
    r['elliott']='수동 파동카운팅·대안 카운팅 검증 필요'
    return r


def build_rows(day,cfg):
    universe=market_snapshot()
    optional=[]
    if cfg.get('enable_optional_company_listing',True):optional=optional_listings(universe)
    selected,total=select_universe(universe,cfg,day)
    logging.info('전시장 보통주 후보 %s종목 (시총·거래대금 상하한 없음); 별도 일일 종목 수 상한 없음 / 보조 목록=%s',
                 total,optional)
    idx=load_indexes(day,cfg)
    valid=[];errors=[];excluded=[]
    history_start=time.monotonic()
    history_budget=float(cfg.get('history_time_budget_seconds',2100))
    if not 120<=history_budget<=2700:
        raise ValueError('상세 가격 조회 실행시간 예산은 120~2700초 이내여야 함')
    history_attempted=0
    for i,r in enumerate(selected,1):
        if i>1 and time.monotonic()-history_start>=history_budget:
            logging.info('과거 차트 상세조회 시간예산 도달: %s/%s종목 처리, 나머지 %s종목 다음 거래일에 재검색',
                         history_attempted,total,total-history_attempted)
            break
        history_attempted+=1
        try:
            v=analyze_history(r['code'],cfg,day);r.update(v)
            ref=idx.get(r['market'])
            if ref:
                r['relative20']=round_or_none(r['price20']-ref['r20'])
                r['relative60']=round_or_none(r['price60']-ref['r60'])
            else:r['relative20']=r['relative60']=None
            r['flows_confirmed']=False
            valid.append(r)
        except InsufficientHistory as e:
            excluded.append([r['code'],r['name'],str(e)])
            logging.info('이력부족 제외 %s: %s',r['code'],e)
        except Exception as e:
            errors.append([r['code'],'가격이력',str(e)[:240]])
            logging.warning('가격이력 오류 %s: %s',r['code'],str(e)[:240])
        if i%30==0:logging.info('일봉 분석 %s/%s',i,len(selected))
        time.sleep(cfg['request_sleep_seconds'])
    if not valid:raise RuntimeError('모든 종목 가격조회 실패 — 결과 덮어쓰기 중단')
    freshest=max(r['price_date'] for r in valid)
    valid=[r for r in valid if r['price_date']==freshest]
    if len(valid)<20:raise RuntimeError('최근 거래일 유효한 기술 분석 종목이 20개 미만: 발행 중단')
    if (day.date()-datetime.fromisoformat(freshest).date()).days>6:raise RuntimeError('분석 기준일이 오래됨')
    # Select candidates for flow check by non-OBV composite, not simply liquidity.
    for r in valid:score_one(r,cfg)
    attempts=sorted(valid,key=lambda x:((x['components']['일봉 구조'] or 0)+
              (x['components']['주봉·추세'] or 0)+(x['components']['거래량·OBV'] or 0),
              x['market_cap_eok']),reverse=True)
    collector=FlowCollector(
        (lambda code: flows(code,day,cfg)) if os.getenv('KRX_ID') and os.getenv('KRX_PW') else None,
        lambda code: flows_naver(code,day,cfg),
        budget_seconds=int(cfg.get('flow_time_budget_seconds',1200)))
    flow_deferred=0
    for i,r in enumerate(attempts,1):
        result,error=collector.collect(r['code'])
        if result is None and isinstance(error,str) and error.startswith('deferred:'):
            flow_deferred=len(attempts)-i+1
            logging.info('수급 조회 공급원 중단/시간예산 소진: %s종목 수급 미확인',flow_deferred)
            break
        if result is not None:r.update(result)
        else:
            errors.append([r['code'],'기관·외국인 수급',error])
            logging.warning('수급 미확인 %s: %s',r['code'],error)
        if r.get('flows_confirmed') and i<=int(cfg.get('flow_crosscheck_count',10)):
            if os.getenv('KRX_ID') and os.getenv('KRX_PW'):
                try:
                    primary=flows(r['code'],day,cfg)
                    alt=flows_naver(r['code'],day,cfg)
                    keys=('foreign_net_shares','inst_net_shares','flow_start_date','flow_end_date','flow_observations')
                    same=all(primary.get(k)==alt.get(k) for k in keys)
                    r['flow_cross_validation']='KRX/Npay 기간·합계 일치' if same else '불일치 — 미확인 처리'
                    if not same:
                        r['flows_confirmed']=False
                        for k in ('foreign_net_shares','inst_net_shares'):r[k]=None
                        errors.append([r['code'],'수급 교차검증','KRX/Npay 기간 또는 합계 불일치'])
                except Exception as e:r['flow_cross_validation']='교차검증 실패: '+str(e)[:100]
            else:r['flow_cross_validation']='Npay 단일 소스 — KRX 인증 미등록, 독립 교차검증 미완료'
        if i%10==0:logging.info('수급 조회 %s/%s',i,len(attempts))
        time.sleep(cfg['request_sleep_seconds'])
    for r in valid:score_one(r,cfg)
    valid.sort(key=lambda x:(x['decision']=='정밀 검토 우선',x['decision']=='관찰',x['score'] or 0,x['coverage_pct'] or 0),reverse=True)
    return valid,errors,datetime.fromisoformat(freshest),{
      'eligible':total,'examined':history_attempted,
      'history_deferred':max(0,total-history_attempted),
      'history_scan_complete':history_attempted==total,
      'history_usable':len(valid),
      'index_markets':list(idx),'optional_listings':optional,
      'excluded':excluded,'flow_attempted':collector.attempted,
      'flow_planned':len(attempts),'flow_deferred':flow_deferred}


def _xml_xlsx(sheets, path):
    """Small OOXML spreadsheet writer using Python stdlib: no Excel package needed.
       Supports Korean inline strings, numeric values, freeze panes and header styling.
    """
    def colname(idx):
        a=''
        while idx:
            idx,rem=divmod(idx-1,26); a=chr(65+rem)+a
        return a
    def sheetxml(rows, widths):
        data=[]
        for ri,row in enumerate(rows,1):
            cs=[]
            for ci,value in enumerate(row,1):
                if value is None: continue
                a=colname(ci)+str(ri); style=' s="1"' if ri==1 else ''
                if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):
                    cs.append(f'<c r="{a}"{style}><v>{value}</v></c>')
                else:
                    text=escape(str(value)).replace('\n','&#10;')
                    cs.append(f'<c r="{a}" t="inlineStr"{style}><is><t>{text}</t></is></c>')
            data.append(f'<row r="{ri}">'+''.join(cs)+'</row>')
        cols=''.join(f'<col min="{k}" max="{k}" width="{w}" customWidth="1"/>' for k,w in enumerate(widths,1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
          '<cols>'+cols+'</cols><sheetData>'+''.join(data)+'</sheetData></worksheet>')
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        content=['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
        for k in range(1,len(sheets)+1):
            content.append(f'<Override PartName="/xl/worksheets/sheet{k}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        z.writestr('[Content_Types].xml',''.join(content)+'</Types>')
        z.writestr('_rels/.rels','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        sheet_tags=''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_,_) in enumerate(sheets,1))
        z.writestr('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+sheet_tags+'</sheets></workbook>')
        relations=[]
        for i in range(1,len(sheets)+1):
            relations.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
        relations.append(f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        z.writestr('xl/_rels/workbook.xml.rels','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(relations)+'</Relationships>')
        z.writestr('xl/styles.xml','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Malgun Gothic"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Malgun Gothic"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF153B53"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
        for i,(_,rows,widths) in enumerate(sheets,1):
            z.writestr(f'xl/worksheets/sheet{i}.xml',sheetxml(rows,widths))

def make_report(rows,errors,analysis_day,cfg,meta):
    OUT.mkdir(parents=True,exist_ok=True)
    d=analysis_day.strftime('%Y-%m-%d')
    guide=[
      ['한국주식 다면 스크리너 v2.0','수급·가격·거래량·일봉·주봉·상대강도·밸류·위험'],
      ['가격 기준일',d],['조건 부합 종목',meta['eligible']],
      ['실제 조회 종목',meta['examined']],['성공 분석 종목',len(rows)],
      ['수급 확인 종목',sum(bool(r.get('flows_confirmed')) for r in rows)],
      ['PBR 확보 종목',sum(r.get('pbr') is not None for r in rows)],
      ['시장 비교 가능 시장',', '.join(meta['index_markets']) or '미확인 (지수 갱신 지연 가능)'],
      ['기업 정보 보조 소스',', '.join(meta['optional_listings']) or '미확인'],
      ['절대 주의','점수는 상승 확률 아님. 미확인 지표를 자동으로 좋은 점수로 채우지 않음'],
      ['수익 전망','1·2·3·6·12개월 수익률은 학습·백테스트 전까지 미산출'],
      ['차트 패턴','통계적 근사 후보. 정확한 차트 확인과 엘리어트 대안 카운팅 필요'],
      ['업종 순환','업종 분류/업황 데이터 신뢰성 확보 전 자동판단 제외'],
      ['투자자 수급','pykrx 조회 성공 시에만 표시. 수급 미확인은 매집 아님'],
      ['PBR','보조 목록에서 확보한 수치만 사용. 공시 확인 전 최종 판단 금지'],
      ['재무 실적/현금흐름','아직 자동 연동되지 않음. 배점 5점을 결측 처리'],
      ['손절선','최근 20~45 거래일 저점 기준 참고선. 확정 지지선 아님'],
      ['상승 여력','52주 고점 대비 공간은 목표주가 예측이 아님'],
      ['종목 선정','전체시장 보통주 1차 검색 (시가총액/거래대금 상하한 없음) 후 실행시간 내 개별 차트 조회. 일별 시작 위치 순환'],
      ['데이터 출처','https://github.com/FinanceData/FinanceDataReader'],
      ['수급 출처','https://github.com/sharebook-kr/pykrx']]
    headers=['순위','종목코드','종목명','시장','업종(가능시)','가격 기준일','종가 원','시총 억원','거래대금 억원',
             '현재 단계','판정','환산점수','확보율 %','관측점수','평가가능만점',
             '일봉 20일 %','60일 %','120일 %','거래량 비율(20/이전40)','당일거래량 비율',
             'OBV 상승','가격-OBV 괴리','20>60일선','일봉 정배열','골든크로스 관찰',
             '주봉 상승정렬','120주선 전환','120주선 괴리 %','52주 고점대비 %',
             '20일 돌파','장대양봉','상승 후 횡보 수렴','하락쐐기 근사','역헤드앤숄더 근사','삼각수렴 근사',
             '20일 시장초과 %','60일 시장초과 %','외국인 20일 순매수 주','기관 20일 순매수 주',
             '외국인 순매수일','기관 순매수일','PBR','손절참고 원','손절폭 %','52주 최고가 원',
             '52주고점 공간 %','단순 손익비','패턴 후보','위험 경고','예상수익률','엘리어트 검증']
    table=[headers]
    for i,r in enumerate(rows,1):
        table.append([i,r['code'],r['name'],r['market'],r['sector'],r['price_date'],int(r['price_latest']),
          round_or_none(r['market_cap_eok'],0),round_or_none(r['trading_eok']),
          r['phase'],r['decision'],r['score'],r['coverage_pct'],r['raw_score'],r['available_max'],
          r['price20'],r['price60'],r['price120'],r['volume_ratio'],r['one_day_volume_ratio'],
          'Y' if r['obv_rising'] else 'N','Y' if r['obv_divergence'] else 'N',
          'Y' if r['ma20_above_ma60'] else 'N','Y' if r['sma_aligned'] else 'N',
          'Y' if r['goldencross_candidate'] else 'N','Y' if r['weekly_aligned'] else 'N',
          'Y' if r['weekly_turn'] else 'N',r['ma120w_gap'],r['high52_gap'],
          'Y' if r['is_breakout'] else 'N','Y' if r['bullish_candle'] else 'N',
          'Y' if '상승 후 횡보·수렴 후보' in r['patterns'] else 'N',
          'Y' if r['is_wedge'] else 'N','Y' if r['is_ihs'] else 'N','Y' if r['is_triangle'] else 'N',
          r['relative20'],r['relative60'],r.get('foreign_net_shares'),r.get('inst_net_shares'),
          r.get('foreign_positive_days'),r.get('inst_positive_days'),r.get('pbr'),
          r['stop_line'],r['stop_pct'],r['reference_resistance'],r['reference_target_pct'],
          r['reward_risk'],' / '.join(r['patterns']),' / '.join(r['risk_flags']),r['forecast'],r['elliott']])
    breakdown=[['종목코드','종목명','구분','관측점수','항목만점','확인상태']]
    for r in rows:
        for key,maxw in WEIGHTS.items():
            v=r['components'][key]
            breakdown.append([r['code'],r['name'],key,v,maxw,'확인' if v is not None else '미확인/미수집'])
    risks=[['종목명','가격 기준일','현재 단계','종가','구조적 손절 참고선','손절폭 %','52주 고점 공간 %','참고 손익비','검증할 위험']]
    for r in rows:
        risks.append([r['name'],r['price_date'],r['phase'],int(r['price_latest']),
          r['stop_line'],r['stop_pct'],r['reference_target_pct'],r['reward_risk'],' / '.join(r['risk_flags'])])
    err=[['종목코드','수집 구간','사유']]+errors
    LOG.joinpath('collection_audit.json').write_text(json.dumps({'errors':errors,'excluded':meta.get('excluded',[])},ensure_ascii=False,indent=2),encoding='utf-8')
    filename=OUT/f"한국주식_다면스크리너_{d}_{datetime.now(ZoneInfo('Asia/Seoul')):%H%M%S}.xlsx"
    _xml_xlsx([
       ('사용안내',guide,[31,100]),
       ('종목순위',table,[8,14,24,12,26,18]+[19]*39+[46,50,42,34,40]),
       ('점수내역',breakdown,[15,24,27,16,14,24]),
       ('위험대응',risks,[24,18,28,16,22,16,20,18,90]),
       ('수집오류',err,[16,28,110]),
       ('이력부족제외',[['종목코드','종목명','사유']]+meta.get('excluded',[]),[15,24,100])],filename)
    return filename


def web_publish(rows,errors,analysis_day,cfg,meta,xlsx):
    SITE.mkdir(parents=True,exist_ok=True)
    reportdir=SITE/'reports';reportdir.mkdir(exist_ok=True)
    view=[]
    for i,r in enumerate(rows,1):
        view.append({
          'rank':i,'code':r['code'],'name':r['name'],'market':r['market'],
          'sector':r['sector'],'price':int(r['price_latest']),
          'market_cap_eok':round_or_none(r['market_cap_eok'],0),
          'trading_eok':round_or_none(r['trading_eok']),
          'price_date':r['price_date'],'phase':r['phase'],'decision':r['decision'],
          'score':r['score'],'coverage_pct':r['coverage_pct'],
          'components':r['components'],'price20':r['price20'],'price60':r['price60'],
          'price120':r['price120'],'volume_ratio':r['volume_ratio'],
          'one_day_volume_ratio':r['one_day_volume_ratio'],
          'obv_rising':r['obv_rising'],'divergence':r['obv_divergence'],
          'ma120w_gap':r['ma120w_gap'],'high52_gap':r['high52_gap'],
          'relative20':r['relative20'],'relative60':r['relative60'],
          'flows_confirmed':bool(r.get('flows_confirmed')),
          'foreign_shares':r.get('foreign_net_shares'),'flow_observations':r.get('flow_observations'),
          'flow_cross_validation':r.get('flow_cross_validation','독립 소스 미검증'),
          'inst_shares':r.get('inst_net_shares'),'flow_source':r.get('flow_source'),
          'flow_start_date':r.get('flow_start_date'),'flow_end_date':r.get('flow_end_date'),
          'pbr':r['pbr'],'patterns':r['patterns'],'signals':r['signals'],
          'stop_line':r['stop_line'],'stop_pct':r['stop_pct'],
          'reference_resistance':r['reference_resistance'],
          'reference_target_pct':r['reference_target_pct'],
          'reward_risk':r['reward_risk'],
          'risk_flags':r['risk_flags'],'forecast':r['forecast']})
    out={'status':'success','version':'2.0','date':analysis_day.strftime('%Y-%m-%d'),
         'generated_kst':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M KST'),
         'candidate_count':len(view),'examined_count':meta['examined'],'eligible_count':meta['eligible'],
          'history_deferred_count':meta.get('history_deferred',0),
          'history_scan_complete':bool(meta.get('history_scan_complete',False)),
          'history_usable_count':meta.get('history_usable'),
         'flow_confirmed_count':sum(x['flows_confirmed'] for x in view),
         'fundamental_count':sum(x['pbr'] is not None for x in view),
         'index_available':meta['index_markets'],
         'error_count':len(errors),'collection_errors':errors,'history_exclusions':meta.get('excluded',[]),
         'flow_attempted_count':meta.get('flow_attempted'),
         'flow_deferred_count':meta.get('flow_deferred'),
         'limitations':['실적/현금흐름 자동연결 미구현','기관·외국인 조회 실패 시 미확인',
           '차트패턴 근사후보: 주봉/일봉 이미지 검증 필요','상승확률/미래수익률 예측 아님',
           '업종 순환 자동판정 미구현'],
         'weights':WEIGHTS,'stocks':view}
    # Write only AFTER successfully computing valid new results; do not replace on full failure.
    shutil.copyfile(xlsx,reportdir/xlsx.name)
    shutil.copyfile(xlsx,reportdir/'latest.xlsx')
    current=SITE/'history.json'
    try:hist=json.loads(current.read_text(encoding='utf-8')) if current.exists() else []
    except (ValueError,OSError):hist=[]
    entry={'date':out['date'],'file':'reports/'+xlsx.name}
    hist=[entry]+[x for x in hist if x.get('file')!=entry['file']]
    known={x.get('file') for x in hist}
    for report in sorted(reportdir.glob('*.xlsx')):
        match=re.search(r'\d{4}-\d{2}-\d{2}',report.name)
        ref='reports/'+report.name
        if match and ref not in known:
            hist.append({'date':match.group(0),'file':ref});known.add(ref)
    hist.sort(key=lambda x:x['date'],reverse=True)
    current.write_text(json.dumps(hist,ensure_ascii=False,indent=2),encoding='utf-8')
    previous=SITE/'data.json'
    if previous.exists():
        archive=SITE/'archive';archive.mkdir(exist_ok=True)
        shutil.copyfile(previous,archive/f"data_before_{datetime.now(ZoneInfo('Asia/Seoul')):%Y%m%d_%H%M%S}.json")
    previous.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    # Generate date-aligned research dossiers for the BEST qualified stocks
    # from the COMPLETE set of valid price observations, not preselected 20.
    # Failure prevents git publication of a mismatched dashboard snapshot.
    from recommendation_engine import publish as publish_recommendations
    research=publish_recommendations(out,SITE)
    logging.info('심층분석: 전체 %s / 자격 %s / 보고서 %s / 검증 매수후보 %s / 신규 %s / 이탈 %s',
         research['screened_count'],research['research_eligible_count'],
         research['research_dossier_count'],research['confirmed_recommendation_count'],
         len(research['daily_changes']['entered']),len(research['daily_changes']['exited']))
    return out


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',help='YYYY-MM-DD 한국 시장 날짜, 생략하면 현재 KST')
    a=parser.parse_args()
    cfg=cfg_read();OUT.mkdir(exist_ok=True);LOG.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
         handlers=[logging.StreamHandler(sys.stdout),
                   logging.FileHandler(LOG/f"run_{datetime.now(ZoneInfo('Asia/Seoul')):%Y%m%d}.log",encoding='utf8')])
    today=datetime.strptime(a.date,'%Y-%m-%d') if a.date else datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    rows,errors,day,meta=build_rows(today,cfg)
    path=make_report(rows,errors,day,cfg,meta)
    info=web_publish(rows,errors,day,cfg,meta,path)
    logging.info('완료! 날짜 %s / 후보 %s / 수급확인 %s / 오류 %s / 엑셀: %s',
       info['date'],info['candidate_count'],info['flow_confirmed_count'],info['error_count'],path)

if __name__=='__main__':main()

