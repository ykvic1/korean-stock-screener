# 2026-10-19 KRX 27건 코드 형식 조사 및 연구용 수집 복구

## 근거가 확인된 사실
- KRX history run #39188415626 (2026-10-15 KOSPI): 숫자만의 6자리 패턴 밖의 `ISU_CD` **27건**, 6자리 숫자 중복 0건, 누락 0건, 비문자열 0건, `A+6자리` 0건, 12자 패턴 0건.
- 이는 **27건 모두 6자리 영문·숫자 혼합코드라는 증거는 아니다.** 실제 길이/형식은 이번 변경을 배포한 다음 결과에서 추가로 검증한다.
- KRX 일별매매정보 API의 `ISU_CD`는 문자열로 정의된다. KRX 종목기본정보 API는 `ISU_CD` (표준코드), `ISU_SRT_CD` (단축코드)를 구분한다.
- 공시된 시장에서 신형 우선주 같은 종목코드는 `00088K` 또는 `0220WL`처럼 **영문자가 섞인 6자**일 수 있다. 영문 종목코드가 곧 우선주라는 판정도 금지한다.

## 이 PR의 제한적 해결
- KRX 전체시장 **raw** 종목코드가 정확히 6자의 대문자 영숫자 `[0-9A-Z]{6}`인 경우, 해당 문자열을 변환·삭제·접미사 제거하지 않고 원형 그대로 가격 관측의 구분자로 취급한다.
- 숫자형 6자리와 영숫자혼합 6자리의 수신 종목 수를 분리해 로그에 개수만 출력한다. 문자열 자체는 결과/로그에 노출하지 않는다.
- **모든 종목코드의 정확한 일치·고유성·날짜·OHLC/거래량 숫자값 검증은 유지한다**. 누락, 중복, 다른 길이, 소문자, 공백, 비ASCII 등은 계속 실패 처리한다. 종목코드를 절삭하거나 0을 붙이지 않는다.
- 기존 `inspect_krx`의 6자리 숫자 샘플 검증은 **변경하지 않는다**. 이 별도 historical collector는 영문혼합 원본 코드를 정규장 거래량 또는 개별 기업 ID라고 단정하지 않는다.
- 투자용 스코어, 120주선·OBV·외국인/기관 매집 분석 입력으로 원본 가격을 승격하지 않는다. 종목기본정보 API 등으로 독립된 기업식별 검증 전까지 `opaque_alphanumeric_identity_reconciled_with_official_master=false`, `historical_issuer_universe_verified=false`, `v3_release_ready=false`.

## 안전한 다음 시험
- Actions → `KRX two-market historical daily pilot (manual only)` → main → dates=`2026-10-15,2026-10-16`, mode=`live`.
- 성공 시 날짜별 두 시장의 전체 수신행/숫자형코드/영숫자혼합 6자코드/유효한 가격 없는 행 건수를 비교한다.
- 여전히 `non_six_digit_code`가 나온다면 **영문혼합 6자 외에 다른 비정형 코드가 존재한다는 증거**이므로 차단 상태를 유지하고 다음 단계에서 별도 식별정보를 확인한다.
- 실제 250 거래일은 여전히 미수집. 이번 수동 Actions 테스트는 raw bytes를 GitHub에 남기지 않으므로 이력 영구 누적을 의미하지 않는다.

## 외부 근거
- KRX 주식 일별매매정보: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC
- KRX 유가증권 종목기본정보: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=PiwgMdTwmsenXhmqqxuj
- 국내 코드 예시: https://www.kokstock.com/stock/item.asp?item_cd=00088K

v2.2 정기 자동실행, docs/ 공개 자료, 기존 official_probe 검증, 수급 인자, 실전 종목추천은 변경하지 않는다.
