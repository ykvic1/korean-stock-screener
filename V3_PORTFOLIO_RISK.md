# v3 오프라인 위험관리 — 손절선·갭 하락·최대 포트폴리오 손실

**2026-10-18 개발 내용:** 공식 KRX 가격·수급과 OpenDART 기업 PIT 자료를 확보하기 전에 **수학적으로만 검증할 수 있는 위험관리 부분**을 별도 모듈로 구현한다. 실행 경로는 `v3_portfolio_risk.py`, 테스트는 `tests/test_v3_portfolio_risk.py`다.

## 실제 계산

1. 종목별 **진입 예정 가격과 구조적 무효화(손절) 가격의 거리**를 계산한다. 손절거리 3% 미만, 3~8%, 8~15%, 15% 초과는 별도 경고/분류일 뿐 **실제 손절 가능성의 통계가 아니다.**
2. 사용자가 넣은 **추가 갭 하락 스트레스**(예: 손절선 아래 5%)를 적용해 보수적으로 가정한 체결가격을 계산한다. 실제 낙폭이나 체결을 예측하지 않는다.
3. 계획한 종목별 투자비중으로 **손절선 기준 가정 손실액(총 자산 대비 %)** 및 **추가 갭 스트레스 시 가정 손실액(총 자산 대비 %)**를 계산한다.
4. 사용자가 명시한 자산 대비 총 위험예산(%)과 모든 종목의 갭 스트레스 손실 합계를 비교한다. **세 종목 이하 집중 보유 선호**를 별도 표시하고, 총 투자비중 100% 초과/동일 종목 중복을 차단한다.
5. 위험예산·현금잔액에서 나오는 **이론적 비중 상한**은 내림해 산출한다. 실제 적정 매수 비중이나 자동 주문으로 사용하지 않는다.
6. 목표가가 없으면 잠재 상승폭과 단순 목표가/손절거리 비는 `null`; 목표가가 있어도 **기대수익, 성공확률, 검증된 손익비라고 부르지 않는다.** 가격·수급·재무 등의 인증 전에는 `trade_approval=false`, `v3_scoring_authorized=false`가 고정이다.

## 예시 — 임의 가정, 종목추천 아님

```python
from v3_portfolio_risk import evaluate_portfolio
hypothetical_plan = [{
    "code": "000001", "entry_price": 100, "stop_price": 92,
    "reference_target_price": 120,
    "planned_position_pct": 20,
    "stress_gap_below_stop_pct": 5,
}]
r = evaluate_portfolio(hypothetical_plan, total_nav_risk_budget_pct=1)
assert r["total_stop_loss_of_nav_pct"] == 1.6
assert r["total_hypothetical_gap_loss_of_nav_pct"] == 2.52
assert r["stress_budget_met"] is False
assert r["trade_approval"] is False
```

위에서는 총 투자금 20%, 손절선까지 주가 -8%이므로 전체 자산의 -1.6% 손실 가정. 손절선 밑으로 추가 5% 낮게 체결되면 주가 -12.6%, 전체 자산 -2.52% 손실 가정이다. **손실 예산 1%**를 초과한다. 입력 예산 1%와 갭 가정 5%는 설명을 위한 가상값이며 사용자에게 실제로 권하는 허용손실폭이나 변동성 예측값이 아니다.

## 안전상의 한계

- 기업의 유상증자·상장폐지, 거래정지, 급락 시 미체결, 매도 호가 충격, 공매도·레버리지, 통화/세금/수수료를 포함한 **실제 최악 손실은 계산할 수 없다**. 가정한 갭보다 훨씬 큰 손실도 가능.
- 세 종목 손실을 합산한 것은 단순 동시 악화 스트레스이며 **관측된 상관계수나 VaR, 계좌의 파산확률이 아니다**.
- 입력된 현재가·목표가·손절가가 검증된 실시간 사실이라고 인정하지 않는다. 예상 수익률·확률·목표가를 AI가 만들지 않는다.
- 기존 v2.2 운영, 공개 `docs/`, Excel, GitHub Pages의 종목 출력, 예약 스캔, API 수집 워크플로는 **변경하지 않는다**.

실행:

```bash
python -m unittest discover -s tests -p 'test_v3_portfolio_risk.py' -v
python -m py_compile v3_portfolio_risk.py
```

후속 연결은 실제 공식 가격과 기업 재무 PIT·수급 검증, 이벤트 리스크·스프레드/슬리피지 연구가 끝난 후 별도의 출시 검증을 통과한 경우에만 고려한다.
