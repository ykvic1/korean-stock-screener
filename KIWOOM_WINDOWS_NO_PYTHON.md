# 키움 REST API: 파이썬 설치 없이 PC에서 1회 연결 확인

**PC가 꺼져 있다면 지금 할 일은 없습니다.** 이후 집 와이파이에 연결된 Windows PC를 켰을 때 아래 순서대로 실행하면 됩니다. 등록된 **공인 IP**에서만 시험하고, 키움에 등록된 IP가 현재 PC의 실제 외부 IP와 일치하는지 먼저 확인하십시오.

## 1. 파일 다운로드

프로젝트의 단일 스크립트: [scripts/kiwoom_readonly_probe.ps1](./scripts/kiwoom_readonly_probe.ps1)

GitHub 페이지에서 파일 내용을 확인한 뒤 **Raw → 다른 이름으로 저장**을 선택하고 `kiwoom_readonly_probe.ps1`이라는 파일명으로 다운로드 폴더에 저장합니다. 이름 끝에 `.txt`가 덧붙지 않았는지 확인하십시오.

이 스크립트는 Windows 내장 PowerShell 5.1 또는 PowerShell 7에서 구동됩니다. 파이썬, Git, 추가 API 라이브러리는 필요하지 않습니다.

## 2. 첫 번째: 가상 응답 테스트 (인터넷 API 호출 없음)

파일이 있는 폴더에서 PowerShell 창을 실행합니다. 파일 탐색기 주소창에 `powershell`을 입력하면 해당 폴더 위치로 창을 열 수 있습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\kiwoom_readonly_probe.ps1 -OfflineSelfTest
```

정상이라면 `offline_self_test_passed`가 출력됩니다. 이 단계는 **가상의 숫자/토큰만 사용**, 키 입력과 네트워크 접속은 전혀 없습니다. `Bypass`는 이 실행에만 적용하며 PC 전체 정책을 변경하지 않습니다. **GitHub의 본인 저장소에서 다운로드한 해당 파일만** 사용하고 알 수 없는 스크립트에는 사용하지 마십시오.

## 3. 두 번째: 실제 키움 API 읽기 전용 시험

**키움에 등록된 집 와이파이의 공인 IP**에서, 실전 거래용 App Key/Secret을 발급받았다면 다음 명령을 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\kiwoom_readonly_probe.ps1 -Mode real -Code 252990 -AsOf 20261016 -Live
```

모의투자 App Key/Secret이라면 `-Mode demo`를 선택합니다. 실전/모의 인증키가 다르면 인증되지 않습니다.

- 키움 App Key, App Secret이 차례로 요청됩니다. **입력 문자가 보이지 않는 프롬프트**에 파일 속 키 값만 붙여 넣으세요. 키나 계좌번호를 명령행에 넣지 마세요.
- 시험은 OAuth 토큰 발급 **최대 1회**와 `ka10059` 기관/외국인 일별 수급 조회 **최대 1페이지**뿐입니다. 계좌 조회, 주문, 자동매매는 하지 않습니다.
- 샘씨엔에스(252990), 2026-10-16 기준입니다. 순매수 수량은 `amt_qty_tp=2`, `trde_tp=0`, `unit_tp=1`로 조회합니다.
- 출력은 **응답 날짜 범위, 행 수, 필드 형식 검사, 성공/실패**만 포함하며, 토큰과 키, IP, 주식별 순매수 수량은 출력하지 않습니다.
- 실패하더라도 자동 재시도하지 않습니다. `probe_failed: ...`가 나오면 원문 오류·키·파일 내용은 공유하지 말고 **실패 메시지 분류만** 알려 주세요.
- 단 한 번 연결 성공하더라도 정규장 데이터·연기금 범위·20/60/120/250거래일 이력과 독립 공급원 일치, 데이터 이용 권리가 확인된 것은 아닙니다. IAS 확정 점수, v3 출시 차단은 유지합니다.

## GitHub Actions IP 제한

두 키를 GitHub Secrets(`KIWOOM_APP_KEY`, `KIWOOM_APP_SECRET`)에 등록했더라도 GitHub-hosted Actions의 공인 IP는 대개 집 와이파이/모바일 등록 IP와 다르므로 **키움 접속을 보장할 수 없습니다**. 이 스크립트를 자동 워크플로에서 실제 인증키로 실행하지 않습니다. PR CI에서는 **`-OfflineSelfTest`만 실행**하여 키/토큰/외부 API 접근을 완전히 제외합니다.

기존 v2.2 스크리너, `daily.yml`, 배포, Pages/Excel, 확정 수급 점수 등은 이 단계에서 변경하지 않습니다.

## 관련 공식 규격

- 키움 REST API: https://openapi.kiwoom.com/
- OAuth 토큰 규격: https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/OAuth%20인증/접근토큰발급/create_access_token.py
- 기관별 일자 수급 `ka10059`: https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/종목정보/get_domestic_stock_investor_by_institution.py
