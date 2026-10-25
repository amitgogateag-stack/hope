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

## October 16 continuation — elapsed-time market-data recovery integrity

Starting live HEAD 2cedb5289e1bbda380d9b06a452400fc896f7cf9 was verified against
successful exact-head CI run 38932193248 and the matching issue #5
HOPE_CI_STATUS_V1 marker. Frozen day29-green-baseline remains
7e9d341742818a97accce3e2301b1c7f96596795.

Independent regression execution against an archived copy of the starting HEAD
failed 16 of 17 new unit cases. Demonstrated defects include incorrect DST grid
counts, fold aliases bypassing duplicates or request echoes, reversed UTC
chronology accepted by MarketBar, and lifecycle gaps/overlaps judged by wall
clock. PostgreSQL tests additionally reproduced persisted coverage collapsing
distinct fold hours in a non-UTC client session.

The coordinated correction measures request duration, interval grids, provider
boundaries, echo identity, normalization keys/sorting, bar chronology and window
adjacency in UTC. Manifest strings and hashes remain byte-compatible. Persisted
coverage and sink effective-time retry comparisons also use UTC instants.
See market-data-instant-integrity.md for the boundary and compatibility contract.

Focused validation passed 47 unit checks and seven genuine PostgreSQL tests.
The latter independently generate both DST transition grids and exercise fresh
connection recovery, concurrent duplicate producers, exact persisted counts,
killed-backend rollback before sealing commit, sealing retries, immutable
manifest evidence and effective-time alias/conflict handling. Complete local
suite validation and exact pushed-head CI must pass before this batch is closed.

Fresh-database local full-suite validation passed 2,447 tests in 223.73 seconds
with 89.23% coverage, above the unchanged 89% minimum, on Python 3.12 and
PostgreSQL 17.11. All PostgreSQL tests executed. Three Bash-dependent dashboard
contract files are excluded only on this Windows host; exact-head Linux CI
executes them. No assertion, coverage threshold or authorization guard was
weakened. Complete source and new-test diff review and git diff --check passed.

No strategy/risk rule, NO_TRADE behavior, PAPER-only operation, LIVE-disabled
policy, frozen baseline or AWS deployment changes. Authenticated AWS stored-data
validation remains blocked by the missing configured read-only session.

Next bounded investigation: positively require a supported certified evidence
schema at the certified research-source resolver boundary. An isolated synthetic
reproduction found generic payloads with matching research identity and a
recomputed fingerprint could pass this boundary without certified execution
provenance. That separate defect is recorded for a subsequent green-head batch;
no production evidence or data was created or changed during the reproduction.

## October 16 continuation — recovered data, PAPER timing and bounded health reads

Starting live HEAD c644809f93137270362b45928a4b1034648b14bf was verified against
successful exact-head CI 38950466595 and matching issue #5 marker. User priorities
directed this batch to recovered data at the PAPER decision/execution boundary,
durable restart behavior and read-only operational visibility.

An isolated archived-head run failed 14 of the first 17 new timing unit cases.
PIT visibility, freshness and execution timing used wall-clock comparisons that
could admit future-fold information or undercount elapsed age/latency. Genuine
PostgreSQL tests additionally found sealed coverage rejection and successful
NO_TRADE restart failing identity checks under a non-UTC client timezone.

The coordinated correction uses UTC instants for PIT visibility/order/coverage,
unchanged PAPER freshness age, execution timeline arithmetic and quote bounds,
and durable schedule authentication. Future quote events cannot price a past
explicit fill. Original serialization, execution costs and strategy/risk rules
are preserved. See paper-recovered-data-timing.md.

Parallel dashboard work adds validated connection/statement/lock budgets with
transaction-local read-only settings. Real PostgreSQL tests force lock and
statement timeouts, prove prior snapshot bytes remain untouched, and verify a
later export recovers without leaking timeout settings to subsequent work.
No deployment or authenticated AWS data access was attempted. Browser freshness
aging and overlapping-refresh protection remain a documented next deliverable.

Focused checks passed 53 PAPER/execution tests including three PostgreSQL
recovered-data cases, plus 34 dashboard checks including three PostgreSQL cases.
The PAPER cases prove delayed visibility, stale rejection before side effects,
one NO_TRADE through concurrent execution and restart, and rejection of a forged
other-fold schedule. Complete fresh-database validation and exact-head CI must
pass before closing the batch. The research-schema admission investigation
remains recorded for a later bounded batch.

Fresh-database local full-suite validation passed 2,485 tests in 227.95 seconds
with 89.45% coverage against the unchanged 89% minimum, on Python 3.12 and
PostgreSQL 17.11. All PostgreSQL tests executed. The three Bash-dependent
dashboard contract files remain excluded on Windows and run in exact-head
Linux CI. Complete diff review and git diff --check passed. No production
data, credential, deployment, strategy/risk threshold or baseline was changed.

## October 16 continuation — honest browser freshness and serialized refresh

Starting live HEAD 580bcb56995adb02d3154890e527c43044ae2ddd was
verified against successful exact-head CI 38955744006 and the matching issue #5
marker before implementation.

The private dashboard previously computed snapshot age only when a response was
loaded. A stalled request could overlap later interval callbacks and leave an
old snapshot visually CURRENT until some request eventually completed. A failed
automatic refresh also cleared already verified aggregate data even though the
exporter deliberately preserves its last complete snapshot.

The browser now bounds every loopback JSON request to ten seconds, permits only
one in-flight refresh per endpoint, and re-evaluates the retained snapshot every
thirty seconds. Failed refreshes preserve the last verified aggregates while
showing the failure and original generation time; the existing one-hour age
boundary still moves the retained view to STALE. Missing initial data continues
to fail closed. Legacy research, health and PAPER feeds remain independently
serialized, size-bounded and schema-validated.

Local validation passed 127 focused dashboard/deployment checks and 1,988 unit
tests. The inline script passed Node syntax validation and git diff --check is
clean, including removal of a pre-existing extra test-file newline. No database,
AWS, credential, strategy, risk, PAPER execution or LIVE setting was changed.

## M1 integrated autonomous PAPER operations and private dashboard

Starting authoritative SHA 580bcb56995adb02d3154890e527c43044ae2ddd, successful
exact-head Actions run 38955744006 and matching issue #5 marker were checked
before implementation and rechecked during the milestone. Frozen baseline is
preserved. See m1-operations-milestone.md for architecture inspection, integrated
implementation plan, the required adversarial validation map and acceptance
boundaries. November 15 readiness depends on actual operator/data prerequisites.

Implemented an opt-in bounded service and CLI over existing operational registry,
calendar, ownership and scheduler contracts. Every poll checks exact schema
inventory/checksums and RUNNING authorization before reviewed real-data factory
resolution. SIGTERM, reconnection, sanitized atomic health, deployed commit and
schema observations are integrated. No production synthetic factory or candidate
promotion is supplied. Existing reconciliation/accounting protections remain
authoritative; partial evidence blocks replay.

The private dashboard now has independently aging observations, request deadlines,
serialized refreshes and generation/file fencing. Strict optional aggregate
projections cover research registration/verdicts, stored PAPER outcomes,
NO_TRADE, last completed job and market observations. Missing optional SELECT
permissions remain UNAVAILABLE through savepoint isolation. Portfolio and
unverified freshness/CI information never become fabricated zero/green values.
The service heartbeat is served through a separate sanitized read-only endpoint.

A new deploy/operations package provides isolated systemd services, disabled
installation, private SSH-tunnel access, role/release/schema preflight, resource
limits and expected-pointer code activation/rollback. Existing deployments,
roles, AWS infrastructure and historical data were not modified. The operator
acceptance checklist includes protected backup restore, real-data factory
review, HALT/restart/outage/reboot and independent accounting verification.

Closed the previously recorded positive-certified-schema admission gap. Two
synthetic unknown-schema payloads with valid hashes were reproduced as accepted;
the resolver now positively requires supported certification before independent
execution evidence verification. No research evidence was manufactured or stored.

Focused validation: service 37 tests including nine genuine PostgreSQL cases;
dashboard 206 tests with restricted roles, full-schema projections and actual
shipped JavaScript; deployment/admission 30 passed plus two Windows-unavailable
symlink cases reserved for Linux CI. Added an actual child-process CLI restart
test asserting one durable NO_TRADE/effect across termination and a fresh process.

Initial full regression found one collection-time clock-fixture defect after
2,591 passed tests. A future timestamp calculated during collection had become
past before its test executed. The fixture now calculates the relative time
inside the test; the production rejection assertion remains intact. Focused
rerun passed 26 checks. Fresh-database final regression and exact pushed-head
CI/issue marker verification must complete before M1 is called code complete.

Independent deployment review found and corrected three integration gaps before
publication: activation now holds an exclusive operation lease through stop,
switch, acceptance and rollback; acceptance requires a fresh successful
compatible PAPER-only heartbeat from the newly started process; host preflight
checks root/venv/parent ownership before executing its interpreter. Negative
heartbeat and concurrent-lock regressions were added. Stale deployment locks
after a killed operator process intentionally require explicit incident review.

Final frozen-source fresh-database acceptance passed 2,601 tests in 210.48
seconds with 89.40% coverage on local Python 3.12/PostgreSQL 17.11. Two new
symlink rollback cases cannot run on this Windows host and are exercised in
Linux CI. The existing three Bash-dependent dashboard contract files are also
CI-only locally. The actual child-process CLI termination/restart test passed.
Complete implementation/diff review and independent cross-component review are
complete; no coverage threshold or safety assertion was relaxed. Exact pushed
HEAD CI and its issue #5 marker remain the final publication gate. No AWS
deployment or operator acceptance is claimed by this local result.

Publication lease check detected concurrent authoritative commit
564e02d7c181928868884b4e6730206dbaa0ce0f before any ref update. Its exact-head
CI 38967678505 and issue #5 marker were verified green. That dashboard freshness
work and engineering record were integrated, including retained snapshots after
transient failures and the remote regression intent. The M1 generation fencing,
worker health and projections remain intact; merged focused browser/preview/
probe checks passed 70 tests. A fresh complete merged regression precedes the
new expected-SHA update; no concurrent GitHub work is overwritten.

Complete merged acceptance passed 2,602 tests in 250.24 seconds with 89.40%
coverage; the same two Windows symlink cases are reserved for Linux CI. The
final publication parent is the preserved concurrent green commit 564e02d.

## M2 certified research to autonomous PAPER — 2026-10-17

Original baseline 94ef7290d5766ae723cb669bd3ec8a13fd9c3745 was verified against
Actions 38969418657 and issue #5. Concurrent commits 6e55dcd and 235804e were
preserved; the final parent is 235804e44a659419f84a4b4ca44437af5e48b47e,
verified green against Actions 38999159872 and its exact issue #5 marker.
Frozen day29 SHA 7e9d341742818a97accce3e2301b1c7f96596795 remains unchanged.

