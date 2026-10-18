# v3.0 통합 스크리너 전환 계획 — 데이터 신뢰성 우선

**작성 기준:** 2026-10-17. `main`의 v2.2 운영 스크리너와
OpenDART 수동 연구 파이프라인은 그대로 유지한다.
이 문서는 v3.0 출시 약속이나 투자 성과 예측이 아닌
**실행 순서와 검증/출시 게이트**다.

## 1. 현황 — 실제 파일에서 확인한 숫자

| 구분 | 2026-10-16 최신 발행 자료 | 해석 |
|---|---:|---|
| 정상 수치 연속 구간 | 106/120 | 전체 이력 검증 아님 |
| 원본 고정 실패 코호트 | 53종목 | 매일 바뀌는 수집 유니버스가 아님 |
| 고정 53 연속 구간 회복 | 37/53 | 부분 구간만 정상 |
| 고정 53 전체 이력 검증 | 0/53 | 손익·수급 검증에 사용 금지 |
| 독립 가격 검증 | 0/120 | 상이한 공급원 대조 미확인 |
| KRX 정규장 가격·거래량 | 0/120 | OHLC와 정규장-only 거래량 모두 필요 |
| 외국인·기관 수급 확정 | 0 | 현재 Npay 수급 수집 결함 |
| 공식 재무 PIT·OFS 인증 | 미완료 | OpenDART 5개 기업 원본 연구 성공 ≠ PIT 승인 |

현재 `docs/official.json`은 `key_required`를 기록하고 있으며,
이를 GitHub Secrets 설치/승인 여부의 결정적 증거로 사용하지 않는다.
**실제 공식 KRX 서비스 승인과 정규장 전용 거래량 정의를
확인하지 않은 상태**다.

## 2. 출시 목표와 진행 순서

### 단계 A — v3 배포 차단 조건을 코드화 (이번 PR)

- `v3_release_gate.py`는 `docs/reliability.json`,
  `docs/quant_v22.json`, `docs/data.json`,
  `config/v22_baseline_53.json`을 **읽기 전용**으로 평가한다.
- 고정 53종목의 전체 가격 이력, KRX 정규장 종가와 정규장-only
  거래량, 20/60/120/250일 외국인/기관 수급, 과거 유니버스,
  사업보고서 PIT/OFS, 업종 멤버십, 점수 정규화 산식,
  변경 이력 저장, v3 모바일/엑셀 실검증을 **별개 게이트**로 표시.
- 현재 지원되지 않는 독립 증거는 `false` 고정한다. 단순 플래그
  변경만으로 통과할 수 없도록, 새 공식 공급원 검증이 도입될 때
  해당 게이트별 검증기를 별도로 교체한다.
- 출력은 전체 준비 상태/차단 이유만 포함하고 **v3 점수·순위
  생성은 하지 않는다**. 현재 `v3_release_ready=false`.

### 단계 B — 가격·거래량 공식 검증 (최우선 개발)

1. KRX 승인 계정·허용 API 서비스·약관·세션 범위를 인증키
   노출 없이 조사. 미승인 상황에서는 실제 호출/정규장 판정 없음.
2. 동일 종목·날짜의 정규장 OHLC, **정규장에 한정된** 거래량,
   NAVER/Yahoo, 기업행동·상장·거래정지 자료를 교차 대조.
3. 기존 고정 53개 종목의 모든 관찰 일자에 대해 원본
   SHA256/정제본/수정 사유/달력·세션/버전 증거를 기록.
4. 누락된 날짜와 상장폐지·액면분할을 임의 보간/삭제하지 않음.

**완료 기준:** 샘플 성공이나 37/53 회복이 아니라
고정 53/53의 전체 이력과 공식 세션 검증 및 원본 증거.

### 단계 C — 수급 데이터와 IAS 후보 연구

- 수급: 20/60/120/250거래일 외인·기관, 연기금·투신·보험.
  동일시장·동일세션·거래일 기준 검증 및 결측을 `null` 처리.
- 기존 사용자 설계 가중치(정식 점수 근거 아님):
  IAS25, 재무20, 가치15, 기술15, 업종15, 모멘텀10.
  IAS 내부: 장기보유20, 누적순매수25, 지속성15, 주체질10,
  OBV·거래량15, 가격 미반영15.
- 정규화·분모·누락·주체별 중복계산이 미정이므로
  `IAS 100` 점수, 정식 종합점수, 완전순위를 아직 생성하지 않음.
- 실적: 본문·별도/연결 범위·정정판·공시 시각별 버전을
  검증할 때만 지표 사용. 최신 현재시점 숫자를
  과거 동일한 값으로 간주하는 **룩어헤드 편향** 금지.

