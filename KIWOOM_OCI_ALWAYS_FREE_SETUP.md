# 무료 키움 자동 수집 서버 — Oracle Cloud Always Free 설치 가이드

**상태 (2026-10-18): OCI 사용자 계정·VM·IP 생성 전. 이 문서는 실행 준비이며, 이미 자동 수집이 가동 중이라는 뜻이 아니다.**  
목표: 사용자의 PC를 매일 켜지 않고 키움 실전 REST `ka10059` 기관·외국인·연기금등 데이터를 **평일 한국시간 19:30**에 한 번 수집해 **별도 비공개 서버 디스크**에 저장. 기존 GitHub Actions v2.2 운영이나 공개 점수는 변경하지 않는다.

## 공식 근거 및 비용 통제

- [Oracle Always Free 한도](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm): **home region**에서 무료 VM 제공. 2026년 공식 문서 기준 E2.1.Micro AMD 2개까지 또는 ARM A1 첫 **1,500 OCPU 시간 + 9,000 GB 시간/월**, 무료 계정에서 통상 2 OCPU/12 GB 동등 수준. *A1의 오래된 블로그 4 OCPU/24 GB를 최신 확정 한도라 믿지 말 것.*
- [공식 Free Tier 가입](https://www.oracle.com/cloud/free/): 무료 계정은 카드 인증이 요구될 수 있지만 무료 계정에서 별도 유료 계정 전환 전에는 크레딧 초과에 대해 자동 청구하지 않는 방식. **Pay As You Go 전환하지 않는다.**
- [OCI 공인 IPv4 종류](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm): 일시적(Ephemeral) IP도 **해당 VM이 유지되는 동안** 지속된다. VM 삭제 시 소멸. 예약(Reserved) IP는 VM 교체 시에도 이동 가능. 예약 IP의 이용 요금/무료 적격 여부를 OCI 결제 화면에서 확인하기 전에는 임의로 생성하지 않는다.
- [Google Cloud 공인 IPv4 요금](https://cloud.google.com/vpc/network-pricing): 무료 e2-micro를 쓰더라도 IPv4는 월 1시간을 초과하면 시간당 과금하므로 무료 운영 최우선에서 제외.
- **주의:** Oracle 무료 VM의 실제 가용 용량/가입 가능 여부는 계정·리전별로 다르고, 장기간 낮은 사용량으로 유휴 판정 시 무료 VM이 **회수될 위험**이 있다. 무료라고 무중단을 보장할 수 없다. 이를 회피하기 위한 가짜 CPU 사용 부하를 만들어서는 안 된다.

**설치 전에 반드시 OCI 콘솔의 리소스 목록에 `Always Free eligible` 문구와 예상 비용을 확인**한다. Free Trial에서만 무료인 고사양·유료 부가 서비스를 신청하지 않는다. 비용 예산 알림은 [Cost Management → Budgets](https://docs.oracle.com/en-us/iaas/Content/Billing/Tasks/managingbudgets.htm)에서 낮은 임계치로 설정해 유료 서비스 생성 실수에 대비한다.

## 1. 사용자가 한 번만 해야 하는 OCI 계정/VM 준비

1. [Oracle Cloud Free 계정](https://www.oracle.com/cloud/free/)에 가입하고 OCI Console에 로그인한다. 초기 **Home Region**은 추후 쉽게 옮길 수 없고 Always Free는 보통 홈 리전 내에서만 제공되므로 가입할 때 선택 가능한 리전을 확인한다. 한국 중부(서울)가 이용 가능하면 우선 검토하지만 리전 가용 자원은 보장하지 않는다.
2. OCI 콘솔 **Compute → Instances → Create Instance**로 이동한다.
3. 이름 예: `korean-stock-collector`. 이미지 **Ubuntu 24.04 LTS**. Shape의 **Always Free eligible** 필터 적용:
   - 우선: ARM `VM.Standard.A1.Flex` **1 OCPU / 2GB RAM** (콘솔에서 무료 적격·용량이 실제 확인된 경우만).
   - 대안: AMD `VM.Standard.E2.1.Micro` (메모리 1GB, 간단한 Python 표준 라이브러리 수집에는 시도 가능).
   - `Out of host capacity`면 비싼 Shape을 누르지 말고 다른 허용 AD를 확인하거나 기다린다. A1은 일부 AD에서 제한된다.
4. **Boot Volume / Block Volume도 Always Free 총 한도 내**로 둔다. 콘솔 기본 크기와 부가 디스크가 유료가 아닌지 마지막 생성 화면에서 확인한다.
5. **Networking:** Internet Gateway 경로가 있는 public subnet, 인스턴스의 **public IPv4 1개** (초기에는 무료 적격 기본 ephemeral IPv4로 충분). 공인 IPv4가 서버에서 인증 요청을 보내는 송신 IP와 같은지 실제 1회 확인한다. SSH 인바운드 22번은 가능한 한 사용자 현재 공인 IP /32만 허용. **80/443 외부 인바운드는 열지 않는다.** 서버의 **아웃바운드 HTTPS/443**은 허용되어야 키움 API를 호출할 수 있다.
6. SSH key pair를 생성/다운로드하고 **개인키는 본인 기기에만 보관**한다. GitHub나 채팅에 키 파일을 올리지 않는다.
7. 생성한 인스턴스 상세 화면에서 **Public IP Address**를 확인한다. 공개 IP 주소 자체는 로그인 비밀번호가 아니지만, 채팅으로 전송할 필요는 없다.
8. **키움 REST API 계좌 App Key 관리 → 허용 IP 등록 목록**에 **새 OCI 서버의 공인 송신 IPv4**를 한 번 추가한다. 기존 집 Wi-Fi/모바일 IP를 그대로 쓰면 OCI 서버 인증은 실패할 수 있다.
9. OCI의 유료 업그레이드/추가 VM/불필요한 Load Balancer·NAT Gateway·DB를 만들지 않는다. 예약 공인 IP가 필요하다면 예상비용을 확인하고 적용한다. 무료 VM도 회수되면 ephemeral IP가 변경될 수 있으므로 정기적으로 연결 상태를 확인한다.

**OCI 계정 생성과 보안 키 등록은 사용자 계정에서 수행해야 하는 1회 작업이며, 현재 AI가 사용자를 대신해 OCI 계정이나 VM을 만든 상태는 아니다.**

## 2. 서버에 접속해서 1회 설치 — Python은 PC에 설치하지 않음

인스턴스가 생성되고 부팅된 뒤, 로그인에 사용하는 SSH 개인키/인스턴스 IP로 서버에 접속한다. Ubuntu 이미지 기본 SSH 사용자 이름은 일반적으로 `ubuntu`이다. 실제 OCI 콘솔의 Connect/SSH 안내에 나오는 사용자명을 우선한다.

서버 터미널에 아래 명령을 순서대로 실행:

```bash
sudo apt-get update
sudo apt-get install -y git python3
python3 --version
sudo timedatectl set-timezone Asia/Seoul
sudo git clone https://github.com/ykvic1/korean-stock-screener.git /opt/korean-stock-screener
sudo bash /opt/korean-stock-screener/deploy/oci_prepare.sh --prepare
```

예상 출력: `oci_preparation_complete_timer_not_enabled`.

위 준비 스크립트는 전용 `kiwoom` 사용자와 비공개 데이터 저장 폴더(0700), 비밀환경 파일(0600)을 만들고 **systemd 서비스·타이머 설정 파일을 설치만 한다.** 실제 키움 인증 요청, 파일에 키 입력, 타이머 자동 실행, 외부 요금 발생을 대신 수행하지 않는다. 언제든 코드와 템플릿을 확인한 뒤 실행한다.

## 3. App Key/Secret을 서버의 비공개 설정에 1회 등록

**키움 계정의 실전 App Key/Secret 파일은 채팅·GitHub·SSH 명령줄·사진에 올리지 않는다.** Ubuntu 서버에 안전하게 로그인한 뒤 서버 **내부**에서만 설정한다.

```bash
sudo nano /etc/korean-stock-screener/kiwoom.env
```

파일 내용 형식(실제 키는 사용자만 로컬에서 입력):

```ini
KIWOOM_APP_KEY=실제_키_값
KIWOOM_APP_SECRET=실제_시크릿_값
```

저장 후 `sudo chmod 600 /etc/korean-stock-screener/kiwoom.env`로 권한을 확인한다. 실제 키 값을 `echo` 명령의 인수에 넣거나 화면 공유로 보여 주지 않는다. 실전/모의 키를 혼합하지 않는다.

## 4. 최초 단일 종목 1회 시험 후 자동 실행 켜기

**먼저 키움에 OCI 서버 IP 등록과 API 자동 조회 이용 조건을 확인**한다. 첫 실행은 252990 하나만 조회하는 **읽기 전용** 수집이다.

```bash
sudo systemctl start korean-stock-kiwoom.service
sudo systemctl status korean-stock-kiwoom.service --no-pager
sudo journalctl -u korean-stock-kiwoom.service --no-pager -n 20
```

성공 출력에는 `status=complete`, `symbols_ok=1` 등의 집계 상태만 포함한다. 수급 숫자·키·토큰은 표시하지 않는다. **이 단계는 서비스 호출 1회가 발생하므로 준비 완료 후 운영자가 실행해야 한다.**

해당 서버에서 실제 로그인/조회 성공과 일정·비용을 확인한 다음에만:

```bash
sudo systemctl enable --now korean-stock-kiwoom.timer
sudo systemctl list-timers --all korean-stock-kiwoom.timer
```

기본 [서비스 설정](deploy/kiwoom-investor.service.example)은 252990 하나만 대상으로 하며, [타이머 설정](deploy/kiwoom-investor.timer.example)은 **월~금 19:30 (VM 시간대 Asia/Seoul)**으로 되어 있다. 서버가 꺼지면 자동 수집도 중단된다. 한국 증시 **공휴일/휴장일에도 월~금 시간 스케줄은 실행**될 수 있으므로 수급 데이터 날짜를 공식 거래일인 것처럼 추측하거나 결측을 0주로 치환하면 안 된다.

대상 수십 종목 추가는 API 요금·호출 제한·라이선스 조건·주문 미사용을 확인한 뒤에만 확장. **전체 코스피/코스닥 수급 전종목 수집은 현재 구성 범위 밖**이다.

## 5. 사후 관리: PC 없이 운영하되 무료 서비스의 한계 확인

- OCI Console에서 VM 실행 중/스토리지·네트워크 이용량과 **예산 알림** 확인.
- 콘솔 **Monitoring / Alarms / Notifications** 기능을 통해 VM 중단을 알리는 경보를 설정할 수 있다(무료 한도·지원 지표 확인 필요). 단, VM 실행 중이라도 키움 인증이 실패할 수 있으므로 수집 상태는 별도로 확인해야 한다.
- 원시 수급 파일은 서버 **`/var/lib/korean-stock-screener/kiwoom/investor_YYYYMMDD.json`**에만 저장된다. GitHub Pages에는 올리지 않는다. VM이 회수되거나 디스크가 삭제되면 데이터 손실 위험이 있으므로 운영이 안정화되면 OCI 무료 Object Storage 등 **비공개 백업**을 별도로 설계한다.
- 이 단계에서 **키움 자동 조회 코드를 서버가 알아서 돌리는 부분까지** 구현되지만, 서버에서 **기존 GitHub v3 점수/공개 대시보드로 데이터를 자동 전달하는 부분은 아직 별도 개발·권한 검토가 필요**하다. 서버를 만들었다는 사실만으로 종목 추천 시스템 전체가 완전 자동이라고 표현하지 않는다.
- 키움 수급 단일 공급원을 기본으로 채택하고 **다른 공급원과 숫자 일치 필수 검사 제외**. 데이터 날짜·단위·투자자 범주·누락/중복·페이지 오류 검증은 유지한다.

## 6. 문제 발생 시 안전한 판단

| 증상 | 가능한 원인 / 조치 |
|---|---|
| OCI 가입/VM 생성 실패 | 가입/카드 인증, 리전 용량 부족. **유료로 자동 전환하지 말 것** |
| ARM Shape `Out of host capacity` | E2.1.Micro Always Free 여부를 확인하거나 시간 후 재시도 |
| SSH 접속 불가 | 인스턴스 공인 IP, SSH 키, VCN 보안목록, 22번 허용 원본 IP 확인 |
| `token_denied` / `provider_network_or_ip_failure` | 키움에 등록한 IP가 실제 OCI 송신 IP인지, 키 유형, HTTPS 통신 확인 |
| `partial_failure` | 종목 일부 수집 실패. 누락 데이터는 0주로 대체하지 않음 |
| `timer`는 있는데 수집 없음 | VM 시간대, 인스턴스 실행 상태, `journalctl`, OCI 보안·IP 변경 확인 |
| 무료 VM 회수/공인 IP 변경 | OCI 인스턴스/예약 IP 상태, 키움 IP 목록 재등록, 비공개 자료 백업 복구 검토 |

**추가 승인 전 실행하지 않는 일:** OCI 계정·VM 생성/과금, 유료 업그레이드, 키움 실 API 자동 호출, 키움 키를 채팅이나 GitHub Actions로 재전송, 원시 수급 대외 공개, 기존 v2.2 스크리너 변경.
