# KRX 공식 실자료 5거래일 압축 측정 (2026-10-20)

## 실제 실행 결과

- GitHub Actions: https://github.com/ykvic1/korean-stock-screener/actions/runs/39389278004
- 실행 방식: **KRX 공식 Open API 실제 호출**, 시장별 일별 원본 JSON을 프로세스 메모리에서 읽고 무손실 GZIP으로 압축/원상복원. 원시 시세·원본 JSON·압축 원본은 PUBLIC Git/Actions 아티팩트, 파일, 캐시에 기록하지 않음.
- 시세 기준일: **2026-10-12~2026-10-16**, 5거래일×KOSPI/KOSDAQ = **10회** 원천 API 호출.
- 수신한 시장-종목 행 합계: **13,808행** (일자별 행 수는 당시 KRX 반환 집합; 현재 상장 2,497종목 전용 목록과 다를 수 있음).
- **5일 합계 원본 4,448,563 bytes**, 신규 바이너리 프레임 GZIP 보관 시 **778,484 bytes** → **82.5% 절감**.
- 실제 원본 기준 일평균 GZIP **155,697 bytes**; 샘플 패턴이 유지된다는 단순 가정하에 과거 2,450거래일 **약 363.79 MiB** *압축 원본만* 예상. Git commit/tree/index/history, GitHub API overhead, 공시 원본, 수급 원본, 구조 변화, 영업일수 변화는 별개.
- **실제 Private 저장 성공 건수: 0**. API 연결을 검증했을 뿐 장기 원천 보존은 Private 저장소와 토큰 준비·이용조건 확인 후에만 진행.
- 재무 데이터와 투자자별 순매수 IAS20을 이 시세만으로 재현할 수 없으므로 '수급 데이터 확대'라고 주장하지 않는다.

## 실제 일별 크기(양시장 합산)

| 날짜 | 공급자 관측 행수 | 원본 byte | 압축 byte |
|---|---:|---:|---:|
| 2026-10-12 | 2,762 | 891,409 | 156,224 |
| 2026-10-13 | 2,761 | 890,096 | 155,573 |
| 2026-10-14 | 2,761 | 887,698 | 154,692 |
| 2026-10-15 | 2,762 | 892,416 | 156,641 |
| 2026-10-16 | 2,762 | 886,944 | 155,354 |

## 무료 Private 보관소 연결에 필요한 최소 사용자 작업

연결된 GitHub App은 저장소 파일 읽기·변경은 할 수 있지만 **새 저장소 생성 또는 Secrets/PAT 관리 도구 권한은 제공하지 않음**. 확인된 현재 계정 저장소는 Public `ykvic1/korean-stock-screener` 하나다. 사용자가 직접 다음을 해야 한다.

1. GitHub `https://github.com/new`에서 `ykvic1/korean-stock-private-data` 등 명확한 별도 이름으로 저장소 만들기, **Private** 선택. GitHub Free로 생성 가능. 아직 실제로 생성됐다는 주장은 하지 않음.
2. KRX Open API의 원본 **개인 비공개 자동 보관** 조건과 데이터 재이용 권한을 확인.
3. GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens에서 해당 Private 저장소에만 Contents **Read and write**, Metadata Read 권한을 허용하는 짧은 만료기간의 토큰 생성. 토큰은 사용자 측에서만 관리하고 채팅·Git 코드로 공유 금지.
4. 기존 **PUBLIC** 스크리너 저장소 Settings → Secrets and variables → Actions → Secrets에 `V3_PRIVATE_ARCHIVE_REPO` (값: 실제 저장소 `owner/repo`), `V3_PRIVATE_ARCHIVE_TOKEN` (비공개 PAT) 등록. 기존 `KRX_API_KEY` 유지.
5. **2026-10-20 추가 확인:** KRX 공식 이용약관의 비상업·제3자 제공 제한으로, 위 변수 하나만으로는 더 이상 원본 저장 작업이 활성화되지 않음. 한국거래소로부터 Github Private 장기 원본 보관이 허용된다는 서면 확인을 받기 전에는 실행 보류. 승인 확인 기록, 목적, 90일 내 재검토일 등 모든 필수 변수를 확인한 후에만 평일 19:23 KST 배치가 활성화됨. 자세한 조건은 `V3_KRX_OPENAPI_TERMS_LICENSE_GATE_20261020.md` 참고.
6. 연결 후 첫 실제 수집을 확인할 때 `durably_archived_pair_days=5`와 Private repo의 `krx/2016/` 아래 5일치 압축 파일, `krx/manifest.json` checkpoint, `compressed_private_source_bytes`를 각각 확인. 과거 시작일이 2016-10-17이므로 방금 측정한 2026-10-12~16 자료를 먼저 저장하는 구조는 아님.

## 용량 정책

- Private Git 보고 크기 **750MiB**, GZIP 원본 누적 **400MiB** 도달 전에 중단하는 안전장치가 설치됨.
- 실제 5일 샘플 평균으로 볼 때 순수 시세 GZIP 약 **364MiB**는 400MiB 내부에 있지만 여유 약 36MiB만 남고 Git 이력·공시·수급 저장은 포함하지 않음. 10년치 전체에 무조건 충분하다고 보장하지 않는다.
- **추가 비용 승인 없이** 유료 LFS·유료 서버·클라우드 구독을 신청하지 않음. GitHub Free의 비공개 저장소를 이용하되 Git 기록과 저장소 규모를 운영 중 검증.
