# V3 KRX 실제 과거 가공 수익률 기업행사 의심 구간 식별 (2026-10-20)

## 왜 개발했나

KRX 2016~2026 과거 시세를 비공개 5거래일 단위로 요약하고 종가 구간 수익률을 계산하는 과정에서 **수정주가 / 무상증자 / 주식분할 / 합병 / 지주회사 분할 / 역사적 종목ID 변화** 검증이 아직 이루어지지 않았다. 과거 요약 지표에 큰 가격 변동이 있다고 해서 바로 기업행사로 확정하거나 V3 매수·손절 점수에 반영하면 위험하다.

현재 연결된 KRX OPEN API 공식 공개 서비스는 종목별 시장 시세와 종목기본정보 등을 제공하지만, 외국인·기관·연기금 *종목별* 투자자 순매수 항목은 공개 OPEN API 목록에 없다. 별도의 KRX Data Marketplace에서는 종목별 투자자 거래실적 화면이 있지만, 현재 승인된 KRX API 호출 경로 또는 일별 투자자별 원장 계약은 제공되지 않았다.

KRX 출처:
- 공개 API 서비스 목록: https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
- 종목별 투자자 통계 위치: https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd

따라서 시세 API의 거래량을 사용해서 투자자별 누적 순매수를 만들어낼 수 없다. IAS20은 미검증 유지. **이번 작업은 별도 수급값 생성이 아니라 가격계열 기업행사 위험을 찾아 후속 검증할 목록을 만드는 단계.**

## 실제 비공개 자료 기반 검증

[GitHub Actions #39421050799](https://github.com/ykvic1/korean-stock-screener/actions/runs/39421050799):

- 2016년 45거래일 완료 5일 묶음 **9개**: 종목·시장별 누적 5일 내부 가격 변화율 **18,341건**.
- 묶음 사이의 KRX 실제 변화율 **8개 경계, 16,403건**.
- 휴리스틱 경보 기준: 5일 묶음 내부 첫날→마지막날 가격 변동 **절대값 30% 이상** 또는 인접 5일 묶음의 최종일→다음 첫날 가격 변화의 **절대 로그수익률 0.30 이상**.
- **110건 경보**, **93개 종목·시장 조합** 관측. 정확한 실제 주식분할·합병 110건이라는 뜻이 아니다. 5일간 +30%는 정상적인 급등에서도 가능하고, 변동이 작아도 기업행사가 발생할 수 있다.
- private 저장소 `derived/quality/price-discontinuities-2016-12-16.json.gz` 파일 **1,708 바이트**, 공식 API **0회 추가 호출**, 원본 OHLCV 저장 **0건**. `scores_or_buy_signals_approved=false`, `corporate_actions_verified=false`.
- 등락 경보 **미발생** 역시 수정주가 검증·PIT·역사적 issuer mapping 인증을 의미하지 않는다.

## GitHub Free 최적화: 신규 구간만 검사

기존 45일 전체 검사 파일을 매일 처음부터 다시 생성하면 10년 완성 후 가공 데이터 파일을 하루 수천 건씩 읽어 오버헤드가 커질 수 있다. `v3_krx_corporate_risk_incremental.py`으로 변경:

- 실제 9묶음 스냅샷을 한번 읽어 **진짜 기존 경보 110건**과 이전에 검증한 18,341·16,403건을 **체크포인트만** 작성하여 이어받음. 이미 저장된 변환본을 변경하지 않음.
- [실제 checkpoint 초기화 #39421681893](https://github.com/ykvic1/korean-stock-screener/actions/runs/39421681893): `status=verified_baseline_adopted_without_provider_requests`, `cohorts_examined=9`, `cumulative_flags=110`, `new_quality_files=0`, `source_provider_calls=0`.
- 새로운 5일 묶음과 연결 경계 자료가 함께 검증되어야만 하루 최대 **8개 신규 5일 묶음** 조사. 추가 종목별 위험 경보만 `derived/quality/batches/날짜_날짜.json.gz` 파일 하나에 저장.
- 작은 `derived/quality/incremental-checkpoint.json`에 누적 이상 구간 수·검사 행수·마지막 파일 SHA256·현재 인덱스 보관. 중단 후 재시작 시 이미 저장된 청크를 검증해 재사용하며, 원본 KRX API를 호출하지 않음.
- 매일 **23:43 KST** GitHub Actions 예약, 기존 22:11 5일 요약→22:47 묶음 경계→23:11 20·60·120·250일 근사 연구 계산 이후.
- 동일 private repo 동시 쓰기 concurrency lock. 가공 결과는 **비공개 개인 연구용**에만 보관, 공개 Pages 종목점수에 사용하지 않음.
- GitHub Free 외 유료 데이터/서버/LFS 가입 없음. 약 30MiB까지의 소규모 품질 리포트 안전 상한 유지.

## 남아 있는 실제 검증 과제

1. 거래소 역사적 종목기본정보 API의 승인범위 확인 후 issuer continuity(상장·상폐·종목코드 변경·법인 연속성)와 분할·합병·액면병합 이벤트를 **정식 근거**로 연결.
2. 5일 내부 기간의 BP 반올림을 고려한 이벤트 경고 세분화, 기업행사로 확인된 항목만 구조화된 수정주가 재계산에 반영.
3. 외국인·기관·연기금 수급은 실제 정식 종목별 일별 자료 원장이 확보된 후에만 `v3_ias_flow_ledger.py`를 가동. 시세/OBV나 시장 전체 수급은 IAS 미검증 부분을 대체하지 않는다.
4. 실제 자료 수집·검증·계산·저장에 성공한 숫자와 **투자 점수로 사용 가능한 지표**는 반드시 구분.
