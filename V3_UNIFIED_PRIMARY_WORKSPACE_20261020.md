# V3 unified research workspace — user-first consolidation (2026-10-20)

## Product correction

The user is **not actively using v2.2**. Previous assumptions that the existing v2.2 site is a satisfactory daily product were wrong. The accumulated beta/old/manual/quant UI pages are cumbersome and inconsistent, although they share the same GitHub repository and backend. This task therefore intentionally delivers **one primary user-facing workspace**, not another standalone analysis widget.

### Actual default experience

- Public root `docs/index.html` points immediately to `docs/workspace.html` as the **single normal entry point**. Original index UI is preserved intact as `docs/legacy.html` for reversible access. `v3-beta.html` remains as an older technical research display but is no longer required in the default user workflow.
- Compact mobile-first list: one search, five meaningful research scopes (observed preferred pattern/RSI candidates, chart patterns, RSI recovery, quant availability, complete market), market and sort filters, optional strict downside cutoff. No more hopping through four different pages.
- Click one stock to see **same-screen** day and weekly patterns, RSI30 recross and confirmed divergence, historical legacy score (separately identified), six-pillar source status, technical25 research input (if current), structural stop reference, annual DART reference and same-day quant context.
- Stats banner shows source date, successfully screened market count, watchlist count, *observed technical research count* and official V3 total availability. Six pillars fixed at IAS20/technical25/financial15/value15/industry15/momentum10.
- **Risk-first discovery**: no unsafe structural stop or known chase flag is silently promoted because of a high old score. Unsafe items may still be inspected in `전 종목` explicitly or via search and are clearly marked.
- Chart patterns are heuristics, NOT evidence of institutional accumulation or future returns. Day and completed-week observations have separate date guards. RSI and OBV supporting evidence do not override the stop/risk gate.
- The original v2 observed normalized score is displayed as **기존 관측점수** with data coverage; full **V3 6-domain investment score stays `미완성`** until all authoritative sources and released scoring gates permit it. Do not silently map technical25 into full100. No sell/buy signal, API key, account authorization or auto-order.

### Reuse rather than rewrite

`docs/v3-ranking.js` exports the same `V3RankedCore.prepare` and existing risk, quant, financial provenance rules; it now mounts the older beta UI only when its old DOM exists. New `docs/workspace.js` computes no new raw finance/flow/predictive data. It validates observed same-date day and completed-week structural research as published and combines them with the existing model solely for display. Links to external public ticker charts keep 6-digit code validation. CSS retains one-column mobile stock detail and clean plain text for missing evidence.

### Validation and rollback

Tests include the real currently published `docs/data.json` with `prepare`; malicious/stale/future/incomplete weekly pattern input, unearned V3 investable score, high-risk legacy ranking, mobile CSS, HTML source consistency, initial path routing, no credentials, no fake total and correct weight100. Existing legacy, beta, quant, market quant/flow and backend tests remain active and unchanged.

CI must pass before changing live Pages. This is a **UI/product integration change with zero API calls, schedule changes, finance/price data mutations or automated trading**. If a client rendering regression is found, user can open `/legacy.html` immediately; rollback is a one-line removal of root meta redirect. Actual Chrome/Android visual inspection after Pages deploy remains the final usability QA step.

### Next product milestones (post-consolidation)

1. Populate true IAS20 and point-in-time financial/valuation15, sector15 and momentum10 from verified live sources. Eliminate incomplete date labels and cross-source provenance gaps without synthetic credentials or false scores.
2. Show actual verified V3 full100 only after release approval; add paired day/week indicator chart with chart-reference pivot levels, invalidation prices and scenario analysis.
3. Perform user acceptance in the unified primary workspace: one-handed mobile scroll, few taps, sort/filter/readability, single-screen study summaries, test source update day, then remove the old beta/manual entry pages when approved.
