# v3: KRX 우선 / 고정 IP 추가 서버 보류 결정 (2026-10-18)

## 변경된 사용자 요구와 독립적 결론

- **목표:** 본인과 지인 소수가 무료로 쓰는 종목별 투자 분석 **최종 결과**. 사용자 PC 수동 데이터 갱신은 **개발 검증 몇 회 외 금지**. 가능한 기존 GitHub Actions에서 자동 실행하며 추가 서버 비용을 만들지 않는다.
- **정책:** 키움 1페이지 수급 실조회(252990의 100개 행)는 연결 가능성을 증명했지만, 키움 등록 공인 IP 때문에 일반 GitHub-hosted runner에서는 지속 인증되지 않는다. **Oracle Always Free 구축·키움 실전 자동 수집 서버는 무기한 보류**한다. 관련 개발 파일은 연구용으로 남겨두되 실행하거나 삭제하지 않는다.
- **데이터 공급 전략:**
  1. **KRX 공식 Open API**: KOSPI/KOSDAQ EOD 주가·거래량 및 종목기본정보 우선. 관리자 **서비스 승인 후** 기존 GitHub Actions의 수동 실연결 시험 및 주기 수집을 점진적으로 연결한다. 현재 `KRX_API_KEY`의 실제 서비스 승인 상태는 여기서 확인되지 않았다.
  2. **KRX Data Marketplace 웹 투자자별 거래실적**: 무료 **Open API 서비스 목록에 포함되지 않은 별도 자료**. `pykrx`로 기관합계/외국인합계 날짜별 수량 및 연기금 등 *당일 기간합계*가 제한적으로 조회되는지 GitHub-hosted runner에서 **1종목 읽기 전용 시험**. 이것은 **공식 KRX Open API 승인과 무관**하고, 사이트 구조 변경·접속 제한·권한/재사용 조건에 의해 실패할 수 있다. 단일 데이터 공급원으로 숫자 활용 가능하지만 날짜·단위·범주는 엄격히 검증한다.
  3. **키움 REST**: KRX 웹 수급 무인 운영이 실패하는 경우에만 대안. 사용자에게 PC를 매일 켜거나 서버를 결제하라고 요청하지 않는다.
- **공개 범위:** 현재 GitHub 저장소 `ykvic1/korean-stock-screener`는 **PUBLIC**이다. 원본 KRX/키움 수급·주가 행이나 금융 데이터 원문, 비밀키를 `docs/`, Git 커밋, Actions 로그, 공개 아티팩트에 올리지 않는다. 연구 테스트는 **날짜/행 수/정상 여부**만 출력. 최종 분석 결과의 지인 공유 역시 원본 자료 재배포와 구별하며 원천 이용 조건을 확인한다.

## 확인된 공식 서비스 차이

- [KRX 무료 Open API 전체 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd): `유가증권 일별매매정보`, `코스닥 일별매매정보`, 종목기본정보 등 제공. **개별 종목 기관·외국인·연기금 수급 API는 이 목록에 없음**.
- [KRX Data Marketplace 투자자별 거래실적](https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd?screenId=MDCSTAT022): 투자자별 순매수·금융투자·보험·투신·연기금등 조회 메뉴 제공. 웹사이트의 제공 메뉴가 무료 Open API 서비스 제공을 의미하지는 않음.
- [pykrx 패키지 설명](https://pypi.org/project/pykrx/): `stock.get_market_trading_volume_by_date`는 1종목 기관합계/외국인합계 20거래일 조회 후보, `get_market_trading_volume_by_investor`는 **한 날짜** 연기금등 주체 존재 검증 후보. KRX 외부 화면을 읽는 방식이므로 서비스 변경·빈 응답 사례에 취약.
- [GitHub pykrx issue #228](https://github.com/sharebook-kr/pykrx/issues/228): 정상 조회 조건에서도 빈 데이터프레임을 반환한 사례 기록. **접속 성공 전까지는 대체 가능성을 확정하지 않는다**.

## 현재 개발 패치: PC 없이 1종목 시험

- `v3_krx_website_flow_pilot.py`: 명시적 `--live` 옵션에서만 한 종목 **기관/외국인 45일 범위 일별 순매수** 최대 1회 조회 + 해당 날짜 **연기금등 기간합계** 최대 1회. 원본 숫자·조회 인증키·예외 원문 비출력. 날짜 내림차순·수량 정수·중복·20행 충분성·최근성 검사. 20일 연기금 이력이나 250일 장기 커버리지까지 확인됐다고 하지 않음.
- `.github/workflows/krx-website-flow-pilot.yml`: GitHub Actions **수동** 연구 워크플로, default `dry_run`, 실제 수급 수집을 예약/자동화하지 않음. 사용자의 집 PC·별도 VM·키움 App Key 불필요. **개발 확인 1회 목적**이며 수집 성공 시에만 제한 호출 스케줄을 후속 개발.
- `tests/test_v3_krx_website_flow_pilot.py`: 완전 오프라인 tests. 기존 `daily.yml`, v2.2, 공개 Pages, 정식 v3 점수, 구형 키움 연구 파일은 변경하지 않음.

### GitHub에서 1회 실조회 시험 (서비스 이용조건 검토 후)

[Actions → KRX Investor Flow Portal Feasibility](https://github.com/ykvic1/korean-stock-screener/actions/workflows/krx-website-flow-pilot.yml) → Run workflow

- `asof: 2026-10-16` (검증된 완료일), `code: 252990`, `mode: dry_run`으로 offline 확인
- 한 번 실제 연결 여부가 필요하면 `mode: live`. 1종목, 최대 2번 웹 요청, 서버 로그인/고정 IP 불필요
- `status=research_only_krx_portal_daily_candidate`: 당일 runner에서 날짜·부호 있는 기관/외국인 숫자 형식 정상 응답. **향후 모든 날짜에 지속 접속 보증 없음**.
- `status=research_unavailable`: 접속 불가, 빈 데이터, 변경된 형식 등. **수급 0 아님**. 점수에 반영하지 않음
- `single_day_pension_etc_row_observed=true`: 단일일자 **연기금등 후보 행** 존재일 뿐 20/60/120/250일 연기금 추세 검증 아님.

## 다음 통과 조건

1. KRX 공식 Open API 승인/서비스 접근 → **GH Actions live** 한 날짜 KOSPI/KOSDAQ EOD 정확한 읽기, 데이터 이용 권한 검토.
2. GitHub Actions `pykrx` 실제 수급 1종목 성공 여부/20일 데이터 커버리지 확인. 실패한다면 내부 결측으로 처리하고 **키움 재검토**.
3. 성공한다면 적절한 속도 제한/대상 수·거래일 달력/비공개 증거 보관 전략을 확정한 뒤에만 **평일 무인 수급 자동수집**을 연결. 기존 운영은 지키고 마이그레이션 전 회귀 테스트.
4. 기관/외국인/연기금 정보가 누락될 경우 임의 0주로 입력하거나 수급 점수를 강제 생성하지 않는다. 최종 `v3_release_ready=false` 게이트는 기술적·재무적 나머지 검증 완료 전까지 유지.

**중요:** 현재 KRX 주가·거래량 공식 API에 대한 GitHub Actions 자동 실조회·저장이 이미 완료됐다고 말할 수 없으며, 수급 무인 수집도 아직 시험 전이다. 두 공급 경로의 승인이 확인된 뒤 자동화한다.
