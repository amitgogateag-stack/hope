# PAPER control authorization and transaction snapshots

PAPER environment authorization and control transitions require PostgreSQL
READ COMMITTED. They fail closed under REPEATABLE READ, SERIALIZABLE or READ
UNCOMMITTED; callers must select a supported transaction before starting work.
The implementation never downgrades an active transaction's isolation.

The control journal is append-only. An advisory lock serializes competing
writers but does not refresh an already established repeatable snapshot. A
transaction can otherwise observe RUNNING, wait until a peer commits HALTED,
acquire the lock and continue trusting its old RUNNING snapshot. Executable
PostgreSQL tests reproduced this for assertions, direct operational claims and
repository/direct control transitions under both repeatable and serializable
isolation.

The repository checks isolation before acquiring the control lock in
`assert_running` and `transition`. Migration 230 adds independent insert guards
for the control journal and operational `paper:` job claims. It leaves existing
claim provenance, chronology, sequence readiness, no-op, resume and append-only
guards intact. Direct SQL cannot bypass the transaction contract.

`current_state` is an observation of the caller's snapshot, not authorization to
execute work. It remains usable in repeatable/read-only monitoring transactions,
including the dashboard exporter. Generic non-PAPER job claims are unaffected.
Single-run and batch reconciliation call `assert_running` before any proof or
transition and therefore reject stale-snapshot authorization too.

READ COMMITTED still uses the existing control advisory lock and fresh reads
after that lock is acquired. A peer that committed HALTED while a caller waited
is observed as HALTED and blocks authorization. No guard grants permission to
resume, replay trading work or clear an incomplete claim.

Tests use synthetic records in an isolated local database, restore RUNNING and
terminalize their own claims during cleanup. No production control state, AWS
resources, strategy/risk behavior or LIVE policy is changed by this work.
