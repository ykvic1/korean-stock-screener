# 2026-10-20 v3 six-pillar reweight: technical 25, financial 15 (research only)

## User-requested changes and necessary 100-point balance

The user specifically requested **technical analysis = 25** (patterns on BOTH daily and weekly candles) and **financials = 15**. If all other original weights were left unchanged, the sum would become 105. To retain the original scorecard denominator of 100 and avoid double-counting chart-momentum signals, this branch uses the following **explicit provisional balance**:

| Pillar | Before | After |
|---|---:|---:|
| IAS foreign/institution/pension and quality | 25 | 25 |
| Financial health and cash flows | 20 | **15** |
| Valuation | 15 | 15 |
| Technical daily+completed weekly | 15 | **25** |
| Industry and sector cycle | 15 | 15 |
| Stock momentum | 10 | **5** |
| **Total** | **100** | **100** |

**This momentum reduction (10→5) is an analyst design choice to make the user-named two changes arithmetically feasible**, not an explicit separate user confirmation. Revisit the distribution if the user prefers to deduct 5 from another category. Previous IAS 25 and its six internal subcomponents stay exactly unchanged.

## Subfactor distribution and patterns first

**Technical 25**: daily favorite chart patterns 7, **completed weekly** favorite chart patterns 8, daily RSI30 reclaim/bullish divergence 2, weekly RSI 3, weekly MA120 relative position 3, daily confirmed trading volume 2. Both independent timeframes contribute; overlapping patterns on the same candle never stack. Bull Flag, double bottom, falling wedge, inverse head and shoulders, triangles and post-rise consolidation are research heuristics (not certified trades). OBV gets no duplicate technical bonus; IAS contains OBV/volume subfactors.

**Financial 15** (future verified candidates): 3-year revenue growth 4, operating margin 4, FCF/assets 4, net debt/equity 3; prior 6/6/5/3 redistributed without dropping leverage risk.

**Momentum 5** (separate from pattern/trend): 20-session RS 2, 60-session RS 2, positive close days in last 20 sessions 1. The factor engine's full-six-pillar peer percentiles must retain these exact separate maxima.

## Guardrails, deployment and remaining work

- Only technical evidence (day / finished Friday-ended week and current snapshot-date) can be scored from existing OHLCV. Unobserved financial, IAS, valuation, industry and momentum **remain null**, never 0 or estimated.
- A source-unconfirmed 25-point technical strength does NOT replace a complete six-pillar score, historical probability or trading decision. Original v2.2 score, existing investment recommendation, KRX/OpenDART jobs and data publication order remain unchanged.
- Structural stop width, illiquid trading, late-stage rallies and missing price/session provenance continue to block provisional ranking regardless of pattern shape.
- v3_score_contract.py is the canonical six-pillar score budget; v3_research_factor_engine.py factor maxima, v3_pattern_composite_score.py geometric daily/weekly pattern evidence and published screener metadata all derive from this updated budget.
- No HTML / JS / CSS changes. User explicitly deferred the unified UI upgrade.
- PR must pass full GitHub Actions CI on latest head; merge only if no conflicting current live data edits. Pages deployment and next genuine close-price scan must be verified separately.
