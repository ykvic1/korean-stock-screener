# V3 research source readiness — offline execution, no UI

User approved IAS20 / Technical25 / Financial15 / Valuation15 / Industry15 / Momentum10 (total100) in PR #125. This PR is the immediate next step: **observability of coverage and specific rank blockers from ACTUALLY PUBLISHED data**.

Sources: `docs/data.json` (last successful legacy v2.2 full-market price/risk scan), `docs/v22-market-catalog.json` (current-listed issuer census), `docs/v3-dart-book-reference.json` (rolling annual DART observations). The audit validates source dates, membership, unique codes, source counts and explicit provider uncertified flags. It does not call providers, mutate market data, run stock screens, adjust weights, publish buy decisions or issue a future probability.

The audit deliberately distinguishes:
- **Legacy prices and flow confirmation** (v2.2 observation) from verified 20/60/120/250 session IAS.
- **Completed technical-research evidence** from independently certified price feed and validated 25/100 investment score.
- **Annual observed DART statements** from 4 years of same-scope financial history, separately verified receipt revisions and historical PIT required for financial15.
- **Simple computed P/B references** from official exchange PBR plus earnings/FCF yields required by valuation15.
- Stock-relative 20/60d price indicators from independent market-calibrated momentum10.
- Current sector labels from a complete as-of historical sector-constituent cohort required for industry15.

Output is source-labeled, date-stamped diagnostic JSON with **observed counts and blockers**, NOT an investment ranking. It does not claim that six-pillar released scores are available. It is stored only as a 14-day GitHub Actions diagnostic artifact from a read-only workflow `V3 Six-Pillar Source Readiness Audit (offline)`, with **no repository write** and **no UI changes**. It runs once automatically on `main` push of its own audit code/workflow and can be run manually afterwards. Ordinary daily trading-data commits do not trigger it. GitHub scheduled market screening, OpenDART rolling calls, Pages and v2.2 decisions are unchanged.

Use `python v3_six_pillar_source_readiness.py --output reports/v3-source-readiness.json` locally or the manual workflow to generate an audit from the currently published snapshots. If market/catalog dates differ or DART is unexpectedly future-dated, abort without a misleading success report. A rolling DART file older than market date is identified by its precise lag, not forged into a same-day valuation.

Next priority after real audit: improve 4-year coherent annual financial observations/issuers with market-pricing provenance while preserving the bounded rolling API quota; obtain verified 20/60/120/250 day institutional/foreign/pension flows and complete historical sector mappings. No speculative fill-ins or unrestricted live API load.
