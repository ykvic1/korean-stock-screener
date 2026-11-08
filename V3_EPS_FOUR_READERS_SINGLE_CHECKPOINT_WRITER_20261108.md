# Four bounded original-EPS readers, one checkpoint writer

The first expanded live pass reviewed six issuers within its deadline despite
a sixteen-issuer request ceiling. It recovered partial EPS for one issuer,
with no newly complete three-year series. Source latency is a separate
bottleneck from source interpretation.

Use at most four concurrent original readers with separate thread-local HTTP
sessions. Keep the existing sixteen-issuer ceiling, six-minute submission
budget and 120-second reserve. The shared company map and selected records
are read-only. API-call accounting is synchronized. No extra retry or larger
per-pass issuer/provider-call budget is introduced.

Only the calling thread updates retained records, validates snapshots and
atomically checkpoints each completed issuer. A provider failure stops new
submissions, drains and checkpoints valid in-flight results, then surfaces the
failure through existing bounded retry/publication behavior. Failed requests
do not acquire negative review markers. Deadlines stop new reads without
discarding already started, independently verified source observations.

The shared EPS workflow writer lock, exact source admission and previous
review preservation, final publication, finite continuation and formulas are
unchanged. Offline tests cover actual concurrent readers with a single writer,
in-flight preservation on failure, stopped refill, deadline draining and fixed
worker/issuer caps. Live throughput must be measured after deployment.
