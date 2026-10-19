# KRX full-market stock-code identity mismatch — 2026-10-19

## Live evidence and what is NOT known

In run [39187759087](https://github.com/ykvic1/korean-stock-screener/actions/runs/39187759087), `mode=live`, `dates=2026-10-15,2026-10-16` received a KRX response on the first requested market/day, but fail-closed validation blocked it with `ambiguous_or_duplicate_market_code` at KOSPI / 2026-10-15, prior complete market pairs 0. The earlier 10-symbol `official_probe` verified only requested sample codes; it does not imply that every full-market code is a unique six-digit stock code.

This **does not establish** that KRX returned an actual duplicate, a null, a non-six-digit security code or a malformed row. The old branch combined those conditions into one failure. The KRX specification identifies `ISU_CD` as `종목코드` of type `string`, with no universal six-character guarantee asserted by this document. The instrument basic-information API separately identifies standard code and short code; an equivalence must be proven instead of guessing.

## Improvement in this PR

Before converting raw KRX rows into historical stock identities, perform a complete bounded audit of code shapes. In the private Actions summary preserve only these 6 integer counters:

- `missing_code`
- `non_string_code`
- `non_six_digit_code`
- `duplicate_six_digit_code`
- `prefixed_six_digit_pattern` (subgroup of non-six: A + six digits)
- `twelve_character_pattern` (subgroup of non-six: twelve-character string)

No actual issuer codes or names, untrusted response text, URLs, personal information, or API secrets are emitted. A source row cannot be silently removed, reformatted, mapped from 12-digit ISIN to short code, or silently de-duplicated. If anomalies are present the entire date remains **blocked**. Consequently the investor universe, PIT backtest eligibility, v3 official score, and trade readiness remain unverified.

## Follow-up diagnostic

Run the existing manual `KRX two-market historical daily pilot` on `main` for `2026-10-15,2026-10-16` in `live` mode. The resulting counters disambiguate provider code formats versus duplicates. If only KRX-specific different-length identifiers occur, use separately *authorized* issuer-basic-info evidence to develop an authenticated identifier mapping, with unit tests. Do not assume A-prefix stripping or guess a unique six-digit code. If duplicates occur, examine identities through an authorized private route; *do not* dedupe by company name or price.

KRX public service reference:
- https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC
- https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd

## Production isolation
No new live API call in CI, no automatic dispatch, no raw KRX data stored in GitHub, no change to existing v2.2 reports and 18:30 workflow, no activation of v3 investment score.
