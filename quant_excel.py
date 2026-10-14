"""Export verified quant JSON to a daily workbook through v2's existing writer.
No invented probabilities are written when sample checks fail.
"""
import argparse
import json
from pathlib import Path

PERIOD_ORDER=['초단기 1주','단기 2주','스윙 1개월','스윙 2개월','중기 3개월','중기 6개월','장기 1년']

def create(json_path:Path,out_path:Path):
    # Reuses the in-repository workbook writer to avoid extra dependencies.
    from screener import _xml_xlsx
    d=json.loads(json_path.read_text(encoding='utf-8'))
    desc=[['항목','내용'],['분석 종류','과거 유사조건 관측 빈도. 예측 확률 또는 자동매매 신호 아님'],
      ['가격 기준일',d.get('asof')],['상태',d.get('status')],
      ['유효 가격 이력 종목',d.get('available_symbols')],
      ['표본 편향','오늘 후보군의 과거 사례만 포함, 과거 상장폐지/편입종목 미포함'],
      ['진입시점','신호 발생 다음 거래일 시가'],
      ['비용 가정','왕복 0.40%p, 예시값. 실제 세금·슬리피지와 다를 수 있음'],
      ['목표·손절 판정','같은 일봉에서 목표·손절 동시 도달이면 손절 우선. 갭하락은 시가 체결'],
      ['최대상승폭 MFE','해당 보유 기간 중 경로상 최대 유리 변동폭; 실현수익이 아님'],
      ['최대하락폭 MAE','해당 보유 기간 중 경로상 최대 불리 변동폭'],
      ['검증결과 한계','종목 간 시장 동조성, 배당·기업행동 미검증, 상장폐지 편향. 확률 보장 불가'],
      ['최소표본','40 사례, 최소 10종목·12분기 미달 시 공란'],
      ['DART 재무','아직 점수에 반영되지 않음. 실제 과거 공시 공개일 기준 별도 연동 필요'],
      ['KRX 업종','업종별 과거 지수 데이터 연동 전. 아직 정량 확률에 반영 안 함'],
      ['데이터 원천','종목별 OHLCV/FinanceDataReader; 이후 공식 KRX/상장폐지 보완 필요']]
    header=['종목코드','종목명','시장','기간','상태','표본 수','서로 다른 종목 수',
            '서로 다른 분기 수','보유말 종가 수익+ 빈도 %','종가 손실/보합 빈도 %','상승빈도 신뢰구간 하단 %',
            '상승빈도 신뢰구간 상단 %','목표 선도달 빈도 %','손절 선도달 빈도 %',
            '전략 수익+ 빈도 %','비용차감 전략 평균수익 %','비용차감 중앙수익 %','수익률 하위10% %',
            '최대상승폭 중앙 %','최대상승폭 75분위 %','최대상승폭 90분위 %',
            '최대하락폭 75분위 %','종목별 구조적 손절폭 %','진입기간 참고','검증 제한']
    result=[header]
    for s in d.get('stocks',[]):
        for key in PERIOD_ORDER:
            p=s.get('periods',{}).get(key,{})
            valid=p.get('status')=='exploratory'
            result.append([s.get('code'),s.get('name'),s.get('market'),key,p.get('status','insufficient'),
                p.get('sample_n'),p.get('symbol_n'),p.get('quarter_n'),
                p.get('up_pct') if valid else None,
                p.get('down_or_flat_pct') if valid else None,
                p.get('up_wilson_range_pct',[None,None])[0] if valid else None,
                p.get('up_wilson_range_pct',[None,None])[1] if valid else None,
                p.get('target_first_pct') if valid else None,
                p.get('stop_first_pct') if valid else None,
                p.get('strategy_win_pct') if valid else None,
                p.get('mean_net_return_pct') if valid else None,
                p.get('median_net_return_pct') if valid else None,
                p.get('net_return_p10_pct') if valid else None,
                p.get('max_favorable_p50_pct') if valid else None,
                p.get('max_favorable_p75_pct') if valid else None,
                p.get('max_favorable_p90_pct') if valid else None,
                p.get('max_adverse_p75_pct') if valid else None,
                s.get('structural_stop_pct'),
                s.get('period_suggestion',{}).get('period',s.get('period_suggestion',{}).get('decision')),
                p.get('reason') if not valid else p.get('limitation')])
    sources=[['원천','공식 URL','활용'],
      ['가격/거래량','https://github.com/FinanceData/FinanceDataReader','일봉 OHLCV'],
      ['금감원 OpenDART','https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020','후속 재무 공개일 연동'],
      ['KRX 공개 API','https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd','후속 업종/과거 구성 보완']]
    out_path.parent.mkdir(parents=True,exist_ok=True)
    _xml_xlsx([('주의사항',desc,[26,105]),
               ('퀀트유사조건',result,[14,23,12,19,17,11,14,15]+[19]*17),
               ('데이터출처',sources,[23,85,35])],out_path)
    return out_path

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--json',default='docs/quant.json');p.add_argument('--output',default='reports/quant_study.xlsx')
    a=p.parse_args();print(create(Path(a.json),Path(a.output)))
