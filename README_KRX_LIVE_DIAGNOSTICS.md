# KRX 공식 API 인증 완료 후 수동 진단 — 2026-10-19

## 이전 상태

2026-10-19 오전 KST manual run 39178753384: workflow 전체는 success였으나 실제 KRX는 failed, DART not_requested, investment_data_ready=false였다. 이 오류를 인증키 자체의 오류로 단정할 수 없다. Secret KRX_API_KEY는 runner에 전달되었지만 공식 API 수신/검증 단계는 실패했다.

## 수정한 안전 진단

공식 KRX 연결 어댑터에서 키·요청 URL·헤더·본문을 일절 기록하지 않고 아래 **기계 판독 가능한 고정 코드만** 저장한다.

- `http_unauthorized` — HTTP 401
- `http_forbidden` — HTTP 403 (서비스 접근권한, GitHub 실행 IP 제한 등 추가 검토 필요)
- `http_not_found` — HTTP 404 (승인된 API endpoint/경로 검토 필요)
- `http_rate_limited` — HTTP 429
- `http_server_error` — HTTP 5xx
- `http_other` — 기타 HTTP 오류(상태 숫자는 범위검증)
- `transport_failure` — 요청 또는 네트워크 레벨 예외
- `empty_body`, `oversized_body` — HTTP 200이나 본문 크기 이상
- `invalid_json`, `missing_outblock_1`, `empty_market_rows`, `invalid_market_rows` — HTTP 200이지만 응답 구조가 기대한 공식 KRX 주식 일별 데이터와 다름
- `response_validation_failed` — 개별 OHLC/거래량·기준일 일치 등의 연구용 검증 실패

이 정보는 private evidence artifact와 Actions의 **안전한 필드만 표시하는 상태 메시지**에 보관한다. 저장 시점의 요청은 `mode=live`, `provider=krx`, `asof=2026-10-16`; 기준일은 워크플로 입력과 기존 공개 스냅샷 날짜의 작은 값 이하여야 한다.

## 수동 실행과 성공/실패 규칙

1. GitHub → Actions → Official KRX and OpenDART API Probe (manual only) → Run workflow → main.
2. `asof`는 완료된 거래일이며 `docs/data.json` 기준일 이하. `mode=live`, `provider=krx`, `symbols` 생략 가능(표본 10개 제한). KOSPI/KOSDAQ 모두 확인하려면 기존 data.json에 포함된 양 시장 종목코드를 선택한다.
3. 실행 후 Steps의 `Report clearly separated collection and validation status`에서 **HTTP 숫자 + 시장 + 고정 reason만** 확인한다.
4. 결과 파일은 private artifact로 **7일 보관**. 마지막 `Fail the workflow if requested live KRX observation failed`에서 KRX sample status가 `observed_unverified`이고 요청 표본이 최소 1종목 수신되었을 때만 green. 실제 데이터 진위/PIT/수급/정규장 검증은 별도다.
5. HTTP 401/403이면 Secret 값 및 개별 KRX API 서비스 승인/허가 범위, IP 허용 정책을 KRX와 확인한다. 404이면 승인된 엔드포인트 URL/서비스 코드, 429이면 요청한도, 200 + JSON/schema 오류이면 실제 승인 계정에서 반환된 응답 규격을 별도 비공개 경로로 확인한다. 메시지 본문과 비밀키는 채팅·로그·PR에 게시하지 않는다.

## 변경하지 않은 부분

기존 v2.2 screener.py, public Pages, 정기 Actions, 정규장 거래량 및 기관/외국인/연기금 수급 검증 산식, v3 실전 추천 플래그는 변경하지 않았다.

**연결 성공 != 수익률 예측 적격.** `observed_unverified`는 표본 응답을 받았다는 뜻만 있으며 `investment_data_ready=false`, `confirmed_scoring_allowed=false`가 유지된다.
