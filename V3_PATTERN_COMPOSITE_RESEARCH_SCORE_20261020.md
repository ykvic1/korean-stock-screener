# 2026-10-20: Pattern-first composite research priority, independent of v2/v3 trading decisions

## Dependency / deployment order

PR #122 (completed-week weekly pattern/RSi evidence) is still OPEN and its GitHub Actions CI is QUEUED at the time this branch is authored. This new branch is based on PR #122's head commit; **do not merge this before PR #122**. A pending CI is not a pass. The user requested **backend scoring only**, with **no UI changes** in this step.

## Why a separate 100-point research priority index

The old v2.2 ranking weights are already published and operational. The unreleased v3 formal six-pillar model is contractually fixed at IAS25/financial20/valuation15/technical15/industry15/momentum10 and lacks certified source evidence. Replacing either system's weights with a heuristic pattern score would be misleading.

Instead, `v3_pattern_composite_score.py` computes a completely separate **observed-technical research priority** out of 100 points only when ALL constituent data have valid source dates:

| Component | Max score |
|---|---:|
| Daily preferred chart pattern | 25 |
| **Completed weekly** preferred chart pattern | **25** |
| Daily RSI14 30 recross / confirmed bullish divergence | 10 |
| **Completed weekly** RSI14 30 recross / confirmed bullish divergence | **10** |
| Turnover + optionally OBV/volume (OBV at most 4 of 10) | 10 |
| Long weekly trend / verified weekly MA120 proximity | 10 |
| Structural price-stop distance (3% to 15% eligible) | 10 |
| **Total** | **100** |

Patterns are half of the total. Bull Flag and double bottom have explicit `forming` and `breakout_observed` tiers. Daily wedge/inverse-H&S/triangle/rise-compression heuristics and **weekly** wedge/inverse-H&S/triangle/rise-compression geometry are included. On the SAME timeframe, only the highest independent pattern is rewarded (prevents adding Bull Flag+triangle+compression for the same exact candles). Distinct **daily and weekly** evidence can each earn their respective maximum.

RSI crossing and divergence are separately checked; the latter only uses confirmable pivots. OBV is corroboration, never a gateway, never a known institutional buyer. A price that only has 160 daily sessions cannot be treated as having 90 completed weekly candles; weekly observation is **missing**, not zero, and no final total is issued. Incomplete components are `null` with `observed_weight` and coverage; no normalization to manufacture a perfect score.

## Safety and output

- High technical points **cannot** override an invalid/missing structural stop, stop distance outside 3–15%, thin liquidity, steep rally, long-trend weakness, abnormal trading surge or recent high-chase warning. These rows get `risk_excluded`, no rank.
- Research priority **is not** a probability, target price, entry decision, full six-pillar V3 rank, automatic trade order or independent KRX source certification.
- Each stock's **new** nested field `pattern_composite` includes fixed component scores / coverage / risks, complete or partial status, day/week top patterns, 100-point technical research priority and **deterministic rank** only if fully covered and passes the risk gate.
- Top-level `pattern_ranking` is a pre-sorted list of eligible `{code,rank,research_priority_score}`; `pattern_scoring_status` provides complete / excluded / missing counts and explicit disclosure.
- Existing `docs/data.json` stock ordering, UI, v2.2 score, v3 six-pillar score contract, recommendation engine, quant, OpenDART 320/day schedule and existing 18:30 screening schedule are **unchanged**.
- The next actual successful full-market scan after both PRs merge will be the first possible source of weekly observed composite rankings. **No current candidate counts / scores / picks are claimed in this PR**.
- After source-quality checks, a **separate future UI rollout** may display pattern-first ranks; this PR does not change HTML, JS or CSS.

## Validation and limitations

Offline synthetic tests cover exact 25+25 daily/week pattern weights, forming vs breakout, RSI recross/divergence, missing/stale/future/incomplete weekly evidence, double-count prevention, score/decision immutability, risk veto and deterministic whole-market sorted ranks. **Entire existing CI must be green** before promotion. Patterns remain unvalidated heuristic geometry; a historical holdout/false-positive test is needed before estimating pattern success probabilities.

Remaining work: cup-and-handle, box-bottom and other attached image patterns require individual causal detectors and separately validated shape/reference levels rather than keyword-only bonus; daily and weekly chart visuals will be released together later per user request.
