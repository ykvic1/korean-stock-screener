# Collection and validation efficiency

## Changes

- Remove 62 per-file Python unittest commands already covered by the single
  full discovery in the integration workflow. Preserve independent CLI,
  JavaScript, PowerShell and publication checks.
- Data-only quant runs validate actual saved market status, canonical dates,
  issuer identity, duplicate codes, count consistency and missing/unpriced
  rows. Remove the unrelated full code suite and its optional test-only
  calendar installation from this collection path.
- EPS audit and saved-source operational refreshes keep actual-source
  validation/recalculation; do not rerun code unit suites on each data refresh.
- Split safely parsed XML context selection from parsing. EPS audits reuse the
  instance for each year, scope and ordinary-share selection. Each selector
  still checks its requested year, scope and issuer independently.
- Preserve the previous dated complete EPS factor during a partial same-day
  source refresh, as already done for partial volume components.

## Measured result

Synthetic instance with 10,000 unrelated facts and three annual periods,
median of three runs on this workspace:

| Metric | Before | After |
|---|---:|---:|
| Safe XML parses per archive | 8 | 2 |
| Extraction seconds | 0.33564 | 0.13959 |

Extracted evidence was identical. This measures local XML processing, not
provider/network throughput or the entire 2,501-issuer collection.

Actual saved-source replay kept every issuer's score, available weight and
rank unchanged. Full-input coverage stayed 1,017 of 2,501. No collection gain
is attributed to this refactor. EPS source reader fingerprints intentionally
change, so the finite audit refresh runs after merge; previous dated complete
EPS factors remain visible during that refresh.

Focused checks covered affected XBRL/EPS selectors, operational score retention,
workflow routing and the new input validator. Full integration runs once for
this combined change. No generated market or score files are included here.
