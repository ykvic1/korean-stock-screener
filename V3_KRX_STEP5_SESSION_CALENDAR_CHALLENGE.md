# KRX 거래일 달력 검증 — Step 5: 거래소 양시장 관측과 XKRX 후보의 실제 대조

## 설계 이유

기존 XKRX 250개 후보는 제3자 라이브러리의 휴장일 계산이며, **한국거래소(KRX) 공식으로 확정된 250거래일**이 아니다. 예를 들어 제3자 일정에 없는 임시 거래소 휴장일이나 향후 일정 수정이 포함될 수 있다. 기계적으로 이 날짜들을 가격 히스토리의 인증된 날짜로 오인하지 않도록 실제 KRX 일별 시세 API의 **날짜 일치 증거**와 대조한다.

## 이번 개발 결과

- `v3_krx_session_challenge.py`: 완료된 **1~5개** 평일 날짜를 수동 선택해 XKRX 4.13.1의 개장일 후보와 비교한다. 기본 `dry_run`은 API **0회** 호출.
- `live`를 명시해야 KRX 공식 **KOSPI/코스닥 일별매매정보**(기존 승인된 두 API) 각각 조회한다. 각 날짜 최대 **2개 API 호출**, 한 실행에서 최대 **10개** 요청. 외부 API에 대량 요청하지 않으며 자동 반복·예약 없음.
- KRX에서 원본 JSON·각 행 BAS_DD·중복 종목코드·OHLC 값 검사를 통과한 두 시장 자료가 있으면 `paired_date_observed_in_krx`. 그것도 해당 날짜에 유효 데이터가 **관측됐다는 증거**이지 날짜 범위 전체의 독립적인 휴장일 인증은 아니다.
- XKRX 제외일에도 두 시장 자료가 관측되면 `xkrx_excluded_day_has_two_market_observations` → 워크플로 **실패**로 표시하고 달력 자료 재검토.
- XKRX가 개장일이라고 예상했는데 두 시장에 행이 없는 경우 `xkrx_session_but_two_market_empty_unverified` → **실패**. 특정 날짜가 실제 휴장했는지, API 장애인지 확인되기 전에는 스킵·보정하지 않는다.
- XKRX 제외일에 KRX가 두 시장 모두 `OutBlock_1=[]`을 반환하면 `xkrx_exclusion_and_krx_empty_unverified`. **빈 응답은 거래소의 공식 휴장확인서가 아니다**. 영구 거래일 인증에 활용할 수 없다.
- 양 시장 응답 불일치, KRX 401/403/429/5xx, 요청일과 내부 날짜 불일치, 중복 종목코드, 가격·거래량 구조 오류 등은 `source_error_or_unverified_response` 혹은 시장 불일치로 **차단**.
- 공개 GH 로그 및 임시 7일 private artifact는 날짜·시장별 관측 상태·원본 해시·검역 **건수**만 사용. 종목코드, 가격, 거래량 원본, 인증키, 요청 URL, 임의 원문 오류 미포함.
- CI는 합성 KRX 응답만 사용하며 실제 인증키·실제 KRX 호출 없음.

## GitHub에서 수동 표본 대조
`Actions → KRX session-calendar challenge (manual research only) → Run workflow`.

- Branch: `main`
- dates: `2026-09-23,2026-09-24` — XKRX상 **개장 후보 1일 / 제외 후보 1일**. 기준일은 이미 지났지만 실제 시장 관측은 실행 후에야 확인된다.
- mode: 먼저 `dry_run`, 실제 2×2회 조회가 필요한 경우에만 `live` 선택.
- 2일 모두 API가 빈 응답이라고 해서 공식 휴장이나 전면 장애를 자동 확정하지 않는다.

## 250일 영구 누적의 현재 제한
1. 현재 2025-10-13~2026-10-16의 250일은 **XKRX 후보**일 뿐 원본 KRX 250일의 개인용 영구 SQLite 축적 증빙은 없음.
2. 이 수동 일자 챌린지는 개인 저장소에 날짜별 원본을 저장하지 않음.
3. KRX 운영 문서에 따른 원본 데이터 저장 및 재배포 라이선스/목적 범위는 운영자가 직접 확인해야 함. 암호화 저장, 독립 백업/원본 리비전 감지, 종목기본정보 신청, 수정주가 및 정규장 거래량 검증은 별도.
4. `official_calendar_whole_range_authenticated=false`, `historical_250_sessions_verified=false`, `confirmed_scoring_allowed=false`, `v3_release_ready=false`를 유지. 임의 날짜 스킵/가짜 일봉 보정/투자추천 사용 금지.
5. v2.2 자동 실행·보고서·공개 페이지·예약·지표·기존 KRX 일별매매정보 API 사용범위는 변경하지 않음.

## KRX 참고
- https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp
- https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