### 단계 D — v3.0 출시

- 공식 증거가 있는 부분점수만 `value/status/asof/source_refs`
  형태로 분리하여 개발; 결측치를 0점으로 치환 금지.
- 날짜별 주식 유니버스(상폐 포함), 업종 과거 편입, 원본 불변 저장,
  수정 시 지표 재계산, 데이터 수집 성공/실패 경계 확립.
- 전체 확인 배점과 획득 점수 분리. `confirmed_score`,
  `complete_rank`는 모든 입력 검증 및 산식 동결 전까지 `null`.
- 출시 전 CI + 실제 수집 테스트 + 모바일/엑셀 + 접근성 +
  기존 v2.2 비교 + 명시적 승인 확인.
- 투자가능성은 기대상승률보다 **유효한 입력, 손실 통제,
  실패시 원상복구**를 우선.

## 3. 즉시 실행 전략

- **지금:** 신뢰성 게이트 구축 및 스냅샷에 기반한 v3 차단 사유 확인.
- **다음 PR:** KRX 공식 정규장 OHLC와 정규장-only 거래량의
  서로 다른 증거를 표현하는 공급원 계약·오프라인 테스트.
- **다음 데이터 작업:** 기존 v2.2 Npay 수급 0건 문제를
  실제 브라우저 증거로 진단한 뒤 고쳐야 함.
- 외부 승인이 없거나 증거가 부족하면 억지로 전체 점수 출시하지
  않고 v3 연구/데이터 정비 경로를 계속 진행.
- v3.0 정식 릴리즈 여부는 `v3_release_gate`의 근거별
  검증 완료 및 운영 QA로 결정하고 **단순 버전명 변경 금지**.

## v3 KRX 정규장 OHLC와 거래량 관측 계약 (단계 B-1)

KRX 주식 일별매매정보는 일별 시세를 제공하지만 각 API의
인증키 신청과 개별 서비스 관리자 승인이 필요하다.
현재 계정의 실제 승인/사용 가능 여부는 미확인이다.

공식 출처:
https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp

신규 v3_krx_contract.py:
- 이미 숫자/날짜/종목 검증을 거친
  official_probe.validation.inspect_krx 결과만 입력. API 0회.
- 관찰된 OHLC는 ohlc_observed에 보존하지만 아직 독립 검증
  가격이라고 인증하지 않는다.
- ACC_TRDVOL은 aggregate_acc_trdvol_observed에만 보존,
  regular_only_volume=null을 강제. 정규장-only 거래량의
  정확한 범위가 인증되기 전까지 OBV/거래량 지표 금지.
- price_session=UNVERIFIED, volume_session=UNVERIFIED,
  confirmed_scoring_allowed=false, confirmed_score=null,
  technical_indicators_eligible=false 고정.
- 자기 신고로 volume_session_validated=true를 넣거나
  원본에 regular_volume을 추가하면 실패 처리한다.

**단계 B-1 결과는 코드 계약/오프라인 테스트 완료이지
실제 정규장-only 공급원 확인이나 v3 운영 승인이 아니다.**
다음은 KRX 공식 서비스 승인 후 승인된 제한된 샘플에서
정규장 가격/거래량 범위의 실증과 독립 대조를 수행하는 작업이다.

## v3 기관·외국인 수급 신뢰성: 운영 실패 원인 구분 (단계 C-0)

### 2026-10-16 공개 v2 결과에서 직접 확인된 내용

- 공개 `docs/data.json`: 수급 확정 0건, 수급 원천 접근 시도 **3건**,
  수급 오류 레코드 208건.
- 실제 공급원 타임아웃 **3건**: Npay의 기존 교환소 선택 요소
  `TraderTrend_trader-trend-toolbar-exchange`를 기다리다 실패.
- 후속 **205건은 별도 공급원 실패가 아니라 동일 연속
  타임아웃 차단기(circuit breaker)가 조회를 연기한 기록**.
- 따라서 208건 모두가 별도로 Npay에 접속해 실패했다고 해석하거나,
  외국인·기관 순매수 0주로 대체하는 것은 명백히 잘못됨.
- 현재 Npay DOM 구조를 라이브 재검증하지 않았으므로
  **새 CSS 선택자 추측, Npay API 응답 필드 추정, 사용자 인증 우회는 금지**.

### 새 오프라인 연구 계약

`python -m v3_flow_readiness --daily docs/data.json`:

