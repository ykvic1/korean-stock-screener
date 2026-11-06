# All-listed observation recovery

The target is actual observation of every allocated component for all 2,501
listed issuers. A high score, an observation rate and a fully observed issuer
are distinct measures. Missing values remain unavailable; histories and the
25/30/12.5/12.5/20 allocation stay intact.

At the November5 baseline there are556 fully observed and1,945 incomplete
issuers. The existing reconciled all-market priority receipt covers every
incomplete issuer and all missing components, including multiple gaps. Major
component gaps include1,385 FCF-persistence,985 money-structure,813 PER/EPS,
697 FCF-yield,632 balance-sheet and528 weekly-MA observations. Counts overlap.
Source failures and genuine short histories must be investigated separately;
none of these counts establishes that the issuer is newly listed.

## Remove the near-completion acquisition restriction

The owners-profit collector previously admitted a frozen near-completion
cohort plus only one-gap R1d targets. That excludes multi-gap issuers from
acquiring an independently observable earnings-yield input.

Admission now considers every reconciled R1d issuer with missing earnings
yield, an observed current annual statement and exact valid receipt, year,
CFS/OFS and KRW identity. Complete EPS and having only one gap affect priority,
not eligibility. Already observed earnings yield is not queried. On this
baseline,523 annual-book missing-profit issuers were outside the source
cohort,520 also lacked earnings-yield weight, and505 satisfy the full source
identity/status gate. The cohort expands111→616 while preserving all111
previous results. Previously queried missing-standard-account evidence is
not queried again under the same receipt.

A live workflow processes at most six batches of20 issuers. Each response is
checkpointed. A provider error stops the run, preserving validated progress;
a later run resumes remaining identities. At most120 issuer requests are selected, each with the existing maximum
of three transport attempts (360 financial calls), plus six optional
identity-map calls. A shared12-minute budget stops further issuer calls
or retries so validated checkpoints can publish within the job deadline. No unbounded retries or fake
source observations are introduced. Full-observation candidates still go first,
followed by the rest, interleaving KOSPI and KOSDAQ.

## Independent earnings-yield source replay

An observed annual profit and current market cap can establish earnings yield
even when positive-equity PBR is unavailable. The authenticated supplemental
source replay now constructs this peer cohort independently. Negative or zero
profit remains observed, PBR remains unavailable, and equity-risk warnings are
not suppressed. Unobserved profit, invalid currency/scope/receipt, future
receipts and invalid market caps cannot contribute.

The original saved raw-report contract remains unchanged by default. The new
replay is explicitly selected only after independently checking the same-date
source receipt. This avoids invalidating the existing raw report and dropping
its financial observations during deployment. Existing financial values and
source hashes are independently replayed before the supplemented cohort is used.

An isolated replay of actual saved data, with zero provider calls, recovers
earnings-yield observation for34 issuers (56.666678 total allocated weight),
with zero issuers losing observed weight. Full observation remains556 because
these issuers have other missing components. This is an offline replay result;
actual new-source and deployed counts must be checked after publication.

Regression coverage includes all-listed multi-gap admission without EPS,
strict source identity, zero/loss versus missing profit, PBR independence,
future-receipt rejection, six bounded batches with no repeated identities,
source checkpoint preservation and isolated full publication. Synthetic values
used in tests are never provider evidence or published observations.
