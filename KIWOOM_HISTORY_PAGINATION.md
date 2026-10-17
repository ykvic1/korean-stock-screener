# 키움 실조회 성공 이후: 최대 3페이지 날짜·수급 이력 확인

## 확인된 사실과 미확인 사실 (2026-10-18)

사용자가 직접 등록 IP의 PC에서 PowerShell `kiwoom_readonly_probe.ps1`를 실행한 **화면 출력**을 제공했다.

- `offline_self_test_passed`: 가상 시험 통과
- 실전(`mode=real`) `readonly_probe_passed`: 토큰 인증 + `ka10059` 1페이지 조회 성공으로 보고
- 종목 `252990`, 기준일 `20261016`
- **1페이지 100개 행**, 최신 `2026-10-16`, 최과거 `2026-05-20`
- 기관·외국인 부호 있는 단주 수량 문자열과 `penfnd_etc`(연기금등) 응답 필드 검사 통과
- **중요:** 화면은 요청·응답 원본이나 독립 원천 데이터 대조 결과가 아니므로 라이브 실행 보고로 취급. KRX 정규장 범위, 수급 주체 정의, 100개 영업일 연속성, 120/250일 호출 가능성, 제3자 일별 수량 일치 및 API 사용/재배포 허용은 미인증.

## 사용자 PC 추가 시험: 파이썬 불필요

키움에 등록한 공인 IP와 동일한 네트워크에서만 실행한다. 테스트는 read-only `ka10059` 조회이며 **계좌정보/주문/자동매매 없음**. GitHub-hosted Actions는 실제 키움 API 호출 금지.

1. GitHub에서 [키움 3페이지 시험 스크립트](scripts/kiwoom_pagination_probe.ps1)를 열어 내용을 확인하고 `Raw` 파일을 `kiwoom_pagination_probe.ps1` 이름으로 PC 다운로드 폴더에 저장.
2. 다운로드 폴더에서 Windows PowerShell 실행.
3. **가상 시험 먼저 실행**:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\kiwoom_pagination_probe.ps1 -OfflineSelfTest
   ```

   정상 메시지: `pagination_offline_self_test_passed`. 가상 데이터, 무인증, 무네트워크, 키 입력 없음.

4. 해당 PC의 실제 공인 IP가 키움 등록 IP와 여전히 같고 실전 App Key가 발급된 경우만 **명시적 연결 시험**:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\kiwoom_pagination_probe.ps1 -Mode real -Code 252990 -AsOf 20261016 -MaxPages 3 -Live
   ```

5. 키움 App Key/Secret은 마스킹된 두 프롬프트에만 각각 입력한다. 값이나 파일을 채팅으로 보내지 않는다. 모의투자 키라면 `-Mode demo`로 변경.

최대 **토큰 1회 + 수급 3페이지**, 연속조회 헤더 `cont-yn=Y` 및 `next-key` 사용. 최대 750행까지 한 페이지당 250행 제한 검사; 날짜 엄격 내림차순, 미래·중복 거래일, 필수 부호 있는 정수 수량, 연기금등 항목의 부분 누락을 차단.

**화면에 표시되는 결과만** 사용자에게 요청:

- `readonly_pagination_probe_passed`
- `observed_pages`, `rows_by_page`, `unique_ordered_date_records`, `oldest_date`
- `observed_120_records`, `observed_250_records`, `more_pages_at_limit`

실제 투자자별 수량·키·토큰·계좌번호·IP 원문은 출력하거나 저장하지 않음.

이 단계에서는 **100/120/250개의 서로 다른 내림차순 날짜 레코드 존재**만 확인. 정식 영업일 달력/수정주가/거래세션 구분과 20일 Npay와의 **정확한 수량 대조는 아직 별개**이며, `confirmed_scoring_allowed=false`, `ias_flow_component=null`, `v3_release_ready=false`를 계속 유지한다.

기존 `daily.yml`, v2.2 운영, 보고서/Pages, 자동 수집/게시 변경 없음.
