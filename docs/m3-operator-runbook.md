# M3 controlled PAPER acceptance: operator review required

Deadline: November 15, 2026. This package is tooling, not AWS acceptance evidence.
No real factory has been identified or approved. The approved connection method
is AWS Console → EC2 Instance Connect → the HOPE instance; direct SSH/autonomous
terminal access is not assumed. No AWS change or PAPER execution is authorized
by preparing this package. Do not enable persistent PAPER startup.

## Establish the target and release

An authorized operator must confirm the AWS account, region and HOPE instance in
the authenticated console, then the isolated database `hope_m3_restore_test`.
Do not modify the production database. Treat historical PostgreSQL 16, 233
migrations, 62 integration passes, active dashboard and HTTP results as history.
Recheck them. A reachable dashboard alone is not a functioning PAPER stack.

Use an immutable release checked out from amitgogateag-stack/hope on
p0-pit-context. The command requires exact live HEAD, completed successful HOPE CI
push run/attempt, matching Bot issue #5 marker and unchanged frozen baseline
`7e9d341742818a97accce3e2301b1c7f96596795`. GitHub reads are HTTPS to fixed
repository endpoints, time/size bounded, with redirects refused. If the repository
needs authentication, inject an approved scoped GH_TOKEN securely; never paste it
into a command or evidence. A later branch advance requires a fresh review.

The release inspector verifies origin, exact Git HEAD, a clean checkout and
required files. Existing preflight/install templates and service hardening remain
in deploy/operations. Host preparation, package installation, deployment, role
changes and copying configuration require separate authorization. Do not use
activate_release.py for this one-cycle acceptance: that workflow starts persistent
units and has separate approval requirements. Never modify the frozen checkout.

## Read-only reconciliation

With the existing approved scoped database environment injected securely, run
the following from the reviewed prepared release as its intended effective account:

```sh
python -m hope.api.m3_audit --release "$HOPE_M3_RELEASE" \
  --expected-sha "$HOPE_DEPLOYED_COMMIT" --ci-run-id "$HOPE_M3_CI_RUN_ID" \
  --ci-run-attempt "$HOPE_M3_CI_ATTEMPT" --component paper
```

Repeat with `--component dashboard` under the scoped reader environment. The
audit refuses any database other than hope_m3_restore_test, verifies migration
checksums and scoped role restrictions in a READ ONLY transaction, reports the
actual PostgreSQL version and migration count, reads fixed systemctl metadata and
queries only loopback 8788. It makes no grants, migrations or service changes and
emits no environment files, connection strings, principals, raw rows or stderr.
It does not establish operational acceptance. Preserve sanitized output privately.
Also run the existing read-only deploy/dashboard/inspect_ec2_readiness.sh to
check port bindings, service installation, exports and host readiness. A public
listener is a blocker; do not open security groups or public proxies.

## Diagnose operations visibility truthfully

`/status.json` distinguishes server liveness from snapshot freshness. The existing
`/operations.json` returns 503 for absent/unreadable/invalid health; a 200 can carry
HALTED, UNAVAILABLE or STOPPED. Do not create synthetic health to turn 503 into 200.
`/readiness.json` is an observational diagnostic endpoint: HTTP 200 means only that
the server answered. Its body distinguishes missing, unreadable, malformed or
stale worker health; STOPPED/STARTING/UNAVAILABLE; stale or unavailable snapshots;
unverified worker identity; durable HALT; and claimed effects needing review.
READY means current compatible observations, never trading authorization.

The M3 audit preserves this endpoint's closed diagnostic reason, reported worker
state, snapshot/database/reconciliation observations, last successful poll/PAPER
timestamps and sanitized failure history. It validates the versioned PAPER-only,
LIVE-disabled, execution-unauthorized contract before including these fields.
Unknown response fields and free-text failure details are never copied. HTTP
status is reported separately: HTTP 200 with INVALID_RESPONSE means the body
failed validation; STALE_RESPONSE means its generation time is over five minutes
old and its health claims were discarded. Missing/invalid health still leaves
/operations.json at 503 even when /readiness.json returns a useful diagnosis.
Redirects are refused, responses are size/time bounded and only fixed loopback
paths are queried. Dashboard-component audits additionally require `/release.json`
to identify the exact reviewed dashboard SHA; a different or unsafe release claim
is an invalid response. PAPER-component audits do not equate the independently
deployed dashboard and worker releases. None of these observations constitutes
AWS acceptance.
Dashboard HTML, snapshot, heartbeat, and legacy-inventory inputs are read through
bounded no-follow descriptors and must remain the same private single-link regular
inode for the whole read; pathname swaps, links, FIFOs, devices, writable files,
and concurrent in-place changes fail closed.

