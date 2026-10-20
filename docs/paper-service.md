# Autonomous PAPER service

`python -m hope.api.paper_service --factory reviewed_package:build_plan --migrations-dir /opt/hope-operations/current/migrations --status-file /var/lib/hope-paper/health.json`

The service is an opt-in supervisor for the existing operational PAPER scheduler.
It does not implement a broker adapter, enable LIVE, change risk or strategy
thresholds, promote strategies, populate market prices, migrate schemas, or alter
HALT state. No production plan factory is shipped: missing reviewed real-data
bindings are an operational prerequisite, not permission to run fixtures.

## Reviewed factory contract

A root-owned, independently reviewed Python callable receives `(connection,
current_utc)` and returns `PaperServicePlan`. The connection is READ ONLY and has
already passed exact migration filename/checksum verification and RUNNING control
authorization. The callable supplies tuples of existing
`OperationalPaperJobBinding` and `OperationalPaperSchedule`, explicit
`StrategyMarket` calendars, an aware start/end window and `max_lateness`.
Bindings must use real, durable input resolution and the existing freshness and
PIT protections. The supervisor independently loads current durable strategy
candidate classifications and calls the existing registry/schedule validators.
The production CLI now requires `CertifiedResearchPaperWork` for every binding;
there is no command-line compatibility bypass. Its fresh database authorization
and certified evidence reconstruction run before the new durable claim. See
`m2-certified-paper-milestone.md` for configuration, risk anchors and scope.
Factories must include the recovery window needed for earlier interrupted runs;
the service does not discover historical work outside the explicit plan.

Bounds are 96 bindings/schedules, 4096 calendar sessions, a seven-day plan and
lateness horizon, and 256 generated runs. A valid calendar with no sessions can
represent a closed market. Empty bindings are unavailable configuration. The
poll interval is 1–300 seconds (default 30); use 30–60 seconds operationally.
Trusted factory/strategy Python must finish promptly; PostgreSQL calls in the CLI
have 30-second statement and 10-second lock/connect timeouts. The deployment's
stop deadline may terminate a hung process, after which database transaction
and existing recovery proofs remain authoritative.

The systemd unit uses `Type=notify` with a 15-minute watchdog. The main Python
process announces startup and pings only after a complete service cycle; it does
not delegate watchdog authority to callback or child processes. A factory,
provider, strategy, or database path that wedges a cycle therefore stops feeding
the watchdog and is terminated/restarted by systemd instead of remaining falsely
active forever. The 15-minute bound is an operational recovery limit, not trading
authorization. Any open database transaction rolls back on process death and the
existing durable scheduler/reconciliation rules govern the next attempt.
Unsupported Unix-notification transports and notification I/O failures terminate
with the fixed `PAPER_SERVICE_NOTIFY_FAILED` code; platform exception details and
environment values are not exposed. This service unit is supported on Linux with
systemd and does not silently downgrade to an unmonitored process.

## Durable boundaries

Each poll opens new preflight connections. It verifies the complete migration
inventory and UTF-8 SHA-256 checksums without DDL or checksum backfilling. Missing,
extra, altered or unrecorded migrations fail closed. Schema upgrades are separate
operator actions. The existing scheduler owns job locks, admission, explicit
lateness checks, transaction-scoped writes, completed-effect reconciliation and
receipt authentication. Partial evidence blocks execution; the service neither
replays it nor quarantines it automatically. Successful NO_TRADE is durable and
restarts skip it after authenticating effects.

SIGTERM/SIGINT stops admission between cycles and interrupts the polling wait.
Already admitted work finishes under its existing transaction. A committed HALT
prevents new protected work; the service reports HALTED and continues observation
until the operator separately resolves prerequisites. It never resumes control.
Database failures discard pooled connections and the next poll re-verifies every
boundary. No success is inferred from a lost commit acknowledgement.

## Health and logs

The private JSON health file is replaced atomically with mode 0640. Its completed
temporary contents and the replacement directory entry are both synced before
publication succeeds. Its parent must be provisioned with the shared operations
group and mode 0750 by the installation package. A failure before replacement
leaves the old complete file; any directory-sync failure is reported rather than
claiming durable publication. Consumers must still enforce timestamp age. Status
is also emitted as sanitized JSON to stdout for journald. Exception text, SQL,
connection URLs and provider records are never included. `HOPE_DATABASE_URL` is
consumed from the protected process environment; `HOPE_DEPLOYED_COMMIT` is exposed
only when it is 40 lowercase hex characters. It is an operator-provided release
identifier, not proof of CI.

Schema version 1 fields are `generated_at`, `state`, `last_success_at`,
`cycles_succeeded`, `cycles_failed`, `jobs_executed`, `jobs_skipped`, `error_code`,
`paper_only: true`, `live_enabled: false`, `deployed_commit` and
`schema_compatibility`. State is STARTING, CURRENT, HALTED, UNAVAILABLE or STOPPED.
`last_success_at` describes a **service polling cycle**, possibly with zero due
jobs; it is not a portfolio timestamp or proof of a recent trade. Schema
compatibility is COMPATIBLE only after the latest successful verified cycle,
INCOMPATIBLE for explicit schema mismatches, otherwise UNKNOWN. Counters are
process-local observations, not authoritative global trading totals.

`--once` returns 0 for a successful cycle and 1 for a failed cycle; startup
configuration/import errors return 2 with a fixed sanitized message. A successful
one-shot publishes CURRENT while completing its cycle and then atomically publishes
STOPPED before process exit. The final heartbeat retains its non-null
`last_success_at`, COMPATIBLE schema result and counters. STOPPED proves only that
the acceptance process exited cleanly; it is not autonomous-worker health. Failed
and HALTED probes retain their fail-closed diagnostic state. There is no built-in
strategy, market calendar, account, credential or synthetic price source.

## Validation scope

Local tests cover atomic health publication, sanitized errors, plan bounds,
shutdown/retry behavior, genuine PostgreSQL scheduler claims and NO_TRADE effects,
concurrent workers, interrupted transactions, exact schema preflight, HALT and
broken-connection recovery. Service integration fixtures inject only a synthetic
validated candidate catalog to isolate lifecycle behavior from research promotion;
all PostgreSQL trading/control/recovery guards remain active. Existing independent
research admission and accounting suites remain required. A pool restart is not
an EC2 reboot test; actual systemd reboot and AWS acceptance remain operator work.
