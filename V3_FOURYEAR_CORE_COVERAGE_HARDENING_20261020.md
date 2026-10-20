# V3 OpenDART 4-year evidence coverage hardening — 2026-10-20

The user needs a **single upgraded screener** with actually supported financial15/valuation15, NOT synthetic points or an extra detached page. The backend already has the separate manual/bounded 4-year DART pilot; this patch repairs a crucial semantic shortcoming **before** using the responses in a 100-point rank.

## Risk found
Prior pilot `four_consecutive_years_observed_count` counted four continuous year-tagged filing receipts (identical CFS/OFS). Even if these filings had no reported **revenue or operating profit**, they counted as “4y observed.” A prior snapshot with four structurally complete receipts but missing money accounts was excluded from future retries due to `same_scope_four_years`. Thus raw 4y collection progress could look better than actual financial-data coverage.

## Backwards-compatible source qualifiers

Keep `same_scope_four_years` **exactly as it means**: four observed year-tagged receipts of the same accounting scope. Never rename it to “full financial 15 points.”

For each ticker compute separate factual Boolean coverage:
- `four_year_income_values_observed`: all four years have **positive revenue** and a present (possibly negative) operating income.
- `last_three_years_operating_cashflow_observed`: three most recent years have real, non-null operating cash flow values, including legitimate zero.
- `latest_year_balance_values_observed`: latest available year has liabilities, equity and owners' equity present (negative equity remains an important risk, not a missing number).
- `core_financial_observed_for_research`: all three above present. Does NOT mean true FCF, PIT receipt revision history, official valuation/PBR or financial15 is verified.

Publisher counts **filing-year coverage and core-money-account coverage separately**. Issuers with missing core values remain eligible for a future manually bounded recheck; fully observed core issuers are not needlessly recalled.

The read-only continuous audit independently reconstructs each issuer's raw evidence, verifies four coverage flags and aggregate totals, rejects fake completeness claims and writes separate core-vs-receipt counts.

## Real source dry run, zero external requests

The post-close and Tue-Sat 07:00 KST quality workflow now also executes the same 4-year **planning function** on currently published market/listed/annual DART snapshots. It publishes an offline JSON artifact of counts and 0-API provenance. It does not access DART/KRX, add accounts, alter daily/rolling API quotas, change scoring or automatically run the `live` four-year manual pilot.

Prior `docs/v3-dart-four-year-reference.json` is **not yet generated**. A CLI/read-only plan with a nonzero next cohort merely proves identities/scope/market dates can be selected for the next explicit live 1–4 issuer pilot. It is **not** new four-year observed data.

## Release gates
- Strict synthetic tests verify 4 receipts of entirely empty amounts still count as **receipt coverage** but **0 verified revenue/OP/CF/balance coverage**, no full15/100 investor score.
- CI passes both Python full regression and continuous report anti-forgery + manual workflow limits.
- Existing 18:30 all-stock, 8×40 rolling annual DART, six 20/25/15/15/15/10 weighting, legacy chart/quant, unified mobile UI and Pages remain unchanged.
