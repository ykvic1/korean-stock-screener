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

## 공식 연간 공시 접수번호 자동 발견 — 사용자 검토 필수

반복적인 접수번호 입력 부담을 줄이기 위해 **연구용 발견 모드**를 추가했다.

- 수동 워크플로에서 `mode=live`, `provider=dart`,
  `symbols=252990,280360,018880,014680,064290` (5개)
  `xbrl_discover_receipts=true`로 설정. 나머지 샘플·비교 모드는 모두 false,
  접수번호 입력은 비움.
- 기업별 공식 OpenDART A001 목록을 기간 제한(2026-01-01~2026-10-15),
  1페이지 최대 100개, 단 1회 요청하여 2025년 사업보고서 접수번호 후보를 기록.
- 정정 또는 중복 제출이 1건이라도 확인되면 **후보를 확정하지 않고**
  `multiple_or_amended_reports_require_manual_review` 표시. 데이터 오류·
  빈 결과·페이지 누락도 차단. 실제 사업보고서인지 추가 원문 검증 필요.
- 결과는 GitHub Actions의 **7일 비공개 Artifact**에만 포함하며,
  접수번호는 원본 목록 기반의 **검증 전 후보**일 뿐이다. OFS/CFS 제공 여부,
  재무 API 접수번호 일치, 원본 XBRL, 과거 시점 적격성은 전부 미확인.
- 사용자가 후보 접수번호와 개별/연결 회계범위를 별도 확인한 후
  이전의 `xbrl_batch_cases` 필드로 다종목 비교할 수 있다.
- 수집은 자동 예약하지 않고, 사용자 수동 실행만 허용하며 운영 투자 점수와
  기존 v2.2 데이터에는 반영하지 않는다.

## 별도재무제표 단일 차원 후보 진단 (연구 전용)

2026-10-17 샘씨엔에스 실측에서 `ConsolidatedAndSeparateFinancialStatementsAxis`와
`SeparateMember`의 조합을 관찰하였다. 요청 회계범위 `OFS`인 경우 이 단일
축-구성원만 달린 후보의 **개수** 및 공식 재무 API 금액과 일치하는 후보 **개수**를
별도 연구 진단 수치로 기록한다. 예외/주석 차원이 추가된 후보는 제외한다.

**엄격한 제한:** XML QName 로컬 이름만 비교하므로 이름공간·공식 taxonomy
의미·재무제표 본문 배치·원본과 API 응답의 동일 회계범위·정정/PIT 유효성을
입증하지 않는다. `CFS`에 대해서는 관찰된 패턴이 없어 진단값을 null로 둔다.
기존 XBRL 금액 비교의 모호성 차단·전체 투자 점수 금지 정책은 변경하지 않는다.

## 2025년 5종목 원클릭 수동 연구 워크플로

`Actions → OpenDART 2025 Five-Firm OFS Research (one click)`
(파일: `.github/workflows/dart-five-firm-2025-ofs.yml`)에는
**Run workflow 버튼 외에 입력값이 없다.**
기존 공식 API 검증 워크플로의 많은 체크박스를 잘못 설정할 위험을 낮췄다.

- 2026-10-16 기준, 2025년 사업보고서 최초 공시 후보 5건은
  수동 OpenDART A001 공시목록 실행 #39009211491에서 확인한 고정값.
  종목: 샘씨엔에스 252990, 롯데웰푸드 280360,
  한온시스템 018880, 한솔케미칼 014680, 인텍플러스 064290.
- 5개 회사에 `OFS`를 지정하는 것은 **실험할 재무제표 유형을 명시한 것**
  이지 공식적으로 별도재무제표 제공 여부를 확정한 것이 아니다.
  미제공/범위 불일치/정정공시는 그대로 실패·보류할 수 있다.
- PR #16 기존 배치 엔진 호출. 다른 API 제공자, 다른 연도, 자동 실행으로
  확대되지 않는다. 회사별 정정·접수번호/공시 식별 검증을 다시 수행한다.
- 샘씨엔에스 실데이터에서 관찰된 `SeparateMember` 단일 차원
  **후보 개수**와 재무 API 금액 일치 **후보 개수**를 진단만 한다.
  XBRL namespace/role 기반 본문 검증, 재무 공시 시점 PIT, 회계범위
  최종 승인, 실적 기반 투자 점수 반영은 전혀 수행하지 않는다.
- 서버 로그에는 기업별 처리 상태와 후보 수만 표시. 실제 재무 금액은
  기존과 동일하게 7일 GitHub Actions 비공개 Artifact에만 저장.
- GitHub Actions 성공은 **실행 성공**이지 5개 재무제표 검증 성공을
  의미하지 않는다. `partial_or_different_values_unverified`는
  오류 건수도 아니고 서로 다른 금액이 증명됐다는 뜻도 아니다.