The dashboard Safety view shows these distinctions, last successful service poll,
last successful persisted PAPER job when its projection is available, and last
failure timestamp/code from the current process. Database connectivity is observed
only at snapshot generation, not a live ping. Reconciliation counts are not proof.
Worker state is reported heartbeat state, not an independently verified PID or
systemd state. The read-only audit reports systemd separately. Failure history
resets on process restart. All fields are whitelist projections and age locally.

For a 503, check the exact unit --operations-status-file argument and intended
health path, existence, traversal/read permissions for hope-dashboard, size/schema
and worker state with read-only metadata. The M1 template expects
`/var/lib/hope-paper/health.json`; do not assume legacy units use that path.
PAPER inactive plus no health file is an expected UNAVAILABLE possibility, not a
confirmed cause until observed on the host. Never print the worker environment.

Access remains private through an operator-owned authenticated tunnel only.
No SSH alias was confirmed. If SSH tunneling is not available from the approved
connection arrangement, private browser access is a separate unresolved access
prerequisite; do not expose 8788 publicly to work around it.

## Review the real factory and authorize one run

The placeholder `reviewed_module:factory` is not an implementation. Identify,
review and explicitly approve an actual module:function and its code/dependencies
before setting HOPE_PAPER_FACTORY. The module and callable must be defined under
the exact attested release's `src`; external factory paths, site-package factories
and re-exported callables are rejected. No bundled synthetic test factory is approved
for AWS. Review real research comparison/evaluation evidence, current scoped
strategy authorization, sealed PIT universe/feed, market session/calendar,
configured latency, freshness and unchanged risk limits. Compute and review the
exact selected durable run UUID from that plan. The plan must select exactly one
run; zero, another ID, multiple runs, generic non-certified work and stale/revoked
authorization fail closed through the existing scheduler and certified bridge.

Review m3-acceptance.env.example as non-secret configuration. Inject the existing
database credential securely; do not source an unreviewed script or print .env.
Set PAPER mode, LIVE=false, exact release/CI identity, actual reviewed factory,
one selected UUID, interpreter and a NEW absolute private evidence directory.
Use the scoped writer account, not a superuser/schema owner. Evidence directory
creation is exclusive, mode 0700; status/evidence files use the existing atomic
private writer. Never point this evidence at the live dashboard export files.

After separate explicit authorization confirming the target database, factory,
selected UUID and safety state, the exact entry command is:

```sh
bash "$HOPE_M3_RELEASE/deploy/operations/m3_accept_once.sh" --approve-one-paper-cycle
```

Unset required values abort before execution. This command cannot be made runnable
until the actual factory and run selection are reviewed. The wrapper has a
180-second process budget plus a 10-second forced-stop ceiling. It does not invoke
systemd or deploy anything. Before starting Python it replaces the inherited
environment with a fixed system path, locale, reviewed release import path and
only the explicit PAPER/database identity values (plus an optional scoped GitHub
token for attestation); unrelated operator, Python and dynamic-loader variables
do not reach the interpreter. The CLI verifies release/CI/marker, explicit PAPER/LIVE
configuration, exact database, least-privilege role, schema checksums and a fresh
selected run before work. No historical LIVE orders may exist in the isolated DB.

The existing certified service executes once and publishes truthful STOPPED health.
The same one-shot is repeated to prove one terminal skip with no second strategy
call/economic replay. Explicit historical recovery then re-authenticates execution,
accounting and reconciliation receipts. NO_TRADE and RISK_BLOCKED are valid durable
outcomes. No thresholds are loosened and no broker adapter is involved.

## Review evidence, retain HALT and rollback boundaries

