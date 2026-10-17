# Kiwoom REST ka10059: 등록 IP에서만 실행하는 읽기 전용 연결 시험

2026-10-17 기준: 키움증권 기존 계좌의 **App Key 및 App Secret 발급 완료는 사용자가 직접 확인**. 그러나 GitHub Secrets 등록, 허용된 실행 서버 IP, OAuth/실제 조회 성공, 데이터 사용·재배포 권리는 아직 **검증되지 않음**.

## 공식 근거

- 키움 신청 절차: https://openapi.kiwoom.com/intro/serviceInfo
  - 등록된 IP에서만 API 인증 허용, 최대 10개 IP.
  - 실전/모의 키가 다르고 App Key 및 App Secret을 함께 사용.
- 공식 OAuth 인증: https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/core/auth.py
- 공식 ka10059 투자자별 일별 수급: https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/종목정보/get_domestic_stock_investor_by_institution.py
- GitHub Actions 유동 IP 주의: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-githubs-ip-addresses

## 보안 원칙

1. App Key / App Secret 값을 **채팅, Git 커밋, 스크린샷, 코드, 로그, 공개 게시물**로 보내지 않는다.
2. 등록된 **공인 IP**에서만 시험한다. 휴대폰·아이패드에서 키를 발급한 IP가 GitHub Actions 또는 다른 서버의 IP와 같다는 가정은 금지.
3. 일반 GitHub-hosted Actions 러너의 공인 IP는 유동적이다. 현재 **자동 실행 워크플로는 만들지 않았다.**
4. 실전 계좌와 모의투자 키를 혼용하지 않는다.
5. 계좌 조회·주문·잔고 조회는 시험 범위에서 제외한다.

## 수동 1페이지 시험 (PC 또는 등록 IP의 서버)

Python 3.11 이상이 준비된 **키움에 등록한 공인 IP의 컴퓨터**에서 저장소를 체크아웃한다.

명령 (사용자가 직접 입력하고 실행):

```bash
python v3_kiwoom_readonly_pilot.py --mode real --code 252990 --asof 20261016 --prompt-credentials --live
```

- 실전 계좌용 키라면 `--mode real`; **모의투자 키만 있을 때** `--mode demo`.
- App Key와 Secret은 **입력 내용이 보이지 않는 별도 프롬프트**에서만 붙여 넣는다.
- 대안: CI가 아닌 안전한 별도 실행 프로세스의 `KIWOOM_APP_KEY` 및 `KIWOOM_APP_SECRET` 환경변수를 사용할 수 있다. 키 값을 셸 명령행 인수, 저장소 파일 또는 작업 로그로 전달하지 않는다.
- 종목은 **샘씨엔에스(252990)**, 기준일 **2026-10-16**, 1회 인증 + 1회 `ka10059` 조회만 수행한다.
- 금액/수량 `amt_qty_tp=2`, 순매수 `trde_tp=0`, **단주** `unit_tp=1`로 고정.
- 연속조회나 자동 재시도 없음. 응답 원본 전체나 수급 수량은 내보내지 않고 응답 행 수와 필드 형식 통과 여부 등 안전한 요약만 출력.
- **로그에 접근 토큰, App Key, App Secret, 등록 IP를 출력하지 않는다.**
- 실행 결과에 `confirmed_scoring_allowed=false`, `v3_release_ready=false` 유지. 단일 API 성공으로 독립 공급원 교차검증을 통과한 것으로 판단하지 않는다.
- 다음에는 공급원별 수급 날짜/거래시간/정규장·대체거래소/확정상태를 별도 감사하고, 이용약관 허용 범위를 확인한다.

## GitHub Secrets와 이후 자동화

[리포지토리 Secrets 관리](https://github.com/ykvic1/korean-stock-screener/settings/secrets/actions)에 입력할 **이름**:

- `KIWOOM_APP_KEY`
- `KIWOOM_APP_SECRET`

두 항목 등록 여부는 사용자가 직접 확인해야 한다. Secrets에 등록해도 **GitHub-hosted 러너 IP 등록 문제는 해결되지 않는다**. 고정 공인 IP 서버나 등록 IP의 안전한 self-hosted runner 등 **합법적 허용 환경을 확정한 뒤에만** 자동화 설계·승인한다. 키 재발급/로테이션 시 Secrets도 갱신한다.

## 상태

- 실제 연결 및 제공 범위 **미검증**.
- 이 코드는 실전 API에 자동 연결되지 않으며 GitHub Actions/일일 스크리너를 변경하지 않음.
- 기존 v2.2 결과, Pages/Excel, 일일 워크플로, IAS 투자 점수에는 영향 없음.
