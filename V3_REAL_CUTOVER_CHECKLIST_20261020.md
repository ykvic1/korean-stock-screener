# V3 launch-critical blockers and evidence checklist — 2026-10-20 KST

## Binding weights (user approved; code in main since PR #125)

| IAS | Technical (daily + completed weekly) | Financial | Valuation | Industry/sector | Momentum | Total |
|---:|---:|---:|---:|---:|---:|---:|
| 20 | 25 | 15 | 15 | 15 | 10 | 100 |

Within technical25: daily favorite patterns7, completed weekly patterns8, day RSI2, week RSI3, 120-week MA3, volume2. Pattern evidence remains price-derived heuristic. Other five pillars are not auto-filled from the observed day/week patterns.

## Most recent ACTUAL offline source audit

GitHub Actions run [#39342242896](https://github.com/ykvic1/korean-stock-screener/actions/runs/39342242896) succeeded against source date **2026-10-19**:

- Current listed regular shares: **2,497**; existing v2.2 price-screen stock rows: **2,368**, so **129** are not full qualified price-screen observations (the separate catalog classifies 106 price errors, 23 insufficient history).
- DART 2025 reference: **162** attempted current-listed issuers, **157** one-year annual issuer observations, **155** simple annual P/B references. These do not establish four annual statements, PIT scope, or exchange PBR. **2,335** issuers are not yet attempted in that reference.
- Existing v2.2 institutional/foreign verified-flow observations **0**; this is absence of validated observations, *not* proof of zero net buying.
- Existing v2 stock-relative 20/60 data pair **2,368**, NOT authenticated six-pillar momentum scores.
- Valid technical25 candidates in that published v2.2 snapshot **0**, since the snapshot precedes PR #125 and must be refreshed by the next successful 18:30 close scan; this is not proof every chart pattern is absent.
- Eligible complete 6-domain investment ranks **0**; **all six release-certification flags are false**.
- PR #126 after full CI was merged at `490c6b9c974df94de8959a308a5b3c53433c5f80`. The audit job and Pages deployment both succeeded. The audit artifact is private/time-limited, not a stock dashboard.

## Strict critical path — from prototype to official V3

1. **Completed day + week technical data reproduction on real market close**. Verify the day/week source dates, 90+ finished weeks for geometric pattern eligibility, 120 fully closed weeks for 120-week moving average. As-of should be the actual 2026-10-20 successful run, not the 2026-10-19 legacy dataset. Separate unavailable/insufficient from genuine no-pattern scores. Check 23 short-history issuers are not falsely penalized as zero technical ability; do not invent missing coverage. This is immediate release-qualification work; not sufficient for V3 on its own.
2. **Authoritative KRX regular-session and persistent 5–10 year OHLCV archive**, plus split/merger/halts and the frozen 53-symbol full-history problems. A short real API pilot was successful but its raw 5-day evidence is not a long-term permanent authenticated archive. Confirm official terms and prevent unauthorized public redistribution. Earlier GitHub Actions runners discarded raw originals; use a lawful private persistent store before long historical backtests.
3. **Verified 20/60/120/250-session issuer-level institutional/foreign/pension flows**, no double counting pension subset vs institution totals, dated float denominators, nonfabricated missing observations. IAS20 must be computed from actual observed data, never solely from OBV.
4. **4–5 consecutive financial years of actual OpenDART receipts and same accounting scope CFS/OFS**, correction history, original receipt/date PIT, operating cash flow and capex, liability/cash/debt. The currently rolling one-year DART reference, even when reaching all 2,497 stocks, will not by itself satisfy 3-year CAGR/FCF and valuation15.
5. **Valuation at same dated share count/market capitalization** with PBR, normalized earnings/FCF yields, negative equity or loss company rules, independently verified provenance. A simple PB reference != exchange-verified PBR.
6. **As-of industry cohort/sector-strength and market leadership evidence**, 60-session sector relative strength, breadth and industry profit cycle (survivorship-safe sector membership), plus real independently verified 20/60d relative stock momentum10. Do not use present-day memberships to backfill a historical rank.
7. **Six-pillar calibration / risk gate** across all six domains: preserve fixed total100; no missing-to-zero, no dual credit to OBV or RSI across IAS/technical/momentum; risk stop, gap, liquidity, stress days, drawdown, and honest relative research rank. Release confirmed 100-point score only when independent source evidence, as-of consistency and validation approve the data. Historical 1/2/3/6/12-month backtests need 5–10-year PIT sources incl. delisted constituents, held-out statistical validation and signal-specific corporate-event censoring.
8. **One coordinated UI and Excel release after backend validation**, with exact source dates, multi-pillar components, pattern daily/week chart references, support/invalidation, six-scenario risk and missing-source labels. Mobile QA, zero broken links, safe rollback and explicit v2.2–V3 cutover approval. Keep the 18:30 live v2.2 publication and eight bounded weekday OpenDART attempts unchanged until ready.

## Work separation

**Usable now as observational beta**: v2.2 priced 2,368 stocks, basic pattern-watchlists and price / mock-stop research, reference annual accounts, historic exploratory quant; all explicitly heuristic and uncertified, not model-backed buys.

**V3 official NOT ready**: current full six-pillar score cannot be certified; the precise completion date is not predictable until external regular-session, investor-flow and PIT financial rights/evidence are solved. Other modules are mostly built as standalone research contracts and synthetic tests; production source linkage is the larger problem.

This document supersedes the old 2026-10-18 weights shown in the archival V3_SCORE_MODEL_SPEC.md and V3_RELEASE_PLAN.md. Never regress to IAS25/financial20/technical15/momentum10.
