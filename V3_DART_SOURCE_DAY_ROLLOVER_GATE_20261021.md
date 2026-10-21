# DART 공식 원천 기준일 지연 검사 — 2026-10-21

## 발견한 실제 상황
- 평일 18:30 전체 100종목 배치는 **2026-10-21** 시장자료를 성공적으로 발행.
- `docs/v3-dart-book-reference.json`, `docs/v3-dart-bulk-history.json`은 검사 당시 **2026-10-20** 기준.
- 새 DART source-only 전용 큐 적용 후 최초 `V3 Actual DART 3Y Cashflow...`와 `V3 Bulk OpenDART Four-Year...` 작업은 해당 기준일 불일치로 실패. 자료 위·변조나 API 호출 한도 초과 확인이 아니라 **직전일과 오늘 자료가 섞이지 않도록 설계된 기존 안전검증**이 차단한 것.

## 개선

`v3_dart_source_day_preflight.py`는 API 호출 없이 현재 v2.2 전 종목 명부, 시장일자, DART 공식 연간/4개년 source의 계약·실제 기준일을 분리 확인한다.

- 공식 DART source `asof`가 시장 원장과 같으면 기존 수집/점수 미인증 계산을 계속.
- 정상 형식의 현재 사업연도 DART source가 **최대 10달력일** 뒤처진 경우, 해당 source collector만 안전 보류. GitHub Step Output `ready=false`; **공식 API 호출 0회, 원장 수정 0건**, 아예 최신 값을 생성했다고 표시하지 않음.
- 공식 원천이 미래 일자이거나 10일 초과 지연, 상장명부 불일치, 거짓 인증·원시 재배포 표기, 회계 연도 불일치라면 **오류로 종료**(정상 보류 아님).
- 대기 중 이전 저장 원장 숫자는 계속 이전 기준일로만 표시. 누락 값을 0점/0원으로 대체하지 않음.
- 4개년 DART는 이전 source 연도들의 진위 여부를 임의로 인증하지 않으며, IAS20·산업15·정식 100점은 계속 차단.
- DART 단년도 수집기가 동일 시장일자로 원장을 갱신하면 다음 4개년 예약에서 정상 자동 실행. 이어 4개년 source가 갱신되면 다음 현금흐름 예약에서 정상 재개.
- DART 신규 호출 빈도, 18:30 전체시장 동작, 기존 저장 위치·파일 소유권·UI는 변경하지 않음.

## 안전 검증
- synthetic source day same / +1 / 미래 / >10일 / 위조된 출처 검사
- CLI 실행 시 GitHub output에 `ready=false`만 작성하고 데이터 파일 변경 없는지 검사
- 실제 워크플로의 provider/게시 단계에 모두 `if: steps.date_gate.outputs.ready == 'true'` 가 적용됐는지 검사
- 전체 CI 성공 후 병합하고 실제 workflow 2개 모두 '정상 보류' 표시 확인. 보류는 **실제 재무 커버리지 증가가 아님**.
