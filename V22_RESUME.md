# v2.2 data reliability checkpoint

Status: IN_PROGRESS (2026-10-14 KST). Do not start v3.0.
Baseline: main 53f49273b51bc30c9ad768f1c3f31be12f4af24d.
Preserve all historical Excel files and existing working v2 functionality.

Current work: audit_prices.py re-fetches raw NAVER OHLCV, retains source bytes and SHA256, reconstructs all excluded symbols from v2.1 stock statuses (the old missing list was truncated at 50), classifies zero/no-trade and OHLC range anomalies without relaxing validation.

Remaining: independently compare source prices, verify corporate-action/suspension causes before repair, daily/weekly Wilder RSI and confirmed divergence; identical-case baseline versus RSI comparison; remove 500 sample cap and audit distribution; point-in-time finance/sector data; tests, real-data branch workflow, mobile/Excel evidence, PR merge and production verification. Keys may be missing: request only via GitHub Secrets, never commit credentials. Raw-download success is not validation success. All probabilities remain descriptive and unverified for prediction.

Do not launch duplicate work while status is IN_PROGRESS. If interrupted by credit exhaustion, record PAUSED_CREDITS with exact files, latest commit/run IDs and next action; resume only after execution is possible. If waiting for login or keys, record WAITING_AUTH. Mark COMPLETED only with actual evidence and residual limitations; v3.0 requires explicit reliability gate pass.

2026-10-15 resumed. Local v22 implementation adds audit_prices, cross_validate, technical_v22, quant_engine_v22, official_data_v22, financial_metrics, publish_v22, verify_v22, reliability HTML and branch workflow. Baseline reproduced 120 fetched / 67 strict-valid / 53 excluded (25 zero-price,22 close-range,6 short). Independent Yahoo comparison still running. Do not mistake raw download or strict contiguous segment recovery for independent verification. Next: finish comparison; run engine and branch Actions; review real-data outputs; merge only tested code, keep v3_ready=false.

## 2026-10-15 17시 재개 체크포인트
Base head fe4397f64a72dae4f356900abbd102ab195b9cd4, PR #3 OPEN.
Real-data Actions 38670971197 and 38671464271 succeeded, including mobile and
Excel preservation/download. Latest published snapshot: 120 fetched, 67 original
strict-valid,104 contiguous valid,37/53 segment recovery,16 insufficient segments,
0 independently matched AND sufficient history. Full history recovery is NOT verified.
Local earlier 103 differed because 284740 cached data ended 2021-07-23; fresh Actions
acquired to 2026-10-14. Never describe this as validated full-history recovery.
New user requirement: KRX regular-session OHLC AND VOLUME only. Added session_policy.py,
6 evidence-gate tests (25 total pass), audit receipt/session metadata, confirmed_score=null,
mobile/Excel session labels, and V22_ROADMAP.md. Session provenance unresolved; v3 blocked.
Official KRX FAQ says daily OHLC regular, volume includes after-hours. Public access
is not the same as API authorization; DART_API_KEY and KRX_API_KEY missing in actual run.
IAS weights and EA candidate thresholds recovered from prior conversation; exact subscore
normalization and some EA definitions still unspecified. Do not fabricate or rescale.
Next: commit this change set, run branch Actions, verify session labels/Excel; obtain official
regular-only volume and OHLC evidence before claiming v2.2 completion. No v3 implementation.
