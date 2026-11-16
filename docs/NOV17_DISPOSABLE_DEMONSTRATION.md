# November 17 disposable demonstration and release gate

This is a reproducible laboratory demonstration, not operational PAPER admission.
Final engineering qualification target remains December 1, 2026.

## Synchronized dependency order

Start from PR106 cde2ec03d334099fcd4cc500c8e1231f39c28125, which includes
PR105 5e1c489a7bde985e8875b28e6ef01f13b103246e. Preserve all four newer106
commits, including malformed Base64 normalization and dashboard isolation.
Exact106 source43734127839 and PR43734132876 were inspected: all four PG16/17
jobs passed5616 tests each, zero skips, 91.29% coverage. Earlier5615 counts
belong to e2d606e and are superseded. Its independent-security workflow remains red.

Selectively integrate PR103's separately authored37d21c30bfd86c6585a5177b81421597c721049d
responsive fail-closed preview fix (source43721219501/PR43721223649 successful).
Its original author and provenance are retained with cherry-pick -x. This imports
the validation/stop behavior and tests; it does not run a deployment or probe AWS.
PR83–104 refs were rechecked. Other heads remain as recorded in
R1_R2_QUALIFICATION_BATCH.md. Alternative98 still overlaps96/97;104 is an older
lineage whose compatible fixes are already incorporated through105. No blind merges.

## Additional reproduced boundary

The lab observer previously accepted a signed packet again if it first saw expiry
and then its wall clock moved backward into the signed interval. Two strict tests
reproduced actual acceptance, with and without prior successful observation.
The observer now samples its clock once under its sequence lock, validates its
numeric finite type, and retains a high-water mark even after expiry refusal.
Clock rewind is denied. Invalid clocks do not consume the signature sequence.
Observer restart still requires a fresh random challenge. This does not establish
an independently trusted production clock, signer custody or process identity.

## Demonstration that runs today in disposable tests

`tests/integration/test_lab_persisted_demonstration.py` runs six real database
cases: India and USA x certified simulated FILLED, NO_TRADE, RISK_BLOCKED.
It exercises the existing signal/eligibility/risk/fill/accounting route without
changing its rules. A fresh connection reconciles twice; terminal restart does
not rerun strategy. A READ ONLY PostgreSQL transaction reads committed job,
outcome, immutable effect payload hashes and exactly one reconciliation receipt.
The test hashes that readback, signs a bounded lab report with an ephemeral
test-only key, and reads the corresponding private HTTP dashboard projection.
It verifies unsigned substitution becomes UNAVAILABLE, production acceptance
endpoints remain unchanged and unauthorized, and HTTP reporting adds no effects.

No production issuer or report-signing command is provided. The release/session
identity and key in this test are explicitly synthetic fixtures; the readback is
real disposable PostgreSQL evidence. A laboratory producer signature authenticates
its report, not independent production truth or an execution capability. The test
does not create human approval, production keys, host evidence or production admission.

Run with a fresh disposable PG16 or PG17 database and installed test dependencies:

```powershell
# HOPE_DATABASE_URL must identify the disposable test database, never production.
python -m pytest -q tests/integration/test_lab_persisted_demonstration.py tests/integration/test_certified_paper_bridge.py tests/integration/test_paper_accounting_restart_replay.py::test_partial_fill_cancellation_reconciles_without_replaying_economic_effects tests/integration/test_paper_accounting_restart_replay.py::test_reconciliation_rejects_actual_durable_portfolio_state_divergence tests/integration/test_paper_terminal_persistence.py::test_complete_terminal_recovery_survives_restart_and_is_idempotent --junitxml=lab-results.xml
python -m pytest -q tests/unit/test_lab_clock.py tests/unit/test_lab_publisher.py tests/unit/test_lab_dashboard.py tests/unit/test_nontrading_staging.py
```

The inherited tests retain exact accounting/no-replay assertions for partial fill
plus cancellation, rejected/cancelled terminal recovery and failed reconciliation.
Certified tests cover actual India/USA session/PIT/eligibility, stale/missing data,
database interruption and uncertain post-COMMIT authentication. These are simulated
orders with synthetic strategy decisions, not performance validation. Bullish entry
and existing exit/bearish mechanics remain covered by the inherited domain suite;
no new shorting strategy or risk exception is introduced.

