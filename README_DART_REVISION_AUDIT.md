# OpenDART 2025 사업보고서 정정이력·원문 증거 감사

이 기능은 한 종목의 재무 계정이 **과거 기준일 당시 공개된 수치였는지** 조사하기 위한 제한 시험입니다. 거래 신호, IAS/EA 또는 재무점수 계산과는 완전히 독립적입니다.

## 선행 확인

2026-10-16 기준 샘씨엔에스(252990)의 2025 사업보고서가 2026-03-16 접수번호 20260316001120으로 조회되었고, 당시 연결(CFS)이 없어 별도(OFS)로 회계금액을 확인했습니다. 이는 **지난 OpenDART 실조회 증거**이지 정정 이전 수치의 증명은 아닙니다.

공식 OpenDART:
- 공시검색: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001
- 공시서류원본파일: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003

공시검색 list.json의 last_reprt_at=N은 정정보고서 포함 전체 조회를 뜻합니다. document.xml은 하나의 접수번호에 대한 ZIP 원문을 제공합니다.

## 동작

- 정확히 **1종목**, 사용자가 과거 재무 조회에서 확인한 **14자리 접수번호**를 명시해야 함.
- 기준일 전날까지 2025.12 사업보고서와 해당 정정보고서를 찾음.
- 한 번의 공시목록 조회에 최대 100개 행만 사용. 여러 페이지면 누락 가능성 때문에 **즉시 차단**.
- 최초·최종 접수번호의 원문 ZIP을 **최대 2건만 다운로드**하고 원문 파일 단위 SHA256을 메모리에서 계산. 원문 파일 자체는 영구 저장하지 않음.
- ZIP 보안 검증: 총 25MB 압축, 파일 200개, 압축 해제 총 80MB, 개별 파일 45MB, 경로 조작·중복 파일 차단.
- 버전 목록과 접수일을 기록하여 기존 OpenDART 재무제표의 접수번호가 **기준일 전 최신 보고서와 일치하는지** 검토.
- 모든 결과는 GitHub Actions 비공개 artifact(7일)에만 기록. KRX, 자동 스크리너, docs/, Pages, Excel 미변경.
- DART_API_KEY는 GitHub Secrets만 사용. URL·키·원문 내용은 실행 로그에 출력하지 않음. 실패 시 자동 반복호출 금지.

## 중요 제한

- 원문 내부파일 해시가 다르더라도 **매출액·영업이익의 숫자가 바뀌었다는 뜻은 아님**.
- 해시가 같더라도 모든 후속 정정의 부재를 보장하는 것은 아님. 현재 검색은 기준일 이전, 사업연도 하나에 한정됨.
- DART의 현재 재무 API가 과거 원본과 동일한 숫자를 제공하는지도 아직 검증되지 않음.
- 원문의 계정별 XBRL 파싱, 정정 전후 숫자 대조, 최초 공개 시각 증거는 후속 작업임.
- revision_history_validated, historical_numeric_snapshot_verified, asof_join_validated, investment_data_ready, confirmed_scoring_allowed는 **무조건 false**.

## 수동 실행 (main 병합 후)

GitHub → Actions → Official KRX and OpenDART API Probe (manual only) → Run workflow

| 설정 | 값 |
|---|---|
| Branch | main |
| asof | 2026-10-16 |
| mode | live |
| provider | dart |
| symbols | 252990 |
| finance_sample | 체크 해제 |
| history_sample | 체크 해제 |
| revision_audit | 체크 |
| revision_expected_receipt | 20260316001120 |

이 결과로 우선 **정정 이력이 있는지**와 **원문 ZIP 1~2건을 보수적으로 비교할 수 있는지** 확인합니다.
후속 XBRL 숫자 대조를 검증하기 전에는 과거 수익률 평가나 점수에 **일절 넣지 않습니다**.
