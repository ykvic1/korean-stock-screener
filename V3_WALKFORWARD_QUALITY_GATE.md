# v3 오프라인 과거 수익률 검증 품질 게이트

## 목적

과거 수익률을 테스트할 때 **액면분할·합병·거래정지·미확인 가격 급변·연속 관측 중단**이 섞이면 손절/목표가 선도달, MAE/MFE, OBV/기술 지표가 왜곡될 수 있다. 또 과거 신호 시점 이후에 수신한 가격·수급·재무·업종 자료를 당시 알 수 있었던 근거로 취급하면 룩어헤드 편향이 발생한다.

`v3_walkforward_quality_gate.py`는 기존 세 가지 분리된 오프라인 모듈을 **독립적으로 재호출하여** 연구 입력을 검사한다.

- `v3_historical_integrity.audit_price_events`: 기업행위·가격 급변·정지·관측 공백 검역
- `v3_pit_join.join_asof_evidence`: 신호 당시 종목·공시·수급·업종 시점 검증
- `v3_walkforward_research.evaluate`: 동일 종목 중복 기간/시점분리/손절·목표가 기간별 후행 수익 검증

## 품질 판정 규칙

1. 각 연구 사례는 **발행회사 ID 8자리와 종목코드 6자리**, 시장 및 신호일이 서로 같아야 한다.
2. 신호일은 동일 발행회사의 원본 가격 시계열에 실제로 존재해야 한다. 신호일 다음 관측 세션부터의 OHLC가 기존 원본과 **날짜·가격·순서상 완전히 같아야** 하며, 임의 수정주가 치환/행 삭제/가격 단위 변경을 탐지하면 연구 사례 전체를 중단한다.
3. PIT 결합 결과에 사후 수집된 자료 또는 시점 정보 부족이 있으면 **그 신호는 모든 투자기간에서 제외**한다. 메타데이터가 일치한다는 사실이 원본 데이터의 독립적인 진위 입증은 아니다.
4. 신호일이 기업행위·가격 품질 의심 경계 ±2관측 봉 이내라면 모든 기간을 연구 제외한다.
5. **신호일 이후** 21/42/63/126/252 관측기간 도중 품질 경계를 만나면 그 기간의 후행 손익값을 **`post_signal_label_quality_censored`**로 분류하고 제외한다. 미래 기업행위를 신호 당일 예측했다고 가정하지 않는다. 따라서 사건 발생 이후에 검사했다고 해서 전략의 미래 성공 가능성이 증명되는 것은 아니다.
6. 관측기간이 부족하면 기존 엔진의 우측 검열로 집계하며 관측되지 않은 구간 수익률을 만들어내지 않는다. 기간별 제외 사유와 제외 건수를 구분한다.
7. 연구용 결과가 수치상 좋아 보여도 표본의 생존편향 및 **미래 사건에 따른 선택적 검열(informative censoring)**을 해결하지 않았으므로 성과 통계나 향후 예상수익률로 그대로 사용할 수 없다.

## 예측·출시 차단

- `pit_provider_archive_authenticated=false`
- `corporate_actions_officially_certified=false`
- `survivorship_and_censoring_bias_resolved=false`
- `model_parameters_frozen_out_of_sample=false`
- `confirmed_forecast=null`
- `confirmed_v3_score=null`
- `v3_release_ready=false`, `confirmed_buy=false`

**중요:** 이 코드는 검증 대상 데이터를 제공하지 않으며 현재 모든 테스트가 인공 입력으로만 실행된다. 공식 KRX 일봉·정규장 거래량, 실제 액면분할·상폐 기업 목록, 수급/재무 PIT 과거 원본을 외부에서 독립 인증하기 전까지 1/2/3/6/12개월 기대수익률을 만들거나 투자 추천에 연결하지 않는다.

기존 `docs/`, 스크리너, 자동 수집·배포, Excel, 종목 추천 로직과 데이터는 변경하지 않는다.

```bash
python -m unittest discover -s tests -p 'test_v3_walkforward_quality_gate.py' -v
python -m py_compile v3_walkforward_quality_gate.py
```