A real PostgreSQL reproduction showed old registry admission surviving a
committed demotion (one job/effect/NO_TRADE, no authorization receipt). The
certified bridge now revalidates current promotion epoch, market, SUPPORTED
decision and both experiment invalidations under control-then-candidate locks
before claim, in the execution transaction. Migration 232 records immutable
authorization. Fully certified plans avoid the generic scheduler connection's
candidate lock, which would self-block their own execution connection; generic
compatibility plans retain the concurrent upstream catalog protection. Mixed
plans fail before claims. The production CLI always requires certified bindings.

The bridge independently reconstructs both certified research sources, protocol,
nine evaluation stages and comparison, exact version/code/configuration identity,
PIT universe and sealed provider-compatible execution data. It reuses existing
signal/risk/intelligence, order, cost-inclusive fill and accounting primitives.
The bounded path supports zero signals or one ENTRY; other plans and missing
reviewed performance anchors fail closed. No risk thresholds, entry/exit rules,
promotion verdicts, LIVE policy or broker integration were changed.

Migration 233 binds observational NO_TRADE/RISK_BLOCKED/FILLED outcomes to the
actual authorization, research source, input hash, effect topology and certified
dataset/universe. Review found and closed direct-insert hash/dataset gaps before
publication. Successful service cycles independently authenticate execution and
accounting before writing the existing reconciliation receipt. Lost postcommit
acknowledgements recover that receipt without strategy or accounting replay.
Verified research source timestamps now compare instants across PostgreSQL
session timezones while preserving immutable fingerprint spelling.

Dashboard projections add bounded strategy family/version catalogs, dataset
certification metadata, authorization history, certified outcomes, safe risk
reasons and fill/application/P&L gaps. Missing or partial evidence stays unknown
or unavailable; historical permission is never current eligibility. JSON remains
32 KiB bounded; the tested HTML limit is explicitly 64 KiB. No credentials,
deployment, public endpoint or AWS infrastructure changes were made.

Final merged focused acceptance: 103 passed in 34.30 seconds, including genuine
PostgreSQL authorization, bridge, service/child-process restart, dashboard and
preserved generic orchestration regressions. The bridge matrix includes India/
USA NO_TRADE, risk rejection and cost-inclusive fills, exact cash/positions/P&L,
demotion/invalidation/isolation races, worker contention, backend termination
before commit and during postcommit audit, evidence/configuration/provider/
instrument mismatches, stale/future-effective/missing quotes, direct receipt
forgery and immutable history. Explicit market calendars cover US DST transitions,
independent India UTC sessions, closures, duplicate schedules and session close.

Final frozen-source fresh-database regression: **2,717 passed, 2 skipped in
195.44 seconds; coverage 89.82%**, Python 3.12/PostgreSQL 17.11, unchanged 89%
gate. The two symlink tests and three Bash-dependent dashboard contract files
remain Linux-CI-only locally. Earlier runs were stopped for concurrent upstream
integration and a correctly detected migration checksum change; no failed or
interrupted run is reported as acceptance. Complete diff and independent review
are finished. Exact pushed-head Actions and issue #5 remain the publication gate.

AWS operational acceptance remains pending. The operations package documents
approved migrations 231–233, least-privilege grants, genuine feed/candidate/
calendar/configuration dependencies, risk-anchor resolution, compatible rollback,
restart/HALT/outage checks and private dashboard acceptance. No AWS success or
trading edge is inferred from synthetic engineering fixtures or GitHub CI.

## Historical certified recovery after demotion — 2026-10-17

Fetched live branch 9d16f72ee3443405b04aff15ca146011a6373841, verified exact-head
Actions 39000192931 green and the matching issue #5 HOPE_CI_STATUS_V1 before work.
The prior M2 batch is synchronized; this batch addresses a separate recovery gap.

A genuine PostgreSQL reproduction committed a filled certified run, then demoted
its strategy before the service audit step: SUCCEEDED, five effects, zero
reconciliation receipts and one strategy call. Ordinary admission correctly
returned PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE, preventing that service path
from finishing the historical audit. Accounting stayed intact. No current
eligibility rule should be relaxed to repair already-committed history.

Added explicit recovery of at most 32 selected historical certified IDs and a
reviewable incident CLI. It verifies original authorization/classification,
SUPPORTED variant, immutable configuration, sealed PIT provider/universe metadata,
actual input hash, latency, and filled portfolio identity. It uses the existing
reconciliation and ordinary-success authenticators for execution/accounting
proof, completion and audit. It does not invoke strategy/factory code, discover
work, claim jobs, create effects, reconstruct accounting or grant fresh permission.
Current demotion or experiment revocation does not erase legitimate history.

All selected job locks follow canonical order after the fresh RUNNING control
proof; completed CLAIMED owners reconcile before SUCCEEDED audit verification.
The whole batch shares a savepoint and outer caller commit. Missing, failed,
partial or uncertified history aborts the selection. HALT and non-READ-COMMITTED
transactions remain blocked. CLI success is emitted only after commit ACK;
database/configuration/payload errors remain sanitized. No automatic timer,
deployment, migration, role or AWS changes were made.

Focused acceptance: 42 passed in 12.99 seconds (28 genuine PostgreSQL cases plus
14 command/bounds tests). Cases include NO_TRADE/risk-blocked/fill histories,
both research revocations and demotion, CLAIMED/SUCCEEDED histories, old runs,
repeat recovery, competing reversed selections, all-or-nothing later failure,
backend termination before commit, real subprocess CLI retry, missing/partial/
generic history rejection, HALT, isolation, bounded infinite input, sanitized
errors and lost commit acknowledgement. New execution after revocation still
fails its independent authorization proof.

Final frozen-source fresh-database local regression: **2,759 passed, 2 skipped in
214.37 seconds; 89.82% coverage**, unchanged 89% gate. Two Windows symlink skips
and the existing three Bash-dependent contract files require exact-head Linux
CI. Complete diff reviewed; publication uses an expected-SHA non-force update
and exact-head Actions/issue #5 verification. No interrupted run is acceptance.

See docs/paper-historical-recovery.md and the operations README for explicit
selection, commit/retry semantics and staging acceptance. AWS operational
acceptance and actual provider/candidate/configuration prerequisites remain
pending. Existing risk/strategy behavior, PAPER-only/LIVE-disabled policy and
frozen day29 commit 7e9d341742818a97accce3e2301b1c7f96596795 are preserved.

## Post-switch observability identity — 2026-10-17

Fetched live HEAD 85673e14d2aa79c1a85ff07fa3c7d321f59ac618, its exact-head
Actions 39020392867 success and matching issue #5 marker before work. Preserved
that concurrent dashboard activation gate with a fast-forward update.

Reviewed shared-portfolio recovery first. Existing accounting ownership rejects
extending a portfolio whose earlier owner remains CLAIMED, and existing real
PostgreSQL normal/reversed/failure/crash/concurrent tests cover that boundary.
No accounting or risk behavior change was warranted.

Demonstrated a separate activation defect offline: a snapshot generated four
minutes before activation, rewritten afterward, returned accepted=True because
generation allowed a five-minute pre-switch tolerance. The HTTP probe also
checked feed availability without proving it presented that selected export.

Activation now requires snapshot generation at/after its start and no later than
the current clock. It supplies the verified sanitized payload to a strict probe
that requires CURRENT health, matching health/snapshot generation and equality
of the complete served snapshot to the selected local export. Same-time changed
counts and export replacement fail closed. Existing bounded polling can retry
an export rotation; timeout preserves existing stop, pointer/metadata rollback
and operator-review contracts. Ordinary upgrade/retained-feed behavior is
unchanged, as are 32 KiB JSON / 64 KiB HTML limits and loopback-only transport.

Focused validation: **101 passed in 2.75 seconds**, covering rewritten old and
future exports, stale/unavailable health, mismatched generations/payloads,
same-time changed counts, invalid/oversized selection, exact probe arguments,
rollback, and a real loopback server with replacement and successful reselection.
No systemd or AWS host action was executed by these tests.

Final frozen-source fresh-PostgreSQL regression: **2,785 passed, 2 skipped in
215.59 seconds; 89.83% coverage**, unchanged 89% requirement. Two Windows symlink
skips and the existing three Bash-dependent dashboard contract files remain
exact-head Linux CI checks. Complete production/test/documentation diff reviewed;
expected-SHA non-force publication and exact pushed-head Actions/issue #5
verification remain final gates. No deployment, credentials, database migration,
strategy/risk changes or LIVE activation occurred. AWS operational acceptance is
still pending; frozen day29 SHA 7e9d341742818a97accce3e2301b1c7f96596795 is preserved.

## 2026-10-17: complete canonical certified backtest admission

Starting GitHub HEAD: `27213a5e4d58a7768efcfd96c12fd6f2d6a8f8aa`.
Exact-head Actions run `39023925611` and issue #5 Bot `HOPE_CI_STATUS_V1`
reported success before work and were rechecked before publication.

Demonstrated defect: the independent certified-source resolver accepted a
freshly hash-matching nested backtest containing only its schema label, or only
`result: {metrics: {}}`. Provenance and content identity did not validate result
structure. Certified admission now round-trips the complete nested result through
the existing typed BacktestResult and canonical projector. Missing/extra fields,
invalid nested domain models, non-finite decimals and noncanonical wire values
fail closed before implementation factories and PAPER claims. Fixed service error
mapping exposes only PAPER_CERTIFIED_INPUT_UNAVAILABLE. Valid producer bytes,
strategy/risk rules and scientific evaluation thresholds are unchanged; this does
not independently recompute profitability or prove research edge.

Tests include a genuine cost-inclusive engine round-trip and three PostgreSQL
counterfeit admissions with matching fingerprints, complete lineage and explicit
synthetic reviewer verdicts under all existing database guards. Rejection leaves
zero job, authorization, effect, outcome or portfolio rows. Existing certified
execution and historical recovery suites remain controls. No synthetic test data
is production evidence; no immutable research or PAPER history is rewritten.

Validation:
- Focused unit: 88 passed (1.83 seconds).
- Fresh PostgreSQL admission/bridge/historical recovery: 65 passed (26.86 seconds).
- Final full fresh-PostgreSQL regression: 2,815 passed, 13 skipped (222.06 seconds),
  89.33% coverage against the unchanged 89% gate.
- The first full run exposed 11 Windows capability failures in the concurrent
  parent's Linux activation tests. Capability skips now identify absent /proc,
  POSIX ownership and symlink privileges; production lease logic and all original
  assertions are preserved. The focused platform suite passed 14 tests with 13
  skips. Linux CI runs those cases plus the existing three Bash-dependent
  dashboard contract files omitted locally.

