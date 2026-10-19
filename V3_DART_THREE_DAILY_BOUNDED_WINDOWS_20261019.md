# 2026-10-19 OpenDART current-listed rolling coverage: three bounded weekdays

## Live baseline (not a hypothetical extrapolation)

- 2026-10-19 current-listed KOSPI+KOSDAQ ordinary-stock catalog: **2,497**.
- Published source-dated rolling file `docs/v3-dart-book-reference.json`: **42 attempted**, **39 observed annual statements**, **38 finite simple annual P/B references**.
- Successful initial 40-target experiment precedes this scheduling change. This PR does **not** claim any new live DART retrieval or prove future quota/reliability.

## Change

Retain original **maximum 40 companies per execution**, **two batches of at most 20 companies**, 300-second bounded provider calls, 24-minute workflow cap, atomic/rebase verification, `DART_API_KEY` secret handling, and the existing common `korea-stock-screener-publication` concurrency lock.

Add two separated **Korea-local weekday** collection windows to the existing one:

| KST weekday | GitHub UTC cron | Purpose |
| --- | --- | --- |
| 07:00 Monday-Friday | `0 22 * * 0-4` | Increment before local market open |
| 13:00 Monday-Friday | `0 4 * * 1-5` | Increment during the day from last published full-market snapshot |
| 21:00 Monday-Friday | `0 12 * * 1-5` | Existing post-18:30 full-market analysis increment |

GitHub schedule dispatch timing can be delayed; all runs share a publication lock and may queue. Each execution reloads **latest main**, verifies market/catalog same-day census and allowed snapshot age, tries unvisited names first, and rejects stale market-day publication after rebase. No weekday schedule changes to existing v2.2 **18:30 KST** batch collector or quant run.

Expected maximum newly attempted names: **120/day instead of 40/day**. Approximate number of weekday runs remaining for first-pass coverage: `ceil((2497-42)/40)=62`, theoretically `ceil(62/3)=21` weekdays after deployment **if all 3 windows execute and make 40 distinct attempts each**. This is attempted coverage, NOT 2,497 successfully observed annual statements or P/B values; holidays, API failures and rate limits make real completion uncertain.

## Promotion and rollback

- DART value remains **annual filing reference only**; not exchange-certified PBR, point-in-time evidence, investor-flow observation, v3 scoring input or a buy signal.
- Prior observed figures stay in the source-date cache, currently unlisted symbols are pruned, unavailable entries remain missing; no imaginary zeroes.
- CI must pass before merge. On repeated HTTP/rate-limit issues, delay or remove **only** the two extra cron entries while keeping the original 21:00 schedule.
- After first actual three-run weekday, verify GitHub run conclusions, published `asof`, attempted/observed/PB counts, duplicate issuer IDs and existing 18:30 scan success before adjusting any per-run budget.