- 오류 메시지의 직간접 원인과 시간 예산에 따른 미수집을 별도로 집계.
- 원본 수급 에러메시지 전체·종목별 민감한 임의 출력 없이 원인별
  숫자/확정 여부만 출력.
- `examine_daily_flow_candidate`는 향후 업체별 **날짜·기관·외인
  순매수주식수의 실제 일별 숫자**가 확보되었을 때 숫자/날짜/중복/
  신선도/20·60·120·250일 관측 가능 여부를 제한적으로 검증한다.
- 예를 들어 20건만 있다면 20일 관측값만 남기고 60/120/250일은
  **null** 처리. 외인·기관의 0주 실제 관측은 정수 0을 허용하나
  원본 결측은 절대로 0으로 채우지 않는다.
- 데이터가 모두 확보돼도 **공식 시장·정규장 세션, 진짜 거래일
  달력, 주체 정의, 독립 대조, 수급 투자 점수는 인증하지 않는다**.
- 모든 연구 결과에서 `confirmed_scoring_allowed=false`,
  `investor_flow_scoring_allowed=false`, `v3_release_ready=false`.

**운영 영향 없음:** 이 변경으로 기존 `flow_collection.py`,
`screener.py`, `daily.yml`/가격/점수/Pages/Excel은 변경하지 않는다.
필요한 다음 증거는 한 종목의 실제 DOM/테이블/페이지/거래소 선택
구조에 대한 일회성 안전한 읽기 전용 관찰 후 수급 20개 실제 거래일
수집·두 공급원 교차검증이다. 본 단계는 수급 0건을 고쳤다고
주장하지 않는다.

## 단계 C-1 — 실제 Npay 단일 종목 익명 조회·20행 검증 (2026-10-17)

과거 v2.2의 DOM 기반 수급 수집은 거래소 선택 요소 타임아웃
3건 이후 차단기 연기 205건으로 수급 확인 0이었다.

**한 종목(샘씨엔에스 252990)만 최소 요청으로 재검증한 결과**:

| GitHub 연구 실행 | 외부 요청 | 실제 관찰 |
|---|---:|---|
| #39030680950 | 1 GET | HTTP 200, JSON 배열 5행 및 공개 필드명 |
| #39030770908 | 1 GET | HTTP 200, JSON 배열 20행, 관련 4개 필드 모두 문자열 |
| #39030927395 | 1 GET | 20행 날짜·기관/외국인 부호 있는 주식 수량 문자열 변환 성공 |

**실제 관찰된 필드명**:
`bizdate`, `itemCode`, `foreignerPureBuyQuant`,
`organPureBuyQuant`. 요청은 `tradeType=KRX`였지만,
**응답 원천·정규장 시간범위·시장주체 정의가 공식 인증됐다는 뜻은 아님**.
`tradeVolume` 필드도 관찰했으나 정규장 전용 거래량으로 사용하지 않음.

연구 모듈:
- `v3_npay_schema_probe.py`: 1회 익명 GET (252990 한 종목, 최대20행);
  응답 JSON의 필드명·자료형·개수만 출력.
- `v3_npay_numeric_probe.py`: 동일 연구 응답의 날짜/문자열 부호/
  수량 정수 변환과 중복·미래/누락·종목 불일치 차단을 시험.
  수집 숫자는 라이브 로그에 출력하거나 저장하지 않음.
- 두 모듈은 수급 신뢰성이 확정된 운영 공급원으로 **연결하지 않음**.
- 임시 push 트리거 GitHub 진단 워크플로는 3회 실측 후
  **최종 PR에서 제거**해 계속 외부 요청이 발생하지 않도록 한다.

### 다음 검증 및 운영 교체 조건

1. 자동 수집·재사용·배포 가능 여부를 포함한 서비스 이용 조건을 확인.
2. 신뢰 가능한 별도 KRX 출처의 동일 종목 동일 거래일 **순매수 수량**
   과 날짜·개장 세션 기준을 대조(20일 이상). 단순 동일 URL 반복 조회 불충분.
3. 20→60→120→250 실제 거래일의 거래일 달력/누락/주체 세부 분류 검증.
4. 실패 시 null·기존 정상 수집 보존·재시도 제한·타임아웃 회로 보호를
   확인한 뒤에만 v2.2 운영 수집기 교체 후보로 검토.
5. IAS 공식 점수·순위·v3 출시 게이트는 앞선 독립 증거 완료 전까지 차단.

현재 결과는 **익명 공개 데이터의 20행 숫자 구조 해석 성공**이며,
공식 KRX 투자자별 수급 인증 또는 상용 자동 이용 허가가 아님.

