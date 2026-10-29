# Persisted snapshot readiness attestation

The read-only AWS readiness probe now authenticates `/snapshot.json` in the same
observation window as release identity, PAPER heartbeat and `/status.json`.
Healthy process metadata and a recent status timestamp alone cannot establish
persisted database evidence. No AWS command or deployment was performed while
preparing this change.

The snapshot must use the existing bounded PostgreSQL aggregate schema, carry
the authenticated deployed commit, have a nonfuture generation at most fifteen
minutes old, and match the status document's exact generation instant. It must
contain internally consistent recovery counts. Durable HALT, claims with
effects, and claims older than one hour block readiness even when the PAPER
heartbeat reports CURRENT. Missing recovery evidence blocks AWS readiness;
unknown values are never interpreted as zero activity.

A valid zero-job observation or persisted NO_TRADE outcome remains legitimate.
Neither jobs nor fills are required to claim that the observed services are
healthy. The diagnostic grants no execution authority and performs no repair,
replay, order submission, service action or database write.

The file-serving and HTTP-attestation paths share one pure snapshot validator.
Duplicate JSON keys, nonfinite values, oversized documents and excessive nesting
are rejected. Output contains only reviewed aggregate fields; arbitrary input
metadata is not copied into reports. The new fetch uses the remaining portion
of the existing five-second HTTP observation budget, with a fixed private URL,
bounded output, no redirects, no proxies and no credential environment. Closing
systemd/release checks run after the snapshot fetch, so restarts or tampering
during this added observation still invalidate the complete report.

The local dashboard now labels stale claims without effects
`RECOVERY_REQUIRED`, rather than allowing a fresh successful poll to promote
them to READY. Durable HALT retains priority. The audit and browser recognize
that vocabulary, reject READY with unresolved recovery, and age cached results
independently. Existing legacy snapshots can still be viewed; missing evidence
never establishes reconciled execution or permission to replay.

During normal atomic export, the snapshot can advance between `/status.json`
and `/snapshot.json`. This yields `SNAPSHOT_STATUS_GENERATION_MISMATCH` and a
truthfully blocked observation. Retry the read-only diagnostic; do not weaken
the match or change host state to obtain green output.

Validation includes reproduced false-ready regressions, actual loopback HTTP
serving, malformed and ambiguous payloads, release/generation mismatch, bounded
deadlines, worker restart during snapshot observation, shipped browser behavior,
and PostgreSQL repeatable-read snapshots across a competing committed writer.
The PostgreSQL test uses an isolated disposable schema and separately proves
that the dashboard transaction cannot write. Historical stale claims remain
visible in the first transaction; the next transaction sees the completed job.

GitHub CI readiness remains separate from AWS deployment readiness. Use the
approved integration SHA and its exact-head bot/CI evidence for deployment
review. This feature branch and its PR do not authorize AWS activation.
