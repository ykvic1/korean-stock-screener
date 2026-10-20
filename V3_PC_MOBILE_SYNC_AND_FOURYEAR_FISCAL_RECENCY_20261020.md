# PC·모바일 동시 개발 정합성 + 4개년 재무 최신성 게이트 (2026-10-20)

## GitHub 정합성 확인 (정확한 관측 시점)

- `main` latest before this PR: `3fc3aadaec323c6ddb5b350734c10246c545d5f1`. No open PRs; most recent **PR #136 already merged** on the same screener, and its CI #39366342462, Pages #39366634399, source audit #39366634243 all succeeded. The prior session had begun checking missing annual financial values but the PC workflow completed that work first. Do **not** redo/rebase/overwrite PR #136.
- This work therefore branches from **current main** and only adds a further, independent source-quality gate. PR review and CI are still required; do not create parallel UI/another scanner.
- No official API call, scheduled workflows, market data, score weights, existing V2.2 calculation, unified mobile page, or investment recommendation is touched.

## What was missing beyond PR #136

PR #136 correctly split *four same-scope annual filings* from *four usable sets of core financial accounts*. However, even four complete fiscal-year reports **2019–2022** could be treated as a usable current 2026 corporate-finance dataset, and the historical cache could permanently exclude it from subsequent 4-year refresh targets.

This PR introduces an **explicit conservative fiscal recency research gate**:
- Normal as-of dates April 1–December 31 expect the prior fiscal year. During January 1–March 31 a one-fiscal-year filing-deadline grace is allowed; issuer receipts must still be actually dated no later than the source as-of date.
- Four complete statements are counted as historical four-year evidence regardless of age, with their true last fiscal year recorded.
- A separate `recent_four_year_window_observed` boolean and `current_core_financial_research_observed` count require both complete core income, cashflow, balance-sheet and sufficiently recent fiscal years. The legacy historical count is *not overwritten* by the new date gate.
- Cached old four-year windows remain candidates for a bounded **manual** 4-year research refresh; completed current windows are not re-requested. No extra API quota is used automatically.
- The recurring offline quality audit independently recomputes issuer flags, required fiscal year, latest fiscal year and exact aggregate counts and rejects forged "fresh" indicators.

The fiscal recency heuristic **does not** authenticate DART receipts or revisions, verify share count, enable financial15, produce FCF, confirm official PBR or release a full six-domain V3 100-point investment score. Unverified values are null and no buy signal is published.

## Regression tests

- Four reports 2021–2024 on an October 2026 cutoff remain `four_consecutive_years_observed=true` but **not current**; the cache still requests such issuers again.
- Four reports 2022–2025 qualify as current research in October 2026 if the stated receipts are actually cutoff-visible.
- March 30 / April 1 2026 boundary for annual-report grace, duplicate or forged record-level fiscal status and roll-up counts, external provider call-free CI, and prior PC implementation protections.

## Next step after merging

Validate a real **manual four-year OpenDART dry-run** against the actually published same-date annual book before seeking any explicitly approved bounded live research pilot. Publish only independently supported research coverage, not investor scores. Monitor the next 18:30 market run separately for real day and completed-week technical25.
