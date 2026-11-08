# Single read-only PAPER preflight

This workflow observes an already provisioned installation. It does not install
dependencies, deploy artifacts, sign approvals, run migrations, change systemd,
start workers, renew controller verification or issue orders. Do not run it on
AWS until explicit host-access authorization is granted. No AWS host has been
checked as part of this repository batch.

Use the exact reviewed release and independently approved receipt/factory.
Invoke its reviewed Python/source with the established isolated runtime
bootstrap. The module interface is:

```text
python -m hope.api.paper_preflight <approved-release-SHA> --root <approved-operations-root> --receipt <approved-receipt-SHA256> --factory <approved.module:function> --controller-directory <approved-controller-directory>
```

The equivalent reviewed entrypoint is
`deploy/operations/inspect_paper_preflight.py`. Arguments contain no credentials.
Do not substitute an ambient/unattested Python environment for the reviewed
runtime. Existing runtime/build/image verification is still mandatory.

The default command never creates a database engine or reads database credentials.
The database/host stages remain UNVERIFIED without a supplied read-only
connection. For an explicitly authorized database observation, add
`--database-readonly`; it uses the already approved `HOPE_DATABASE_URL` configuration
in memory, never prints it, and never reads arbitrary credential files. Do not
put that URL in command arguments, reports or PRs. Database connection begins
only after root/release/source trust checks pass under the activation fence.

The connection uses REPEATABLE READ with default transaction read-only enabled,
a three-second connection timeout, five-second statement timeout and one-second
lock timeout. It closes/disposes after observation; no persistent setting is
changed. Alternatively pass an already established, bounded read-only connection
to `inspect_preflight` in the Python API. The migration observer refuses a
read-write transaction, unsupported PostgreSQL major, weaker isolation or
unbounded statement/lock timeouts. It never changes those settings itself.

## Report and decisions

The fixed `hope.paper-preflight.v1` report contains source, host, migrations,
image, deployment and controller stages. Each stage has a fixed state/code;
private exception text, paths, factory source, environment contents, SQL rows
and credentials are excluded. Migration counts are bounded; filenames and SQL
contents are never returned. `execution_authorized`, `host_changes`,
`database_writes` and `live_enabled` are always false.

Exit zero means every observation stage was VERIFIED in the checked window.
Exit one means BLOCKED/unavailable evidence; it never requests an automatic
repair. VERIFIED is a read-only preflight result, not authorization to deploy,
activate PAPER, clear HALT or represent AWS as operational. A real operational
GO decision still requires separately authorized host/deployment/real-data and
multi-session acceptance evidence.

| Stage/code | Operator action, after any required separate authorization |
| --- | --- |
| RELEASE_SOURCE_UNVERIFIED | Reconcile exact approved Git SHA, trusted paths and clean source; do not change the frozen baseline |
| HOST_PREREQUISITES_UNVERIFIED | Inspect actual Python 3.13/systemd >=249/public host identity; install or modify nothing during preflight |
| READONLY_POSTGRES_CONNECTION_REQUIRED | Supply authorized read-only access to PostgreSQL 16/17 with bounded timeouts and snapshot isolation |
| MIGRATION_SOURCE_UNVERIFIED | Reconcile trusted canonical contiguous migration files against the approved release |
| MIGRATION_RECORDS_REQUIRED | Investigate missing/unreadable migration metadata; do not create schema objects during diagnosis |
| MIGRATION_HISTORY_MISMATCH | Investigate unknown versions or changed checksums; block activation and preserve evidence |
| REVIEWED_MIGRATIONS_PENDING | Plan/review migration compatibility and backup/rollback; obtain separate DB-change authorization |
| MIGRATION_CHECKSUM_EVIDENCE_REQUIRED | Independently establish legacy checksums; null records cannot prove compatibility and are never backfilled here |
| SIGNED_PRODUCTION_IMAGE_REQUIRED | Supply independent immutable OCI/image/SBOM/package/build approval and current revocation policy; LAB approval is insufficient |
| PINNED_DEPLOYMENT_CONTRACT_REQUIRED | Reconcile signed receipt, host/container/kernel identity, runtime bindings, approved real provider/data/calendar acceptance and rollback artifacts |
| CURRENT_CONTROLLER_VERIFICATION_REQUIRED | Inspect the journal; interrupted/stale/HALTED state needs its documented safe verification/recovery procedure, never an automatic restart |
| PREFLIGHT_WINDOW_UNVERIFIED | Source/root/evidence changed or expired during observation; preserve evidence and repeat authorized diagnosis after reconciliation |

The collector holds the existing shared activation fence. Before returning all
green it rechecks source/root identities, deployment evidence, controller state
and expiry. A concurrent controller HALT or expired receipt cannot be hidden by
an earlier successful stage. Failed closing checks invalidate earlier successes.

## Migration compatibility scope

The observer uses only SHOW and SELECT. It reads bounded migration records from
the configured schema (public by default), checks exact expected filenames and
the migration runner's UTF-8/universal-newline SHA256 checksums, and rereads file
identities and records. Unknown, pending, duplicate, missing, tampered or unsigned
records cannot become VERIFIED. Symlinks, hardlinks and writable migration
storage fail the established POSIX trust checks. No executor is invoked.

The evidence concerns **migration records in one read-only transaction snapshot**.
It does not attest every SQL function/trigger/table definition, migrate an old
database or prove financial readiness. Concurrent database changes outside this
snapshot may require another authorized preflight and existing transactional
financial gates remain mandatory. Neither synthetic metadata nor disposable CI
databases establish production schema or real-provider acceptance.

## Remaining operational prerequisites

The staged process/receipt handoff still requires controlled Linux integration:
approved read-only source/runtime bindings, independently observed actual
container/process identity, correct systemd MainPID and independent signing.
Approved India/USA providers, factories, datasets/calendars and real-data
acceptance remain external requirements. No authority is manufactured by this
workflow. Host access, deployment, schema changes, service activation and actual
PAPER execution each retain their separate explicit approval boundaries.
