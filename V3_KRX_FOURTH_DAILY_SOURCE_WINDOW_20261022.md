# V3 KRX fourth bounded daily source window (2026-10-22)

## Verified starting point

- Last confirmed official-source run: [39728875264](https://github.com/ykvic1/korean-stock-screener/actions/runs/39728875264), finished 2026-10-22 14:35 KST.
- Historical replay completed: **105 / 490 five-session cohorts** (21.4%); **385 remain**.
- Previous run reported 525 session candidates and 219,764 derived issuer-cohort records, **not raw source OHLCV**.
- These are run-verified values, not a prediction of current progress.

## Change and hard limits

Add UTC 17:11 (**02:11 KST**) to the existing source-job schedules UTC 22:11, 05:11 and 13:11 (KST 07:11, 14:11, 22:11). No change to the weekday **18:30 KST** main screener.

- Each source run remains capped at **16 five-session cohorts**, 10 official KRX market-day source requests per cohort (**160 max per run**) and a 55-minute job timeout.
- Four scheduled source runs imply at most **64 additional cohorts / 640 KRX source calls per day**, assuming sufficient backlog and successful runs. The separately scheduled boundary workflow allows at most **288** further KRX calls, totaling a *theoretical* **928 scheduled KRX calls per day across those two workflows**. This is neither observed usage nor a promise of progress; other workflows and any push/manual runs may add calls.
- Same concurrency group serializes source replays; checkpoint and immutable private derived output remain source of truth, with readback before recording progress.
- No raw OHLCV or volumes are persisted into the repository or archive. Private derived checkpoint stays in `ykvic1/korean-stock-private-data`.
- A workflow-change push on main also triggers a one-off source run. It is additional to scheduled theoretical call count and must be assessed by its real run logs.
- KRX service-side daily or per-minute quota is authoritative. If quota errors, throttling or repeated retries occur, reduce scheduling frequency; do **not** bypass provider limits.

## Verification and score integrity

1. Check workflow PR CI and the `main` YAML schedule has exactly 4 source cron entries after merge.
2. Check at least one post-merge official source run and its private checkpoint; update completed and remaining cohorts **only** from observed logs.
3. Preserve the existing weekday 18:30 KST stock screener, 6-domain score evidence thresholds and V3 unified display.
4. No effect on IAS/industry factual acquisition; no verified 100-point score until all source areas satisfy their verification requirements. Unknown inputs remain unknown, never synthesized.
