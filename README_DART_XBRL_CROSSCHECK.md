# OpenDART 2025 원본 XBRL ↔ 재무 API 숫자 비교 (연구용)

## 목적

샘씨엔에스(252990)의 2025년 사업보고서 접수번호 20260316001120에 대해
접수번호 단위 원본 XBRL ZIP(fnlttXbrl.xml)과 현재 단일회사 전체 재무제표 API
(fnlttSinglAcntAll.json)를 별도(OFS) 기준으로 대조한다.
이는 기존 정정 이력 검증의 연장선이며 투자용 확정 자료 또는 PIT 인증이 아니다.

## 공식 API

- 원본 XBRL: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019
- 단일회사 전체 재무제표: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020
- 재무제표 정정시 수치 변경 가능: https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003

## 비교 및 안전 계약

1. 정확히 한 회사 및 14자리 사전 검증 접수번호, 기준일 = 운영 JSON 기준일 이하.
2. list.json last_reprt_at=N으로 기준일 이전 사업보고서 접수 이력 재확인.
   예상 접수번호가 당시 최신이 아니거나 정정이 관찰되면 추가 API 요청 차단.
3. 동일 기업·사업연도·보고서 코드·OFS/CFS·접수번호의 재무 API 응답만 수락.
4. 원본 XBRL ZIP 한 건 요청. DTD/ENTITY, 경로조작, 과대압축, 2개 이상
   instance, 복수/불명확한 context는 차단. 원본 바이트는 저장하지 않음.
5. 매출액·영업이익·영업현금흐름·자산·부채·자본 6개 계정에 대해
   표준 taxonomy, 2025-01-01~2025-12-31 duration 또는 2025-12-31 instant,
   KRW 원단위, context 단일 여부를 확인.
6. XBRL 숫자가 같아도 OFS/CFS 정합성이 XBRL 내부에서 독립적으로 증명되지
   않으면 equal_observed_scope_unverified로 표기. 숫자가 다르면
   different_observed_scope_unverified로 표기하며 자동 보정하지 않음.
7. XBRL 후속 정정 버전 복원, 서명된 당시 원문, 공개시각 검증이
   끝나지 않았으므로 다음 모든 flag는 계속 false:
   - financial_numeric_changes_verified
   - xbrl_accounting_scope_verified
   - historical_numeric_snapshot_verified
   - revision_history_validated
   - asof_join_validated
   - confirmed_scoring_allowed
   - investment_data_ready
8. 시도당 최대 공시목록 1회, 재무 JSON 1회, XBRL ZIP 1회 + 기존 기업 매핑.
   자동 재시도 없음, 비밀키/원문 파일 미노출, KRX 호출 없음.
9. 결과는 7일 비공개 GitHub Actions artifact JSON에만 기록.
   docs/, Pages, Excel, 운영 v2.2 스크리너, IAS/EA 점수 변경 없음.

## 첫 실데이터 시험 (PR 병합 후)

GitHub → Actions → Official KRX and OpenDART API Probe (manual only) → Run workflow

| 입력 | 값 |
|---|---|
| Branch | main |
| asof | 2026-10-16 |
| mode | live |
| provider | dart |
| symbols | 252990 |
| finance_sample | false |
| history_sample | false |
| revision_audit | false |
| revision_expected_receipt | 비움 |
| xbrl_compare | true |
| xbrl_receipt | 20260316001120 |
| xbrl_fs_div | OFS |

실패·불일치 시 원본 문맥 및 회계범위를 검토한 뒤 parser를 보수적으로 확장한다.
단일 표본으로 완전 검증을 선언하거나 전체 종목 자동 수집으로 확장하지 않는다.