## 단계 C-2 — Npay 20일 × pykrx(KRX 인터페이스) 교차대조 연구

KRX 정보데이터시스템에는 개별종목 투자자별 거래실적 메뉴가 존재하지만
공식 OPEN API에서 해당 필드·서비스를 현재 사용자 계정이 승인받아
접근할 수 있는지는 **아직 확인되지 않음**.
- 공식 KRX 데이터 메뉴: https://data.krx.co.kr/
- 공식 Open API 서비스 및 이용승인: https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp
- 기존 `screener.py`가 pykrx `get_market_trading_volume_by_date`를
  사용하지만 과거 정기 실행은 이 방법에 대한 인증이나 교차 검증이 없었음.

### 구현: 엄격한 두 출처 비교, 운영 연결 없음

- `v3_flow_crosscheck.py`는 이미 실증한 Npay `organPureBuyQuant`,
  `foreignerPureBuyQuant` 문자열과 pykrx 응답 `기관합계`,
  `외국인합계` 숫자를 **공통 최근20개 실제 일자**별로 비교.
- 날짜가 하나라도 다르면 `trading_day_sets_differ`;
  외인 또는 기관 중 하나라도 다르면 `investor_quantities_mismatch`.
  일치 건수만 기록하며 숫자 원문/날짜별 순매수는 출력·커밋 금지.
- 한 출처의 오류, 날짜 중복, 누락, 잘못된 주체 컬럼, 정수 아님,
  미래자료, 주식코드 오류, 20개 미만이면 fail-closed.
- `v3_flow_crosscheck_once.py`는 한 종목(252990), 기준일(2026-10-16),
  Npay GET 1회와 pykrx 조회함수 1회만 시도. 두 자료 모두
  실패 없이 있으면 일치 건수 요약만 로그에 출력.
- **pykrx는 공식 KRX 유료·허가 API의 인증과 동의어가 아니며**
  두 출처가 완전히 일치해도 정규장 09:00~15:30 주체/거래량,
  자동 데이터 활용 허가, 거래일 PIT, 60/120/250일 수집은
  별도 검증해야 한다.
- `confirmed_scoring_allowed=false`, `v3_release_ready=false`,
  `ias_flow_component=null` 불변.
- 네트워크 결과가 없는 유닛테스트는 오프라인. 임시 branch-only
  실조회 워크플로는 단발 실험 후 PR에서 제거하며 main 예약 실행으로
  연결하지 않는다.

합의되지 않은 약관/응답 정의·KRX 공식 유료 계정 키를 추측하지
않는다. 현 단계는 데이터를 **독립적으로 대조할 수 있는지** 확인하며
정식 v3 출시 일정 약속이 아니다.

### 2026-10-17 실제 1회 교차대조 실행 결과

한 종목 252990 / 거래일 기준 2026-10-16 / 임시 연구 GitHub Actions
#39032607039 (read-only, 1회):
- 오프라인 7개 회귀테스트 통과.
- Npay 익명 공개 트렌드 20행 파싱 완료. 제공 데이터 해석 수준은
  날짜·기관/외인 부호 있는 순매수 숫자이며 독립 검증 미완료.
- 이어서 pykrx `get_market_trading_volume_by_date` 1회 호출했으나
  내부 KRX 제공자 함수 `get_market_trading_value_and_volume_on_ticker_by_date`
  처리 단계에서 `Expecting value: line 1 column 1 (char 0)`
  메시지와 함께 **`ValueError` 발생**.
- 연구 결과: `alternate_krx_interface_unavailable_or_invalid`,
  `independent_crosscheck_verified=false`,
  `confirmed_scoring_allowed=false`.
- 이것은 공급원 조회/응답 형식 문제이며 **실제 Npay vs KRX 숫자
  불일치라고 판단할 근거가 아니다**.
- 차단 원인은 KRX 승인 부재라고 단정하지 않는다(현재 서버 응답 원문,
  라이브 인증/스키마 진단 미확보).
- 실제 요청은 이 1회로 종료. 무한 재시도·봇 탐지 회피·예측용 숫자
  채택 없이 임시 GitHub push 실험 워크플로 삭제.

**다음 우선순위:** 공식 KRX 개별종목 기관/외인 수급 조회의
제공 서비스 범위/계정 권한, 검증 가능한 원본 단위와 거래소
세션 계약을 확인. 이후 다른 독립 공급원이 제공되는 경우,
이번에 구현한 일자별 20/20 수량 비교기를 재사용한다.
현재 시장 데이터 자동화·재배포는 라이선스 확인 전 보류.
v3 IAS 수급 배점과 완전 순위는 여전히 미출시.

