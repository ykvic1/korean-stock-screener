# v3 Step 6 — 비공개 KRX 원본 재검증(네트워크 0회)

## 현재 근거 (2026-10-19)
- 수동 KRX 달력 실조회 [run 39196849545](https://github.com/ykvic1/korean-stock-screener/actions/runs/39196849545) `live` 성공.
- 2026-09-23: KOSPI 942행, KOSDAQ 1,821행의 해당 날짜 응답이 실제 관측됨. 검역 건수 각각 30/85.
- 2026-09-24: XKRX 휴장 후보, 두 시장 API 0행. **이 사실만으로 한국거래소 휴장일을 독립 인증한 것은 아님.**
- 검증된 범위는 *표본 두 날짜*뿐. GitHub에 250거래일 원본이 누적됐다는 증거 없음.

## 이번 PR의 실제 변화
1. `v3_krx_archive_reaudit.py`: 사용자가 별도로 허용하여 로컬에 보관한 비공개 SQLite를 **읽기 전용**으로 검토. 기존 내부 SHA256/체인 검증과 별도로 원본 JSON `OutBlock_1`, 각 행 `BAS_DD`, KOSPI/KOSDAQ, 6자리 숫자·영대문자 종목 코드, 중복, 가격 OHLC 구조와 누적 거래량, 영가(0) 검역을 일자별로 전수 재검사. 한 날짜/시장 단위 처리로 원본 전체를 동시에 재적재하지 않음.
2. 서로 다른 양 시장에 같은 단축 종목코드가 나타나거나, 저장한 원본 바이트/날짜/해시가 불일치하거나, 누락·역순 날짜로 후보 달력의 **처음부터 이어지는 접두구간(prefix)** 이 성립하지 않으면 실패. 임의 휴장 스킵/원본값 치환 없음.
3. 연구 리포트는 날짜·총건수·검역건수·전일 대비 종목코드의 출현/소멸 **집계만** 출력. 개별 종목명/코드/가격/거래량·API 응답 본문·키·경로 출력 금지. 원본 저장·업로드 없음.
4. `v3_krx_local_resume_batch.py`: 로컬 **수동 live 재개** 시 이미 보관된 날짜가 있다면 외부 API 조회보다 **먼저** 재검증. 구조 오류 확인 시 새 데이터 호출 0회로 차단.
5. CI는 100% 합성 원본 테스트. 기존 v2.2 앱·18:30 예약·웹 페이지·추천 결과 및 기존 API 계약은 수정하지 않음.

## 실행 방법

```bash
# KRX 공식 API 호출 및 원본 저장이 전혀 없는 로컬 읽기 전용 검사.
# 기존 개인 비공개 SQLite가 없으면 날짜 후보만 표시한다.
python -m pip install 'exchange-calendars==4.13.1'
python -m v3_krx_archive_reaudit \
  --start 2025-10-13 --end 2026-10-16 \
  --calendar-source xkrx \
  --private-db ~/.local/share/korean-stock-screener/v3/private_market.sqlite3
```

이 명령은 **기존 원본 저장 사용 허가가 있는지 검증하지 않는다.** 개인 PC에 원본을 새로 저장하라는 승인이 아님. 기존 데이터가 없다면 네트워크/DB 파일 생성 모두 0회이고 단순 후보 계획만 출력한다.

## 출시 금지 조건

반환값의 다음 플래그들은 원본 구조/내부 해시가 모두 맞더라도 **계속 false**:
- `independent_raw_source_authentication_verified`
- `official_exchange_calendar_authenticated`
- `independent_archive_head_anchor_verified`
- `historic_issuer_master_and_delistings_verified`
- `corporate_action_and_adjusted_prices_verified`
- `regular_session_only_volume_verified`
- `external_pit_financials_and_investor_flow_verified`
- `historical_250_sessions_verified`
- `v3_release_ready`, `confirmed_scoring_allowed`, `confirmed_buy`

이는 저장소 속 가격의 **형식 검증**이지 **공식 원본의 서명 인증, 저작권/사용권, 완전한 거래일 인증, 거래정지 원인, 액면분할 조정, 수급 신뢰성**까지 증명하는 것이 아니다. 추가 KRX 서비스 신청, 라이선스 확인, 별도 암호화 보관 및 독립 PIT 교차검증이 완료되기 전에는 투자 추천으로 승격 금지.

## 검증 수칙
- PR CI 녹색을 확인하고서만 main 병합.
- 실제 KRX live/비공개 원본 영구 수집 작업은 **요청하지 않음**.
- 이 단계에서 사용자가 수행할 수동 GitHub Action 없음.
