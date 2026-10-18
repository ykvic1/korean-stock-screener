# KRX 승인 대기 중 개발 완료/미완료 경계 — v3 안전 수집 모듈

기준일: 2026-10-18. 사용자가 KRX 무료 Open API를 신청했으며, **공급자 승인 처리는 평일 예정**. 가입/신청만으로 승인이나 실제 KRX Open API 실조회가 완료된 것은 아니다.

## 무엇이 실제로 확인되었나?

| 구분 | 상태 | 정확한 의미 |
|---|---|---|
| GitHub Actions 기존 v2.2 평일 자동 실행 | 구현·최근 CI 확인 | 일별 기존 보고서 생성 경로 존재. 모든 항목이 유효한 실측 수급이라고 의미하지 않음 |
| Kiwoom REST `ka10059` | **사용자 등록 IP PC의 live 100개 행 조회 성공** | 기관·외국인·연기금등 필드 응답. GitHub-hosted runner 자동수집을 보장하지 않음 |
| OpenDART | 기존 제한된 라이브 표본 조사 경로 | 과거 5개 기업 연구가 성공했더라도 전종목 과거 공시 PIT가 완성됐다는 뜻 아님 |
| KRX 공식 무료 Open API | **신청 완료, 관리자 승인/실전 KOSPI·KOSDAQ API 호출 미확인** | 공식 EOD 가격·누적거래량 코드와 오프라인 테스트 준비 |
| KRX Data Marketplace 웹 수급 (`pykrx`) | 오프라인 모의 테스트 통과, **GH-hosted live 미실시** | KRX 무료 Open API와 다른 공급 경로. 종목별 기관·외국인·연기금 이용 가능성은 별도 실험 필요 |
| KRX 정규장 거래량·역사적 수정주가·20/60/120/250일 수급 | **인증 미완료** | v3 공식 IAS 종합 점수/순위 출시는 여전히 잠금 |

## 이번 신규 코드: 승인 후 바로 검증하도록 준비

- `v3_krx_private_daily.py`: `KRX_API_KEY` 환경변수로 **KOSPI+KOSDAQ 각 1회만** 호출하는 연구용 공식 EOD 수집기. 현재 사용자 계정의 승인 상태를 모르므로 `--live` 없이는 **네트워크 호출 자체를 하지 않는다**.
- 전체 코스피·코스닥 자료를 받더라도 **6자리 종목코드 기준 500개 이상** 보이지 않거나, 날짜 오류·중복 코드·시장 간 중복·OHLC 불일치·잘못된 누적거래량이 있으면 실패 처리. 운영에 사용할 최소 커버리지 숫자는 승인 후 관측한 실제 서비스 계약에 맞춰 재검토. 가격이 0인 매매정지 후보는 가격 0으로 조작하지 않고 `None` 처리.
- 어느 한쪽 시장이 실패하면 **양쪽 모두 저장하지 않는다**. 양쪽이 성공해도 **정규장-only 거래량·수정주가·기업행위·거래소 세션 확인되지 않았다**는 표기가 강제로 유지된다.
- `krx_YYYYMMDD.json`은 **현재 PUBLIC Git 저장소 밖** 사용자의 비공개 로컬 폴더만 지원하며 Linux에선 파일 0600·디렉터리 0700. 같은 날짜/내용은 멱등 처리하고, 이미 저장된 날짜의 내용이 다르면 **조용히 덮어쓰지 않고 충돌로 표시**한다.
- KRX API 요청 헤더에 쓰는 비밀키, 공급자 원문 응답·인증 오류 URL, 투자자별 수급 원본 값 등은 터미널/공개 Pages/Excel로 출력하지 않는다. 실제 응답은 검사한 뒤 개인 연구용 비공개 기록으로만 다룬다.
- `.github/workflows/krx-official-private-ingest.yml`: **수동** `workflow_dispatch`만 제공, `dry_run`이 기본. 관리자 승인 후 `mode=live`를 명시적으로 선택하면 GitHub-hosted runner에서 두 시장 **EOD 실조회**를 검증. 비공개 수신 파일은 오직 GitHub runner의 `runner.temp`에만 잠시 만들어지며, Git 커밋/업로드/공개 아티팩트 생성은 **금지**. 잡 종료 후 데이터는 남지 않으므로 **자동 수집·증분 영구 보관이 이미 완성됐다고 주장할 수 없음**.
- `tests/test_v3_krx_private_daily.py`: 승인·키 없이 오프라인 가상 응답에서 두 시장/단일 시장 장애/불변 스냅샷/오류/비밀정보 보호를 테스트.

### 승인됐을 때 1회 확인

- 사용자가 [KRX 공식 OPEN API](https://openapi.krx.co.kr/)에서 **KOSPI/코스닥 일별매매정보 서비스 각각의 관리자 승인**을 확인.
- GitHub Actions Secrets `KRX_API_KEY`가 본인 저장소에 등록됐는지만 확인. **실제 키 문자열·토큰을 채팅에 올리지 않음**.
- [GitHub Actions → KRX Official Two-Market Private Ingestion](https://github.com/ykvic1/korean-stock-screener/actions/workflows/krx-official-private-ingest.yml) → `Run workflow`; 과거 완료된 거래일 `asof=2026-10-16` / 최초엔 `mode=dry_run` 선택, 승인 후 `mode=live`.
- 성공 시 `new_private_snapshot_written`, `kospi_rows`, `kosdaq_rows` 확인. 실패하면 `ingestion_blocked`로 표시하고 데이터 0건/거래량 0이라고 추론하지 않음.
- [GitHub Actions → KRX Investor Flow Portal Feasibility](https://github.com/ykvic1/korean-stock-screener/actions/workflows/krx-website-flow-pilot.yml) **별도** 수동 연구 워크플로에서 수급 `pykrx` 실제 접근 테스트. 기관·외국인 수급 연결 검증은 공식 가격 API 테스트 결과와 혼동하지 않는다.

### 실제 매일 자동 갱신을 시작하기 전 추가할 기능

1. 관리자 승인된 무료 공식 API KOSPI/코스닥 가격·누적거래량 응답을 실제 한 거래일 관찰.
2. GitHub-hosted runner에서 KRX 웹 수급(기관·외국인·연기금등)이 이용 가능한지 실측.
3. **공개 저장소에 KRX 원시 시세·수급을 커밋하지 않는 비공개 영구 저장소** 결정. GitHub runner 임시 디스크에 쓰는 것만으로 과거 이력은 축적되지 않음.
4. 성공/실패·휴장일 판단·증분 갱신·20/60/120/250일 관측 충족·수급/차트 지표 품질 검증.
5. 원본 데이터를 남들에게 재배포하는 서비스가 아니라 최종 분석 결과만 지인에게 제공하는 목적을 유지. 화면·배포는 데이터 이용 조건에 맞게 추후 연동.
6. 기존 `daily.yml` v2.2, Excel, Pages는 출시 검증 전 그대로 유지하며 **v3_release_ready=false**.

**현재 외부 고정 IP Oracle/키움 서버는 보류**, 사용자의 PC에서 반복 PowerShell 실행도 요구하지 않는다.
