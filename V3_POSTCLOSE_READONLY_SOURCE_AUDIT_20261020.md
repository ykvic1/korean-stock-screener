# Post-close, read-only six-pillar readiness check — 2026-10-20

PR #128 has attached daily and completed-week pattern research to the **actual** 18:30 full-market merge path. PR #127 already reports per-day and per-week valid observation counts and full technical25 scores; the previous market snapshot (2026-10-19) cannot confirm the fix until a new real scan.

This PR makes the **existing** `V3 Six-Pillar Source Readiness Audit (offline)` GitHub Actions workflow run automatically after the **scheduled** `Full-Market 100-Stock Batches` workflow successfully completes. GitHub `workflow_run` trigger observes completion, not a speculative wall clock and not the code pushed by the bot. Failed scans or manual audit-mode runs are *not* claimed as new successful trading data.

- Read-only `contents: read` permission, explicit `main` checkout, no credentials persisted.
- No exchange/DART calls, no OHLCV download, no additional API use, no expensive analysis of full-market prices.
- Reads only the current published JSON snapshots and writes a 14-day diagnostic GitHub Actions artifact, not new `docs/` files.
- One 18:30 scheduled batch unchanged; eight separate bounded DART runs unchanged. No UI/Excel or investment rating change.
- The audit checks actual `market_asof`, day/week source coverage, RSI/pattern/120w observation availability, and six-pillar blockers. A successful job means **the source audit ran**, not that official investment scoring is ready.

The next valid trading-close output must be checked against actual newly published full-market `docs/data.json` source dates and the artifact at Actions > V3 Six-Pillar Source Readiness Audit (offline). Zero scores after a real full-market successful run should be investigated as a legitimate code/data problem, not interpreted as zero bullish stock opportunities.
