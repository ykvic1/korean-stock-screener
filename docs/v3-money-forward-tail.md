# V3.0b 최근 거래대금 5거래일 증분 원장

- 과거 10년 `derived/money-historical-checkpoint.json`은 **490개 코호트 역순 수집 상태 그대로 보존**한다. 종료일을 오늘로 변경하여 기존 코호트의 인덱스를 이동시키지 않는다.
- 과거 날짜 후보가 5일로 나뉘지 않아 남은 마지막 **2거래일 후보(2026-10-15~10-16)**부터 이어 붙여 첫 증분 5거래일 묶음을 생성한다. 후속 증분 묶음과의 단절을 방지한다.
- 새로운 `derived/money-forward-checkpoint.json`은 각 확인된 5거래일 접두 구간의 해시를 저장한다. 오늘 이후 새 후보 날짜가 추가되어도 과거 저장된 묶음·구간을 재해석할 수 없다.
- 한국시간 08:57에 수집을 시도하고 20:57과 01:57에 재시도한다. 5거래일 후보가 채워지고 마지막 거래일 자료의 다음 영업일 08:00 공개 시각이 지난 뒤 조회한다.
- 실제 금액은 공식 `ACC_TRDVAL`을 읽고, 원본 일별 OHLCV·거래대금은 보관하지 않는다. 개인 비공개 저장소에 오직 검증 가능한 비재구성형 5일 파생값만 저장한다.
- 기존 과거 거래대금 수집과 **동일한 동시실행 잠금**을 사용해 저장소 PUT 충돌 및 과도한 요청을 피한다.
- 기업행위, 상장폐지·코드변경, 정규장 전용임을 독립 검증하기 전에는 `score_authorized=false`다. 증분이 없거나 실패해도 기존 종목의 최종점수나 순위를 삭제·0점 처리하지 않는다.
- **실제 달력 주봉과 당일별 금액 OBV는 별도 검증 대상**이다. 5일 코호트를 정확한 1주일로 표시하지 않는다.

진행률은 GitHub Actions의 `V3 KRX actual-money after-anchor forward private derived cohorts` 실행 결과 중 'completed_forward_five_session_cohorts' 항목으로 확인한다. 개별 비공개 파생값은 공개하지 않는다.

## 정규장 별도 필드 대조

[코스피](https://koscom.gitbook.io/open-api/api/marketv3/stocks/closeda)와 [코스닥](https://koscom.gitbook.io/open-api/api/marketv3/stocks/closedb) 종목 종가 명세에 별도의 `mainAccTrdVol`과 `mainAccTrdVal`이 있다. 공개 응답 예시에는 `main*` 필드가 빠져 있으며 과거 `history` 명세에도 이 필드가 없다. 실제 자료의 거래일·종목·제공 범위는 응답과 계약 명세를 확인한다. 코스피 명세의 `mainAccTrdVol` 단위 표기와 실제 제공 단위도 확인 대상이다.

`python v3_koscom_regular_close_probe.py samples.json docs/v3-regular-close-prices.json`은 제공받은 종목 종가 응답의 정규장 필드가 모두 존재하는지 확인한다. `samples.json`은 `asof`와 `samples`를 가진 객체이며 각 항목은 요청 경로의 `market`, `code`와 원본 JSON `response`를 담는다. 응답 `result`에 원천 거래일 `trdDd`가 있어야 동일 날짜의 공개 KRX 확인 종가와 대조한다. 공개 예시처럼 날짜나 정규장 필드가 빠진 응답은 해당 결측으로 집계한다.

이 도구는 입력을 로컬에서 읽어 시장별 건수와 입력 해시를 반환한다. 종가 일치만으로 거래량·거래대금 세션 범위 인증이나 과거 OBV 검증이 성립하지 않는다.
