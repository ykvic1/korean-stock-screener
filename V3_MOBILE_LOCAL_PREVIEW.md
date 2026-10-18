# v3 API 승인 이전 모바일 연구용 로컬 HTML 미리보기

v3_mobile_report.py는 기존 실행형 v3 연구 워크벤치에서 계산한 **6영역 점수·누락 자료·지표 중복·가상 손실 위험**을 모바일 친화적인 정적 HTML에 표시한다. 실제 주식 추천을 공개 게시하거나 기존 v2.2 화면을 변경하지 않는다.

## 합성 데이터로 실행

    python -m v3_mobile_report --demo --output /home/user/private/v3-research.html

예제 경로는 사용자가 통제하는 **비공개 로컬 디렉터리**다. 실제 경로 및 접근권한을 확인해야 한다. Linux/macOS에서 개인 디렉터리 접근권한 0700, 파일 0600을 요구한다.

64개 가상 종목(코스피·코스닥 각각 32개)의 원시 일봉·수급·공시·업종 구성으로부터 6영역 잠정 점수와 업종·상관분석을 실행한 뒤 HTML을 생성한다. 공식 KRX/OPEN DART 호출이나 매매 주문은 없다.

개인 비공개 JSON 연구자료도 지정할 수 있다:

    python -m v3_mobile_report --input /home/user/private/research.json --output /home/user/private/v3-research.html

## 출력 정보

* 맨 위에 **실전 매수·매도 추천이 아니라는 경고**를 명시한다.
* 입력 유형(SYNTHETIC_DEMO/UNVERIFIED_LOCAL_INPUT), 분석일, 전체 종목 수, 완성 6영역 자료 종목 수, 지표 중복 경고 수를 표시한다.
* 종목코드 순서의 모바일 접이식 카드. **점수순 정렬이 아니고 확정 투자 순위가 아니다.**
* IAS25, 재무20, 가치15, 기술15, 업종15, 모멘텀10 잠정 연구점수, 자료 커버리지, 빠진 원시 지표 개수 표시.
* 결측을 0점 대신 미확인으로 표시한다.
* 사용자가 별도로 입력한 가상 손절/갭 위험 예산이 있으면 계좌 하방 스트레스 값을 표시하되 매매승인으로 해석하지 않는다.

## 보안과 실전출시 게이트

스크립트·외부 네트워크 호출을 사용하지 않고 CSP로 외부 자원 접근을 제한하며 출력 텍스트를 HTML escape한다. 공개 저장소 내부, 특히 docs 폴더 아래에는 로컬 보고서를 쓸 수 없도록 차단한다. 안전한 임시파일 후 원자적 교체로 HTML을 완성한다.

이 기능은 데이터 원본에 대한 수집 권한, 공시 PIT, 과거 상폐기업 전체, 유통주식수, 가격 수정이력, 정규장 거래량 및 성능 검증을 대체하지 않는다. 확정 점수·확정 순위·미래수익률은 없으며 v3_release_ready 및 confirmed_buy는 항상 거짓이다.

    python -m unittest discover -s tests -p 'test_v3_mobile_report.py' -v

기존 v2.2 자동 실행, 공개 Pages, Excel 및 공식 API 연결은 수정하지 않는다.
