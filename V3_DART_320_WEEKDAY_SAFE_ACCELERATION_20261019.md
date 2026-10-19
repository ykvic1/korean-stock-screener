# OpenDART 320 listed-issuer daily reference attempts — safe weekday accelerator

## Verified starting point (2026-10-19)

- `main` after PR #118: **2,497** currently listed stocks in the full-market catalog.
- `docs/v3-dart-book-reference.json`: **42 distinct company attempts**, **39 annual statements observed**, **38 simple nonofficial P/B references** (2026-10-19 source day).
- Full v2.2 KOSPI+KOSDAQ screen runs separately at **18:30 KST**. The rolling source-date annual book/PB file does not change existing official scoring, 6-pillar v3 release status or historical PIT.

## New bounded cadence

Eight runs **Monday–Friday KST** at 01:17, 04:17, 07:17, 10:17, 13:17, 16:17, 21:17 and 23:17.
Each run selects **at most 40 previously unattempted CURRENT listed tickers**, at most 20 per OpenDART batch and max 300s per batch. Existing 24-minute run deadline and shared publication concurrency lock are unchanged. Moving schedules to minute 17 reduces the GitHub Actions top-of-hour burst risk. UTC Sunday rollover is tested.

At full schedule success: at most **8 × 40 = 320 new issuer attempts per weekday** compared to former 120. With 42 already attempted, **2455 remain** and earliest theoretical first-visit target is `ceil(2455 / 320) = 8` successful weekdays. These are attempts, not guaranteed actual statements or financial ratios. Scheduled invocations may be delayed or skipped; shared scan/publication lock can queue work, and daily named-stock batch failures lower coverage. No increased instantaneous parallelism.

## Important fail-closed behavior

Official OpenDART guide: application status `020` = request limit exceeded, generally 20,000 calls but account-specific thresholds may differ. This is **not** a guaranteed account quota. Other global account/service statuses `010`, `011`, `012`, `800`, `900`, `901` now stop a multi-issuer batch rather than being treated as dozens of missing financial statements. They do not leak secrets or get recorded as P/B zero.

`013` remains normal no-data response; missing or malformed per-issuer data still remains unconfirmed. The collector writes its result atomically only after the full 40-target batch completes, so a quota exception never overwrites previously published good financial ratios. Newly completed partial work in a failed batch may need retry. An error should be investigated before moving to more than 8 runs per weekday.

## Out-of-scope and validation

- No change to `full-market-batches.yml`, 18:30 v2.2 processing, `quant.yml`, ranking, weights, investor flow or buy/sell classification.
- No official exchange PBR, current financial PIT certification, XBRL revision approval or historical backtesting from these annual-derived references.
- CI checks the eight KST-specific UTC windows, bounded publication/caching behavior, provider 020 stop, no continued requests after throttle, and existing full test suite. Merge only after CI green.
- Observe first actual weekday for runs completed, unique attempts increase, observed/PB coverage, and API throttling. If throttling or unwanted queueing repeats, restore original PR #118 three-window cron only, keeping existing stable data. GitHub cannot guarantee scheduled runs at exact minutes.

## References

- OpenDART official messages: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020051
- GitHub Actions schedule-delay documentation: https://docs.github.com/en/actions/reference/events-that-trigger-workflows#schedule