## 단계 C-3 — KIS 공식 문서 기반 투자자별 일자 수급 계약 (오프라인)

2026-10-17 재확인한 공식 한국투자증권 Open API 소스:

- 한국투자증권 공식 API 카탈로그:
  https://apiportal.koreainvestment.com/apiservice-category
  - 국내주식 기본시세: **주식현재가 투자자**
  - 국내주식 시세분석: **종목별 투자자매매동향(일별)**
- 공식 GitHub 예제:
  https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor/inquire_investor.py
- 공식 필드 매핑:
  https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor/chk_inquire_investor.py
- 요청 계약: 실전 TR `FHKST01010900`,
  경로 `/uapi/domestic-stock/v1/quotations/inquire-investor`,
  `FID_COND_MRKT_DIV_CODE=J`(KRX), 종목코드,
  응답 `stck_bsop_date`, `frgn_ntby_qty`, `orgn_ntby_qty`.
  외국인 항목은 '외국인+기타외국인'을 뜻할 수 있어 Npay
  정의와의 동일성은 **실제 일자별 비교 전에는 미인증**.

`v3_kis_investor_adapter.py`는 상기 공식 샘플 스키마의
**오프라인 가상 응답**을 엄격히 검사하여 기존 Npay 날짜별
20개 비교기와 연결한다. 누락/문자열-소수/중복/미래 날짜/
틀린 종목/잘못된 시장코드/20행 미만/응답 성공코드 오류는
예외 처리한다. 연결·토큰 발급·로그 출력·주문/거래·구독은 없다.

KIS 실제 이용 전:
1. 사용자/계정의 KIS Open API 서비스 이용·API 키 승인 확인.
2. 별도 GitHub Secrets에만 키를 등록(코드·로그·채팅 입력 금지).
3. 실제 단일 종목/최대20~30거래일 자료를 공식 응답과
   Npay 후보의 **일자별 수량 일치**로 연구 검증.
4. 시장/체결세션, 확정·가집계 여부, 재이용 조건,
   수급집계 주체 및 최근 60·120·250일 확장성을 별도 평가.

지금 당장 KIS 키가 없고 실조회도 수행하지 않았으므로
`kis_live_authentication_and_service_access_verified=false`,
`kis_historical_source_retrieval_verified=false`,
`confirmed_scoring_allowed=false`, `v3_release_ready=false`
고정. pykrx 인터페이스 오류와 독립된 현실적인 대안 후보이다.


## 단계 C-4 — KIS 종목별 투자자매매동향(일별) 독립 계약 (오프라인)

공식 KIS 저장소에는 PR #34에서 연구한 **주식현재가 투자자**
`FHKST01010900`과 구분되는 API가 있다. 이를 혼동해
장기 수급/연기금 확정치로 승격하면 안 된다.

- 공식 소스:
  https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py
- 공식 필드 매핑:
  https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/chk_investor_trade_by_stock_daily.py
- 서비스: **종목별 투자자매매동향(일별)**,
  TR `FHPTJ04160001`,
  GET `/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily`.
- 요청 계약: `FID_COND_MRKT_DIV_CODE=J`, 6자리 종목코드,
  기준 조회일, 공란 가격/기타 구분 필드. 공식 Python 샘플은
  `output1`/ `output2`와 `tr_cont` 연속조회 처리를 별도로 보유.
- 일별 수급 후보: `output2`의 `stck_bsop_date`,
  `orgn_ntby_qty`, `frgn_ntby_qty`; 세부 후보는
  `ivtr_ntby_qty`(투자신탁), `insu_ntby_qty`(보험),
  `fund_ntby_qty`(기금). **'기금'만으로 '연기금'이라고
  단정하지 않음**. 세부 주체를 기관 총합에 중복 가산하지 않음.

새로운 `v3_kis_daily_investor_adapter.py`는 실제 공급자
조회 없이 이미 전달받은 **가상/오프라인 `output2`**를
검사한다. 20~40개 일별 레코드, 기준일, 종목코드, 시장,
부호 있는 정수 수량, 중복/미래/결측을 fail-closed 검증하고,
Npay와 **동일한 최근20일 날짜별 일치 여부만** 요약한다.
세부 주체가 전부 누락되면 null, 일부 날짜만 존재하면 실패.
30행 합성 테스트는 실제 30거래일 수신 실증이 아니다.
`output`(주식현재가 투자자용)을 `output2`로 몰래 해석하지 않음.

