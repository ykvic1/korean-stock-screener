# V3 score rank matches visible score — 2026-10-20

## Source of correction
The approved design-only image has Nongshim 51.4 listed #1, Namhae Chemical 48.6 listed #2 and Sebang Global Battery 65.7 listed #3. This is internally inconsistent. Correct DESCENDING order of these **illustrative, not actual investment scores** is Sebang 65.7 #1, Nongshim 51.4 #2, Namhae 48.6 #3. Never insert illustrative values in a real stock data file.

## Production UI correction
- Prior main UI defaulted to pattern/risk heuristic order while displaying a different (legacy v2.2) score. This was misleading to users expecting a score-based rank.
- Default is now DESCENDING score that is actually shown as existing v2.2 **observed score**; its data coverage is also clearly shown. Pattern/risk priority and stop-distance sorts remain optional and explicitly labelled.
- One market-global rank derived from the *displayed one-decimal score* is stable across KOSPI/RSI/pattern and search filters. Tied displayed scores share rank; coverage and stock code deterministically order the tied cards. One tap TOP100 uses global score ranking, not input row order or a local filter rank.
- Six-pillar reference strip: IAS20, technical25 (independent daily+weekly pattern evidence), financial15, valuation15, sector15, momentum10, V3 verified total. Missing source pillars show —, total shows 미산출. The legacy observed score is clearly separate from the unreleased V3 official aggregate.
- High score does not override structural risk; risk badges are shown rather than silently suppressing higher scoring names. A separate optional safe-only filter can remove unsafe names.
- Main unified screen uses the user's approved dark and bright green concept styling, but legacy.html retains previous UI for fast rollback.

## No fabricated scores and unchanged operations
- This change does NOT create official V3 6-pillar values or investment recommendations. It never assumes a complete IAS/financial/valuation/industry/momentum pillar from one technical signal.
- No data-source API calls, OHLCV calculations, backend investment weights, 18:30 scheduled market task, DART probes, Excel data, existing V2.2 recommendations, or trading credentials change.
- Comprehensive Node real-market tests and Chromium 390px mobile tests verify scores sorted from largest to smallest, illustrative 65.7/51.4/48.6 order, tied rank, TOP100 toggle, warning and missing-source labels and no horizontal overflow. Never merge until both CI suites pass.