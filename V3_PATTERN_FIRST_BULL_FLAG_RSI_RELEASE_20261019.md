# V3 chart-pattern-first watchlist: Bull Flag + Double Bottom + RSI recovery

## User intent and distinction

The user favors chart patterns first: falling wedge, inverse head & shoulders, double bottom, bull flag, ascending/symmetric triangle and post-rise consolidation. RSI(14) rising through 30 and bullish divergence are independent recovery signals. OBV is **supporting evidence only**, never a required gateway or a representation of institutions buying shares.

## Verified code scope

- Existing v2.2 `daily_patterns()` wedge / IHS / triangle / consolidation observations and existing 8-factor weights remain **unchanged**.
- New `v3_pattern_first_observations.observe(df)` consumes the same actual historical OHLCV used for the daily market scan, with no extra API calls.
- **Bull Flag:** preceding rise >=16%, peak near end of pole, short 11-session pullback, 15–55% of pole retracement, 2.5–18% peak drawdown, lower flag trading volume than pole, flag not a steep uptrend; distinguish near-breakout consolidation vs a price-and-volume breakout versus previous flag high.
- **Double Bottom:** two confirmed local swing lows 9–50 candles apart (each requires three later candles), trough levels within 6.5%, intervening neckline at least 8% over lows, recent second trough and observed support; price-volume neckline breakout distinguished from forming.
- **RSI 30 reclaim:** Wilder RSI14 genuinely crosses from below 30 to >=30 in the last seven trading sessions; latest RSI still 30–60 (not a stale overbought rebound). This differs from merely RSI>30.
- **Bullish RSI divergence:** two confirmed closing-price pivot troughs where price makes lower low and RSI higher low, via the existing date-causal `technical_v22.divergence`; a confirmed occurrence within the last 12 observation candles is shown.
- The research object includes explicit daily timeframe, actual per-ticker OHLCV source date, heuristic/unverified status and hard `scoring_allowed=false`, `buy_signal=false`, `official_regular_session_verified=false`.
- The new fields are only published to `docs/data.json` on a **subsequent successful 18:30 market batch scan**. Previously published snapshots lack these new observations; user UI shows legacy chart-pattern candidates immediately but does not fabricate RSI/Bull Flag evidence until the new snapshot arrives.
- User beta page has separate first tab `패턴·RSI 우선 관찰`, then separate preferred pattern, RSI and optional OBV tabs, with existing recommendations/quant/all-rank modes intact.
- User candidate discovery no longer requires `obv_rising=true`; validated pattern and RSI can qualify independently while 3–15% structural stop distance, safe price movement, observed score/coverage and liquidity/risk warnings are mandatory.
- Existing v2.2 score components, investment verdicts, official PBR, investor flows, future predicted returns and quantified win probabilities are **not adjusted**.

## Limits and deployment boundary

Geometric rules are *heuristic candidates*, not certified pattern recognition or validated performance. Daily pattern detection is implemented first; independent weekly pattern detection and source-verified KRX regular-session volume remain future verification tasks. RSI bullish divergence is pivot-based and cannot be established on an unconfirmed same-day pivot. Pattern signal labels do not imply buy or operator accumulation.

Offline synthetic unit tests must prove: pole/flag/volume/breakout vs forming, two bottom confirmation and neckline, RSI below-30 reclaim vs no-cross, recent confirmed pivot divergence vs stale flag, invalid OHLCV fail-closed, user UI observation without OBV, invalid schema or buy certification rejected. CI must pass before merge. Existing v2.2 18:30 batch and OpenDART 8×40 daily schedule remain unchanged.