Complete production/test/documentation diff reviewed; expected-SHA non-force
publication and exact pushed-head CI/issue #5 verification remain final gates.
No AWS deployment, role/network changes, credentials, migrations or LIVE
activation occurred. AWS operational acceptance remains pending. Frozen day29
SHA `7e9d341742818a97accce3e2301b1c7f96596795` remains preserved.

## 2026-10-17: recovered feed PAPER timing and restart validation

Parent GitHub HEAD `2562e287d6b03416a1ee2b2a7aedd899eb010d7b`, exact-head
Actions `39027230347` and matching issue #5 Bot HOPE_CI_STATUS_V1 were verified
green before work. Recovery receipt inspection confirmed the existing writer
already isolates insertion and authenticated reread in a savepoint; no redundant
production repair was made.

Added 23 genuine PostgreSQL integration cases for sealed recovered same-source
feed vintages through certified PAPER admission, freshness, execution and restart.
Paired exact-cutoff / one-microsecond-late availability, ingestion and effective
timestamps exercise both decision and fill boundaries. Exact 120-second freshness
and one-microsecond-stale cases prove freshness precedes strategy evaluation.
NO_TRADE retains its existing independence from unavailable execution quotes.
Terminal polls preserve exact durable payloads rather than reinterpret past data.

Overlapping FILLED and NO_TRADE workers are held at the first transaction's
uncommitted outcome. The observer proves the second worker is actually blocked
by the first using pg_blocking_pids, then verifies one execution and one terminal
skip. Backend termination after effects but before outcome insertion leaves zero
job, authorization, effect, outcome or portfolio rows. Retry uses identical PIT
inputs and commits once, with exact durable snapshots stable on restart. All
existing PostgreSQL guards remain enabled; research vintages remain immutable.

No production defect was reproduced by this matrix. This is a coordinated
regression-validation batch, with no policy or strategy/risk implementation change.
Focused suite: 23 passed in 12.79 seconds. Full frozen-source fresh-PostgreSQL
regression: 2,838 passed, 13 skipped in 392.48 seconds; 89.33% coverage against the
unchanged 89% gate. Existing Windows platform skips and three Bash contract files
omitted locally remain exact-head Linux CI checks. Initial local server startup
failed before the focused run; corrected startup/database creation and rerun
completed successfully. No evidence from that failed setup is counted as a pass.

Complete test/documentation diff reviewed. Expected-SHA non-force publication and
exact pushed-head CI/issue #5 verification remain final gates. No deployment,
credentials, role/network changes, migrations or LIVE activation. AWS operational
acceptance remains pending. Frozen day29 SHA
`7e9d341742818a97accce3e2301b1c7f96596795` is preserved.

## 2026-10-17: guarded stale activation lease recovery

Starting GitHub HEAD: `2562e287d6b03416a1ee2b2a7aedd899eb010d7b`.
Exact-head Actions run `39027230347` and issue #5 Bot `HOPE_CI_STATUS_V1`
reported success before work.

The prior authenticated read-only diagnosis proved when an interrupted activation
was safely still at its original pointer, but offered no bounded recovery action;
an operator otherwise had to delete the retained lease manually without an atomic
recheck. The new explicitly approved root command accepts only a safely stale lease
whose embedded expected/target commits exactly match operator arguments. It stops
and verifies every M1 unit, rechecks the exact file fingerprint and every release
fact after quiescence, durably removes only that lease, and leaves services stopped.
Active, unknown, mixed, pending, mismatched or changed states fail closed. No
pointer, deployment metadata, database state or service is started or repaired.

Adversarial tests cover active and reused identities, wrong commit arguments,
mixed metadata, pending switches, stop failure, post-stop state change, root
authorization and a unit that remains active. The success path proves pointer and
metadata bytes are unchanged. No systemd or AWS action is performed by tests.

Validation: **59 focused tests passed** and the complete unit regression passed
**2,231 tests**. Python compilation and `git diff --check` also passed. This
host-filesystem/systemd boundary does not require PostgreSQL and no local
PostgreSQL test was claimed. Expected-SHA non-force publication and exact-head
CI/issue #5 verification remain final gates.

## 2026-10-17: serialize activation and stale recovery lease mutation

Fetched and fast-forwarded live GitHub HEAD
`2e9f37900007128af575a3de5cdabd9782b7637e`. Exact-head Actions `39039990447`
and matching issue #5 Bot HOPE_CI_STATUS_V1 were green before code modification.
That parent added explicit stale activation recovery.

Reproduced a lease pathname race with the actual recovery function: after its
final fingerprint read, a competing recovery can remove the stale inode and a
new activation can create a replacement. The first recovery then unlinks the
replacement and returns RECOVERED. The controlled interleaving printed
`RECOVERED replacement lease exists: False`. Fingerprint equality cannot provide
atomic compare-and-unlink. No real host activation or service action was used.

Activation and stale recovery now hold the same nonblocking kernel flock on a
persistent .activation.guard inode across all lease mutation. The guard is never
unlinked by these operations. Contenders fail before entering activation or
quiescence. Kernel release on process death preserves the separate authenticated
lease for explicit recovery. Existing fingerprint, process identity, stopped-unit,
release-pointer and metadata checks remain in place. Guard metadata rejects
foreign owners, writable-by-others files, nonregular files and hard-link aliases;
O_NOFOLLOW refuses symlinks. Read-only diagnosis remains read-only. The runbook
requires every activation/recovery writer to use this guarded version and root;
manual replacement/removal of the guard is unsupported.

Added 14 guard tests: independent descriptors, persistent inode reuse, exceptional
exit, both mutation entrypoints, complete activation lifetime, untrusted metadata,
link aliases, actual Linux subprocess death and stale recovery contention. Windows
uses an explicit test-only kernel byte-range lock adapter and modeled POSIX mode
metadata for orchestration tests. Native Linux flock, link and recovery tests run
only in Linux CI; these are not claimed as locally executed Linux evidence.
Focused suite before the final Linux-only link additions: 26 passed, 22 skipped
in 8.46 seconds. Initial focused failures were Windows mode-bit fixture assumptions;
the adapter was corrected without weakening production validation.

Final frozen-source fresh-PostgreSQL regression: 2,850 passed, 24 skipped in
371.14 seconds; 89.22% coverage, unchanged 89% gate. Existing three Bash dashboard
contract files omitted locally remain Linux CI checks. Complete diff reviewed;
expected-SHA non-force publication and exact pushed-head CI/marker remain final
gates. No PAPER strategy/risk/accounting change, database migration, credentials,
AWS/host deployment or service operation occurred. LIVE remains disabled. AWS
operational acceptance remains pending. Frozen day29 SHA
`7e9d341742818a97accce3e2301b1c7f96596795` remains preserved.

## 2026-10-17: consolidated AWS host operational readiness verdict

Starting GitHub HEAD `1c8564b158ccf83f6e2d7cd54d0fc452aefb3bcc`, exact-head
Actions run `39043342388` and matching issue #5 status were green before work.
Authenticated read-only EC2 inspection demonstrated a real acceptance gap: the
legacy dashboard service returned HTTP 200 on private port 8787 while its snapshot
was absent, the export timer and autonomous PAPER worker were not installed, and
the checkout was older than current GitHub. PostgreSQL itself was accepting local
connections. HTTP reachability alone was therefore not an operational dashboard.

The metadata-only EC2 diagnostic now consolidates source identity, clean-state,
legacy and M1 service installation/active/enabled state, snapshot metadata,
loopback/public bindings, required database tables, migration ledger, dashboard
role flags, memory, swap and root-disk capacity. It produces separate legacy and
M1 verdicts and cannot report an operator-acceptance candidate unless the complete
M1 service/feed/database/private-binding conjunction is proven. A hermetic shell
simulation reproduces the observed HTTP-200/no-snapshot/no-worker state and proves
the result is `WEB_ONLY`, `NOT_INSTALLED`, and globally `BLOCKED`.

No AWS resource, service, role, credential, database row or checkout is changed by
the diagnostic or tests. No deployment is claimed; host activation still requires
an approved immutable release, backup/role review, real-data factory, resource
decision and explicit operator acceptance.

Validation: **7 readiness contract/behavior tests passed**, the broader private
dashboard/deployment suite passed **187 tests**, and the complete local unit suite
passed **2,247 tests** after installing the local package required by the existing
Linux subprocess guard test. Its initial dependency-only run passed 2,246 tests
and failed only that subprocess import; no production or assertion change was
made. Shell syntax and `git diff --check` are final local gates. This diagnostic
does not require PostgreSQL fixtures and no local PostgreSQL test is claimed.

## 2026-10-17: bounded read-only PAPER recovery inspection

Live GitHub parent `f17a54929a8ecb7745a62c118893b55494aeb2f1`, exact-head
Actions `39044411660` and matching issue #5 Bot HOPE_CI_STATUS_V1 were verified
green before work. The previous recovery/control investigation executed 55
PostgreSQL tests successfully and confirmed that HALTED recovery/resume restrictions
are explicit policy, not a defect to bypass. This batch adds separate observation
so operators can inspect selected incomplete runs without relaxing those guards.

Added hope.api.paper_recovery_inspect and bounded inspection of up to 32 explicit
canonical UUIDs. Both CLI and function require a READ ONLY, REPEATABLE READ
PostgreSQL snapshot; the CLI also verifies exact schema compatibility and bounds
connection/query/lock waits. Observations include recognized lifecycle, shared
effect-topology classification, counts and authorization/outcome/reconciliation
presence. MISSING, OUT_OF_SCOPE and INVALID identities are explicit. Every result
retains proof=NOT_VERIFIED and recovery_authorized=false. Presence/COMPLETE topology
never authenticate lineage, accounting, eligibility or receipt payloads. There is
no discovery, claim, execution, reconciliation, quarantine, resume or writer path.
Counts are not expanded unboundedly; overflow consumes at most 33 generator values.
Job keys, payloads, input hashes, principals, free-text failure reasons and driver
exceptions are not output. No dashboard CURRENT status is inferred.

Genuine PostgreSQL tests cover HALTED observation; FILLED, NO_TRADE, RISK_BLOCKED;
maximum selection and missing/out-of-scope IDs; actual read-only write rejection
(SQLSTATE 25006); inappropriate transaction modes; and a peer transaction that
terminalizes a job and HALTs control between observation queries. The peer commits
without inspection lifecycle/control locks. One result remains a consistent prior
snapshot and the next sees the new state. Durable row counts and strategy call
counts remain unchanged by inspection. These are synthetic test fixtures, not
production evidence. Unit tests cover bounded generators, canonical IDs, duplicate
selection, missing configuration and sanitized driver failure.

