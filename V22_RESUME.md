# v2.2 data reliability checkpoint

Status: VALIDATED_BRANCH_BLOCKED_BY_LIVE_MOBILE_GATE (2026-10-16 KST). PR #7 merged; PR #6 remains OPEN. See final checkpoint below; no v3.0 work.
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

## Final verified checkpoint, 2026-10-15
Actions 38703776587 SUCCESS on source 21367f4924435fcfe63d592e0d76b67692d11996;
report publication b2600dee9c939b73a93569f318ece8da1fde91e1. Unit tests25, mobile
search/download, data-contract assertions and previous Excel hashes passed. Latest
index partial-point display 04bf4a8ff0b1794d6d3d1ad3aaa296eff2584751 passed PR checks.
Actual asof 2026-10-14: raw120, numeric contiguous104, segment recovery37/53, residual16,
independent-match0, KRX-regular verified0, full-history recovery verified0.
Raw earliest2017-09-18;108/120 raw histories span at least5years. Session unverified.
DART_API_KEY and KRX_API_KEY were absent in the executed workflow. Need authorized
keys in Secrets AND a documented regular-only volume feed; daily KRX total volume
is insufficient. Missing IAS formulas/EA definitions are recorded in V22_ROADMAP.md.
Release scope is audit/UI fail-closed labels, not completion of data trust or v3.
Next action: verify official KRX regular OHLC and regular-only volume on known
dates, reconcile discrepancies/adjustments and corporate actions, then recalculate.
Current120-candidate collection remains legacy; full-universe incremental operation
and new100-point model are design only. Do not claim operational completion.

## 2026-10-15 resumed after deployment
PR #3 MERGED at a77a77b734833dababe4e572b566dab2ca0144fc. Pages run
38704599193 SUCCESS; live reliability.html displays 120/104/37/0, session
unverified, Excel link and RSI table correctly. Published asof remains 2026-10-14.
New production failure: daily run38716787585 stopped after60min; OHLC210 done,
flow70/209 at19:53KST, save/report skipped, downstream v2.1/v2.2 skipped.
Logs show about43sec/flow; code redundantly calls failed Npay fallback twice
when KRX credentials absent. Actual supplier exception was not logged before
termination, so its underlying cause is still unknown (do NOT assert login cause).
Patch: flow_collection.py calls fallback once; logs each supplier exception;
three consecutive timeout exceptions stop further supplier requests;20min flow
budget defers remaining requests as missing, never fabricated zero observations.
Stock-specific short-history errors do not open the timeout circuit. Attempted
count now counts actual requests rather than all planned stocks.
Local29tests pass (25existing+4failure-path tests). Live rerun pending.
Next: commit patch on v22-data-reliability, PR checks/merge and daily rerun;
inspect exact supplier error and actual new-data availability. Official session
validation/auth blockers above still apply; v3 remains blocked.

## Supplier UI root cause confirmed, 2026-10-15
PR #4 merged409f94f998e7d26c63976ff7ac9faefeb2b3274e; CI38746713866
passed29tests. Daily rerun38746993312 started.
Live Npay UI confirmed flow table is /tradingTrend, not /price; date now
YY. MM. DD.;10 rows per page, and foreign-holdings label exists only as button
aria-label (old has_text locator never matches). Exchange defaults to integrated.
Follow-up patch explicitly selects trader-toolbar KRX, reads3pages, parses2/4digit
calendar dates and signed net shares, ignores holdings percentage. It still marks
regular_session_unverified: KRX exchange selection does not prove regular-only flow.
Two parser regressions added;31localtests pass. Need CI and live rerun to verify
end-to-end supply, not just code. No full-history recovery claim or v3 work.


## 2026-10-16: fixed original cohort and publication collision repair (LOCAL ONLY)
Remote base main: 3a8e59160ef4fafe21ec826973fb6bb33e4b852d.
Local branch: v22-data-reliability. PRs #3/#4/#5 merged; no new PR created.

Verified run 38748070575: analysis completed but flow success 0/210. Npay errors:
exchange toolbar absent on some symbols; other symbols expose only 10/20 required
rows. Publication failed with rebase conflicts in data.json/history.json/latest.xlsx
because daily and reliability writers overlapped. Downstream runs skipped.

Changes prepared:
- config/v22_baseline_53.json pins original 53 failures and original-source SHA256.
- baseline_cohort.py validates membership and exchanges; no fallback to today's cohort.
- audit_prices.py/cross_validate.py audit union of current120 + missing26 =146;
  original53 are separately reported in baseline_cohort; research universe unchanged.
- reliability.html displays fixed cohort count or explicit not-yet-retested label.
- all three writer workflows share concurrency group; daily checks out current main;
  upload-artifact runs even on failure. Actual Actions execution still pending.
- verify_v22.py asserts full fixed cohort; three regression tests added.
- predictive validation removed as basic data gate; regular-session evidence remains required.

