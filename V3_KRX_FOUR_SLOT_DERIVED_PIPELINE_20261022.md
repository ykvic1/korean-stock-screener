# V3 KRX 4-slot historical derived pipeline alignment — 2026-10-22

## Verified live state before change (all KST 2026-10-22)

| Stage | Last observed completed job | Private progress |
| --- | --- | --- |
| Five-session official KRX transformed source | [39763778110](https://github.com/ykvic1/korean-stock-screener/actions/runs/39763778110) | 121 / 490 cohorts; 369 remain; 254,428 transformed records; 160 calls this run |
| Adjacent cross-cohort price-change bridges | [39732663554](https://github.com/ykvic1/korean-stock-screener/actions/runs/39732663554) | 40 verified links, 82,631 issuer-link observations, 64 official KRX calls that run |
| Private 20/60/120/250 research returns | [39735852120](https://github.com/ykvic1/korean-stock-screener/actions/runs/39735852120) | 38 / 30 / 18 / **0** complete private analysis windows by horizon, no market calls |
| Private discontinuity-risk review | [39740598446](https://github.com/ykvic1/korean-stock-screener/actions/runs/39740598446) | 41 cohorts processed; 445 quality *flags*, not verified corporate actions |

These counts were read from real job logs. Individual historic results and formal V3 investment ranks are **not** verified.

## Change

The fourth daily **02:11 KST** source slot was merged by PR #230. Align all downstream stages to four KST cycles per day:

| Stage | 1st | 2nd | 3rd | 4th | Per-run cap | Max daily KRX calls |
| --- | --- | --- | --- | --- | --- | --- |
| Historical KRX source | 02:11 | 07:11 | 14:11 | 22:11 | 16 5-session cohorts | 4 × 160 = 640 |
| KRX derived cross-cohort bridges | **02:47** | 07:47 | 14:47 | 22:47 | 24 links | 4 × 96 = 384 |
| Derived return composition | **03:11** | 08:11 | 15:11 | 23:11 | 24 per horizon | 0 |
| Derived discontinuity review | **03:43** | 08:43 | 15:43 | 23:43 | 16 cohorts | 0 |

Theoretical aggregate is **1,024 official KRX market calls per fully utilized day** for **only** the two noted KRX source-consuming workflows. These are caps, not usage estimates, and exclude other KRX jobs, PR push and manual runs. All jobs share the same serial private history concurrency lock, with `cancel-in-progress: false`; if delayed, scheduled jobs may queue and must validate source checkpoints.

Four sources can produce 64 cohorts/day; four bridges can process 96 boundaries/day. This is sufficient to catch up the observed link backlog **in theory**, but KRX responses, immutable readback, provider rates, holiday dates and queued workflow delays determine real completion. No code raises existing per-run source limits.

## Safety and operational acceptance

1. Keep `.github/workflows/full-market-batches.yml` at weekday **18:30 KST** unchanged and the existing V3 unified mobile UI intact.
2. Only the two source-consuming stages see `KRX_API_KEY`; rolling composition and risk review have **0** vendor calls.
3. No raw OHLCV/volume/foreign-institution buyer identity in public GitHub or private derived archive. Outputs are transformed, hashed, immutable and checked before checkpoint advance.
4. Unadjusted derived research returns do **not** certify adjusted 20/60/120/250-session returns, issuer continuity, exact events, holding-period performance, IAS/volume20, industry15 or a complete 100-point score.
5. Same daily API vendor/key quota and license limits apply; provider errors must fail closed rather than prompt quota circumvention.
6. Verify repository CI before merging, confirm all four schedules on `main`, then inspect actual new scheduled/manual source, bridge, rolling and quality job logs before reporting any post-change increase.
