# V3 KRX fifth bounded daily historical replay — 2026-10-23 KST

## Verified baseline
- Official transformed-only source: **169/490** complete five-trading-session cohorts, **321** remaining, last completed candidate session **2020-03-25** (2026-10-23 morning public verified aggregate).
- Adjacent private derived links: **112** completed out of **168** currently required, **56** pending.
- Rolling 20/60/120/250-session private research and discontinuity-risk snapshots up to the already bridged evidence; no verified PIT universe, corporate action-adjusted prices, investment-grade 100-point certification.
- The last completed KRX source job `39865719466` used exactly **160 market calls** to append 16 cohorts. The last bridge job `39870304145` used **96 calls** for 24 links. Both succeeded. These are observed individual job counts, not a guaranteed future rate.

## One additional bounded KST daily slot

| Stage | Pre-existing slots | Added slot | Maximum calls per added run |
| --- | --- | --- | ---: |
| Official KRX 5-session private transformed-only source | 02:11, 07:11, 14:11, 22:11 | **11:11 KST** | 16 cohorts × 10 calls = **160** |
| KRX private cross-cohort derived link | 02:47, 07:47, 14:47, 22:47 | **11:47 KST** | 24 links × 4 calls = **96** |
| Private derived corporate-event *heuristic* review | 03:43, 08:43, 15:43, 23:43 | **12:43 KST fallback** | **0** |
| Rolling 20/60/120/250-session private research | After successful boundary `workflow_run`, plus 03:11 KST daily fallback | Triggered after successful fifth boundary | **0** |

Five theoretical full source+bridge slots yield up to **5 × (160 + 96) = 1,280** KRX market calls per complete day, an extra 256 maximum versus four slots. KRX published open API terms currently state at most **10,000 requests per API key per day**, but the exchange may set tighter limits or temporary restrictions; no limit circumvention, retries that double-fetch, or silent escalation. The theoretical additional source throughput is from 64 to **80 complete five-session cohorts per full day**, if the provider/Actions schedule performs normally. No completion-day promise.

## Security and data gates retained
1. The private nonreconstructive derived-only checkpoint, immutable blob readback, shared serial concurrency group, strict per-run caps and raw OHLCV prohibition remain **unchanged**.
2. No new storage of raw KRX or issuer-specific official source prices in public pages; license/redistribution permission is *not* inferred from a request quota.
3. Keep the existing weekday **18:30 KST** stock screen, its publication and six-pillar score semantics unchanged. Industry15, actual institution-flow IAS20, adjusted historical returns and PIT 100-point ratings remain unverified.
4. Static workflow tests assert the exact five cron windows, caps, private/no-provider separation and 18:30 operation. Full CI must pass before merge.
5. After merging verify **actual** source/boundary runs and a rolling job with event=`workflow_run` before claiming the extra slot is operational. A GitHub Actions schedule may be delayed or skipped.