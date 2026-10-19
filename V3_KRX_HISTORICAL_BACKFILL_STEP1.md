# 1단계 — KRX 공식 일별매매정보의 250개 이상 양시장 날짜 이력 구축 준비

## 현재 구현
- **실제 KRX API 2시장·1일 2회 호출을 지원**하는 독립적 읽기전용 수집기 `v3_krx_history_collection.py` 추가.
- 한 번에 최소 1일~최대 5일, 완료된 평일 날짜를 명시해 호출. 휴장일은 공식 응답으로 확인하며, 평일을 실제 KRX 거래일로 간주하지 않음.
- 양 시장(KOSPI/KOSDAQ)을 각각 호출하여 6자리 종목코드, 중복, 날짜 오염을 확인. 각 시장에서 OHLC 정합성·ACC_TRDVOL의 숫자 형식을 점검, 가격이 없는 종목은 임의 보정하지 않고 quarantined_unpriced_candidates로 집계.
- **두 시장이 모두 유효한 하루만** 일치된 한 쌍의 관측일로 인정. KRX 원본 응답은 기본 GitHub Actions 경로에서 디스크에 보관하지 않으며, 원본 거래량을 정규장 09:00~15:30 거래량으로 승격하지 않음.
- 명시적 보관 동의가 있고 로컬의 **공개 저장소 밖** `--private-db`를 지정한 경우에만 기존 v3_local_private_store.store_pair를 활용해 일별 두 시장 원본을 로컬 개인용 SQLite에 원자적으로 보관. 저장 중단 시 이미 완료된 과거의 일별 양시장 쌍만 보존, 실패한 반쪽 날짜는 생성하지 않음. 보관 전에 공식 데이터 사용권한을 반드시 확인해야 함.
- `mode=dry_run`은 KRX 호출을 전혀 하지 않음. `mode=live`도 사용자가 수동 선택하고 날짜 5개 이하일 때만 조회.
- `evidence/krx_history/`는 .gitignore로 공개 커밋 방지, Actions artifact는 검증 숫자·시장별 해시만 7일 유지. 데이터 재배포 권리나 외부인에게 artifact 접근을 허용한다는 뜻 아님.
- 원본은 SHA-256 검산·중복방지 대상이고, KRX 승인만으로 **정규장 전용 OHLCV·수정주가·기업행위·과거 상장폐지 회사·외국인·기관 수급**이 인증되지는 않음.

## 사용자 수동 시험 (GitHub)
GitHub 저장소 → Actions → `KRX two-market historical daily pilot (manual only)` → Run workflow → main.

- dates: `2026-10-15,2026-10-16` (예시: 완료된 정규거래일, 2일×2시장=4회 API 호출)
- mode: 먼저 `dry_run`; 기본 테스트 통과 후 `live`.
- 이 워크플로는 테스트용 요약을 제외한 원본 OHLCV 및 기관·외국인 수급을 출력하지 않으며 영구 데이터베이스도 생성하지 않는다.
- 실패 시 GitHub Actions가 실패로 표시되며 로그에는 허용된 정적 오류 코드만 남는다.

## 로컬 개인 컴퓨터에서 직접 이력 구축 (별도 보관 계약 검토 후)

```bash
# KRX_API_KEY를 환경 변수로 별도 등록, 키를 명령행/스크립트/로그에 쓰지 않기
python -m v3_krx_history_collection --dates 2026-10-15,2026-10-16
# 주식/시장 숫자와 해시 요약만; 원본 저장 없음

# 로컬의 안전한 외부 경로에 영구 보관하고자 하는 경우에만:
python -m v3_krx_history_collection --dates 2026-10-15,2026-10-16 --live \
  --private-db ~/.local/share/korean-stock-screener/v3/private_market.sqlite3 \
  --ack-private-storage
```

백필은 **과거 날짜부터 최신 날짜 순서로** 보관해야 한다. 저장된 같은 날짜 원본이 달라졌거나 요청 타임스탬프가 바뀌면 원본을 덮어쓰지 않고 실패한다. 각 5일 묶음마다 진행 상황과 누락을 검사한 후 다음 5일을 진행하는 것이 목적.

## 현재 불가능한 것과 후속 연결
**아직 250개 거래일이 수집된 상태가 아니다.** GitHub Actions는 실행 때마다 새 러너이므로 2일 수동 시험을 125회 반복해도 동일한 로컬 SQLite에 이력이 누적되는 것이 아니다. 250개 실제 관측일 확보를 위해서는 암호화 및 운영 정책을 갖춘 지속성 데이터 저장소 또는 사용자의 안전한 로컬 환경에서 5일씩 누적하는 워크플로가 추가로 필요하다.

이 수집기는:
- 달력상 평일을 거래소 공식 휴장일까지 검증한 거래일이라고 단정하지 않는다.
- 거래소가 제공한 일별 `ACC_TRDVOL`을 정규장 전용 거래량으로 단정하지 않는다.
- 비거래·거래정지·액면분할·배당락·신주인수 등 데이터를 자동 보정하지 않는다.
- 합성 스코어링 워크벤치나 v2.2 공개 Pages/Excel/매일 18:30 자동 실행에 연결하지 않는다.
- `verified_history_250_sessions=false`, `v3_release_ready=false`, `confirmed_buy=false`를 항상 유지한다.

## 근거
- https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd (2010년 이후 일별 데이터)
- https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp (인증키당 1일 10,000회 이하 이용 원칙)
- https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC (일별매매정보 계약)
