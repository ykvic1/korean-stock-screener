# Fix scheduled V3 technical25 output: full market 100-stock batch route — 2026-10-20

## Observed root cause

The real weekday **18:30 KST** publisher is `.github/workflows/full-market-batches.yml` calling `full_market_batch.py scan -> merge -> publish`, not the legacy single-process `screener.build_rows` path.

PR #123 added the six-pillar technical candidate adapter `compute_candidates(valid)` ONLY to `screener.build_rows`. Consequently on the actual scheduled route `screener.score_one(r,cfg)` produced only legacy v2 weights and `screener.web_publish` serialized `v3_pattern_integrated: null` for every stock, even though `analyze_history()` already generated `pattern_research` and `pattern_weekly_research`. The 2026-10-19 published data source predated the new code, so earlier zero counts were not themselves proof of this issue. Source inspection confirms the missing hook would have persisted.

## Safe change

At the single final batch merge, AFTER flow data collection and final v2 `score_one`, run `v3_pattern_composite_score.compute_candidates(current)` before existing v2 sort. This attaches the user-approved day+finished-week pattern research technical25 **once**, preserving all original rows and v2 decisions.

It keeps the canonical six-pillar weight budget exactly **IAS20/technical25/financial15/valuation15/industry15/momentum10**. Pattern points are day7+week8; RSI day2+week3, 120-week MA3, confirmed volume2. OBV is optional corroboration in IAS and never a prerequisite. Confirmed six-pillar rank, investable score and buy certifications stay NULL/FALSE while unverified IAS, financial, valuation, industry or momentum are unavailable.

One real-scheduled-path synthetic integration regression now checks:
- 105 symbols passing plan → scan all parts → merge.
- 104 valid same-date day and fully completed week signals provide source-separated technical25/25; 1 missing weekly signal has observed maximum11/25, null full technical candidate (not zero).
- Every record retains identical v2 score, decision and ordering; final 6-pillar research score and buy certification remain unavailable.

**No schedule change, no new API calls, no UI/Excel redesign, no result generation before the actual next successful scan.** After code merges, check the real `docs/data.json` per-ticker pattern/RSI source dates, technical observed counts, full-market census, v2 scores and reported source failures. If the actual run fails, do not replace the last known-good publication.
