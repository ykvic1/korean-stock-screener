# v3 API 승인 대기 중: 손절·목표가 선도달 / 기간별 워크포워드 연구 (오프라인)

**2026-10-18 개발 범위:** 공식 KRX 가격/거래량, 전 종목 기관 수급 및 OpenDART 전종목 PIT 데이터를 아직 인증하지 못한 단계에서 연구 엔진의 **검증 규칙만 먼저 구현**한다. 현재 v2.2 일별 운영·보고서·추천 데이터·GitHub Pages·Excel을 건드리지 않는다.

## 실제 제공되는 기능

- 1/2/3/6/12개월을 임시로 **21/42/63/126/252 거래세션**으로 정의한다. 이는 달력상 정확한 1/2/3/6/12개월과 같지 않다.
- 신호 생성일 **다음 유효 세션 시가**로만 진입. 진입일 시가가 미리 지정한 손절선/목표선 밖이면 진입 오류로 제외한다.
- 손절선/목표선은 신호일에 미리 정해진 설정이라는 **입력자 주장**과 신호 스냅샷 SHA256 형식을 요구한다. SHA256이 있다고 신호 당시 실제 고정돼 있었다는 사실까지 증명되지는 않는다.
- 손절/목표가 중 먼저 도달한 사건을 기록. 같은 날 OHLC에 **둘 다 포함되면 손절 먼저 도달**한 것으로 보수적으로 가정한다. 시초가 갭 하락에서 손절가보다 불리한 시가 체결을 반영하고, 목표가 위 갭 상승 시 목표가 체결로 제한한다.
- 1회 왕복 비용은 **기본 30bp(0.30%) 가정**이고 실제 거래 비용의 확정값이 아니다. 설정 가능하며 단위/범위를 검증한다.
- 향후 전체 기간의 봉이 부족한 신호는 **우측 검열(right-censored)** 로 처리해 표본에서 제외하고 숫자를 채우지 않는다.
- 분할 기준일 이전 신호도 **평가기간 전체가 기준일 전에 종료된 경우에만** 학습 참고군에 포함한다. 기준일 이후 신호는 달력일 기반 embargo 기간 경과 후 홀드아웃에 포함, 중간 사례는 제거한다.
- 표본 **40회 이상, 10종목 이상, 서로 다른 12개 분기 이상**일 때만 회고적 평균 순수익률/손절 선도달률/목표 선도달률/최대 역행 폭 P75/상승 기회 폭을 표시한다. 기준 미달 시 수치 null.
- **결과는 과거 관측일 뿐이다.** 데이터 공급원·상장폐지/생존편향·동일종목 중복신호·세션과 수정주가·법인행위·PIT 기업목록·독립 OOS 검증은 완료되지 않았다. `forward_expected_return_pct`, `forecast_success_probability`, `buy_signals` 및 v3 투자 점수 승인 모두 null/false.

## 입력 계약 / 예시

연구 사례 하나의 기본 구성 (아래는 **임의의 합성 숫자**, 실제 종목이 아님):

```python
case = {
    "code": "000001", "signal_date": "2023-01-03",
    "stop_price": 90.0, "target_price": 110.0,
    "evidence": {
        "pit_universe_asserted": True,
        "regular_session_asserted": True,
        "corporate_actions_audited_asserted": True,
        "trading_calendar_complete_asserted": True,
        "signal_setup_frozen_asserted": True,
        "signal_snapshot_sha256": "a" * 64,
    },
    "bars": [
        {"date": "2023-01-04", "session": "KRX_REGULAR_0900_1530_ASIA_SEOUL",
         "open": 100, "high": 102, "low": 98, "close": 100},
        # ... 동일 형식의 실제 거래세션 순서 봉
    ],
}
from v3_walkforward_research import evaluate
report = evaluate([case], split_date="2024-01-01",
                  embargo_days=252, round_trip_cost_bps=30)
assert report["authorized_for_v3_scoring"] is False
```

`evidence` 항목은 **독립 공급원 검증이 아니라 입력자가 명시한 주장**이므로 임의로 True를 넣어도 운영 승인이 되지 않는다. 진짜 공식 원시 데이터에 연결하려면 외부 수집기, 자료의 취득 시각, 가격 및 거래량 정의, 공식 거래 캘린더, 수정주가/PIT기업목록, 상장폐지 포함 역사 유니버스의 재현성이 필요하다.

## 개발·실행·안전 경계

```bash
python -m unittest discover -s tests -p 'test_v3_walkforward_research.py' -v
python -m py_compile v3_walkforward_research.py
```

- 네트워크, 공식 키/토큰, 비공개 보관소, 비동기 수집, 주문 기능 **사용하지 않음**.
- CI에는 합성 데이터만 사용. 현재 게시된 `docs/data.json`을 억지로 과거 OHLCV 데이터로 변환하지 않음.
- 분석 결과를 공개 `docs/`, Excel이나 운영 `recommendations.json`에 연결하지 않음.
- 다음 단계: 검증된 거래일·종목 생존 및 상장폐지 이력·재무 공시 PIT 자료를 확보하고 **별도 adapter + 독립 검증 뒤에만** 전체시장 장기 백테스트 진행.
- 신호 구성 규칙/손절선이 미래 봉을 보지 않았는지 재현 가능한 시점고정(frozen setup) 증거, 독립 기간 최적화/홀드아웃 유효성 검증 없이는 기대수익·예상 확률로 전환 금지.
