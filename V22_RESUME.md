# v2.2 data reliability checkpoint

Status: IN_PROGRESS (2026-10-14 KST). Do not start v3.0.
Baseline: main 53f49273b51bc30c9ad768f1c3f31be12f4af24d.
Preserve all historical Excel files and existing working v2 functionality.

Current work: audit_prices.py re-fetches raw NAVER OHLCV, retains source bytes and SHA256, reconstructs all excluded symbols from v2.1 stock statuses (the old missing list was truncated at 50), classifies zero/no-trade and OHLC range anomalies without relaxing validation.

Remaining: independently compare source prices, verify corporate-action/suspension causes before repair, daily/weekly Wilder RSI and confirmed divergence; identical-case baseline versus RSI comparison; remove 500 sample cap and audit distribution; point-in-time finance/sector data; tests, real-data branch workflow, mobile/Excel evidence, PR merge and production verification. Keys may be missing: request only via GitHub Secrets, never commit credentials. Raw-download success is not validation success. All probabilities remain descriptive and unverified for prediction.

Do not launch duplicate work while status is IN_PROGRESS. If interrupted by credit exhaustion, record PAUSED_CREDITS with exact files, latest commit/run IDs and next action; resume only after execution is possible. If waiting for login or keys, record WAITING_AUTH. Mark COMPLETED only with actual evidence and residual limitations; v3.0 requires explicit reliability gate pass.

2026-10-15 resumed. Local v22 implementation adds audit_prices, cross_validate, technical_v22, quant_engine_v22, official_data_v22, financial_metrics, publish_v22, verify_v22, reliability HTML and branch workflow. Baseline reproduced 120 fetched / 67 strict-valid / 53 excluded (25 zero-price,22 close-range,6 short). Independent Yahoo comparison still running. Do not mistake raw download or strict contiguous segment recovery for independent verification. Next: finish comparison; run engine and branch Actions; review real-data outputs; merge only tested code, keep v3_ready=false.
