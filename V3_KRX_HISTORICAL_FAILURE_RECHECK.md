# KRX official historical API two-market pilot — first live failed, diagnostic fix

## Confirmed record
- Workflow `39187149670` on 2026-10-19 10:50 KST, `mode=live`, `dates=2026-10-15,2026-10-16`.
- GitHub offline tests passed; actual historical request failed on the first **KOSPI** market/date with generic `krx_kospi_schema_or_transport_rejected`.
- The error category previously swallowed **ALL internal market validation failures**, as well as unexpected client exceptions. Therefore the record does **not** prove an API key/authorization failure or confirm a duplicate, missing date, adjusted stock code, or OHLC field issue.
- Earlier official probe run `39182381585` received sampled KRX data for 2026-10-16; a past successful 10-stock probe **does not** certify all-market data for 2026-10-15.
- Failure occurred before full pair was stored, and no usable 2-day history collection was confirmed. The failed workflow also left no downloadable diagnostic artifact.

## Fix
- Preserve whitelisted **internal data-validation failure reason**, and separately keep KRX HTTP/network reason if the client raises `KRXProbeError`.
- Safe diagnostic fields include `failed_date`, `failed_market`, `completed_pairs_before_failure`, and validated numeric HTTP status when available.
- On failure, the process returns nonzero and **writes a private counts/reason-only summary artifact**, even when real KRX could not be parsed.
- GitHub logs print allowlisted result code/date/market only: no vendor response body, stock price data, raw URL, authorization header or token.
- No automatic retry, no changes to 18:30 KST v2.2 scheduler, no persistent GitHub storage, no publication, no v3 confirmed scoring.

## Recheck
Open `Actions → KRX two-market historical daily pilot (manual only)`, select branch `main`, `dates=2026-10-15,2026-10-16`, `mode=live`. This may invoke at most four day-market KRX requests, within the previously bounded contract. If blocked again, inspect the new safe diagnostic from Actions and the private 7-day artifact. **Do not infer the exact cause before this run.**
