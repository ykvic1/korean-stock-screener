# 2026-10-21: 점수표 발행과 원천 수집 대기열 분리

GitHub Actions의 `concurrency`는 그룹별 대기 항목을 제한한다. 긴 18:30 전 종목 배치가 실행되는 동안 DART 업종·FCF·점수표 예약 작업이 같은 그룹에 계속 쌓이면, 아직 시작하지 않은 이전 예약 작업이 취소될 수 있다. 2026-10-21 실제 Actions에서 DART/FCF 대기 취소를 확인했으며, 취소가 곧 기존 공개 원장의 삭제를 뜻하지는 않는다.

## 변경

- **기존 데이터 수집 그룹/우선순위는 그대로:** `korea-stock-screener-publication`에 남겨 둠.
- **전 종목 연구 점수표 전용 그룹 추가:** `korea-stock-screener-scorecard-publisher`. 매시 24·54분 KST 두 번, DART/KRX API는 호출하지 않음.
- 전용 작업의 쓰기 권한은 `docs/v3-all-listed-scores.csv`, `docs/v3-all-listed-scorecard-summary.json` 두 파일로만 제한. DART 실적·공시·업종/영업이익 표본·18:30 시장 명부 데이터는 **수집기 전용 발행** 유지.
- 원천 수집 중 main SHA가 바뀌면 최대 **3번만** `git pull --rebase` → 현재 저장원장으로 재계산 → 소스 일치 검증 → 강제 푸시 없는 정상 `git push`. 실패하면 종료코드 1로 명확히 실패 표시, 자료 덮어쓰기 없음.
- 별도 전용 그룹 실행이 수집 작업 자체를 더 빠르게 하거나 미검증 수급·산업 점수를 확정하지 않음.
- 현재 상장만 포함, 가격 보정 및 공식 IAS 20·산업 15·역사적 PIT 미인증 한계를 계속 표시한다.

## 검증/운영 확인

전체 CI와 `tests/test_v3_scorecard_workflow_isolation.py` 실행. 병합 후 점수표 Actions, 실제 18:30 시장 배치, DART 업종코드/현금흐름 수집이 각자 정상 종료하는지 추적한다. GitHub 스케줄 지연·동시 푸시 충돌 발생 시 **검증 후 실패**하며, 성공을 사칭하지 않는다.
