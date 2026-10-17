# v3 키움 REST API 수급 데이터 자동 수집 — PC 없이 운영하기

**현재 단계: 프로그램·서버 설정 템플릿 준비, 서버 미구축, 자동 실행 비활성.**  
사용자는 등록 IP의 Windows PC에서 실전 `ka10059` 100개 일별 수급 데이터 수신을 이미 확인했다. 반복적인 사용자 PC/PowerShell 동작을 정식 운영 방식으로 사용하지 않는다.

## 운영 원칙

- 키움 REST API `ka10059`만 기관·외국인·연기금등 수급 공급원으로 사용한다. **네이버/pykrx와 날짜별 순매수 숫자의 교차검증은 필수 출시 조건에서 제외**한다. 단, 유효한 날짜/순매수 수량/수량 단위/투자자 범주/상하위 집계 중복은 계속 검사한다.
- IP가 매번 바뀌는 일반 GitHub-hosted Actions에서 키움 API를 호출하지 않는다.
- 키움에 등록된 **고정 공인 IP**의 별도 소형 서버에서, KST **평일 오후 7:30**, 1일 1회 데이터 수집(예시). 서버의 OS 시간대를 `Asia/Seoul`로 설정해야 한다.
- 키움 API 실전 키는 서버 비공개 설정 파일 또는 승인된 비밀 저장소에서만 읽는다. 이미 등록한 **GitHub Actions Secrets가 자동으로 별도 서버에 전달되지는 않는다.** 서버 비밀값은 사용자/운영자가 별도 등록해야 한다.
- 실제 키움 API 이용약관/정기 자동수집·보관·재배포 허용 범위는 자동화 활성화 전에 확인한다. 자동매매/주문/계좌조회 기능은 일절 사용하지 않는다.
- 처음에는 **종목 1~3개로 수집 빈도/응답/사용량을 검증**한 후, 실제 호출 허용 범위에 맞춰 최대 35종목으로 늘릴 수 있도록 구현한다. 전체 상장종목 대량 수집은 쿼터/이용권한 검증 전 금지.

## 구조

```text
[고정 IP 서버: 평일 KST 19:30 systemd timer]
            |
            v
[키움 OAuth 토큰 1회]
            |
            v
[ka10059: 종목당 최대 3페이지, 최대 300행]
            |
            v
[날짜·수량·페이지 순서·연기금등 완전성 엄격 검사]
            |
            v
[20/60/120/250 "관측 레코드" 누적 수급·양(+)의 순매수 빈도]
            |
            v
[서버 비공개 JSON 파일 (Git 저장소 바깥, 0600 권한)]
            |
            v
[추후 v3 분석기에 연결: 공식 거래일/세션/재무 검증은 별도]
```

**주의:** `observed_rows=250`은 250개의 유효한 내림차순 날짜 행을 뜻할 뿐, 거래소 공식 250 영업일 연속성이나 정규장-only 범주까지 확인한 의미는 아니다. 미확인 구간은 `null`이며 0주로 대체하지 않는다. `penfnd_etc`는 국민연금 단독이 아니라 '연기금등'이고 기관 총합에 다시 더하면 이중계산이다.

## 사전 준비 — 비용 동의 후 1회만

1. **고정 공인 IPv4를 제공하는 소형 Linux 서버**를 선택한다. 무료 티어는 용량 제한·회수·공인 IP 정책 변경이 있을 수 있으므로 지속성 보장되지 않는다. 유료 서버 역시 IP 유지 조건, 월요금, 사용량을 확인한다.
2. 서버의 실제 **외부 송신 IP**를 키움 REST API 계좌 IP 허용목록에 한 번 등록한다. 이전의 PC Wi-Fi/모바일 IP는 이 서버 IP와 다르다.
3. 서버를 최신 보안 패치, 제한된 SSH 접근, 전용 비관리자 계정으로 구성하고, 서버 시간대를 `Asia/Seoul`로 설정한다.
4. 저장소 코드를 `/opt/korean-stock-screener`에 읽기 전용에 가깝게 설치한다. Python 3.11 이상 필요(이는 **서버**에서 사용하는 것이며 사용자 PC에 설치할 필요가 없다).
5. `/etc/korean-stock-screener/kiwoom.env`를 root 소유 **0600 권한**으로 만들고, 서버에서 직접 `KIWOOM_APP_KEY`, `KIWOOM_APP_SECRET`을 넣는다. 절대 GitHub 커밋·채팅·셸 명령인수에 넣지 않는다.
6. 전용 `kiwoom` 사용자와 `/var/lib/korean-stock-screener/kiwoom` 비공개 저장공간(0700)을 마련한다.
7. [service 템플릿](deploy/kiwoom-investor.service.example)을 `/etc/systemd/system/korean-stock-kiwoom.service`, [timer 템플릿](deploy/kiwoom-investor.timer.example)을 `/etc/systemd/system/korean-stock-kiwoom.timer` 이름으로 설치한다.
8. **등록 IP에서 종목 1개 수동 read-only 호출**로 확인한 다음에만 자동 timer를 활성화한다. 암호·액세스 토큰·수급 원본을 공개 로그/퍼블릭 Git에 올리지 않는다.

시험 호출(비밀번호를 명령행에 직접 적지 않음):

```bash
# 운영 서버의 비밀정보 환경이 구성된 뒤에만:
python3 v3_kiwoom_auto_collector.py --live --codes 252990 --private-dir /var/lib/korean-stock-screener/kiwoom
```

수집 성공 뒤, 서버 운영자가 systemd 타이머를 활성화한다:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now korean-stock-kiwoom.timer
sudo systemctl list-timers korean-stock-kiwoom.timer
```

실제 서버가 아직 없으므로 위 명령은 **현재 실행하지 않으며** 자동화가 켜졌다고 주장하지 않는다.

## 보안과 결과

- `v3_kiwoom_auto_collector.py`: 이미 공급한 `KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET` 환경변수, `--live`가 없으면 미실행. OAuth 1회, 종목당 최대 3페이지·종목 간 1초 대기, 유효성 실패 시 수량 0으로 날조하지 않음.
- 수집한 수급 수량은 `investor_YYYYMMDD.json` 원본을 **Git 작업 디렉터리 밖**의 비공개 경로에만 저장. 파일은 임시파일 작성→원자적 교체, POSIX 0600. 스크리너 결과/공개 Pages/엑셀로 자동 전송하지 않는다.
- 기능 성공: `status=complete`, `symbols_ok` 등 데이터 수집 범위/품질만 안전하게 표기. 일부 종목 오류는 `partial_failure` 및 실패 분류를 기록하고 기존 값을 실측 0으로 바꾸지 않는다.
- 가격·거래량의 KRX 검증, OpenDART 재무 PIT, 수급 거래세션/권한 등을 확인한 뒤 v3 정식 점수/대시보드와 통합한다. **투자자별 수급의 제3자 숫자 일치 검사는 요구하지 않는다.**

## 운영·비용 주의

- 일반 GitHub Actions는 고정 송신 IP가 아니므로 계좌에 등록한 Wi-Fi/IP만으로 자동 실행 불가. (참고: https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- 소형 유료 VPS는 운영 방식이 단순하지만 **사용자 예산 승인·클라우드 계정 개설**이 필요하다. 서버와 고정 공인 IP의 실제 공급사가 정해지기 전까지 청구금액을 추정하거나 서버가 준비됐다고 주장하지 않는다.
- GitHub 자체에는 v2.2 기존 자동 스크리너가 그대로 남아 있다. 키움 수급 v3 자동수집은 **서버 설정 전에는 작동하지 않는다**.