Windows models only the unsupported lstat/fstat ChangeTime file-read boundary in
the lab HTTP test; real Ed25519 signatures, database transactions, economics and
HTTP calls are exercised. Linux CI uses actual no-follow descriptor reads. Native
startup tests remain Linux-only; skips are reported, never hidden as passes.

## Read-only disposable dashboard refresh

The snapshot refresh entry point is `python -m hope.api.dashboard_snapshot_refresh`.
Each output has a persistent private `.refresh.lock` inode. The refresher takes
a non-blocking exclusive kernel lock across export, verification, snapshot
replacement, and receipt replacement. A concurrent or unsafe-lock-path attempt
exits non-zero without exporting or changing either last-good artifact; process
death releases the lock without a stale-PID recovery path.
It requires an exact expected database name and exact 40-character reviewed commit.
It queries `current_database()` inside the same READ ONLY, REPEATABLE READ
transaction before labeling a snapshot `DISPOSABLE_LAB`. The exported contract
contains a hash of that identity, an explicit synthetic-data flag and immutable
`execution_authorized=false` / `live_enabled=false` claims; the database name and
connection URL are never published.

The refresher first writes and validates a staged snapshot, then atomically replaces
the last-good file. On database mismatch, missing PAPER state, connection failure or
post-export contract failure, last-good bytes remain untouched and a separate atomic
status records only a closed error code. `--interval-seconds` is bounded to 30–3600.
The dashboard now refuses to serve snapshot data older than 15 minutes while its
liveness endpoint remains HTTP 200 with `data_feed=STALE`.
When the server is given the same status path with `--refresh-status-file`, the
read-only `/refresh-status.json` endpoint exposes only the verified commit,
timestamps, closed error code and non-authorizing LAB scope. A failed refresh is
shown separately while the last-good snapshot remains intact; a successful attempt
older than the snapshot freshness budget is labeled `STALE`. Unknown fields,
commit mismatches, symlinks and free-text errors fail closed with HTTP 503.
`CURRENT` refresh evidence is additionally bound to the exact timestamp of the
sanitized snapshot being served. The atomic publication interval between a new
snapshot and its matching receipt is therefore temporarily `UNAVAILABLE`, never a
false assertion that an older receipt authenticated newer bytes.

Example for a disposable database only (do not point this at `hope`):

```bash
HOPE_DATABASE_URL='postgresql+psycopg://.../isolated_lab' python -m hope.api.dashboard_snapshot_refresh \
  --output /private/dashboard/snapshot.json \
  --status /private/dashboard/snapshot-refresh.json \
  --expected-database isolated_lab \
  --deployed-commit 0000000000000000000000000000000000000000 \
  --interval-seconds 60
```

Execution-ledger observations additionally split fills with a canonical `FILL`
effect receipt from historical/untracked rows. A canonical fill lacking portfolio
application is a stronger recovery-review signal; a fill lacking its effect receipt
is reported separately and is never interpreted as trading activity or permission to
replay. No identifiers, prices, quantities or P&L values are exposed.

## November 17 decision

A safe isolated demonstration is achievable using this disposable test path and
its generated JUnit evidence. No AWS deployment is authorized in this sprint.
It demonstrates implementation behavior, including refusal cases, rather than a
continuously operating production-connected trading service.

Actual independently authorized operational PAPER remains NO-GO. Minimum blockers:
independent signer custody and authenticated publisher/controller incarnation;
kernel-to-database session/XID binding; reviewed COMMIT-coordinated current policy
and revocation; independent human security review; genuine provider/factory/data/
calendar/runtime/observer evidence; and verified restore/recovery prerequisites.
The independent security workflow deliberately remains red while positive production
proofs are unavailable. Green full CI cannot satisfy these prerequisites.

Highest priority after this coherent batch: implement and independently review the
production attestor/session/revocation protocol, beginning with its trust-root and
peer-authentication contract. Do not promote the lab verifier as that service.
PAPER-only, LIVE disabled; no real orders, strategy changes, risk loosening, AWS,
production migration/grants/credentials, G7FX or merges. Frozen refs remain untouched.
