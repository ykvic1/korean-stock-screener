# Parent equity from the original annual filing

The first API sweep added five issuer values and published them, but 22
reviewed issuers lacked the standard API parent/NCI rows. Six other issuers
were deferred by source checks. Read actual originals for the unresolved,
receipt/CFO-anchored cohort instead of repeating the same absent API fields.

Require an official issuer profile, exact 2025 CFS CFO in the instance, KRW
units and exact 2025-12-31 instant contexts. The same instance's total equity
must match the independent annual book. Accept standard reported parent equity,
the explicit TOTAL-parent column on ComponentsOfEquityAxis, or total equity
less explicitly reported NCI. Other components, preference/subclasses, wrong
scope/period/entity, nil values, ambiguous facts and unanchored instances do
not qualify. No absent NCI becomes zero.

Original proofs have a separate contract and archive/profile hashes. All
values use the existing parent-equity overlay and unchanged PBR formula. Four
readers and one checkpoint writer share the API collector's writer lock.
Source reviews avoid duplicate archive downloads, batches continue only after
actual progress, and valid checkpoints publish before an interruption surfaces.

The API collector now isolates successful but inconsistent issuer responses
without treating them as missing facts. Mapping/identity/value/conflict
quarantines cannot add scores, do not block other issuers, and retry only on a
changed source anchor or next market day. Provider/auth/malformed failures
still interrupt; sanitized failure labels make genuine issues diagnosable.

Focused tests cover exact instant dimensions, explicit zero vs absent NCI,
entity/scope/currency, CFO/total/parent conflicts, original-proof tampering,
book immutability and global outages vs per-issuer rejection. Actual coverage
gains are counted after the original-file collection and score publication.