Actual fresh source run at asof2026-10-15:
146 NAVER originals collected and Yahoo comparisons attempted; fixed53 fetched53.
Original failure classes reproduced: nonpositive25, range22, short6.
Fixed53 numeric contiguous usable37 (69.81%), residual16; independently matched0,
full-history verified0, KRX regular-session verified0. All53 cross statuses are
mismatch_or_insufficient, NOT verified. Raw original53 spans2017-09-18 to2026-10-15;
46 have raw spans>=5years. Partial normal segments are NOT full repairs.
Today's120: fetched120, original strict69, contiguous111, independently matched0.
Previous published audit contained only27 of original53 (21 usable);26 were omitted.

Evidence: docs/archive/reliability_fixed53_20261016.json and docs/reliability.json;
raw bytes and receipts in local cache/v22_raw; isolated suffixes cache/quant_history_v22.
Tests34 passed; py_compile, JS syntax, YAML and diff checks passed.
All16 existing Excel files match HEAD hashes. Existing quant/audit date, code-set and
available-symbol count agree. No new workbook or predictive-performance claim.
Mobile browser installation failed: downloaded Chromium archive was truncated/invalid;
mobile runtime and new Actions NOT verified. Do not call this a deployed release.

Automatic approval review rejected git push, stating external GitHub remote/code
publication was not explicitly authorized. Did not retry through another channel.
Complete local changes are ready for user approval to push this exact branch and open
PR, run CI, merge and verify deployment. No secrets or raw credentials in changes.

Next: obtain push approval, upload reviewed commit, run Actions and mobile/Excel checks;
then inspect actual Npay toolbar/pagination variants and verify at least20 KRX-selected
observations on representative symbols before full210 rerun. Do not guess locators.
Official regular-only OHLCV and corporate-action reconciliation still block v2.2 completion.
IAS/EA, finance and100point model remain previous state/design; no v3 implementation.


## 2026-10-16 17:20 KST: authorized v2.2 validation checkpoint

- PR #7 merged main -> v22-data-reliability, merge SHA 3f0a2de85979f413dc3a77b1da45d74cd2ec2718. Main remains 3a8e59160ef4fafe21ec826973fb6bb33e4b852d.
- Exact seven hotfix files match uploaded ZIP. No duplicate patch application. Original 53 unique codes/names and pinned Git blob SHA-1 verified. This patch pins a Git blob SHA-1, not SHA256. Independently computed source SHA256: 9ef4317e5a64910f16b3aa4013da9224746a6a23dd5a00275e530f299a3d844a.
- Full local tests: 41 passed after restoring missing environment dependency beautifulsoup4. GitHub PR correctness run 38871794305 passed 41 tests.
- Actual reliability run 38872040246 SUCCESS: 41 tests, raw acquisition, independent comparison, research calculation, JSON/Excel creation, preservation hashes, 390x844 mobile UI and Excel download, artifacts, git rebase and push all succeeded.
- Publication ran while a separate existing HTML fix was uploaded; rebase and push succeeded. Final result commit b0b3dc0c39d1f367a5428778d047e20a71d4e022.
- Existing display fix reused from local ad8b18d in c63c748: summary now reads baseline_cohort.numeric_segment_usable/53, instead of rotating subset recovered/53. No new feature.
- All 16 pre-existing Excel files and existing archive objects unchanged; new report 한국주식_v22_통합검증_20261016_171513.xlsx added. No duplicate history file paths. No second full data acquisition run. Concurrent multi-workflow/repeated-run publication not fully exercised; shared lock regression tests passed.
- Fixed53 asof2026-10-15: fetched53, usable numeric segments37, independently matched0, full-history verified0, regular-session verified0. Status unverified. Current120 fetched120, usable111, independent match0. KRX/DART keys still required.
- Live deployed mobile run 38872626683 FAILED at verify_mobile.py line31: assert stocks.count()==data['flow_confirmed_count']>0. Current deployed flow_confirmed_count=0. This is a pre-existing live data/gate failure, not a hotfix unit-test failure. Did not weaken the assertion or develop a new collector. Later download/filter tests in that run did not execute.
- Bot-created latest PR correctness run 38872521617 initially required maintainer approval; approved through GitHub UI and completed SUCCESS.
- PR #6 deliberately remains OPEN under user instruction not to merge failed validation. New main merge and Pages deployment NOT performed. Existing deployed reliability page still shows old21/53; fixed37/53 exists only on development branch.
- KRX09:00-15:30-only volume, independent prices, corporate actions/suspensions, full53 history, stable foreign/institutional flow collection remain unverified. No v3, IAS/EA engine, backfill or feature expansion.
- Stop here. Resume only to resolve/review the live mobile/data gate within an explicitly approved scope, then conditionally merge PR6 and verify actual Pages. Do not redo full collection merely to regenerate evidence.
