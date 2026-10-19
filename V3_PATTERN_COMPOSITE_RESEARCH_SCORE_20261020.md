# 2026-10-20 correction: Chart patterns are ONE part of original v3 six-pillar 100

## User clarification and correct unmodified scoring framework

The previous proposal of a **separate pattern-only 100** was wrong: it cannot replace the user's already agreed total model. This PR revises the backend so that patterns and RSI fit **inside technical 15** of the ORIGINAL six pillars:

| Original v3 pillar | Exact points |
|---|---:|
| Institutional/foreign accumulation strength (IAS) | 25 |
| Financials | 20 |
| Valuation | 15 |
| **Technical analysis, including DAILY + WEEKLY patterns** | **15** |
| Industry and sector cycle | 15 |
| Momentum / relative strength | 10 |
| **Total** | **100** |

No IAS, financial, valuation, sector or momentum weights are changed. The existing 6-pillar validation contract `v3_score_contract.py` remains unchanged, as does legacy v2.2 scoring.

### Research allocation INSIDE technical 15 (not an extra score)

- Daily favorite chart patterns 3 + **completed weekly** favorite chart patterns 4 = **7**.
- Daily RSI14 30 upward recross / pivot-confirmed bullish divergence 1 + completed weekly RSI 2 = **3**.
- Completed weekly MA120 relative position = **3**.
- Daily confirmed volume breakout = **2**; OBV **not added here**, because IAS already has a volume/OBV component. No double-count.
- Total technical: **15**. The existing `v3_research_factor_engine.py` technical feature-level theoretical weights are adjusted from 4/3/4/4 to **MA120 3 / RSI 3 / preferred patterns 7 / volume 2**, summing to the original 15.

### Evidence

- Bull Flag, double bottom and (geometric heuristic) falling wedge, inverse head and shoulders, triangle and post-rise consolidation; no multiple overlapping pattern rewards inside one timeframe.
- Each independent timeframe contributes its own maximum. A **partially completed current weekly bar is EXCLUDED**, preventing false Bull Flags or RSI due to premature low volume.
- Only actual source-date-matched daily and completed Friday-ending weekly OHLCV research can contribute technical points.
- Missing 120-week history, malformed source date, unconfirmed chart structure, or insufficient recent data => technical candidate **null/partial**, NEVER disguised as 0 or renormalized into total 100.

### Overall score and ordering

- `v3_pattern_composite_score.evaluate` calculates technical research evidence and passes it through the exact existing `v3_score_contract.research_scorecard` as **only technical candidate points**.
- IAS25/financial20/valuation15/industry15/momentum10 remain **null** until independently observed source-verified candidate components are legitimately produced by existing specialized providers/adapters. No use of old v2.2 score, guessed PBR or OBV as a substitute.
- `six_pillar_full_candidate_only` exists **only when all six pillar candidates are present**; only then can provisional same-asof research ordering occur, and only after structural stop, recent price-chase, liquidity risk vetoes. This is not a validated expected return, trained win probability or a buy/sell instruction.
- Current stock output adds `v3_pattern_integrated`, its 15-point evidence breakdown, missing original pillars and completion metrics. Top-level `six_pillar_research_sort` remains genuinely EMPTY while the other five pillars are missing.
- Original stock order, v2.2 trading grade, current recommendation list, historical quant, market routines and existing user-facing HTML/JS/CSS **remain unchanged**. The user explicitly requested the UI be built together later.

## GitHub release guard

PR #122 was closed unmerged because it contained premature UI changes. This backend-only PR supersedes it with the necessary weekly OHLCV aggregation. Only merge PR #123 when ALL latest CI succeeds and conflicts with new market-data commits on main are resolved safely. No successful production data or scores are claimed until the following actual market run.

## Future correctness work

Calibrate patterns including cup & handle, distinguish weekly Bull Flag consolidation windows (current baseline is a multi-week heuristic), validate OHLCV corporate actions / regular-session volume, check historical false positives with survival-biased archives repaired, integrate verified IAS/financial/value/sector/momentum before actual investment ranking. Screenshot-based visual validation belongs in the deferred UI phase.