KRX 정규장 거래량·기관·외국인 수급의 별도 공식 데이터 검증이
끝나기 전까지 운영 v2.2 스크리너의 투자용 점수에 연결하지 않는다.

## 매출액 2중 후보 개념·표시구조 진단 (PR #19 연구 전용)

2026-10-17의 5개 기업 연구 결과에서 OFS 단일 차원 후보 30개 중
샘씨엔에스(252990)와 롯데웰푸드(280360)의 **매출액만 두 후보**가
관찰되었다. 각 매출액에서 공식 API와 일치하는 후보는 하나였으나,
단순 금액 일치를 기준으로 원본을 채택해서는 안 된다.

### 추가하는 진단 증거
- 기존 XBRL 표준 계정 whitelist를 이용해 별도재무제표 단일 차원에 속한
  매출액 후보를 **XBRL 개념 로컬 이름**(예:
  `Revenue` 및 `RevenueFromContractsWithCustomers`)과 원래 namespace의
  SHA-256 앞 16자리로 그룹화한다. 원본 namespace URL은 저장하지 않는다.
- 개념별 후보 개수, 서로 다른 회계 문맥 수, 서로 다른 금액의 **개수**,
  API 금액과 일치하는 후보의 **개수**만 진단한다. 후보 숫자나 XML 원문을
  새 필드에 기록하지 않는다.
- 다중 후보를 `multiple_taxonomy_concepts_or_namespaces`,
  `same_concept_distinct_contexts`,
  `same_concept_same_context_multiple_facts`로 분류.
  이는 **증거 분류**일 뿐 어떤 재무 개념이 정답인지 판단하지 않는다.
- ZIP 안에 발표(presentation) linkbase XML이 **동봉된 경우에만**
  로컬 locator + arc 구조가 참조한 매출액 개념의 건수를 부가 기록한다.
  외부 taxonomy/DTS는 다운로드·해석하지 않는다. linkbase 미동봉은
  정보 미확인일 뿐 발표구조가 없다는 뜻이 아니다.

### 신뢰성 제한
XBRL International에 따르면 차원은 사실의 문맥 측면이며, presentation
linkbase 관계는 재무제표 본문뿐 아니라 기타 보고 목적에도 사용할 수
있다. 로컬 계정명이나 하나의 presentation arc만으로 본문이라는
판정은 불가능하다.

- 참고: https://www.xbrl.org/Specification/xbrl-xml/REC-2021-10-13%2Berrata-2023-04-19/xbrl-xml-REC-2021-10-13%2Bcorrected-errata-2023-04-19.html
- 참고: https://www.xbrl.org/specification/xbrl-recommendation-2003-12-31.pdf
- OpenDART XBRL 원본 API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019

**기존 회계범위/과거 시점(PIT)/재무 점수 차단 상태와
OpenDART API 요청 수, 운영 v2.2 기능을 변경하지 않는다.**
신규 로그 진단은 향후 사용자가 직접 5개 기업 수동 워크플로를 재실행했을
때 비공개 7일 Artifact에 추가된다.

## 2026-10-17 5종목 워크플로 지연 조사와 안전장치 (연구 전용)

### 확인된 사실
- 원클릭 실행 #39010207507에서 실제 5종목 비교 약 **18초**.
- 원클릭 실행 #39010887836에서 실제 5종목 비교 **8분 9초**.
- 두 실행 모두 5종목 수집 성공. 종목별 원본 ZIP 파일 구성은 각각
  7개 엔트리, 6개 XML로 같았으며, 회귀 테스트/설치에 지연은 없었다.
- **정확한 지연 원인 미확정**: 이전 버전에는 HTTP 요청별 경과 시간 및
  XML 처리별 경과 시간이 없어서 GitHub 로그만으로 네트워크 지연인지
  XML 처리 비용인지 증명할 수 없다. 원본 링크베이스 arc가 존재한다는
  사실만으로 처리 시간이 8분이라는 추론도 근거가 없다.

### 개선
- 수동 `xbrl_batch_cases` 연구 모드에 한해 OpenDART 개별 요청의
  timeout을 기존 25초에서 **12초**로 줄인다. `requests` 읽기
  timeout은 **데이터 미수신 간격에 대한 제한**으로, 전체 응답의 절대
  소요시간 제한을 보장하지 않음을 명시한다.
- 수동 파일럿에서 **210초 소프트 예산** 이후에는 새 기업 조회를
  시작하지 않고 `not_requested_due_to_research_time_budget`로
  별도 분류한다. 이미 시작된 요청을 중간에 취소하지 않으므로
  전체 실행 210초 상한이라는 뜻은 아니다.
