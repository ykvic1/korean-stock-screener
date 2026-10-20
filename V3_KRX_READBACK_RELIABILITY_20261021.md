# V3 KRX 비공개 실제 가공파일 업로드 직후 조회 실패 — 2026-10-21 수정

## 실제 실패 원인과 영향

2026-10-20 22:43 KST 예약 [GitHub Actions #39432916198](https://github.com/ykvic1/korean-stock-screener/actions/runs/39432916198)는 기존 정상 5거래일 누적 스크리너 품질 검사를 통과했으나, `v3_krx_derived_backfill.py`에서 `private_derived_archive_readback_failed`로 실패했다.

이 실패는 **이미 성공한 immutable Private GitHub Contents API 저장 직후 GET이 잠시 없거나 오래된 응답을 반환**하는 경우 `store_five_day_factors()`가 단 한 번의 GET 불일치로 중단한 것이었다. KRX 시세 원본 변환 계산이 잘못되었다는 증거가 아니다. 실제 Private 5일 가공 체크포인트는 이후 **17묶음, 85거래일, 34,812 종목·묶음 관측**, 총 **761,604바이트**까지 정상 누적되어 저장된 자료는 보존되었다.

## 정확히 고친 부분

- GitHub Private PUT은 종전처럼 **단 한 번만** 실행.
- PUT이 성공한 직후 **GET만 최대 4회 / 짧은 순차 대기**로 다시 확인. 저장된 실제 bytes가 기대한 가공 GZIP과 정확히 일치해야 성공 반환.
- 매번 API 공급자(KRX) 요청 10회를 다시 실행하거나 이전 저장 파일을 덮어쓰는 재시도는 절대 하지 않음.
- 조회 응답이 끝내 보이지 않거나 **잘못된 바이트를 반환**하면 기존과 같이 명확한 실패 반환. 재실행 시 이전에 저장된 5일 파일이 있으면 `verified_existing_derived`로 검증한 뒤 기존 파일을 재사용, 원천 중복 수집 0.
- Synthetic API + private storage 오프라인 회귀 3개 추가: GET 두 번 누락 후 성공, 4회 누락 후 실패·다음 실행 재개, 변조된 GET으로 false success 금지.

## DART 실제 데이터 확인

- 단년도 DART `docs/v3-dart-book-reference.json`: 2026-10-20 기준, 전체 상장 **2,497기업**, 시도 **403**, 공식 보고서 단년도 실제 관측 **380**, 단순 연간 PBR 참고치 **371**. 이는 **공식 거래소 PBR 확인**이나 실제 V3 가치 15점 확정이 아니므로 구분한다.
- 4개년 `docs/v3-dart-four-year-reference.json`: 2026-10-20 기준 누적 시험기업 **6**, 4개년 연구용 핵심 재무 관측 **5**, 미검증 기업 **1**, 재무 15점/전체 V3 점수는 아직 확정되지 않음.
- 2026-10-21 01:46 KST OpenDART 회사코드 목록 요청 1회 실패([Actions #39460432248](https://github.com/ykvic1/korean-stock-screener/actions/runs/39460432248)), 이후 같은 날 04:50 KST OpenDART 단년도 재실행 [#39486855446](https://github.com/ykvic1/korean-stock-screener/actions/runs/39486855446) 정상 완료. 원인 불명의 transient supplier failure로 분류. 임의로 인증키 교체·재무 수치 보간·완성 배점 입력 금지.

## 빠른 6개 영역 점수 확보를 위한 우선순위

1. DART 공식 공시에서 PIT 재무·기업행사 확인 → `financial15` 연구용 관측률 먼저 확대. 실증되지 않은 시가총액/주식수 단위 수정 금지.
2. 역사적 KRX 수정주가·종목 정체성·기업행사 별도 확인 후 관측 5일·경계·20/60/120/250일 연구수익률 신뢰도 상승. 기존 2016년 실제 경보 216건(17묶음)도 경보일 뿐 실제 corporate-action 확정 아님.
3. 실제 종목별 외국인·기관·연기금 수급은 현재 `KRX_API_KEY`의 시세 엔드포인트로 공급되지 않는다. 인증된 공식 별도 API/등록 IP 가능한 Kiwoom 연결 등의 실제 원장이 검증될 때에만 IAS20 반영. OBV나 시장 전체 순매수로 임의 보충하지 않음.
4. 화면 IAS 크기는 기존 원래 형태 그대로 둠(PR #155에 이미 복원). 기존 공개 사용자 UI, 점수, 근거 없는 확정 랭킹을 건드리지 않는다.

이 수정은 KRX JSON 원본 저장, 새로운 유료 API, DART 공급자 인증 변경, IAS 실제 점수 계산 및 Pages UI 수정은 포함하지 않는다.
