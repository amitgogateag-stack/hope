# HOPE engineering progress

## October 16, 2026 — Batch A: evidence-preserving quarantine

Starting verified branch HEAD: `cae46ba2c1ddc0b37aab39e515af28fc8a685580`.
Its exact-head push CI and issue #5 marker were green before implementation.

### Assessment

The repository separates deterministic domain models, application PAPER/research
workflows, PostgreSQL repositories and migration guards, runtime/scheduler
composition, and read-only dashboard projections. Existing PAPER recovery checks
effect topology, execution lineage, economic history, and immutable receipts.
Research has PIT source verification, cost/slippage evaluation, universe
perturbation and walk-forward evidence boundaries. Operational scheduling has
explicit bindings, eligibility checks, calendars and durable PAPER controls.

The selected correctness defect is in stranded-claim quarantine. Previously the
runner read CLAIMED state then wrote FAILED without checking durable effects.
Even a fully committed fill/accounting lifecycle could lose recovery eligibility
because recovery deliberately rejects FAILED owners. A racing effect writer
could also cross that uncoordinated read/write boundary.

### Implemented boundary

The runner delegates quarantine to an atomic repository operation. It validates
the quarantine completion, authenticates and locks the durable job, rejects any
effect evidence, transitions an empty claim, and rereads the exact terminal
record. All steps run in a savepoint; errors caught by an outer caller cannot
commit a partial quarantine. Concurrent retries authenticate terminal identity
and return the existing terminal outcome. This neither proves the worker is
gone nor automatically quarantines jobs: the existing explicit caller obligation
to establish worker death remains.

Migration 229 independently rejects the reserved quarantine failure transition
when effects exist. It shares the existing job-row serialization protocol with
effect inserts. Quarantine requires READ COMMITTED in both the repository and
database trigger: executable adversarial tests demonstrated that REPEATABLE READ
and SERIALIZABLE snapshots can otherwise miss a peer's newly committed effects
when the job tuple itself is unchanged. Other isolation levels fail closed.
General failure handling remains unchanged. Quarantine never
replays trading work, repairs accounting, or writes reconciliation receipts.
Complete evidence must still pass authoritative reconciliation; partial evidence
remains CLAIMED for investigation instead of being relabeled FAILED.

### Validation coverage

Regression cases exercise empty claims, terminal retries across connections,
missing/forged identity, partial and complete evidence, direct database bypass,
failed terminal reread with outer commit, two simultaneous quarantine workers,
both effect/quarantine lock orderings, aborted effect transactions, backend death
before commit, and a genuine fill/accounting lifecycle whose cash, position and
P&L remain unchanged after rejected quarantine and successful reconciliation.

The previous Windows-only test limitation is reduced by a workspace-local,
isolated PostgreSQL 17 instance bound to loopback. It contains synthetic test
data only. Linux exact-head CI remains the authoritative Python 3.13/Bash check.

Final local validation: 2,395 tests passed on Python 3.12 / PostgreSQL 17.11 in
198.26 seconds, with 89.26% coverage against the unchanged 89% minimum. Three
Bash-dependent contract files were excluded because Bash is unavailable on this
Windows host; a separate dashboard compatibility/readiness run passed 65 checks
with two shell-only checks deselected. All PostgreSQL integration cases executed
locally. The stale-snapshot regression failed in all four API/SQL/isolation cases
before the isolation guard, then passed after it. The initial broad run also
caught the migration inventory's missing 229 entry; the explicit expected list
was extended and the fresh-database rerun passed. No test assertion was weakened.
Final code review found no additional material issue in the quarantine scope.
Exact pushed-head CI and its run URL/result are published by Actions in issue #5;
verify that its SHA equals the live branch before resuming the next batch.

## Parallel Batch D: dashboard upgrade compatibility

The deployed-upgrade acceptance probe still required the original exact PAPER
schema and rejected valid recovery-extended snapshots. The exporter also gained
two source tables without matching readiness/grant documentation. Probe validation
now accepts either exact schema, checks recovery count consistency and rejects
private/unknown fields. Readiness metadata and SELECT-only grant instructions
cover all five required source tables. Real loopback acceptance tests cover both
variants and malformed responses. No deployment or AWS permissions were changed.
Authenticated AWS stored-data validation remains unavailable without a configured
read-only connection.

## Next investigations

1. Reproduce shared-portfolio recovery of multiple complete CLAIMED jobs. Code
   review indicates per-job owner verification may reject a peer in the same
   locked batch. Existing batch fixtures use NO_TRADE; add genuine multi-job
   economic histories before choosing a change. Do not admit arbitrary CLAIMED
   owners or weaken public recovery ownership checks.
2. Batch B: trace PIT effective-time/freshness and immutable research provenance
   through cost/slippage, out-of-sample and walk-forward validation; implement only
   demonstrated defects or justified capability gaps.
3. Batch C: assess India/USA operational readiness without changing strategy/risk
   rules or promoting new strategy capabilities.
4. Batch D: authenticate actual stored-data access with a dedicated SELECT-only
   role, retaining loopback/tunnel access and approval-gated deployment.

## Resumption rules

Hourly automation resumes substantial batches across cycles; it must not create
an hourly commit merely for reporting. Fetch live HEAD, exact-head Actions and
issue #5 before new work. Wait for pending CI, repair actual failures, and publish
completed validated batches using expected-SHA, non-force updates. Record concrete
results and blockers here. Never modify `day29-green-baseline` at
`7e9d341742818a97accce3e2301b1c7f96596795`, enable LIVE, alter existing risk/strategy
rules, expose credentials or fabricate evidence. Confirm scheduler execution from
actual runs; creation alone is not proof that an unattended cycle has executed.