Focused: 22 passed in 3.80 seconds. Final fresh-PostgreSQL regression: 2,873 passed,
24 platform skips, one Bash behavior case deselected in 242.80 seconds; 89.25%
coverage against unchanged 89% gate. Three existing Bash contract files were
omitted locally. The parent added a Bash behavior case unavailable on this Windows
host; the initial full attempt was stopped and the final run used a fresh database
with only that case additionally deselected. Linux CI runs all of these. An initial
focused collection collision was fixed by giving the unit module a unique name.

Complete production/test/documentation diff reviewed. Expected-SHA non-force
publication and exact pushed-head CI/marker remain final gates. No database
migration, role/grant change, credentials, AWS/host deployment or service operation.
PAPER-only, LIVE disabled, risk/strategy rules, NO_TRADE and frozen day29 SHA
`7e9d341742818a97accce3e2301b1c7f96596795` preserved. AWS operational acceptance
remains pending. The new command is observational and is not recovery authorization.


## 2026-10-17: truthful terminal health for PAPER acceptance probes

Exact live parent `c07fd9b75850ffafac9ef0be8adcfcaa37438d48`, Actions run
`39060447033` (#1282) and the matching issue #5 HOPE_CI_STATUS_V1 marker
were green before work. AWS operator evidence showed the private dashboard active
and the PAPER worker inactive, while the M3 runbook requires one reviewed
`--once` cycle before autonomous enablement.

A successful one-shot previously published CURRENT and exited immediately, leaving
a dead acceptance process represented as current worker health. The service now
preserves the successful cycle's schema proof, timestamp and counters, then
atomically publishes STOPPED before returning. Failed and HALTED probes retain
their fail-closed diagnostic state. The operator runbook now pairs this truthful
terminal heartbeat with explicit read-only recovery inspection and separately
approved certified reconciliation; neither grants autonomous acceptance.

Unit coverage asserts the complete STARTING/CURRENT/STOPPED publication order,
terminal file contents, successful evidence retention and exact counters. No AWS
operation, database write, migration, strategy/risk change or service activation
is part of this source change. PAPER-only and LIVE-disabled markers and frozen
day29 SHA `7e9d341742818a97accce3e2301b1c7f96596795` remain unchanged.


Exact-head CI #1283 exercised 2,911 tests and reached 89.99% coverage, but
correctly failed one pre-existing PostgreSQL integration assertion that still
expected a successful one-shot to leave CURRENT. Production behavior and the new
unit contract produced STARTING, CURRENT, STOPPED as intended. The repair updates
that integration contract to require STOPPED plus retained last-success evidence;
no assertion or production safety gate is removed.

## 2026-10-17: M3 isolated acceptance tooling and operational visibility

The user supplied M3 and clarified that only AWS Console / EC2 Instance Connect
is approved; there is no confirmed SSH alias, no approved real PAPER factory and
no authorization for AWS execution, host changes or persistent startup. Those
boundaries remain intact. Browser inventory had no authenticated AWS tab. No AWS
command, broker action, host/service change or production DB mutation occurred.

Live HEAD had advanced from the historical f17a549 to
c6aeb8a358f6ee450df2f6d93260e9b0a55c9e4c. Earlier 32fea974 failed CI; its correction
was already pending at HEAD. No changes were stacked while CI was pending. Exact
run 39061927000 and the matching Bot issue #5 marker then succeeded; the clean
checkout was fast-forwarded before implementation. Frozen day29 remains
7e9d341742818a97accce3e2301b1c7f96596795. Repository migration count remains 233;
no migration was added.

M3 adds a fixed-repository HTTPS release attestation that checks live branch HEAD,
frozen baseline, completed successful HOPE CI push run/attempt and the matching
Bot marker. Reads are bounded and redirects refused. An isolated acceptance CLI
requires explicit one-cycle approval, database hope_m3_restore_test, PAPER mode,
LIVE=false, deployed SHA identity, a reviewed factory, one selected UUID and a NEW
private evidence directory outside the release. It verifies clean exact release,
scoped role and exact migration checksums before work; wrong DB/role/SHA/selection
and multi-run plans fail closed. The wrapper has a 180-second budget plus 10-second
forced-stop ceiling and never invokes systemd. It does not bundle a real or
synthetic production factory.

Acceptance reuses the existing certified scheduler/service/risk/fill/accounting
path. It requires one executed job, truthful STOPPED health, a restart with one
terminal skip and explicit authenticated historical reconciliation. NO_TRADE,
RISK_BLOCKED and FILLED are supported without threshold changes. Fresh invocation
cannot reuse a prior committed run. Concurrent invocations produce at most one
execution; a loser remains blocked. A post-commit proof failure preserves durable
history and produces no acceptance JSON; it never claims a rollback. Evidence
retains dashboard_review=PENDING and operator_review_required=true, rather than
claiming M3 acceptance.

The read-only m3_audit CLI checks isolated DB/version/checksums/scoped role, fixed
systemd metadata and loopback HTTP with sanitized bounded output. New
/readiness.json and the Safety view distinguish HTTP liveness, snapshot freshness,
missing/unreadable/invalid/stale health, reported worker state, last successful
poll versus persisted PAPER job, and within-process last failure timestamp/code.
Reconciliation and DB connectivity remain explicitly observational/unverified.
The shared closed error vocabulary prevents free-text disclosure. Existing
/operations.json keeps its 503 fail-closed behavior. No synthetic health is created.
The historical AWS 503 cause remains unverified; inactive PAPER plus absent health
is a hypothesis, not a confirmed diagnosis.

New reconciliation report separates historical AWS PostgreSQL 16 / 233 migrations /
62 test passes / endpoint observations from current repository and local evidence.
The operator runbook and unset-by-default environment checklist define exact gated
command syntax, factory/data/session prerequisites, private SSH-tunnel boundary,
rollback/HALT procedures and separate persistent-start approval. Actual runnable
factory/run selection and AWS actions remain blocked on human review/authorization.

Focused final M3 suite: 122 passed, 2 Linux wrapper skips in 16.80 seconds. Genuine
PostgreSQL tests execute as a non-superuser scoped writer, covering all three
outcomes, no-replay restart/reconciliation, competing invocations, wrong target,
privileged role, wrong release identity, wrong/multiple runs and postcommit proof
failure. The initial isolated role fixture lacked SELECT on control sequences;
that fixture was corrected (no AWS/production grant change). Full frozen-source
fresh-PostgreSQL regression: 2,929 passed, 26 platform skips, one Bash case
deselected in 388.79 seconds; 89.03% coverage against unchanged 89% gate. Existing
three Bash contract files are omitted locally; Linux CI runs all omitted/skipped
platform cases. The shipped JavaScript runs in Node behavioral tests, including
readiness aging and fail-closed field validation. Local loopback browser Safety
view verified HEALTH_MISSING with web RUNNING, feed UNAVAILABLE and no authorization.
Local preview/server and PostgreSQL were stopped after validation.

The complete source/test/template/documentation diff was reviewed. Expected-SHA
non-force publication and exact pushed-head CI/marker are final gates. No local
PostgreSQL 17/synthetic result establishes AWS PostgreSQL 16 acceptance. M3
CONTROLLED PAPER ACCEPTED is not claimed. AWS audit, real factory review, one-cycle
operational authorization and private dashboard evidence remain explicit blockers.

## 2026-10-17: preserve sanitized M3 audit diagnostics

Before edits, GitHub live p0-pit-context HEAD was
1c0bd9a0e28c78c2996ebd0f658a7dc24b385484, with exact-head HOPE CI
39064093164 attempt 1 successful and matching issue #5 Bot HOPE_CI_STATUS_V1.
The frozen day29-green-baseline remained
7e9d341742818a97accce3e2301b1c7f96596795. The local checkout was clean.

Demonstrated defect: feeding the actual missing-health readiness body to the old
audit returned only http_status=200 and paper_health=UNAVAILABLE. It dropped
HEALTH_MISSING, server liveness, reported worker state, snapshot/database and
reconciliation observations, timestamps and failure history. An operator using
the sanitized audit could not retain the cause already exposed by readiness.

The audit now validates the versioned observational readiness contract, exact
PAPER-only/LIVE-disabled/execution-unauthorized booleans, closed vocabularies,
timezone-aware canonical timestamps, temporal ordering and failure-history pairs.
It retains safe observations and normalizes unknown failure details to the shared
generic code; arbitrary fields/strings are discarded. Contradictory READY,
future-dated, malformed, oversized and invalid safety responses fail closed with
INVALID_RESPONSE while preserving the actual HTTP status. Responses over five
minutes old yield STALE_RESPONSE without replaying health claims. Queries are
restricted to the four existing fixed loopback paths; redirects remain refused.
The runbook explains HTTP liveness versus body validation and observations.

Added 43 adversarial/HTTP cases. Focused audit/readiness/attestation/acceptance-CLI
suite: 87 passed in 6.83 seconds. Real local HTTP cases cover missing/invalid health
(operations 503), stale health, STOPPED, HALTED, current observations and redirect
refusal. Initial test setup errors (bytes fixture and its default HALTED state,
plus an oversized pytest-generated Windows filename) were corrected in fixtures;
no production behavior was loosened. Before/after reproduction confirmed reason
HEALTH_MISSING and worker UNKNOWN now survive with execution_authorized=false.

Full frozen-source fresh PostgreSQL 17 regression: 2,972 passed, 26 platform skips,
one Bash behavior deselected in 347.78 seconds; 89.10% coverage versus unchanged
89% gate. Three Bash contract files were omitted locally; Linux CI remains the
gate for these and platform skips. Complete source/test/runbook diff reviewed;
publication uses expected-SHA non-force branch update and exact pushed-head CI.
This batch changes audit reporting only: strategy/risk/NO_TRADE, execution,
database schema and deployment behavior remain unchanged. No AWS actions,
credential exposure or PAPER cycle occurred. Actual AWS diagnosis, reviewed
factory/run selection and separate execution authorization remain pending; M3
operational acceptance is not established by these local tests.
## 2026-10-17: guarded M3 dashboard-only activation

The prior full-release activation path moved the shared `current` symlink and
started PAPER, so it could not safely activate only the staged M3 dashboard. The
new workflow introduces a root-owned `dashboard-current` pointer, separate
dashboard deployment metadata, a fixed loopback-only M3 web unit, and a dedicated
authenticated operation lease. It never references the PAPER unit and leaves the
snapshot exporter/timer unchanged.

Activation validates both exact immutable releases and the existing bounded fresh
snapshot, installs only an exact reviewed unit, stops the two dashboard web unit
identities, atomically switches the dedicated pointer, and verifies the exact
selected snapshot plus `/readiness.json`. Readiness must continue to report
PAPER-only, LIVE disabled and execution unauthorized; missing PAPER health remains
truthfully unavailable. Failed acceptance restores the old pointer/metadata and
restarts the legacy dashboard. A process kill retains a component-specific lease;
approved recovery authenticates stale process identity and exact SHAs, rejects
ambiguous paths, restores the expected dashboard pointer and starts only the old
web service. The shared release pointer, database, migrations, exporter and PAPER
service are not modified.

Focused dashboard/deployment validation passed 146 tests, including target failure,
concurrent/tampered lease rejection, interruption before/after pointer switch,
authenticated pending-pointer recovery, exact unit matching, missing PAPER health,
rollback, and proof that operator commands never name the PAPER unit. The complete
unit suite passed 2,365 tests after rebasing on the concurrent green M3 audit
diagnostics commit. No AWS action was executed. Exact-head CI remains
required after expected-SHA publication.

## 2026-10-17: guarded dashboard-only activation milestone

The dashboard-only mission was received while the preceding audit batch was in
CI. No new source edits began until d6737f7200ffcbf9daa73e4d2f6349a07c2b4735
completed HOPE CI 39074054022 attempt 1 successfully (3,011 passed, 89.81%) with
matching issue #5 Bot marker. Live HEAD and frozen baseline
7e9d341742818a97accce3e2301b1c7f96596795 were reverified before the new batch.
User-supplied instance i-019d427e5a9b6fa87 / ap-south-1, active f17a549 release,
staged 1c0bd9a release, Python/PostgreSQL/service/HTTP observations are recorded as
HISTORICAL, not newly observed AWS evidence. No AWS console/terminal was operated.

Architectural finding: existing web/export units both follow shared current;
the full activator starts PAPER. New dashboard_release.py / dashboard_activation
provide dedicated read-only preflight, explicit approved activation, status and
rollback. A separate dashboard-current pointer and two known service drop-ins
leave shared current, deployed.env, base units, credentials, schemas and all unit
enablement bits unchanged. Export timer activity is preserved; an initially
inactive timer stays inactive and freshness will decay unless separately reviewed
refresh is configured. Unknown base-unit/drop-in configurations fail closed.
Reviewed full activation is blocked until dashboard overrides are rolled back;
old unguarded tools/manual root edits remain outside the supported contract.

Prerequisites include exact-head CI/marker/frozen-branch attestation, trusted
root-owned/non-writable release/Git/venv paths, standalone Git state, clean SHA,
Python 3.13 imports and file access as hope-dashboard, scoped read-only role and
exact target/rollback migration checksums on hope_m3_restore_test, valid current
snapshot, resources, known systemd config and exact private listener/PID/python.
Trust scans, file reads, subprocesses, HTTP and verification retries are bounded.
The EnvironmentFile is parsed as data with a closed key vocabulary: no runtime
PYTHONPATH/loader overrides, duplicate keys, LIVE=true or non-PAPER mode. Managed
Python commands use -B -s -P. No credentials enter output or unit backups.

The shared persistent kernel guard serializes dashboard/full activation and
recovery. A fsynced atomic journal/backup precedes mutations; the pointer switch
is atomic and directory-fsynced. ExecStartPre gates restart/boot. A separate
request-time owner/journal/source-path fence closes HTTP after activator death
even if web already started. Loaded module paths and exact PYTHONPATH must match
the scoped release. COMMITTED follows actual endpoint/listener/page/snapshot and
release-identity validation; 503 health and HALTED/STOPPED/UNAVAILABLE remain valid
observations. New /release.json is unavailable without a pinned launch SHA.

Ordinary failures attempt dashboard-only restoration of the prior pointer,
drop-ins, web/timer activity and committed journal. SIGKILL/power loss require
explicit authenticated stale-owner recovery; no code can run cleanup after
SIGKILL. Rollback failure durably records ROLLBACK_FAILED and quiesces dashboard
units where possible. Review found/fixed an unsafe failure path: new web must be
stopped before restoration prerequisites that might fail. Stops are always allowed
for dashboard units even if PAPER became unsafe; starts remain blocked. A service
command firewall refuses every PAPER mutation. Database history is never rewound.

Pre-reconciliation focused suite: 84 passed, 6 native Linux cases skipped in 7.72 seconds.
Real disposable PostgreSQL preflight accepts the scoped reader and rejects writer,
privileged owner, wrong DB and incompatible staged migration copies. The database
immutability trigger correctly rejected an initial fixture attempting to alter
history; the test was corrected to corrupt only a disposable code copy. Native
Linux tests cover PREPARED/config/pointer/VERIFYING/started/COMMITTED SIGKILL,
competing processes, stale recovery and committed boot-gate behavior. The started
case uses a separate real HTTP process loading a copied release and proves HTTP
503 after the activator dies. Local Windows tests use a real kernel lock plus
explicit pointer/metadata adapters; native cases are deferred to Linux CI.

Pre-reconciliation frozen source on a fresh local PostgreSQL 17 database: 3,056 passed,
32 platform skips, one Bash behavior deselected in 330.58 seconds; 89.22% coverage
against unchanged 89% gate. Three Bash contract files omitted locally remain in
Linux CI. An earlier full run was intentionally stopped for the demonstrated
EnvironmentFile override fix; it is not counted as validation of final code.
Complete diff and whitespace reviewed; local PostgreSQL stopped after tests.

The consolidated EC2 Instance Connect procedure separates read-only inspection,
approved local-object-seeded staging, preflight, explicit activation, independent
private HTTP observations and rollback. It needs no local installation or pasted
credentials. Missing current AWS facts, trusted prepared target/runtime/config,
fresh snapshot/reader scope, PAPER disabled state and separate host-change approval
are operational prerequisites. This package does not establish actual AWS reboot
or activation acceptance. PAPER was not activated; no trading/risk/NO_TRADE rules,
migrations, historical claims/fills/audits or public network exposure were changed.
Expected-SHA non-force publication and exact pushed-head CI/marker are final gates
before M3 DASHBOARD ENGINEERING COMPLETE. M3 DASHBOARD ACTIVATED is not claimed.

Publication reconciliation and final validation: the expected-SHA check detected
concurrent green commit a9169a3711df2229830e2c0fcbff4059cc467222 (CI 39074596955,
issue #5 exact marker). Preserved that commit by fast-forward and reconciled both
engineering records. Historical dashboard entry points now delegate to the single
journalled workflow or refuse conflicting state; all activation uses the shared
kernel guard. No force update was used.

The first merged Windows run exposed a coverage failure (88.25%) because native
legacy recovery tests were skipped. Added a Windows filesystem metadata adapter
while retaining real kernel locks and all assertions; Linux retains native tests.
Final clean regression: 3085 passed, 32 platform skips, 1 deselected, 331.94 seconds,
89.26% coverage against the unchanged 89% requirement. Local Python 3.12/PostgreSQL
17; Linux CI must independently exercise Python 3.13 and native crash tests. Three
Bash contract files and one Bash-dependent case omitted locally. PostgreSQL stopped.
AWS remains unmodified and activation pending separate authorization.

Exact-head CI 39089027959 for 904cdca completed with 3125 passed, 5 failed,
89.93% coverage. Six native crash tests passed. Four CLI dispatch fixtures failed
because Linux runner is non-root: explicitly model required root for dispatch.
The existing full-activation freshness fixture also raced filesystem mtime versus
wall-clock: explicitly set new-export mtime to its asserted activation timestamp;
old/future/malformed rejection assertions remain unchanged. Production root and
freshness checks are unchanged. Focused repair validation: 75 passed in 3.22s.
This is repair of actual CI failures, not a new batch. Exact-head CI remains a
publication gate; AWS activation remains pending approval and host prerequisites.

Repair CI 39089407279: 3128 passed, two CLI fixture failures, 90.01% coverage.
The root mock modified shared os.geteuid and incorrectly changed kernel guard
ownership expectations. Scope that mock to a private OS facade in the CLI test;
real Linux guard UID/flock checks remain native and unchanged. Focused validation:
75 passed in 3.19s. No production code change. Await next exact-head CI.


## PAPER recovery database wait bounds (2026-10-18)

Started from live aff6c7d120125f9cbbb0bd7d294583d7f86862b8 after fast-forwarding
all eight concurrent commits. Exact-head Actions run 39126641024 and issue #5 Bot
HOPE_CI_STATUS_V1 were SUCCESS, attempt 1. Frozen day29-green-baseline verified at
7e9d341742818a97accce3e2301b1c7f96596795 before code changes.

Demonstrable gap: both recovery batch APIs bounded selection to 32 runs but their
savepoint/control advisory lock/job row locks/proof queries inherited unlimited
PostgreSQL waits from direct callers with zero timeout settings. The CLI already
configured connection-level limits; the library contract did not. A competing
transaction could therefore indefinitely delay an explicitly selected recovery.

Added one shared savepoint scope for ordinary and certified recovery. It caps
lock_timeout at 5000ms and statement_timeout at 30000ms with SET LOCAL, preserving
stricter nonzero caller limits. Success restores prior values before releasing
the savepoint; PostgreSQL rollback restores values on failure/cancellation. Caller
work preceding recovery remains outside that savepoint. Fixed sanitized timeout
codes distinguish SQLSTATE 55P03/57014 while other database failures retain their
existing unavailable codes. No retry, new claim, factory, migration, economic
replay or strategy/risk change was introduced. Limits are per lock/statement,
not an end-to-end SLA or caller commit/connection deadline.

Real PostgreSQL focused validation: 62 passed in 26.81 seconds. Tests cover zero,
looser and stricter settings; nested scopes and pooled connection reuse; proof
failure/interrupt/statement cancellation; environment-control and job row lock
contention through both APIs; cancellation after the first ordinary receipt and
after the first certified authentication; caller-transaction usability; retry
with one receipt and unchanged strategy invocation/effect counts. Existing
backend-termination, opposite-order concurrency and revoked historical recovery
tests also pass. Initial focused test targeted a later uncertified run before
historical validation, so it correctly failed receipt proof; corrected contention
fixture to block the first certified identity without bypassing receipt checks.

First full Windows run: 3120 passed, 32 skipped, one deselected, one pre-existing
Linux staging fixture failure, 89.31% coverage, 427.14 seconds. The fixture used
POSIX PATH, /dev/null and shell hooks without a platform declaration. Marked that
single native test POSIX-only; Linux CI still executes every assertion. No
coverage threshold or production behavior was weakened. Clean rerun: 3120 passed,
33 platform skips, one deselected, 421.39 seconds; 89.31% coverage against the
unchanged 89% gate. Local Python 3.12/PostgreSQL 17. Three Bash contract files and
one Bash-dependent case omitted locally; Linux CI is authoritative for these and
all native POSIX tests. Complete diff/whitespace reviewed; local PostgreSQL stopped.
Expected-SHA non-force publication and exact pushed-head CI/marker remain final gates.

No AWS operations, dashboard activation, PAPER execution or public exposure.
Factory review and separate AWS authorization remain operational prerequisites.


## Public single-run recovery atomicity (2026-10-18)

Fetched/fast-forwarded live 02996d05daf75a2dede2534cefd0fe2adef9fb4f before
code changes. Exact-head CI 39130559117 and issue #5 HOPE_CI_STATUS_V1 were
SUCCESS, attempt 1. Frozen baseline verified unchanged at
7e9d341742818a97accce3e2301b1c7f96596795. Preserved the concurrent controlled
acceptance environment-hardening commit; no AWS execution was performed.

Reproduced a distinct atomicity defect on real PostgreSQL: successful-run
authentication records a receipt inside the repository savepoint, releases it,
then performs independent final verification with no outer operation savepoint.
Injecting failure only at that final verification, catching the error, and
committing the caller transaction persisted one receipt where zero was required.
The regression failed on unmodified code (1 failed in 1.37s), proving the gap.
The repository's inner atomicity did not cover the final public-operation proof.

Wrapped successful-run authentication in the shared bounded recovery savepoint;
its full lock/proof/receipt/final-verification sequence is now one rollback unit.
The direct single-run reconciliation entry point also uses that scope, closing
the remaining public-entrypoint wait-bound gap after the previous batch APIs.
No lifecycle transition or economic replay is introduced by authentication.
Existing committed receipts survive failed reauthentication. Raw SQLAlchemy
exception types remain intact for the service's connection disposal and existing
sanitized health classification. Single-run reconciliation retains its existing
unavailable code and now uses the shared timeout classifier. Caller commit
ownership and per-lock/per-statement limits remain unchanged.

Focused validation: 106 passed in 23.78s. Fifteen new PostgreSQL cases cover late
false/error/interrupt/statement cancellation with and without a prior receipt,
control/job lock contention through both direct APIs, backend termination after
return but before commit, fresh-connection retry and simultaneous authentication.
They verify caller-marker preservation, timeout restoration, unchanged terminal
state/effect cardinality and one durable receipt. Existing batch timeout,
backend-crash, historical-revocation and single-run proof unit tests also pass.
The initial regression plus 47 existing unit tests passed immediately after fix.

Initial full Windows regression: 3135 passed, 33 platform skips, one deselected,
one existing Bash-wrapper environment test failed because Bash was unavailable;
89.32% coverage, 267.77 seconds. Added the missing POSIX/Bash platform guard to
that newly inherited native test; all Linux assertions remain active. Clean
rerun: 3135 passed, 34 platform skips, one deselected in 316.82s, 89.32%
coverage against the unchanged 89% requirement. Local Python 3.12/PostgreSQL 17.
Three Bash contract files and one Bash-dependent case omitted locally; Linux
CI remains authoritative for Python 3.13 and all native cases. Complete diff and
whitespace reviewed, PostgreSQL stopped. Exact pushed-head CI and marker remain
final gates after expected-SHA non-force publication. No migration, strategy/risk rule, LIVE policy, factory,
service configuration, dashboard deployment or public exposure changed. AWS
activation and actual PAPER factory review remain separate operational gates.


## M3 final dashboard deployment and PAPER readiness (2026-10-18)

Fetched live 59d517da020aa18016703a8266f102bebf9476e2, verified exact-head HOPE
CI 39134860861 SUCCESS and issue #5 matching Bot marker before changes. Preserved
37cb5ae public recovery atomicity and 59d517d operator service isolation after
fast-forward. Frozen day29-green-baseline verified unchanged at
7e9d341742818a97accce3e2301b1c7f96596795. No AWS execution or deployment.

Six before-fix regressions failed (0.57s): the managed unit command contract and
five false READY cases with RUNNING snapshots (future heartbeat, stale successful
poll, future snapshot, zero successful cycles and CURRENT with an error). Existing
base-unit UnsetEnvironment removed PYTHONPATH after drop-in Environment=, so the
managed interpreter could not import/gate the intended dashboard release.

New drop-ins scrub inherited Python/loader variables and set the reviewed path
through /usr/bin/env after systemd environment removal, preserving -B -s -P and
existing service isolation. Complete legacy pairs are recognized for upgrade and
backed up verbatim; rollback restores their exact prior bytes/journal. Mixed or
unreviewed pairs fail closed. Shared current/deployed.env and PAPER enablement
are untouched. Existing journal/lock/owner/request fences remain authoritative.

Readiness now rejects future observations and stale successful polls, and cannot
promote inconsistent CURRENT health to READY. Only a current snapshot can supply
current HALT/claim/activity facts. A current durable HALT survives missing/invalid/
stale health. The read-only audit/probe vocabulary also rejects fabricated READY
without a current poll. Every result remains execution_authorized=false/LIVE=false.
Privileged Git inspection now uses a fixed binary/minimal environment with hooks,
fsmonitor and optional index writes disabled; it cannot execute ambient PATH,
loader or global Git configuration. Native adversarial Git tests accompany this.

Added a guarded operator dispatcher (isolated Python bootstrap, no staging/
service/PAPER commands) and one consolidated EC2 paste procedure. Inspection is
separate from explicit staging, snapshot refresh, activation and rollback approval.
Preflight observes PostgreSQL server_version_num in READ ONLY mode, supports the
tested 16/17 majors and rejects unknown versions. CI now runs full Python 3.13
suites on digest-pinned PostgreSQL 16 and 17 images; the status publisher depends
on aggregate matrix success. A dedicated read-only test proves actual server major.
The PostgreSQL 16 manifest digest was resolved from the official registry; no
registry credential or database URL is stored in this record.

Repository inspection found no eligible real production plan factory in src.
The consolidated evidence table distinguishes production certified work/plan
interfaces from abstract/research-only catalogs and synthetic integration factories.
None was invented, selected or approved. PAPER factory/input/run review and any
one-cycle execution remain separate gates. No strategy, research threshold, risk
control, migration file, production database or public-network change occurred.

Focused engineering suite: 286 passed, 34 platform skips, 36.13s, including local
PostgreSQL 17 scoped-reader/schema and public-recovery tests. Earlier focus: 264
passed/20 skips. Corrected two fixture-import errors and a comment-sensitive static
assertion during development; no production check was weakened. Full local
regression: 3162 passed, 44 platform skips, one deselected, 544.69s, 89.35%
coverage against the unchanged 89% requirement (Python 3.12/PostgreSQL 17).
Three Bash contract files and one Bash-dependent case omitted locally. Native
crash/process/launch and Bash cases execute in Python 3.13 Linux CI on both PG
majors. PostgreSQL stopped after tests; complete diff/whitespace reviewed. Actual
AWS systemd activation/reboot/rollback acceptance has not been performed.


Final browser contract review also updated the shipped readiness vocabulary and
requires a current successful poll for READY. Cached READY ages when the poll
expires even if the response itself is newer. Deterministic Node browser tests
verify independent poll aging, future/missing poll rejection and new diagnostic
reasons. Browser/final-contract focus: 29 passed, one native-platform skip, 0.96s.

Expected-SHA non-force publication and both exact-head CI jobs/marker remain final
engineering gates. AWS activation/reboot/rollback and PAPER operations are pending.

## M3 absent PAPER unit review (2026-10-18)

Started from live faaddd81c03fa74ff9b66698d5e5a6ec843a44d8 after its exact-head
run 39153715153 succeeded and issue #5 Bot marker matched. Frozen baseline was
verified unchanged. Preserved that concurrent shell-isolation change by fast
forwarding before work.

Operator-reported not-found/inactive canonical unit, empty installation fields
and no matching unit files do not prove no independent PAPER execution. More
fundamentally, a root census at preflight cannot enforce future absence at the
restricted dashboard account's restart gate. No reviewed host execution-boundary
inventory or persistent enforcement exists. Therefore absent-unit activation
remains blocked; no not-found allowlist, dummy unit or reusable audit permit was
introduced. This batch improves diagnostics and strict rejection, and does not
claim AWS activation is unblocked.

Canonical guard now requires complete nonduplicated load/state/PID/fragment/
drop-in properties. Even an exact not-found result with a permitted nonzero
systemctl exit remains a separate blocked review state. Installed units require
loaded/inactive, disabled or masked, zero PID, absolute fragment, no overrides.
A masked LoadState requires the exact masked state and /dev/null fragment.
Unknown/active/activating/ambiguous responses fail closed.

Added root-only paper-audit dispatcher: read-only process identity/cgroup and
command census, both installed and transient systemd inventories with execution
metadata independent of service names, timer/path/socket review, cron/at/user
schedulers, and existing deployment pointer/journal/lock checks. The kernel
guard probe opens only a preexisting inode read-only, rejects active owners,
symlinks, hardlinks and writable inodes, and never creates or replaces a file.
Limits,
permissions, unsupported metadata, disappearing/reused processes and conflicts
cannot produce a safety attestation. Only fixed codes and counts leave the audit;
no commands, schedule contents, exception details or secrets are emitted. The
audit always returns BLOCKED/exit 2, execution and activation unauthorized,
including zero-match scans. Renamed executables, other namespaces and external
schedulers still need host inventory and restart-safe enforcement review.

Adversarial tests cover absent fields, forged not-found with active PID or
overrides, duplicates, nonzero command exits, rogue Python/renamed services,
scheduled triggers, visibility failures, PID reuse, scan limits and wall-clock
bounds, concurrent full/legacy deployment and interrupted journal states. The
operator package explains staging approval, diagnostic-only use and why the
current active dashboard/shared pointer must remain intact at this blocker.
No AWS command, database change, migration, trading cycle, strategy/risk change,
factory selection or LIVE enablement occurred. Full regression and final
GitHub/CI evidence are recorded in the batch completion comment on issue #5.

Initial full local run: 3230 passed, eight fixture/platform failures, 47 skips,
one deselected (447.72s). Four retained tests mocked the former two-property
interface; updated their complete records without weakening startup denial.
Four upstream native /usr/bin/bash tests lacked Windows platform guards; added
Windows skips while preserving all Linux assertions. The final full local run
and exact-head Linux CI validate those corrections. Focus before final full:
128 passed, 19 native-platform skips (3.34s).

Fresh full run: 3235 passed, 55 platform skips, one deselected, one Windows
synthetic Git-fixture failure (428.20s); coverage 89.31%, required 89%. The fixture
used ambient global autocrlf while the inspector correctly ignored global Git
configuration, producing a racy clean/dirty mismatch. Pin the fixture repository's
own autocrlf=false, preserving production isolation and all clean-release checks.
Final unit regression and exact-head full CI validate the correction; this record
does not label either intermediate full local run green.

Final complete local unit suite: 2542 passed, 55 platform skips, one deselected,
67.66s. Local runtime is Python 3.12/PostgreSQL 17; Python 3.13 and native Linux
process/Bash/guard cases are validated by both exact-head PostgreSQL CI jobs.
PostgreSQL was stopped after local validation. GitHub publication/CI/marker
results are supplied in the completion record rather than anticipated here.

## M3 diagnostic collection resource/race hardening (2026-10-18)

Fetched live 8f1de655a13d60e3331f9695b3f198260f780cf8, confirmed exact-head run
39155973272 success and matching issue #5 Bot marker, with frozen baseline
unchanged. Inspection found a demonstrable audit-only file race: lstat checked
one inode, then open could consume a replacement inode. The portable regression
failed before the fix (one failed/one native skip). A replacement symlink/FIFO
could also defeat the reader's type check. Scheduler pending.extend eagerly
materialized names before limits were checked, and systemd capture_output could
retain unbounded output before the collector's post-read size check; each
command could spend the full ordinary Host timeout despite the census deadline.

Use a no-follow/nonblocking/close-on-exec descriptor on Linux; compare checked,
opened and final inode/type/size/mtime, cap reads and close on every exit. Stream
scandir entries and bound discovered scheduler work before queue insertion,
including the initial explicit root selection. Add a POSIX selector-based
command reader with incremental byte limits, deadline, private clean environment,
discarded stderr and no shell. Abort hung/flooding/inherited-pipe commands by
killing their unreaped session and reaping the leader. Avoid PID-reuse cleanup
after a successful wait. Always close pipes/selectors. Restrict the Host adapter
to exact read-only systemctl list/show shapes, fixed binary and metadata fields;
start/stop/reload, option-like units and environment queries are denied before
spawning. One ten-second systemd census budget is propagated to each query.

All audit failures remain redacted UNVERIFIED under status BLOCKED. Zero matches
still grant neither activation nor execution. Regular filesystem calls can
stall inside the kernel; documented checked deadlines are not a fabricated
absolute wall-clock SLA. No host-wide absence attestation or new permit was
introduced. No AWS execution, PAPER/factory activation, database/migration,
strategy/risk/research or LIVE change occurred.

Adversarial tests cover changed/replaced files, symlink/FIFO replacement, bounded
directory and explicit-root consumption, forbidden systemctl mutations, fixed
environment and shared budget. Native Linux tests exercise exact output bounds,
stderr flooding, stdout overflow, hung leaders, closed stdout with a hung leader,
exited leaders with descendants retaining the pipe, cleanup/reaping and invalid
encoding/nonzero exits. Initial focused result: 138 passed, 14 native skips,
3.69s. Full local results and exact-head CI/marker are in the completion record.

Full local regression (Python 3.12/PostgreSQL 17): 3253 passed, 65 native platform
skips, one deselected, 420.35s. Three Bash contract files and one Bash-dependent
case remain omitted on Windows. No coverage floor was changed; the full Python
3.13 Linux PostgreSQL 16/17 CI jobs enforce the existing 89% requirement and run
all native subprocess/descriptor cases. Final focus after adding real-procfs,
reaped-PID and canonical bounded-probe tests: 141 passed, 18 native skips, 3.10s.
Local PostgreSQL stopped after validation; complete diff/whitespace reviewed.

## M3 deployment-guard special-file bound (2026-10-18)

After exact-head run 39157791705 and its issue #5 Bot marker succeeded, a
follow-up descriptor review found one remaining blocking-open path. The
read-only absence audit opened `.activation.guard` before validating that the
descriptor was a trusted regular file. If a corrupted or raced host supplied a
FIFO, root audit could wait indefinitely for a writer and never reach its
fail-closed type check.

The guard probe now opens no-follow, nonblocking and close-on-exec before the
existing descriptor-based regular-file, owner, mode and link-count checks. A
native regression constructs a real FIFO with no writer and proves prompt
`GUARD_UNTRUSTED` rejection. Focused audit/activation tests: 159 passed, with
one managed-PID-namespace procfs identity assertion deselected locally. Broader
unit regression: 2638 passed with that same assertion deselected and the known
native activation crash file excluded; exact-head Linux CI exercises both.

This does not attest host-wide PAPER absence or authorize activation. No AWS
command, database/migration, PAPER cycle, strategy/risk/research change, factory
selection, LIVE enablement or public exposure occurred.

## Market-data approved identity snapshot batch (2026-10-18)

Fetched live c80426ad74de54b56a8dfb30f7678c16836804f2; exact-head CI
39157791705 and issue #5 Bot marker green/matching. Frozen baseline unchanged.
Reviewed PAPER recovery savepoint, timeout, receipt and portfolio lock paths.
Ran 40 PostgreSQL adversarial recovery tests (17.33s), including lock cancellation,
caught-error rollback, concurrent reconciliation and backend death; no new defect
was reproduced in those paths. No artificial recovery change was made.

Found a distinct market-data integrity defect: recovery approves a mutable
caller-owned identity map, then provider normalization reads it after callbacks.
A deterministic provider mutation changed the instrument supplied to the sink
after preflight. The regression also reproduced against the exact c80426a
provider/recovery source loaded in an isolated test process. Protect recovery,
full ingestion/sealing and direct provider normalization with a scoped immutable
binding snapshot. Authorize/validate that snapshot, retain it through all windows
and finalization, and copy only requested keys once. Preserve missing/invalid
values and defaultdict semantics, existing rejection codes, timestamps, calendars,
research thresholds, risk/strategy behavior and caller transaction ownership.
Every retry captures and reauthorizes afresh; no cache or new permission exists.

Adversarial unit tests cover replacement/deletion/clear during fetch, mutation by
preflight or sink between windows, mutation denial on the view, concurrent reload,
single lookup per requested key and no enumeration of unrelated mappings. Eight
real PostgreSQL cases verify manifest authorization, distinct instrument prices,
concurrent/callback drift, savepoint interruption and retry, exact idempotency,
unchanged manifest hashes and lifecycle-only sealing. Their synthetic rows are
rolled back. Focused combined suite: 52 passed (1.00s).

No AWS deployment/execution, production database/migration, PAPER factory or cycle,
LIVE enablement or public exposure. The dashboard absent-unit activation gate
remains blocked pending reviewed execution boundaries and restart enforcement.
Full regression and exact-head CI/marker outcomes are in the completion record.

Full local Python 3.12/PostgreSQL 17 regression: 3282 passed, 69 native-platform
skips, one deselected, 283.56s; coverage 89.08%, unchanged 89% requirement. Three
Bash contract files and one Bash-dependent case omitted on Windows; full native
Python 3.13/PostgreSQL 16/17 CI remains the publication gate. Local PostgreSQL
stopped after tests. Complete diff and whitespace reviewed before publication.

Before publication, live HEAD advanced to 775dc83b6c3905b569d20f26799b366641063d24
(deployment-guard special-file hardening). Verified exact-head CI 39175287096
success and matching Bot marker before incorporating it. Preserved that change
and its complete engineering record; only the two appended documentation blocks
needed merge resolution. The full local result above predates that integration;
combined focused validation and final full CI verify the published composition.
Post-integration combined focus: 193 passed, 19 native-platform skips, 3.92s;
local PostgreSQL stopped again. No source conflict or override was needed.
## 2026-10-19 — Autonomous PAPER calendar snapshot integrity

- Reproduced a per-cycle time-of-check/time-of-use defect in `PaperServicePlan`: the
  frozen plan retained the factory's mutable calendar mapping. A configuration reload
  or callback could replace a market calendar after plan validation but before run
  construction, changing which scheduled jobs were due without revalidation.
- Plan construction now reads each calendar binding exactly once, validates and bounds
  that same value, and retains an immutable mapping snapshot for the rest of the cycle.
  No session times, scheduling rules, strategy behavior, risk rules, or execution
  authority changed.
- Added adversarial regressions for caller mutation, attempted mutation through the
  approved plan, and a custom mapping that returns a different calendar on a second
  read. The regressions fail against the prior implementation.

## 2026-10-19 — Autonomous PAPER hung-cycle watchdog

- Demonstrated an operational recovery gap: `Restart=on-failure` restarts a
  crashed worker, but cannot detect a process that remains alive while a trusted
  factory, provider, strategy, or database path wedges inside a cycle. Its health
  file eventually becomes stale while systemd continues reporting the unit active.
- The PAPER unit now uses `Type=notify`, main-process-only notification, and a
  15-minute watchdog. The Python supervisor sends fixed, credential-free protocol
  messages at startup, after complete cycles, and during graceful shutdown. A hung
  cycle stops heartbeats and becomes a normal systemd failure/restart; database
  rollback and existing durable reconciliation remain authoritative.
- Added a real Unix datagram protocol regression, fail-closed environment parsing,
  foreign-PID suppression, unit-contract coverage, and operator documentation.
  PAPER-only/LIVE-disabled policy, strategies, schedules, and risk rules are unchanged.

## 2026-10-19 — PAPER notifier unsupported-platform fail-closed boundary

- Reproduced the notifier on a Python build without `socket.AF_UNIX`: the health
  boundary leaked `AttributeError` instead of HOPE's fixed notification failure.
  The fixed-protocol mocked regression also evaluated the missing constant before
  reaching its socket double, preventing cross-platform validation.
- Notification now explicitly rejects a missing Unix datagram transport as
  `PAPER_SERVICE_NOTIFY_FAILED` before opening a socket. The protocol regression
  supplies its transport constant explicitly, while a new adversarial case removes
  it and proves no socket is opened and no platform detail escapes.
- This does not add a non-systemd fallback, relax the watchdog, authorize PAPER,
  change LIVE disablement, or alter strategy/risk behavior.

## 2026-10-20 — Crash-durable operational JSON publication

- Demonstrated that the dashboard snapshot, legacy research inventory, and PAPER
  health publishers fsynced complete temporary contents and atomically renamed
  them, but returned success without fsyncing the containing directory. A host
  crash could therefore lose the newly acknowledged filename update even though
  the process had reported successful publication.
- Added one shared bounded replacement primitive. It opens the same directory
  before rename, rejects cross-directory use, atomically replaces the file, fsyncs
  the already-open directory, and closes the descriptor on every failure path.
  Failure to establish directory durability occurs before replacing the prior
  target; a post-rename sync failure is surfaced and never reported as success.
- Applied the primitive to the current M3 aggregate snapshot, separate legacy
  research inventory, and autonomous PAPER health file. Tests cover exact operation
  ordering, unavailable directory sync, replace/fsync failures, descriptor cleanup,
  cross-directory rejection, prior-target preservation, and all three real callers.
  No database write, AWS action, PAPER activation, LIVE enablement, strategy/risk
  change, or trading authority is introduced.

## 2026-10-20: PAPER systemd readiness follows verified cycle readiness

Exact live parent `88121e8df75675c8a56999b2adf01ef8595a5757`, Actions run
`39391453571` (#1320) and the matching authenticated issue #5 marker were green
before work. The service unit is `Type=notify`, but the supervisor sent READY
immediately after STARTING health, before schema/control/factory/candidate checks
or any complete PAPER cycle. That could make systemd and its dependents treat an
unvalidated worker as ready, contrary to the operator contract.

The supervisor now withholds READY and watchdog refreshes until the first complete
successful cycle has durably published CURRENT health. Initial failed cycles remain
visible but cannot announce readiness. A later successful retry announces readiness
exactly once; subsequent completed cycles retain the existing watchdog behavior.
The systemd startup deadline is aligned to the existing 15-minute watchdog/recovery
ceiling so delayed readiness stays bounded rather than restoring the premature
announcement. Tests assert notification order against the durable health file,
failure-before-readiness silence, retry-to-readiness, and the deployed unit bound.

No AWS/service action, database or migration change, strategy/risk modification,
PAPER execution or LIVE enablement is part of this source change. Frozen day29 SHA
`7e9d341742818a97accce3e2301b1c7f96596795` remains unchanged.

## 2026-10-20: pinned and authenticated operational publication

Exact authoritative parent `f99d9222dda2ca67501b16dd7bb2a5e66662f982`,
Actions run `39392930793` (#1321), and the authenticated issue #5 marker were
green before integration. The separate three-commit durability review ending at
`e017ee4e5a92a67c40a29ecc1afa7903556666c2` was a clean fast-forward from that
parent and independently passed PostgreSQL 16/17 CI run `39413952471` (#1326).

That reviewed delta pins rename operations to one verified directory descriptor;
rejects symlink, FIFO, hardlink, writable-source and identical-path inputs; and
detects directory replacement, inode substitution, size/mtime changes, and an
in-place rewrite that restores mtime by authenticating ctime before rename.

Review exposed one remaining mismatch between its stated security precondition
and executable policy: a pinned directory could still be group/world writable,
allowing another writer with its own descriptor to race entries after validation.
A failing adversarial regression reproduced publication from modes 0770, 0707,
and 0777. The primitive now rejects those directories before rename; the deployed
0750 PAPER state and 0700 dashboard state remain valid. The shared behavior covers
the dashboard snapshot, legacy research inventory, and PAPER health publishers.

No AWS/service action, production database mutation, migration, PAPER execution,
strategy/risk change or LIVE enablement is included. Frozen day29 SHA
`7e9d341742818a97accce3e2301b1c7f96596795` remains unchanged.

## 2026-10-24 — Crash-durable activation lease pathname transitions

- Demonstrated that activation fsynced the `.activation.lock` contents but did
  not fsync the containing deployment directory after creating or removing the
  lease. A host power loss could therefore lose recovery intent before release
  pointer work, or resurrect a lease after a clean operation reported success.
- Activation now completes bounded writes, fsyncs the lease, and fsyncs the
  trusted deployment directory before entering pointer work. Clean removal also
  fsyncs that directory. Directory durability failures expose only a fixed code
  and cannot admit activation work.
- Added adversarial syscall-order and directory-sync-failure tests. This changes
  no release pointer, service state, database, strategy, risk rule, PAPER cycle,
  AWS resource, or LIVE policy.

## 2026-10-24 — Activation lease inode-bound cleanup

- Reproduced that clean activation teardown deleted `.activation.lock` by
  pathname even when the path had been replaced after lease creation. That could
  remove independent recovery evidence and falsely make the next activation look
  unfenced.
- The activation lease now records its created device/inode identity and removes
  only that same, singly linked regular file. Missing, replaced, special, or
  multiply linked paths fail closed as `OPERATIONS_ACTIVATION_LEASE_CHANGED` and
  remain available for operator review.
- Adversarial tests cover disappearance, replacement with independent bytes, and
  a hard-link alias. No deployment, service, database, PAPER, strategy, risk, AWS,
  or LIVE state is changed.

## 2026-10-24 — Descriptor-bound activation recovery inspection

- Reproduced a check/open race where recovery inspection validated metadata for
  one `.activation.lock` inode and then reopened the pathname, allowing a
  replacement file to be parsed as authenticated recovery evidence.
- Recovery now opens the lease once with no-follow and nonblocking flags,
  validates ownership, type, permissions, link count, and the 4096-byte bound,
  reads through that descriptor, and revalidates inode metadata after the read.
  Changed or untrusted records fail closed with fixed error codes.
- Activation setup also preserves the pathname when descriptor identity cannot
  be established, rather than deleting unverified replacement evidence during
  exception cleanup. Regression tests demonstrate both former deletion/read
  paths before the fixes. No AWS, service, database, PAPER cycle, strategy, risk,
  or LIVE state is changed.

## 2026-10-24 — Crash-durable release-pointer switching

- Demonstrated that `current` was atomically replaced but its containing
  deployment directory was not synced before acceptance. A host power loss
  could therefore acknowledge a release whose selected pointer was not durable.
- Every target and rollback pointer rename is now followed by a trusted
  directory fsync before acceptance or failure is reported. If target fsync
  fails after the visible rename, the previous pointer is restored and synced;
  acceptance is never called for the unsynced target. An unexpected visible
  pointer fails closed instead of being overwritten.
- Adversarial tests assert syscall ordering and durable rollback after a target
  sync failure. No AWS, service, database, PAPER cycle, strategy, risk, or LIVE
  state is changed.

## 2026-10-24 — Descriptor-bound deployed-metadata recovery snapshot

- Reproduced that recovery checked `deployed.env` as a regular non-symlink and
  then reopened its pathname, allowing a replacement symlink to be parsed as
  deployment truth. Multiply linked metadata was also accepted.
- Recovery now opens metadata once with no-follow and nonblocking flags,
  validates ownership, type, permissions, single-link identity, and a 128-byte
  bound, then revalidates device/inode/size/mtime/ctime after the bounded read.
  Invalid, changed, special, linked, or replacement inputs classify as `OTHER`
  and cannot make stale recovery safe.
- Adversarial tests demonstrate the former replacement and hard-link behavior.
  No AWS, service, database, PAPER cycle, strategy, risk, or LIVE state changes.

## 2026-10-24 — Durable stale-lease evidence quarantine

- Reproduced that stale recovery authenticated `.activation.lock` and later
  unlinked the pathname, so a replacement introduced at that final boundary
  could be destroyed while recovery incorrectly reported success.
- Recovery now creates a unique mode-0700 archive, persists it, atomically moves
  the lease to `lease.json`, syncs both source and archive directories, and
  authenticates the moved record against the original bounded fingerprint.
- Successful recovery retains the authenticated record for audit. A raced
  replacement is also retained but returns
  `OPERATIONS_ACTIVATION_STATE_CHANGED`; no recovery evidence is pathname-deleted.
- The Linux quarantine transition, directory syncs, and bounded record read are
  bound to validated open root/archive directory descriptors. Replacing the
  archive pathname cannot redirect the lease into an external directory;
  archive identity is revalidated before recovery reports success.
- Regression-first tests cover both authenticated archival and replacement at
  the quarantine boundary, including archive-parent replacement. No AWS,
  service, database, PAPER cycle, strategy, risk, or LIVE state changes.

## 2026-10-24 — Crash-durable deployed metadata publication

- Demonstrated that activation synced the staged `deployed.env` contents but
  never synced the deployment directory after renaming it. A host power loss
  could therefore acknowledge a selected commit whose metadata name was not
  durable.
- Publication now validates the exact lowercase commit, creates a private
  single-link regular file, authenticates its inode and bounded content before
  and after fsync, renames it relative to a no-follow root descriptor, reopens
  the published inode without following links, and fsyncs the root directory.
- A staged sync failure retains the previous metadata and removes only the
  authenticated unpublished temporary inode. Regression tests cover ordering,
  cleanup, permissions, and malformed commit rejection. No AWS, service,
  database, PAPER cycle, strategy, risk, or LIVE state changes.

## 2026-10-25 — Deployment-root-bound metadata publication

- Reproduced that `deployed.env` staging still reopened the configured root by
  absolute pathname after a root descriptor was acquired, and publication
  never proved the pathname still named that descriptor. A concurrent root
  replacement could make the function sync and report success for a detached
  directory.
- Staging, inspection, rename, cleanup, reopen, and directory sync are now all
  relative to one no-follow root descriptor. The root must be a non-writable
  directory and its pathname/device/inode identity is checked before publication
  and again after the directory sync.
- Adversarial tests replace the deployment root before staging and during the
  final sync. Both fail closed, never touch the replacement root, and preserve
  authenticated evidence without reporting false success. No AWS, service,
  database, PAPER cycle, strategy, risk, or LIVE state changes.

## 2026-10-25 — Generation-consistent AWS readiness observation

- Reproduced that AWS readiness authenticated the deployed release, observed
  systemd, and fetched the dashboard status without the deployment activation
  fence. A legitimate release switch could therefore interleave those steps and
  produce one report assembled from two deployment generations.
- The complete read-only observation now holds the existing shared activation
  fence. Reviewed activation/recovery mutations take its exclusive side, so an
  in-progress change fails readiness closed before any release, unit, or HTTP
  probe runs and cannot interleave during an admitted report.
- Adversarial tests prove that exclusive activation is excluded at every probe
  boundary and that an already-active mutation prevents all observation. This
  adds no host write, deployment, service activation, credential/database access,
  PAPER cycle, strategy/risk change, or LIVE capability.

## 2026-10-25 — Reboot-persistent autonomous unit readiness

- Demonstrated that readiness collected but ignored systemd `UnitFileState` and
  `SubState`. A transiently started but disabled PAPER worker/timer, or a service
  marked `active/exited` with no running process, could therefore report READY.
- PostgreSQL, both web services, the export timer, and PAPER must now be enabled;
  PostgreSQL/web/PAPER must also be `active/running`. The timer-triggered oneshot
  exporter remains correctly accepted as static rather than falsely requiring it
  to run continuously.
- Nine adversarial state combinations reproduce the former false-positive paths.
  Systemd observations also require every allow-listed property and a bounded
  numeric restart count. No unit is installed, enabled, started, or modified;
  AWS, credentials, database, PAPER execution, strategies/risk and LIVE remain
  unchanged.
