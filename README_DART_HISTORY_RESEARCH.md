# OpenDART 3년+분기 금융데이터 연구 표본

## 목적 / 안전 경계

- 기존 v2.2, IAS/EA, docs/, Excel, Pages, KRX에 **영향 없음**.
- 수동 Actions에서 history_sample=true를 선택한 경우에만 실 API 호출.
- 조회 종목 **정확히 1개**, 기준일은 docs/data.json 기준일 이하여야 함.
- 최근 3개 사업연도 사업보고서 + 현재 사업연도 1분기(11013)·반기(11012) 자료.
- 연결(CFS) 우선; **연결재무제표 자체가 없는 경우에만** 별도(OFS) 조회.
- 기준일 이전 공시 접수번호 확인. 정정본 전후 원문 비교는 아직 미구현.
- 접수번호가 일치해도 공시 당시 원문(PIT, point-in-time)과 동일하다는 보장은 없음.
- CFS/OFS 혼재 시 annual_scope_consistent=false, 성장률 계산 금지.
- 분기·반기 thstrm_amount 및 thstrm_add_amount를 **원문대로 분리 저장**.
  단일 분기 수익·현금흐름, 성장률, 투자점수는 생성하지 않음.
- 누락·중복·통화 불일치·공시 미존재 시 null로 보존하고 0으로 추정하지 않음.
- revision_history_validated=false, asof_join_validated=false, confirmed_scoring_allowed=false, investment_data_ready=false 유지.
- API 네트워크 실패 시 자동 재시도 없음. 비밀키와 요청 URL을 로그에 출력하지 않음.
- evidence/official_probe에 비공개 artifact(7일), Pages/운영 보고서에 미게시.

## 최초 제한 시험

GitHub → Actions → Official KRX and OpenDART API Probe (manual only) → Run workflow.

| 입력 | 값 |
|---|---|
| branch | main (PR 병합 이후) |
| asof | 2026-10-16 |
| mode | live |
| provider | dart |
| symbols | 252990 |
| finance_sample | false (체크 해제) |
| history_sample | true (체크) |

기준일 당일 공시는 보수적으로 제외한다. 첫 시험이 성공해도 5년 백필,
120종목 수집, 재무20점 계산, 운영 DB 승격은 별도 검증 전까지 보류.

공식 API 문서:
- https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020
- https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001

## 완료 기준

1. 변경사항에 대한 신규·기존 테스트 통과.
2. 기준일/종목코드/한 회사/실행모드 제한 확인.
3. 2023~2025 연간 + 2026 Q1/H1 원문 필드·접수번호·회계범위·누락 상태 기록.
4. 운영 docs/, Pages, Excel, IAS/EA 비변경 확인.

## 이후 별도 개발 대상

- 원문 공시 버전/XBRL 파일 보존과 정정 전후 수치 비교.
- 재무계정 원본 해시와 공개 시각을 통한 PIT-eligible 검증.
- 단일 분기·누적 항목 및 연결/별도 범위 검산, 이후 성장률/FCF 산출.
- 5년 데이터·증분형 장기 저장소, 중복 방지와 재처리 기능.
