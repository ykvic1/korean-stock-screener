> **2026-10-19 후속 정정 (Step 11):** 이 문서의 250일 범위는 *당시 파일럿 표본*이며 전체 원본 보존 기간 또는 v3.0 학습 모집단의 상한이 아니다. 5~10년 이상 이력과 원본 보존 정책은 [Step 11](./V3_KRX_STEP11_DECADE_HISTORY_ARCHITECTURE.md)에서 확장했음. 과거 실제 데이터 확보·공식 세션 인증은 계속 미완료.

# KRX 공식시세 250개 거래일 확보 준비 — 4단계: 휴장일 후보 및 해시 연속성

## 최신 실제 결과
Run [39191714723](https://github.com/ykvic1/korean-stock-screener/actions/runs/39191714723) / 2026-10-19:
2026-10-15, 2026-10-16의 KOSPI·KOSDAQ 4개 일시장 모두 정상 수신.
- 총 5,524행, 숫자 유효 5,300행, 검역 224행.
- **검역 224행 전부 O/H/L=0, C는 0이 아니며 ACC_TRDVOL=0**.
- 이 패턴만으로 거래정지나 상장폐지·종가 보정 여부는 확인되지 않음. 0인 OHLCV는 전일종가로 대체하지 않고 계속 검역.

## 병목과 이번 해결
기존 `v3_krx_private_backfill_plan.py`은 모든 월~금을 수집 대상으로 계산해 추석·설날·대체공휴일에 KRX 응답 0행으로 중단될 수 있음. 저장된 날짜는 append-only라 건너뛴 휴장일을 기존 평일 prefix 플랜에 맞출 수도 없음.

이번 기능은 아래 **두 후보 달력 중 사용자가 명시적으로 선택**하는 것으로 제한.

- `--calendar-source weekdays`: 기존 월~금 전부 후보, 하위호환용 기본값
- `--calendar-source xkrx`: third-party `exchange-calendars==4.13.1` 패키지의 XKRX 휴장일을 제외한 **후보**를 만듦. 공식 KRX가 서명한 거래일 목록은 아님.
- 후보 일자 목록 SHA-256, 패키지 버전, 제외한 평일 개수 출력. 나중에 외부 공식 달력이나 실증 검증으로 교차 검증 가능하지만 해시가 곧 증명은 아님.
- 양 시장에서 실제 응답이 정상 검증된 날짜만 SQLite에 원자적으로 누적. 비정상 응답/휴장일 누락은 실패 처리하고 다음 기회에 수정. 자료 없이 거래일을 삽입하거나 0주가를 보정하지 않음.
- 원본은 사용자 개인용 비공개 SQLite만 허용. GitHub Actions 원본 백업이나 공개 커밋 없음. 원본 보관은 KRX 이용계약 및 보안환경 확인 후에만 로컬에서 수동 활성화.
- 동일 DB에 서로 다른 후보 달력 순서를 섞어 과거 일자를 건너뛰거나 소급 삽입할 수 없음: archive-prefix 검사에서 중단.

## 연구용 수집 후보 예시 — 원본 수집 0회
```bash
# 사용자의 로컬/안전한 연구 환경에만 OPTIONAL 설치
python -m pip install exchange-calendars==4.13.1

python -m v3_krx_private_backfill_plan \
 --start 2025-10-13 --end 2026-10-16 \
 --calendar-source xkrx --batch-size 5 \
 --private-db ~/.local/share/korean-stock-screener/v3/private_market.sqlite3
```

패키지 v4.13.1 기준 이 기간 XKRX는 정확히 **250개 거래일 '후보'** 를 반환함. 실제 공식 KRX 거래소 휴장 변경·공휴일 수정·사전 지정 임시 휴장 등이 반영됐다는 보장은 없어 `official_trading_days_250_verified=false`.

## 권한 및 개인 암호화 저장이 확인된 이후에만 수동 live
```bash
python -m v3_krx_local_resume_batch \
 --start 2025-10-13 --end 2026-10-16 \
 --calendar-source xkrx --batch-size 5 \
 --private-db ~/.local/share/korean-stock-screener/v3/private_market.sqlite3 \
 --live --ack-private-storage
```
이 코드는 수동 실행 **한 번에 최대 5일×2개 시장 = 10 API 요청**만 한다. 250개 시계열 증명 및 원본 영구저장은 외부 로컬 환경에서 각 날짜가 실제로 관측·검증·암호화돼 누적됐을 때만 평가한다. 현재 운영 GitHub에 250일 자료가 축적됐다는 뜻이 절대 아님.

## 아직 미완료
- 공식 KRX 휴장일 이력과 변경가능성 검증, 종목기본정보 API 별도 서비스 승인
- 실제 250일 양시장 원본 영구 누적, 기업행위/수정주가, 정규장 누적 거래량 검증
- 외국인·기관·연기금 수급 출처, 독립 시계열 PIT 입증 및 v3 실전 점수 활성화

**보호범위**: v2.2 18:30 예약 워크플로, docs/Pages 및 공개 투자 추천/보유현황 변경 없음. PR CI는 순수 오프라인 합성 시장자료 검증으로 제한.
