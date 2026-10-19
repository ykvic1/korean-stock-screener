# V3 independent completed-week price patterns and RSI — 2026-10-20

## Verified release baseline

PR #121 is already merged at main `d980a51f6002...`, all PR CI green (run 39275673619), Pages successful (run 39276169798). Original daily Bull Flag / double bottom / RSI-30 reclaim and bullish-divergence engine is deployed, but data only become available after a subsequent successful full-market 18:30 KST screening run. No need to reopen PR #121.

## Scope of this PR

- Add **weekly** independent OHLCV aggregation to the existing source-dated daily observations, not a second online provider.
- A week is a calendar Friday-ending bar. Read `Open=first, High=max, Low=min, Close=last, Volume=sum`; **do not include this week's Monday-Thursday partially observed bar** because lower volume and incomplete candle shape can produce spurious Bull Flag or RSI.
- Compute Bull Flag and double-bottom formation / confirmed-breakout **heuristics** plus 14-week Wilder RSI 30 recross and pivot-confirmed bullish divergence on 90+ complete weekly bars; keep a distinct `timeframe=weekly`, `completed_week_only=true` and `asof=completed Friday`.
- Publish nested `pattern_weekly_research` alongside daily `pattern_research` only with next successful full-market scan; no new scheduled API requests, no changes to legacy v2.2 scoring, DART 8x40 or quant publication.
- Page prioritizes **weekly technical observations** for long-term research and separate **daily observations** for swing research. Add a weekly tab. The UI only accepts completed Friday reference within 13 calendar days, not a future/current partial/stale week. OBV remains optional corroboration.
- Preserve score and 3%-15% price/support risk gates, explicit liquidity/chase flags, no certified buy, no institutional accumulation attribution. Weekly patterns do **not** prove a bullish primary Elliott wave count.
- The 2026-10-19 market file predates this work; no weekly pattern counts should be invented until data are published on a later full-market run.

## Tests and rollout

Mocked weekly-Friday source with a new anomalous high-volume **Tuesday** must produce exactly identical completed-week research. Adding a future Friday may update. Invalid/duplicate OHLCV dates and <90 weekly bars fail closed. JS regressions cover daily/weekly independent selectors and per-ticker week-end, staleness, future-source, forged scored-buy controls. Full GitHub Actions CI green is required for merge.

## Known remaining gaps

Existing weekly flag geometry assumes 11-week consolidation / 23-week pole whereas daily uses 11-day/23-day window: this is a specific **multi-month structural heuristic**, not a 2–4 week short-term flag. Source OHLCV corporate action/revisions and regular-session verification are not sufficient for certified backtests or live trading. Long-run portfolio-grade v3 remains gated on investor-flow, multi-year PIT financials, sector/cycle and survivorship-controlled quant evidence.
