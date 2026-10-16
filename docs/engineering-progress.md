# HOPE engineering progress

## October 16, 2026 - Batch A: evidence-preserving quarantine

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
The first push (`57dd1cbb4ff4da7dff4f567206340f76cdc112d0`) and its retry
could not initialize CI containers: ECR Public returned `toomanyrequests: Data
limit exceeded` before checkout or tests. CI now pulls the identical pinned
PostgreSQL digest from Docker Hub. Its registry manifest was verified by digest;
the database image, Python version, test command and coverage requirement are
unchanged. This is a repair of this batch's actual CI infrastructure failure.
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

1. Shared-portfolio investigation now has an executable reproduction; see the
   October 16 continuation below. Keep verifying supported writer boundaries
   before introducing any wider recovery capability.
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

## October 16 continuation - shared-portfolio reconciliation order

Starting HEAD 76d8fd734127cd859f5d898beee93d0f0c3408c1 was reverified against
successful exact-head CI run 38909626010 and issue #5 before work.

The initially suspected state of two complete CLAIMED owners sharing a portfolio
is rejected by the authoritative fill/accounting writer. Executable tests confirm
that it refuses to extend history owned by another CLAIMED job, preserving cash
and effects. No owner-allowlist bypass or relaxed accounting verification is needed.

A narrower, reachable batch defect was reproduced using only supported writers:
reconcile the first job, then persist the second job's complete accounting. A
mixed selection containing the first SUCCEEDED receipt and second CLAIMED job
fails if UUID processing order verifies the first receipt first. That receipt
checks the entire current portfolio and rejects the newer CLAIMED owner, even
though the same batch could safely reconcile that explicitly selected owner.

The fix keeps every job lock in UUID order, then processes selected CLAIMED jobs
before terminal receipt verification. All existing topology, lineage, accounting,
owner and immutable receipt proofs run unchanged under the original batch
savepoint. The batch still rejects omitted CLAIMED peers, partial evidence,
FAILED members and corrupt histories; any late verification error rolls back the
new transition and receipt. This neither discovers jobs nor reapplies accounting.

Regression scenarios cover deterministic UUID order, both input orders,
incomplete-member rollback, omitted peer refusal, concurrent retries, historical
proof failure after new receipt insertion, backend death before commit, and exact
cash/position/P&L/effect/receipt cardinality after restart. Local test results and
exact pushed-head CI are recorded in the completion report and Actions issue #5.

The existing AWS stored-data authentication limitation remains unchanged. No
AWS deployment, credential disclosure, risk/strategy changes or LIVE enablement
is part of this continuation.

Continuation validation: five new real PostgreSQL scenarios passed; the batch
unit suite passed 18 checks. The complete local Python 3.12 / PostgreSQL 17.11
run passed 2,401 tests in 212.14 seconds with 89.26% coverage (unchanged 89%
minimum), including all PostgreSQL tests. Three Bash-dependent contract files
remain excluded locally because this Windows host lacks Bash; exact-head Linux
CI executes them. The updated batch module has 100% statement coverage. No
existing test assertion, owner authorization, strategy or risk rule was relaxed.

## October 16 continuation — fresh PAPER control authorization

Starting live HEAD e6c0f510b173feb783c6e7d7b0bb18d1de3ed7ad was verified against
successful exact-head CI run 38923693777 and matching issue #5 before work.

Eight executable PostgreSQL cases reproduced a stale-snapshot authorization
defect: under REPEATABLE READ and SERIALIZABLE, a transaction observed RUNNING,
a peer committed HALTED, and assertions, operational claims or control writes
still used the old snapshot despite advisory serialization. The transactions
were deliberately rolled back during reproduction; no production state was used.

The control repository now requires READ COMMITTED before taking its control
lock for assert_running or transition. Migration 230 adds independent insert
isolation guards for operational paper: claims and control events, preserving
all existing guard functions rather than replacing their contracts. Observational
current_state reads remain available in repeatable/read-only transactions, and
generic non-PAPER job claims remain unaffected. See paper-control-isolation.md.

Focused validation passed 28 checks, including 16 real PostgreSQL cases covering
stale API/direct SQL paths, single/batch reconciliation, READ COMMITTED lock-wait
freshness, observational compatibility, generic jobs and killed-backend rollback
of an uncommitted HALT. The original eight cases failed before the correction.
Final review found no additional material issues in the bounded change. A fresh
local full-suite run precedes the commit; exact-head CI and marker must then be
verified before starting another batch.

No strategy/risk rule, PAPER-only policy, LIVE-disabled control, frozen baseline
or AWS deployment is changed. Authenticated AWS stored-data validation remains
blocked by the missing configured read-only session; no credential is exposed.

Fresh-database local validation completed: 2,423 passed in 228.52 seconds on
Python 3.12 / PostgreSQL 17.11, with 89.27% coverage against the unchanged 89%
minimum. All PostgreSQL integration tests executed. The three Bash-dependent
contract files remain excluded on this Windows host and run in exact-head Linux
CI. Focused control checks passed 28 tests. No test assertion or coverage minimum
was weakened, and the explicit migration inventory now includes 230.