- GitHub 잡의 최대 실행 시간은 기존 20분에서 8분으로 제한.
  초과 시 GitHub가 실패 처리하며 부분 수집된 결과까지 성공으로
  간주할 수 없다.
- 업체별 총 처리 시간, **민감한 URL·인증키·접수번호가 없는**
  요청 단계(기업 매핑 ZIP, 공시목록, 재무 JSON, XBRL ZIP)의 ms,
  요청 성공/실패와 기타 처리 시간만 연구 Artifact에 기록.
  공개 Actions 로그에는 이 **제한된 이름과 소요시간**만 출력.
- 각 종목 실패는 예전처럼 `failed_or_unavailable`, 공식 회계범위/PIT/
  투자 점수 검증 플래그는 계속 false. 자동 재시도 없음.
- 다른 수동 분석 모드, 운영 v2.2, KRX 승인 대기 경로, 데이터 게시,
  사용자 입력값, 연구 대상 연도/기업 및 원본 XBRL 파서는 변경하지 않는다.

### 확인해야 할 다음 실증
최신 원클릭 워크플로를 한 번 수동 실행하면 비공개 요청별 소요시간으로
실제로 지연된 단계가 네트워크 수집인지 JSON/XML 처리인지 구분한다.
**1회 빠른 실행만으로 지연 해소를 확정하지 않으며**, 느린 요청의 로그와
향후 여러 번 실행의 경향을 확인한 후 연결 유지·요청 제한·파서 개선 등을
판단한다. 자료가 불완전한 채로 다음 대규모 종목 확대를 승인하지 않는다.

## 2026-10-17 실제 속도 병목 확인 및 PR #21

수동 원클릭 실행 #39012280057 결과:
- 전체 기업코드 파일(`corpCode.xml`) 다운로드 **202,931ms**;
  XML 파싱 등을 포함한 매핑 단계 **204,219ms**.
- 샘씨엔에스(252990) 공시목록 **826ms**, 재무 API **3,206ms**,
  XBRL ZIP **22,179ms**, 비네트워크 처리 약 **154ms**.
- 전체 비교 단계 **230,584ms**. 기존 210초 소프트 예산 초과로
  샘씨엔에스만 완료, 나머지 4종목은 조회 자체를 하지 않음.
- 그럼에도 GitHub Actions는 Success로 종료되어 **미완료 검증에 대한
  작업 상태 표시 결함**도 발견됨. 성공을 수집/검증 완료로 혼동해서는 안 됨.

### 반복되는 전체 기업 매핑 파일 다운로드 제거 (한정된 파일럿만)
- 이전 공식 DART 실조회 #39010207507, #39010887836의 원본 Artifact에서
  동일하게 확인된 상장종목↔기업코드 5쌍을
  `official_probe/pinned_2025_pilot.py`에 **검증 시점이 명시된
  별도의 과거 관찰값**으로 고정한다.
- **2026-10-16 기준 2025년 OFS 사업보고서, 동일한 5개 종목·접수번호·
  입력 순서**에만 `--xbrl-use-reviewed-2025-corp-map`을 허용한다.
  다른 날짜/대상/접수번호/회계범위는 API 호출 **전에** 거부.
- 다른 OpenDART 조사 모드에서는 계속 공식 `corpCode.xml`로
  현재 시점의 기업코드 매핑을 조회한다.
- 이 빠른 경로는 **현재 시점 기업코드 매핑을 다시 조회한 것이 아니다**.
  회사/공시/연도/접수번호의 실시간 동일성은 각 기업의 공식 공시목록,
  재무 API, XBRL 교차검증으로 확인하고, 결과가 불분명한 경우
  미검증으로 남긴다.

### 조사 누락 시 반드시 실패
- `official_probe/pilot_completion.py`는 기업 수와 순서(5개),
  접수번호·기업코드·회계범위·재무 6개 계정 구조,
  원본 ZIP 수집 및 공식 API 신원 일치 여부를 재검사한다.
- **한 기업이라도 미수집 또는 불확실한 원본 신원이라면 CI 잡을 실패 처리**.
  `if: always()` 연구 Artifact 7일 업로드는 그대로 유지하므로,
  실패 원인을 확인할 수 있다.
- 반대로 5/5의 **수집 성공**은 회계범위/OFS의 본문 적격성,
  과거 시점(PIT) 또는 투자 점수 승인이 아니다. `investment_data_ready`
  및 `confirmed_scoring_allowed`는 반드시 false.
- `requests` 12초 읽기 timeout은 총 다운로드 시간을 보장하지 않는다.
  ZIP API 응답이 장시간 스트리밍되어 느려지면 다음 계측·제한 개선이 필요.