이 단계가 **검증하지 못한 것**: 실제 KIS API 계정/권한,
응답 원본성, 확정/가집계·시장/시간 세션, 60/120/250거래일
연속조회 성공, KRX 원본과 독립 일치, 자동 수집/상용 재이용
허용, 연기금 범주 동일성. 모든 점수와 v3 출시 게이트
false/null 유지. 키를 저장하거나 라이브 조회/주문/배포하지 않음.

**다음 필요 증거**: KIS 접근 승인과 사용범위 확인 후
단일 종목/일자부터 실증하고 일별 20건 교차대조.
별도 KRX 정규장-only 거래량·53종목 전체 이력 및
재무 PIT 확인 전에는 어떠한 완전 종목 순위도 발행하지 않음.


## 단계 C-5 — KIS 분할응답/연속조회 영수증 무결성 연구 (오프라인)

공식 KIS 종목별 투자자매매동향(일별) 예제는 `output2`
자료와 응답헤더 `tr_cont`가 `M` 또는 `F`인
경우 다음 요청의 `tr_cont="N"`을 사용한다.
이 규약을 데이터 확인 없이 단일 페이지 250거래일이라고
간주하면 누락·중복으로 IAS 점수가 왜곡될 수 있다.

- 공식 예제: https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py
- 새 `v3_kis_daily_pagination.py`는 외부 네트워크 요청 없이
  **이미 전달받은** 요청/응답/헤더 영수증의 형식·순서만 검증.
  입력 요청 종목 `252990`, `J`, 날짜 `20261016`,
  수정가격/구분 공란, 후속요청 `N` 일치 요구.
- 페이지별 1~40행, 최대 16페이지/250행(연구용 안전한 상한).
  응답header가 `F/M`인데 다음 페이지가 없거나,
  끝났는데 추가 페이지가 존재하면 전체 실패.
- 페이지 경계를 포함해 날짜가 엄격한 내림차순인지,
  중복/미래일이 없는지, 기관·외국인 수량이 문자열 정수인지,
  세부주체 필드가 부분적으로만 빠졌는지 전수 확인.
  한 필드라도 누락/오류이면 임의 보간/0 치환 없이 실패.
- 전체 이력 후보 자료를 관찰했더라도 비교는 가장 최근
  20일의 원본 날짜·숫자에 한정. 250개 **합성 테스트**는
  실제 250거래일 응답 성공 또는 거래소 달력 증거가 아니다.
  영업일 연속성/휴장일 여부는 KRX 달력의 독립 인증 전까지
  판정하지 않는다.
- KIS 실계정 승인, 원본 응답 진위, 거래소 정규장 세션,
  주체 정의(기금!=인증된 연기금), Npay 이용약관,
  60/120/250일 이력, 데이터 수집/재배포 허용은 계속 미확인.
  모든 `confirmed_scoring_allowed=false`,
  `ias_flow_component=null`, `v3_release_ready=false`.
- 기존 운영 코드, 스케줄, GitHub Pages 및 엑셀 변경 없음.
  후속 실제 제한조회는 승인된 서비스/Secrets/약관이 확인된
  뒤에만 별도 범위로 실시.


## 단계 C-6 — 키움 REST API ka10059 기관·외국인·연기금등 수급 검증 계약 (오프라인)

사용자는 기존 키움증권 계좌에서 REST API 사용을 **신청 완료**했다.
그러나 신청 사실만으로 **앱 등록, App Key/Secret 발급, 실거래
API 접근 성공**까지 인증한 것은 아니다. 키는 채팅·커밋·웹 문서·
로그에 입력하지 않는다. GitHub Secrets 설정은 실제 발급 후 별도 확인한다.
계좌주문/주문 API, 자동매매, 포트폴리오 거래 실행은 범위 밖이다.

키움증권 공식 소스:
- https://github.com/Kiwoom-Securities/Kiwoom-REST-API
- https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/종목정보/get_domestic_stock_investor_by_institution.py

**확인된 공식 ka10059 계약**
- POST `/api/dostk/stkinfo`, 요청 헤더 `api-id: ka10059`.
- 요청 `dt`(YYYYMMDD), `stk_cd`(6자리 KRX 코드),
  `amt_qty_tp=2`(수량, `1`은 금액),
  `trde_tp=0`(순매수, `1/2`는 매수/매도),
  `unit_tp=1`(단주, `1000`은 천주).
  이 코드를 다른 TR의 코드표와 절대로 혼합하지 않는다.
- 응답 `stk_invsr_orgn` 배열의 `dt`, `orgn`(기관계),
  `frgnr_invsr`(외국인), `penfnd_etc`(연기금등),
  `invtrt`(투신), `insrnc`(보험),
  `fnnc_invt`(금융투자), `samo_fund`(사모펀드).
  **연기금등은 국민연금 단독 매매와 다르며**,
  기관계와 세부 주체를 중복 합산하면 안 된다.
