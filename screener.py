"""Korean stocks daily screening. Research tool; no trading orders.
Requires internet on Windows at execution time. Optional DART key not required.
"""
import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import shutil
from pathlib import Path

import numpy as np
from pykrx import stock
import FinanceDataReader as fdr
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
SITE = ROOT / 'docs'
OUT = ROOT / 'reports'
CACHE = ROOT / 'cache'
LOG = ROOT / 'logs'
CONFIG = ROOT / 'config.json'

DEFAULT = {
  'min_market_cap_eok': 5000, 'max_market_cap_eok': 50000,
  'min_daily_trading_eok': 5, 'preselect_limit': 160,
  'historical_days': 1000, 'top_candidates_for_flows': 35,
  'flow_days': 20, 'min_history_bars': 150,
  'max_data_age_days': 5,
  'preferred_pbr_max': 1.5,
  'max_watch_rows': 100,
  'request_sleep_seconds': 0.18
}

def config():
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT, ensure_ascii=False, indent=2), encoding='utf-8')
    return {**DEFAULT, **json.loads(CONFIG.read_text(encoding='utf-8'))}

def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError): return None

def pick_day(day, lookback=12):
    # FDR stock listing represents most recently available EOD snapshot.
    # Exact trading day is corroborated by subsequent history() calls.
    return day

def snapshot(day):
    # KRX listing from FinanceDataReader. It may fail on KRX auth changes.
    # Strict fail-closed: never reuse cached market values silently.
    listing = fdr.StockListing('KRX-MARCAP')
    if listing is None or listing.empty:
        raise RuntimeError('전 종목 시세 조회 실패: FinanceDataReader KRX-MARCAP')
    required={'Code','Name','Market','Close','Volume','Amount','Marcap'}
    if not required.issubset(listing.columns):
        raise RuntimeError(f'예상과 다른 KRX-MARCAP 컬럼: {set(listing.columns)}')
    if len(listing) < 1000:
        raise RuntimeError(f'시장 전체 종목 수가 비정상적으로 적습니다: {len(listing)}')
    rows=[]
    for _, r in listing.iterrows():
        code=str(r['Code']).zfill(6)
        if not (len(code)==6 and code.isdigit()): continue
        market=str(r['Market']).upper()
        if 'KOSPI' not in market and 'KOSDAQ' not in market: continue
        px,vol,amount,mcap=map(num, [r['Close'],r['Volume'],r['Amount'],r['Marcap']])
        if not (px and vol and amount and mcap) or min(px,vol,amount,mcap)<=0: continue
        rows.append({'code':code, 'name':str(r['Name']), 'market':market,
          'close':px, 'volume':vol, 'value_eok':amount/1e8,
          'market_cap_eok':mcap/1e8, 'pbr':None, 'per':None})
    if len(rows)<800:
        raise RuntimeError(f'KRX-MARCAP 종목 가공 후 비정상 결과: {len(rows)}')
    return rows

def history(code, day, cfg):
    start=(day-timedelta(days=cfg['historical_days'])).strftime('%Y-%m-%d')
    end=(day+timedelta(days=1)).strftime('%Y-%m-%d')
    df=fdr.DataReader(code,start,end)
    if df is None or df.empty: raise ValueError('가격 이력 없음')
    df=df.loc[df.index <= day.strftime('%Y-%m-%d')].sort_index()
    if len(df)<cfg['min_history_bars']: raise ValueError(f'이력 부족 {len(df)}봉')
    close=df['Close'].astype(float); vol=df['Volume'].astype(float)
    if close.iloc[-1]<=0 or vol.iloc[-1]<=0: raise ValueError('가격/거래량 오류')
    last= df.index[-1].date()
    if (day.date()-last).days > cfg['max_data_age_days']: raise ValueError('가격 이력 최신성 부족')
    signed=np.sign(close.diff().fillna(0).to_numpy())*vol.to_numpy()
    obv=np.cumsum(signed)
    if len(obv)<61: raise ValueError('OBV 데이터 부족')
    # OBV divergence: flat/down price vs rising OBV over last 60 sessions
    price60=close.iloc[-1]/close.iloc[-61]-1
    obv_slope=np.polyfit(np.arange(60),obv[-60:]/max(1, np.mean(vol.iloc[-60:])),1)[0]
    obv_rising=bool(obv_slope>0)
    vol_ratio=float(vol.iloc[-20:].mean()/max(1, vol.iloc[-60:-20].mean()))
    ma20=float(close.iloc[-20:].mean()); ma60=float(close.iloc[-60:].mean())
    ma120=float(close.iloc[-120:].mean())
    high52=float(close.iloc[-min(len(close),252):].max())
    weekly=close.resample('W-FRI').last().dropna()
    ma120w=float(weekly.iloc[-120:].mean()) if len(weekly)>=120 else None
    return {'price60':float(price60),'obv_slope':float(obv_slope),'obv_rising':obv_rising,
      'divergence':bool(obv_rising and price60<=0.10), 'volume_ratio':vol_ratio,
      'ma20':ma20,'ma60':ma60,'ma120':ma120,'ma120w':ma120w,
      'distance_120w':float(close.iloc[-1]/ma120w-1) if ma120w else None,
      'distance_52wh':float(close.iloc[-1]/high52-1),
      'price_latest':float(close.iloc[-1]),'price_date':last.isoformat()}

