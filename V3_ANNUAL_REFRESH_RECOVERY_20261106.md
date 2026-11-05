# Annual refresh interruption recovery — 2026-11-06

The earlier four-year recovery run 42082786137 succeeded: 8 issuers were
processed, with 51 four-year fiscal windows and 44 usable core-financial
issuer records in that bounded reference. This is separate from full-market
R1d observation availability (556 of 2,501 at the November 5 close).

Annual-source rebind run 42115622945 subsequently failed under the generic
`source_provider_quota_or_validation_stop` label. That old log cannot
establish whether the cause was transport, quota or validation failure;
no provider response content is inferred from it.

The refresh now keeps an explicit mask of completed, normalized issuer/year
responses. On a timeout, connection failure, known quota/maintenance status,
HTTP 429/5xx or bounded-budget exhaustion, requests stop without retrying.
Only completed pairs reach the existing newer-receipt/no-value-loss adoption
rules. Unrequested pairs retain their previous values and are counted as
not rechecked; a genuine no-accounts response is distinguished from them.
The report marks the incomplete slice and the waiting reason explicitly.
No previous amount is relabeled with a new receipt, and no new score is
awarded for missing or unrequested data.

Authentication failures, unknown provider statuses and malformed source
responses remain blocking. Even previously completed pairs in that attempt
are not published after a source integrity failure. A sanitized review
artifact is written before propagating the failure, without raw responses,
URLs or credentials. Existing exception messages remain compatible; only
allowlisted status metadata is carried for classification.

The 16-request/1,200-second source budgets, four scheduled partitions,
strict post-rebase verification, source receipt rules and all research-only
flags remain in effect. Partial output must still pass strict source/score
verification before publication. This change does not certify historical
PIT disclosures, adjusted prices, corporate actions or investment outcomes.