- 공식 예제의 `cont_yn`·`next_key`를 사용한 연속조회
  구조와 최대 10페이지를 오프라인 영수증으로만 검증한다.

신규 `v3_kiwoom_flow_adapter.py`는 실제 요청이 아니라
**가상/사전 제공된 페이지 영수증**만 해석한다. 페이지 이어짐,
날짜 엄격한 내림차순·중복·미래일, 20~250행 한도, 날짜별
부호 있는 주식 수량, 항목 완전 결측/null vs 실측 0주를 구분한다.
기존 `v3_flow_crosscheck.audit_research_pair`를 재사용해
Npay 후보와 최근20일 수량을 날짜별 비교하되, 모든
`confirmed_scoring_allowed=false`, `ias_flow_component=null`,
`v3_release_ready=false`를 유지한다.

**추후 실측에서 확인할 사항**
1. 키움 API 사용 승인/앱 등록/키 발급 및 사용 환경(실전·모의),
   허용 IP, 자동 데이터 수집·보관·재배포 약관.
2. 샘씨엔에스(252990) 단일 종목 제한조회로 응답 원본의
   단위·실제 조회 기간·연속조회·거래일 확인. 인증 전 호출 금지.
3. Npay와 기관/외국인 수량 20/20 일자별 독립 대조,
   시장/거래세션·투자자 주체 정의·확정/잠정 상태 확인.
4. 검증 가능한 60/120/250 거래일과 실제 공급 한도를 확인.
   합성 250행 통과는 실제 250거래일 수신 증명이 아님.
5. 별개 KRX 정규장-only 거래량, 원본 고정 53종목 전체 이력,
   OpenDART 과거 PIT 확인 전에는 종합 IAS 점수·순위 출시 금지.

기존 `screener.py`, `flow_collection.py`, `daily.yml`,
Pages/Excel/공개 데이터, v2.2 운영 배포는 이 단계에서 변경하지 않는다.


## 단계 B-2 — 원래 오류 53종목의 우선 복구 대상 분리 (API 승인 전)

실제 `docs/reliability.json`(기준일 2026-10-16)과
`config/v22_baseline_53.json`의 **원본 고정 53개**에서 확인:

| 오류/연구 상태 | 종목 | 의미 |
|---|---:|---|
| 원본 650행 이상, 오류 이후 유효 연속 구간 650행 미만 | **10** | 원본 문제 행·거래정지·기업행동/공급원 차이 확인 필요 |
| 관찰된 원본 650행 미만 | **6** | 실제 상장/자료 제공 기간부터 확인, 상장 전 가격을 만들어내지 않음 |
| 연속 숫자 구간 650행 이상 | **37** | 기술지표 후보의 부분 이력일 뿐, 전체 가격 시계열 복구 인증이 아님 |
| 53개 모두 공식 검증 완료 | **0** | Yahoo/Naver 비교가 KRX 정규장 전용 검증을 대신할 수 없음 |

53개 중 **53개 모두** 기존 Yahoo/Naver 가격 비교 상태가
`mismatch_or_insufficient`이며, 최근 대조 가격·거래량의 세션
범위와 수정주가 정책은 여전히 독립 인증되지 않았다.
Yahoo/Naver 최근 거래량 비교 47종목은 하나 이상 일치하지
않았지만, 이 사실만으로 어느 공급원이 잘못됐다고 단정하지 않는다.

이번 `v3_fixed53_triage.py`는 **다운로드·API 요청 0회,
읽기 전용**으로 실제 원본 고정 53 멤버십과 Blob SHA를
`baseline_cohort.load_cohort()`에서 검증한 후,
기준일·데이터 개수·기초 종목코드·날짜·연속구간·교차비교
집계의 무결성을 확인한다. 원본 650행 미만은 재수집이나
상장일 확인이 필요하고, 문제 행 이후 연속 숫자 구간이
650행 미만이면 원본 오류/기업행동 검증 대상으로 우선 분리.
분류가 충돌하거나 본래 53종목이 사라지면 출력 자체를 거부.

`python -m v3_fixed53_triage` 실행 시 JSON으로 53개의
**검증 필요 우선순위**와 관측 수치만 표시하고,
원래 `docs` 파일·캐시·운영 수집기·분석/점수/엑셀·Pages는
수정하지 않는다. 결과의 `independent_full_history_verified=0`,
`regular_session_only_volume_verified=0`, `confirmed_score=null`,
`v3_release_ready=false`는 실제 외부 증거가 확보될 때까지
변경하지 않는다. 이 도구는 데이터 오류를 자동 복구하지 않는다.