def flows(code, day, cfg):
    # pykrx units: investor volume is number of shares; sign indicates net buying.
    a=(day-timedelta(days=cfg['flow_days']*2+12)).strftime('%Y%m%d')
    b=day.strftime('%Y%m%d')
    df=stock.get_market_trading_volume_by_date(a,b,code)
    if df is None or df.empty: raise ValueError('수급자료 없음')
    df=df.tail(cfg['flow_days'])
    if len(df)<max(8,cfg['flow_days']//2): raise ValueError('수급 거래일 부족')
    def field(s):
        if s not in df.columns: return None
        x=df[s].fillna(0).astype(float)
        return (float(x.sum()),int((x>0).sum()))
    fr=field('외국인합계'); ins=field('기관합계')
    if not fr or not ins: raise ValueError('수급 컬럼 미확인')
    return {'foreign_net_shares':int(fr[0]),'inst_net_shares':int(ins[0]),
      'foreign_positive_days':fr[1],'inst_positive_days':ins[1],
      'flow_observations':len(df)}

def score(r, cfg):
    # Each dimension computed only when its dependent fields exist. No fake flow score.
    tech = 0
    tech += 12 if r['divergence'] else (7 if r['obv_rising'] else 0)
    tech += 7 if 1.10<=r['volume_ratio']<=3.0 else (3 if r['volume_ratio']>1 else 0)
    tech += 5 if r['price_latest']>r['ma60'] else 0
    tech += 3 if r['ma20']>r['ma60'] else 0
    tech += 3 if r['distance_120w'] is not None and 0<=r['distance_120w']<=.12 else 0
    early= 7 if -.32<=r['distance_52wh']<=-.08 else (3 if r['distance_52wh']<-.02 else 0)
    early += 8 if -.06<=r['price60']<=.10 else (4 if r['price60']<.2 else 0)
    valuation=0
    if r.get('pbr') is not None and r['pbr']>0:
        valuation=12 if r['pbr']<=cfg['preferred_pbr_max'] else (5 if r['pbr']<=3 else 0)
    # Earnings and revenue intentionally NOT inferred from price or PBR.
    # Financial category max 20 but 8 points unscored until validated statements.
    risk= 5 if r['price_latest']>r['ma120'] else 2
    risk+= 5 if r['distance_52wh'] <= -.05 else 0
    flow_score=None
    if 'foreign_net_shares' in r:
        flow_score=(9 if r['foreign_net_shares']>0 else 0)+ (9 if r['inst_net_shares']>0 else 0)
        flow_score+= round(7*(r['foreign_positive_days']+r['inst_positive_days'])/(2*r['flow_observations']))
    r.update({'technical_score':tech,'early_score':early,'valuation_score':valuation,
              'risk_score':risk,'flow_score':flow_score,
              'score_observed':tech+early+valuation+risk+(flow_score or 0),
              'score_available_max':30+15+12+10+(25 if flow_score is not None else 0)})
    r['score_normalized']=round(100*r['score_observed']/r['score_available_max'],1)
    r['category']=('검토 우선' if flow_score is not None and r['score_normalized']>=75 else
                   '관찰' if flow_score is not None and r['score_normalized']>=58 else
                   '수급 미확인' if flow_score is None else '제외/대기')
    return r

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


def report(rows, errors, day, cfg):
    OUT.mkdir(exist_ok=True)
    guide=[
      ['한국주식 모바일 자동 스크리너','v1.2 / 연구용, 매매 신호 아님'],
      ['가격 기준일',day.strftime('%Y-%m-%d')],
      ['시가총액·거래대금','FinanceDataReader KRX-MARCAP'],
      ['가격이력·거래량','FinanceDataReader (개별 종목)'],
      ['기관·외국인 수급','pykrx 성공 시에만, 최근 20거래일'],
      ['필터','시총 5천억~5조·거래대금 5억 이상'],
      ['분석 범위','거래대금 상위 160개→상위 35개 수급 조회'],
      ['OBV 주의','OBV 상승으로 기관매집 단정 불가'],
      ['PBR·실적','소스 미검증으로 점수에서 제외'],
      ['점수주의','측정 가능한 지표만 환산·상승확률 아님'],
      ['차트 패턴','IHS·쐐기·엘리어트 자동판정 아님'],
      ['수집 실패','오류로그 참조, 누락점수 임의 보충 금지'],
      ['원자료','https://github.com/FinanceData/FinanceDataReader']]
    headers=['순위','코드','종목명','시장','종가(원)','시총(억원)','거래대금(억원)','PBR',
      '60일 변동률(%)','OBV 상승','OBV-가격 괴리','거래량 증감비','52주 고점대비(%)','120주선 괴리(%)',
      '외국인 20일 순매수(주)','기관 20일 순매수(주)','외국인 순매수일','기관 순매수일',
      '수급점수/25','기술점수/30','밸류점수/12','초기점수/15','위험점수/10',
      '관측점수','평가가능만점','환산점수','분류','가격기준일']
    table=[headers]
    for i,r in enumerate(rows[:cfg['max_watch_rows']],1):
        table.append([i,r['code'],r['name'],r['market'],int(r['close']),round(r['market_cap_eok']),round(r['value_eok'],1),r.get('pbr'),
          round(100*r['price60'],2),'Y' if r['obv_rising'] else 'N','Y' if r['divergence'] else 'N',
          round(r['volume_ratio'],2),round(100*r['distance_52wh'],2),round(100*r['distance_120w'],2) if r['distance_120w'] is not None else None,
          r.get('foreign_net_shares'),r.get('inst_net_shares'),r.get('foreign_positive_days'),r.get('inst_positive_days'),
          r.get('flow_score'),r['technical_score'],r['valuation_score'],r['early_score'],r['risk_score'],
          r['score_observed'],r['score_available_max'],r['score_normalized'],r['category'],r['price_date']])
    err=[['종목코드','상세 오류']]+[[str(a),str(b)[:500]] for a,b in errors]
    filename=OUT/f'한국주식_스크리너_{day:%Y-%m-%d}.xlsx'
    _xml_xlsx([('안내',guide,[24,84]),('종목순위',table,[8,13,24,12]+[18]*24),('오류로그',err,[18,86])],filename)
    return filename

def publish_web(rows, errors, day, cfg, xlsx):
    SITE.mkdir(exist_ok=True)
    (SITE/'reports').mkdir(exist_ok=True)
    view=[]
    for i,r in enumerate(rows[:cfg['max_watch_rows']],1):
        view.append({'rank':i,'code':r['code'],'name':r['name'],
          'market':r['market'],'price':r['close'], 'market_cap_eok':round(r['market_cap_eok']),
          'trading_eok':round(r['value_eok'],1), 'pbr':r.get('pbr'),
          'price60':round(100*r['price60'],1),'volume_ratio':round(r['volume_ratio'],2),
          'obv_rising':r['obv_rising'], 'divergence':r['divergence'],
          'ma120w_gap':round(100*r['distance_120w'],1) if r['distance_120w'] is not None else None,
          'high52_gap':round(100*r['distance_52wh'],1),
          'foreign_shares':r.get('foreign_net_shares'),'inst_shares':r.get('inst_net_shares'),
          'flows_confirmed':r.get('flow_score') is not None, 'score':r['score_normalized'],
          'available_max':r['score_available_max'],'category':r['category']})
    flow_count=sum(r['flows_confirmed'] for r in view)
    payload={'status':'success','date':day.strftime('%Y-%m-%d'),
      'generated_kst':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M KST'),
      'candidate_count':len(view),'flow_confirmed_count':flow_count,
      'error_count':len(errors),'source':'FinanceDataReader KRX-MARCAP/price; pykrx flows optional',
      'limitations':['실적·PBR 점수 미반영','수급 실패 시 미확인 표시','매수 신호 아님','최종 차트/재무 검증 필요'],
      'stocks':view}
    (SITE/'data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=SITE/'reports'/xlsx.name
    shutil.copyfile(xlsx,archive)
    shutil.copyfile(xlsx,SITE/'reports'/'latest.xlsx')
    historyfile=SITE/'history.json'
    existing=[]
    if historyfile.exists():
        try: existing=json.loads(historyfile.read_text(encoding='utf-8'))
        except Exception: existing=[]
    entry={'date':day.strftime('%Y-%m-%d'),'file':'reports/'+xlsx.name}
    hist=sorted([entry]+[r for r in existing if r.get('date')!=entry['date']],
                key=lambda x:x['date'],reverse=True)
    historyfile.write_text(json.dumps(hist,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--date', help='YYYY-MM-DD 기준일, 기본값 오늘(KST PC 기준)')
    args=p.parse_args(); cfg=config()
    LOG.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True); CACHE.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
       handlers=[logging.FileHandler(LOG/f'run_{datetime.now():%Y%m%d}.log',encoding='utf-8'),logging.StreamHandler(sys.stdout)])
    date=datetime.strptime(args.date,'%Y-%m-%d') if args.date else datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    day=pick_day(date)
    logging.info('조회 기준 KST: %s',day.date())
    universe=snapshot(day)
    universe=[x for x in universe if cfg['min_market_cap_eok']<=x['market_cap_eok']<=cfg['max_market_cap_eok'] and x['value_eok']>=cfg['min_daily_trading_eok'] and not x['name'].endswith('우') and not any(t in x['name'].upper() for t in ('ETF','ETN','SPAC','스팩'))]
    universe.sort(key=lambda r:r['value_eok'],reverse=True)
    pool=universe[:cfg['preselect_limit']]
    logging.info('대상 %s, 1차 분석 %s',len(universe),len(pool))
    done=[]; errors=[]
    for i,r in enumerate(pool,1):
        try:
            r.update(history(r['code'],day,cfg));score(r,cfg);done.append(r)
        except Exception as e: errors.append((r['code'],str(e)))
        if i%25==0:logging.info('가격 이력 %d/%d',i,len(pool))
        time.sleep(cfg['request_sleep_seconds'])
    done.sort(key=lambda r:r['score_normalized'],reverse=True)
    for i,r in enumerate(done[:cfg['top_candidates_for_flows']],1):
        try:r.update(flows(r['code'],day,cfg))
        except Exception as e:errors.append((r['code'],'수급: '+str(e)))
        score(r,cfg)
        if i%10==0:logging.info('수급 %d/%d',i,min(len(done),cfg['top_candidates_for_flows']))
        time.sleep(cfg['request_sleep_seconds'])
    done.sort(key=lambda r:(r['flow_score'] is not None,r['score_normalized']),reverse=True)
    if not done: raise RuntimeError('유효 종목 분석 결과 0건: 빈 결과를 정상 보고서로 저장하지 않습니다')
    # Validate the actual final available price date rather than the run date.
    valid_dates=[r['price_date'] for r in done]
    analysis_date=max(valid_dates)
    current_days=[r for r in done if r['price_date']==analysis_date]
    if len(current_days)<min(20, len(done)):
        raise RuntimeError('가격 기준일이 섞여 있거나 최근 거래일 자료가 충분하지 않습니다')
    if (day.date()-datetime.fromisoformat(analysis_date).date()).days>7:
        raise RuntimeError(f'수집 자료 최신성이 부족합니다: {analysis_date}')
    done=current_days
    for r in done: r['close']=r['price_latest']
    true_day=datetime.fromisoformat(analysis_date)
    xlsx=report(done,errors,true_day,cfg)
    publish_web(done,errors,true_day,cfg,xlsx)
    logging.info('저장 완료: %s / 분석 %s건 / 오류 %s건',xlsx,len(done),len(errors))
    print('\n완료:',xlsx)

if __name__=='__main__':main()
