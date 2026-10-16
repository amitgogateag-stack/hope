# Atomic PAPER reconciliation batches

`reconcile_completed_paper_batch(connection, job_runs, current=..., max_runs=32)`
is an opt-in engineering boundary for explicitly selected interrupted PAPER jobs.
It does not discover jobs, execute strategies, change risk decisions, or enable LIVE.
The frozen `day29-green-baseline` branch remains at
`7e9d341742818a97accce3e2301b1c7f96596795`.

The batch consumes at most `max_runs + 1` input entries and rejects excess input,
empty selections, duplicate run IDs, non-PAPER identities, invalid clocks, and
limits outside 1–32 before issuing database operations. It takes the existing
PAPER control lock, checks RUNNING, then locks job rows in UUID order before any
proof. This keeps opposite-order batches consistent with the existing control
and effect-writer serialization protocol.

All selected runs must pass the existing exact topology, durable execution lineage,
accounting, terminal state and immutable audit receipt checks. Already reconciled
runs are reverified and returned separately. Missing, FAILED, partial, conflicting,
or unauthenticated runs abort the entire batch. A savepoint encloses every batch
transition and receipt so catching an error and committing the caller's outer
transaction cannot preserve a partial batch.

The caller owns the outer transaction. The result reports provisional changes;
only a successful outer commit makes them durable. Connection loss before commit
requires a fresh connection and re-verification of the same selection. Never infer
commit success from the returned result alone. The batch has a work-count bound;
operators should configure PostgreSQL statement/lock timeouts appropriate to their
maintenance window. There is no automatic retry, skip-locked selection, claim,
accounting repair, or scheduler activation.

PostgreSQL tests exercise two concurrent reversed selections, failure after the
first receipt while the caller commits, and termination of the reconciliation
backend after its first receipt and after batch return. They assert CLAIMED state,
zero receipts and unchanged effect cardinality after transaction loss, then exact
once success after restart. Tests terminate only their own captured backend PID.
