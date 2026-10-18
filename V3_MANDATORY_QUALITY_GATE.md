# v3 필수 데이터 품질 게이트 — API 승인 전 구현

새 v3_mandatory_quality_gate.py를 실행형 v3_offline_workbench의 **모든 종목**에 연결했다. 기존 PIT join 및 기업행위 검역을 통과하지 않고 연구상 점수가 완성돼도 실전 단계로 승격시키지 않는다.

* 회사 issuer_id·6자리 종목코드·시장·종가 기준일이 모두 같아야 한다.
* 워크벤치의 실제 점수 산출에 사용한 OHLCV와 독립 선언된 이벤트 가격 장부의 날짜·OHLC·정규장 라벨·거래량을 1:1 비교한다. 차이가 있으면 전면 거부한다.
* PIT 결합의 역사적 종목 목록, 주가·투자자 250일 수급·공시 정정 시점·업종 분류가 신호 발생 시점 전에 이미 관측된 것인지 확인한다.
* 기업행위/거래정지/비정상 급등락 발생일 ±2 관측봉은 연구 신호에서 제외한다.
* 증빙이 없으면 quality_blocked. 증빙이 완전히 일치해도 internally_aligned_not_authenticated만 가능하다. 형식·자체 일관성 검사는 진짜 거래소 공인 과거 기록이라는 증거가 아니다.
* eligible_for_confirmed_scoring=false, eligible_for_real_backtesting=false, trade_approval=false, v3_release_ready=false 유지.
* 기존 합성 64종목은 점수 계산을 수행하지만 전 종목 증빙 상태가 blocked임을 집계한다. 데이터가 없는 경우 합성 점수가 높더라도 실제 추천은 금지한다.
* 기존 v2.2 라이브 스크리너/Excel/Pages 공개 결과나 API Secrets는 변경하지 않는다.

테스트: python -m unittest discover -s tests -p 'test_v3_mandatory_quality_gate.py' -v
