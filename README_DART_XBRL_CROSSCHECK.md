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
3. 동일 기업·사업연도·보고서 코드·접수번호의 재무 API 응답만 수락. OFS/CFS는
   API 요청 인자로 명시하고, 응답에 fs_div가 존재하면 반드시 요청값과 일치해야 한다.
   공식 단일회사 전체 재무제표 API 응답에는 fs_div가 정의되어 있지 않으므로
   **필드 부재만으로 차단하지 않지만 회계범위가 입증됐다고 해석하지 않는다.**
   금융감독원 공식 응답 필드 정의: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020
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

## 2026-10-17 단일 종목 첫 실행 점검

- 수동 실행 #39004015739는 GitHub Actions 자체는 성공했으나 재무 API 식별 검사에서
  `financial_api_report_identity_mismatch`로 XBRL 원본 다운로드 전에 중단됨.
- 기존 검사는 요청 인자인 `fs_div`가 API 응답에도 항상 존재한다고 가정해 오탐 가능성이 있었음.
  본 패치는 **이 한 필드의 문서상 선택적 반환만 허용**하며, 명시적 값의 불일치·
  접수번호/사업연도/기업 식별 불일치·정정 이력은 기존과 동일하게 차단함.
- 실제 실패가 이 한 조건 때문이었는지는 수정 후 **새로운 수동 실행으로만 확인 가능**.
  아직 원본 숫자 대조, 회계범위, PIT 및 투자 점수는 확인되지 않음.

## XBRL 다중 문맥 진단 (연구용)

- 2026-10-17 수동 검증에서 OpenDART 식별정보 정합성 및 XBRL 원본 ZIP 수집은 성공했지만,
  여러 IFRS/XBRL 후보 사실(fact)의 회계 문맥을 확정할 수 없어 숫자 비교는 보류됨.
- 원본 금액·문서·인증키를 공개하지 않고, **비공개 Artifact**의 계정별 결과에 한해
  후보 사실 수, 세그먼트/시나리오 존재 후보 수, 비차원 후보 수, 고유 문맥 수,
  보수적 차단 이유를 추가 기록함.
- 계정 후보의 중복 또는 차원 표시는 특정 금액이 개별(OFS)/연결(CFS) 재무제표
  본문이라는 증거가 아니므로 값 선택, 자동 복구, 점수 계산, PIT 승인은 하지 않음.
- 기존 XBRL 비교 상태·6개 계정 숫자·API 요청 횟수·워크플로·운영 데이터는 변경하지 않음.

## 회계 문맥의 축·구성원 조사 (PR #15 연구 전용)

2026-10-17 검증 #39005873116에서 XBRL 표준 계정 후보 24개 전부에
segment 또는 scenario가 있는 것으로 관찰되었다. 재무 API 접수번호와 원본 ZIP
수집은 성공했으나 후보의 회계범위가 증명되지 않았기 때문에 숫자는 판정 보류다.

- 이전 수집 경로와 요청 횟수를 유지하면서 수동 연구 Artifact에 한해 XBRL
  `xbrldi:explicitMember`의 축/구성원 이름(로컬 QName)과
  `typedMember` 존재 여부만 기록한다. typed 값, 원본 XML, 전체 context id,
  별도 후보 금액은 출력하지 않는다.
- 계정별 현재 재무 API 금액과 정확히 같은 XBRL 후보의 **개수**만 진단값으로
  기록한다. 후보가 하나이고 금액이 같아도 OFS/CFS 회계범위 및 PIT 승인과는
  독립된 사항이다.
- axis 이름이 'Separate'를 포함해도 원본 재무제표 본문이라고 자동 선택하지
  않는다. 표준 분류체계/원문 구조의 독립 검증 전에는 모든 투자 점수 차단 유지.
- 축·구성원 진단은 최대 16개 샘플로 제한하고 민감한 원시 값은 내보내지 않는다.
- CI/오프라인 회귀 테스트 통과 전 main 병합 금지. 본 패치는 전용 연구기능 및
  설명서만 수정하며 v2.2, KRX, 모바일, Pages, Excel, 자동 스케줄은 변경하지 않는다.

## 5개 기업 파일럿 — 접수번호 명시형 수동 배치

연구 범위를 샘씨엔에스 1개에서 업종/시장/재무제표 유형이 다른 2~5개로
확대할 수 있는 **별도 수동 모드**. 재무 API 기반 원본 숫자 검증은
일치 후보 수와 회계범위의 유효성 판정을 구분한다.

- `xbrl_batch_cases`에는 실제 OpenDART 사업보고서 목록과 재무 API에서
  독립적으로 확인된 기업별 `종목코드:14자리접수번호:OFS|CFS`를
  쉼표로 구분하여 입력한다. 예시에서 **252990의 접수번호만 실확인**;
  다른 기업의 접수번호를 임의로 만들거나 예시 값을 실조회에 사용하지 않는다.
- `symbols`에는 같은 종목코드를 같은 순서로 입력(2~5개).
- `mode=live`, `provider=dart`, `xbrl_compare=false`,
  `finance_sample=false`, `history_sample=false`, `revision_audit=false`,
  `xbrl_receipt`와 `revision_expected_receipt`는 비움.
- 종목은 기존 `docs/data.json` KOSPI/KOSDAQ 120종목에 포함돼야 한다.
  지정 기업/접수번호/회계범위는 API 호출 **전** 형식 검증한다.
- 건별로 기준일 이전 2025년 공시 접수번호·정정공시 존재 여부를
  다시 대조한다. 정정이 관찰되거나 신뢰할 수 없으면 그 기업은 차단한다.
  무조건 성공 처리하거나 실패 기업을 0으로 간주하지 않는다.
- 상한 5개 기업. 각 기업당 기존과 같은 공시목록 및 1회 재무 API,
  1회 XBRL ZIP 요청 제한을 적용한다. 재시도/자동 스케줄 없음.
- 원본 수치와 문맥 후보 정보는 비공개 Artifact 7일 보관. 모든
  `investment_data_ready`/`confirmed_scoring_allowed`/PIT flag는
  계속 **false**. v2.2, 매매 점수, KRX, Pages, Excel, 자동 운영에 반영 금지.

**표본 후보(아직 공시 접수번호 검증 전)**:
252990 샘씨엔에스(KOSDAQ), 280360 롯데웰푸드(KOSPI),
018880 한온시스템(KOSPI), 014680 한솔케미칼(KOSPI),
064290 인텍플러스(KOSDAQ).
각 사의 실제 2025년 사업보고서 접수번호·정정 여부·CFS/OFS 제공 여부를
확인한 후에만 배치를 실행하며, 미확인 경우는 제외한다.