향후 키움 REST/공식 KRX 데이터 권한을 확보하면 **우선
10개 격리 후 연속구간 부족 종목**부터 날짜별 정규장 OHLCV,
액면분할·합병·거래정지 확인을 하고 6개 짧은 이력은
정상 상장 이력 부족과 공급원 누락을 구분해야 한다.


## 단계 C-7 — PC 없는 자동 수급 운영 방식 (2026-10-18 정책 변경)

사용자 요구: 평일 자동으로 수급을 수집해야 하며 PC/PowerShell 수동 실행은
개발 중 일부 검증에만 사용. **기관·외국인 수급의 다른 공급원 간
일별 수량 대조는 정식 운용의 필수 조건에서 제외**한다.

- 2026-10-18 등록 IP Windows 사용자의 화면에서 키움 실전
  `ka10059` 100행 수신(2026-05-20~2026-10-16) 보고 확인.
  따라서 기관/외국인/연기금등 수급의 **주 공급원 후보**는 키움.
  라이브 수신 100행 자체는 서버의 독립 인증/시장 세션·
  거래일 달력·장기조회 250행/자동수집 이용약관 증명이 아니다.
- `v3_kiwoom_auto_collector.py`는 **별도 고정 송신 공인 IP 서버용**
  읽기 전용 후보 수집기. OAuth 1회, 관심종목 최대35개, 한 종목
  최대3페이지, 1초 간격, 20/60/120/250행 관측 수급·순매수
  지속성의 부분 지표를 생성한다. 누락 범위는 null이다.
- 유효 날짜·부호/단주 수량·페이지 연속성·중복·집계 범주를
  **내부 무결성 검증**하고 키움 공급원임을 명시한다.
  `penfnd_etc`는 국민연금 단독과 다르며 기관계에 중복 가산하지 않는다.
- 비밀/원자료는 사용자 전용 사설 서버 경로에만 저장한다.
  저장은 원자적 방식이며 파일 0600/디렉터리0700 POSIX 권한.
  GitHub Pages, v2.2 Excel/공개 점수, 기존 자동 실행
  `daily.yml`는 변경하지 않는다.
- `deploy/*.example`은 별도 Linux 서버의 systemd 평일
  **KST 19:30** 스케줄을 설정하는 **비활성 템플릿**이다.
  현재 서버를 프로비저닝하거나, 외부 고정 IP를 키움에 등록하거나,
  자동 수집을 활성화하거나 별도 비용을 발생시키지 않았다.
- GitHub Secrets의 키가 외부 고정 IP 서버에 자동 전달된다고
  가정하지 않는다. 계정/지출·IP·서버 보안 및 키움 자동 이용약관을
  검토하고 1회 실제 호스트 시험 후 활성화한다.
- **이제 서로 다른 기관 수급 공급원의 수치 일치가 없다는 이유만으로**
  키움 기반 *후보 분석* 자체를 차단하지 않는다. 단,
  거래세션/기본 입력의 정확성, 기업 재무 PIT, 원래 53종목
  OHLCV 복구, v3 점수 정규화는 별개로 계속 검증한다.
  정식 종합 점수와 완전순위의 게이트는 여전히 미완료 상태다.

## 단계 C-8 — 2026-10-18 KRX 우선, 외부 고정 IP 서버 보류

사용자가 별도 서버의 무료 운영·PC 수동 갱신을 최종 대안으로만 허용하므로, **KRX 공식 무료 Open API의 가격·거래량 + KRX 데이터 홈페이지(공식 Open API와 별개)의 종목별 기관/외국인/연기금 수급 연구를 우선한다**. KRX 공식 무료 Open API에는 현재 투자주체별 순매수 endpoint가 공지된 서비스 목록에 없다. 기관/외국인 수급은 `v3_krx_website_flow_pilot.py`로 GitHub Actions에서 **1종목 수동 현장 시험** 후 무인 수집 가능 여부를 판단한다. 그 전에는 공식 수급 자동수집 또는 v3 IAS 점수 연결을 주장하지 않는다. **OCI/키움 서버와 키움 자동수집 활성화는 보류**한다. 별도 상업 계약이나 PC 매일 실행은 추진하지 않는다. 자세한 내용은 `V3_KRX_FIRST_NO_SERVER_PLAN.md` 참조. 기존 v2.2 `daily.yml`은 변경하지 않는다.
