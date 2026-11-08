# Recover missing five-session reported money alongside weekly recovery

After the first 62 reviewed weekly periods, 85 issuers had complete weekly
money observations but internal daily-money gaps. The five-session collector
historically required valid OHLC to retain a reported trading amount, so fixing
only weekly archives could leave persistence and full-100 observation blocked.

This adds original-digest-pinned, immutable private receipts for missing
five-session issuer amounts. Scoring and recovery share the exact same 480-
session calendar/prefix/anchor validation. Target only internal gaps between
observed periods, prioritize issuers missing only money components, and query
all five exact source sessions. Every established amount must match the fresh
official source before additions are accepted. Explicit zero is an observation;
absent/invalid amount remains unavailable.

Only total reported amount and the observed positive-amount day count are
retained in the additional proof. Scoring receives no reconstructed price,
price change, intrabucket directional pressure or correction evidence. Existing
records, original files, long-window formulas and uncertified flags are kept.
New private amounts/issuer time series never enter public reports; only bounded
points and aggregate repair counts do.

Use the proven immutable-write and remote Git-blob CAS retry helpers. Save an
index after each examined cohort, including unavailable results; reuse orphaned
receipts and never re-fetch KRX for repository contention. A run examines up to
12 five-session windows, with at most 144 source reads including retries. It
reserves the worst-case request allowance before starting a new cohort, ending
successfully between cohorts when the remaining run budget is low.

The existing trusted recovery job processes weekly and daily gaps together and
dispatches its next main run while either finite queue remains. All shared
private-money writer workflows retain pending jobs with `queue: max`, preserve
the existing mutual exclusion, and cannot cancel a pending repair merely by
arriving later. Offline PR checks have no private tokens.

Live continuation also exposed saved private weeks without a subsequent
personal-money projection. A checkpoint now marks its changed flag immediately,
and the live workflow explicitly dispatches saved-source projection after
private snapshot removal, including when a later source step fails. Publication
does not depend solely on completion notifications through a long workflow
chain. The next recovery dispatch remains success-only and finite.

Validation includes explicit zero vs absent, source revision rejection, exact
calendar/original hash and no-price proof validation, digest tampering,
end-to-end full long-window replay, immutable originals, no public source
leakage, unavailable checkpointing, orphan/idempotent resume, bounded source
retry, repeated repository HTTP 409 without extra KRX calls, separate daily
index, combined continuation and private snapshot path coverage.
