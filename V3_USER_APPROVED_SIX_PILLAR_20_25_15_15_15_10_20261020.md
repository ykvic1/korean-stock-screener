# 2026-10-20 — User-approved V3 six-pillar weights (total 100)

## Canonical final budget

| Domain | Final points |
| --- | ---: |
| IAS — verified foreign / institutional / pension flows, accumulated buying | 20 |
| Technical analysis — DAILY & COMPLETED WEEKLY preferred chart patterns and RSI | 25 |
| Financials — sales growth, profitability, cash flow, debt | 15 |
| Valuation — earnings & cash-flow yields, P/B evidence | 15 |
| Industry / sector-relative strength & profit cycle | 15 |
| Momentum — independent stock relative performance and up-day persistence | 10 |
| **Total** | **100** |

This allocation is **explicitly specified by the user**. It supersedes provisional momentum5 from PR #124, while preserving the same daily/weekly pattern-first technical25 design.

### Nested allocations, unchanged except where necessary

- IAS domain still contains the original six independently observed inner metrics totaling **100 IAS units**: 20 ownership/long-holding, 25 accumulated flow, 15 buying persistence, 10 foreign+pension quality, 15 OBV/volume, 15 price-not-reflected. Their result is normalized to **20 of the total 100**, not 25. Underlying 20/60/120/250-day periods retain 15/25/30/30 internal accumulated-buy weights.
- Technical25 stays **daily preferred pattern7 / finished weekly preferred pattern8 / daily RSI2 / weekly RSI3 / MA120 weekly3 / price-volume confirmation2**. RSI30 recovery and confirmed price-pivot bullish divergence are independent supports. Bull Flag, double bottom, falling wedge, inverse head & shoulders, triangles and pullback/consolidation are observed heuristics, not trade certifications.
- Financial15 stays revenue CAGR4 / operating margin4 / FCF assets4 / net debt ratio3.
- Valuation15 and sector15 unchanged.
- Momentum returns to **10**: 20d relative strength4 / 60d relative strength4 / 20d positive days2. It remains a distinct risk/leadership breadth factor; avoid confusing it with merely confirming an individual chart pattern.

### Strict data/portfolio safety contract

- Changing IAS20 from IAS25 does **not** change source assertions or cure unavailable foreign/institution/pension flows. Observed buyer/industry information must not be invented.
- Existing legacy v2.2 active buy/hold interpretation, OHLCV data, 18:30 schedule, 8 × 40 daily OpenDART bounded runs, user UI and quant results are **unchanged**.
- Until current PIT source material and independent pricing/flow checks support all six domains, an observation of technical25 is NOT sufficient to mint a full investment score or confirmed rank; unavailable metrics remain null and full-score publication is blocked.
- Tests validate exactly 100, IAS100 inner metrics scaled to20, financial15, technical25 daily+weekly, momentum10, rejected out-of-domain IAS21, missing parts, risk veto and no v2 score mutation.
- PR must pass complete CI and merge without overwriting independently updated daily source data. UI is deferred by user request.

## Next backend development step

Build a **source-readiness audit of the six pillars** from the latest published observed input fields, keyed to 2026-10-19/20 market date, to identify which blockers prevent genuine comprehensive rank eligibility. This is a quality dashboard *data artifact*, not an arbitrary score or UI. Make actual observed counts, source day, missing reasons and first-trade/calendar coverage explicit, retaining the existing scans and published investment decisions.