Review health.json and acceptance.json in the private evidence directory. A success
records the actual outcome, PostgreSQL version, migration count, one execution,
one restart skip and authenticated reconciliation; dashboard_review remains
PENDING and operator review is required. It is NOT M3 CONTROLLED PAPER ACCEPTED.
Do not manually copy health.json into an active operational path. Arrange a
separately approved read-only dashboard export/status configuration and verify
actual private HTTP/UI observations, release identity and database scope. Review
the real result and authorize any persistent service activation separately.

On any error/timeout, keep PAPER stopped; preserve the selected ID, health and
durable DB records. A post-commit verification failure does not roll back committed
history. Inspect/reconcile the same ID under the existing reviewed recovery
contract; never delete claims or auto-retry a new ID. No acceptance JSON is
published until all postchecks pass. Code rollback uses the reviewed prior release
only after schema compatibility checks; it never rewinds the database or fills.

If a separately authorized operator needs a durable HALT on this ISOLATED DB,
use the existing SqlAlchemyPaperEnvironmentControlRepository transition with
expected RUNNING → HALTED, a reviewed reason and actor inside engine.begin(),
after asserting current_database() == 'hope_m3_restore_test'. Keep credential
injection private. This is a separate database mutation requiring authorization,
not part of read-only audit. Stopping a unit is not a durable HALT, and restarting
or rollback never resumes it. Resume remains separately reviewed and blocked by
incomplete claims. Do not alter production control as part of M3 acceptance.

## Milestone status

M3 ENGINEERING COMPLETE: code/tests/CI verified; AWS acceptance remains pending.
M3 CONTROLLED PAPER ACCEPTED requires an actually authorized AWS cycle, durable
outcomes, reconciliation and reviewed dashboard evidence. M3 BLOCKED applies while
a required target, factory, data, permission, authorization or access prerequisite
is unresolved. No local/synthetic test result can establish AWS acceptance.


## Explicit recovery database wait bounds

Both direct recovery library entry points (`reconcile_completed_paper_batch` and
`recover_certified_paper_batch`) apply transaction-local PostgreSQL limits inside
their batch savepoint: at most 5 seconds per lock acquisition and 30 seconds per
statement. A caller's stricter nonzero setting wins. The CLI already configures
connection timeouts; direct callers now receive the same bounded-wait protection.
These limits do not constitute a total batch wall-clock deadline, connection
establishment deadline, or a timeout for a caller's later outer COMMIT.

A server lock timeout/cancellation raises the entry point's fixed
`*_DATABASE_TIMEOUT` code with the database cause retained for internal diagnosis;
other database failures retain `*_DATABASE_UNAVAILABLE`. The CLI still emits its
fixed sanitized failure and never publishes VERIFIED before successful commit.
No automatic retry, new claim, factory invocation, or accounting replay occurs.

Timeout rolls back every transition and receipt produced by that batch, including
an earlier successful member. Savepoint rollback restores the caller's settings;
success restores them explicitly before release. Caller work preceding the batch
remains under the caller's transaction. Preserve selected IDs, inspect contention
and durable state, and retry only the same reviewed explicit recovery operation
after the blocker is resolved. A lost connection/commit acknowledgement remains
ambiguous until a fresh read/recovery proves the same IDs. These engineering
changes do not authorize AWS mutations or a PAPER cycle.


## Atomic single-run recovery and successful-run authentication

The same transaction-local wait bounds also cover the direct
`reconcile_completed_paper_run` and `authenticate_successful_paper_run` APIs.
Successful-run authentication now owns a savepoint around control/job locks,
lineage/accounting proofs, receipt insertion and final independent verification.
The repository insertion's own savepoint alone cannot undo a receipt if a later
public-operation proof fails after that inner savepoint was released.

Catching a late proof error, cancellation or interrupt and committing the outer
transaction leaves no new receipt. A receipt committed before this call remains
intact. Successful authentication remains observational: no lifecycle transition,
strategy invocation, claim or accounting replay. Concurrent callers serialize on
the existing locks and produce one receipt. Single-run reconciliation retains its
existing lifecycle behavior and fixed database-error codes, with the timeout code
added by the shared classifier. Successful-run authentication preserves its raw
SQLAlchemy exception contract so service connection disposal and sanitized health
classification continue to work. Caller work before the savepoint remains intact;
a returned result is still provisional until the outer commit succeeds. Backend
loss before that commit requires a fresh durable-state check for the same run.
