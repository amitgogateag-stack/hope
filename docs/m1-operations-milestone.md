# M1 autonomous PAPER operations milestone

Starting checkpoint: `580bcb56995adb02d3154890e527c43044ae2ddd`.
Target: locally validated code and deployment readiness, with authenticated EC2
acceptance tracked separately. Operational deadline: November 15, 2026.

## Architecture inspection and implementation plan

Existing scheduler/registry code already validates candidate scope and markets,
builds calendar-bound schedules, serializes competing schedulers and calls the
authoritative transactional runtime. Job/effect ownership, NO_TRADE, risk,
accounting, portfolio replay, completed-effect reconciliation and independent
receipts already have PostgreSQL guards and adversarial tests. Replacing those
layers would weaken the evidence rather than complete the application.

The missing integration was a long-lived process with schema/authorization
admission, bounded plans, restart observations, graceful termination and a
service status channel. M1 adds that supervisor and a CLI while retaining all
existing work/recovery paths. Plans come from a reviewed real-data factory;
neither candidate promotion nor strategy selection nor market-price fabrication
is a service responsibility. India and USA scheduling reuse explicit approved
session calendars, including closures, rather than inferring business days.

The existing dashboard provided safe loopback serving and atomic aggregate
exports, but it lacked independently aging browser observations, serialized
requests, worker health and several operational projections. M1 adds these with
strict file/API whitelists. Optional permission/schema gaps show UNAVAILABLE.
Authenticated aggregate zeros remain zeros; policy-dependent freshness and CI
state remain UNKNOWN when not verified. Portfolio remains unavailable without
independently authenticated accounting projections. Counts do not certify
research performance or prove that reconciliation is safe.

The existing dashboard-only deployment remains separate. A new operations
bundle prepares independent services under `/opt/hope-operations`, with private
port 8788, disabled installation, resource/restart bounds, expected-release
preflight and rollback. Read-only compatibility checks never repair a database.
The package does not change AWS, security groups, roles or existing services.

An existing research admission defect was also reproduced with synthetic
negative fixtures: unsupported payloads with matching hashes could pass the
certified-source resolver. M1 positively requires a supported certified schema
before independent execution-evidence verification. Existing strategy behavior
and risk limits are preserved; unverified evidence receives no certification.

Implementation sequence: verify live HEAD/CI/marker; implement service and
private observations in parallel; build guarded deployment and acceptance
package; run focused and full PostgreSQL/Node regression; review complete diff;
publish one coherent expected-SHA non-force commit; verify exact-head CI/marker.
No second commit is stacked while CI is pending.

## Validation map and honest boundaries

| Required scenario | Evidence exercised |
| --- | --- |
| Clean startup/first cycle | New service PostgreSQL test authenticates schema/control, executes one durable NO_TRADE and publishes health. |
| Reboot/restart | systemd enabled-target/order/restart contracts, fresh-pool tests and actual CLI child termination/fresh-process restart with durable NO_TRADE assertions; actual EC2 reboot remains operator acceptance. |
| Two workers | New real PostgreSQL service competition plus existing scheduler/advisory-lock concurrency tests. |
| Crash around commit | New killed-backend-before-commit rollback and lost-result-after-real-commit restart tests; existing accounting crash/replay tests. |
| Partial evidence | New service refuses incomplete claims; existing reconciliation tests authenticate topology, lineage and accounting rather than replaying effects. |
| Duplicate NO_TRADE | New concurrent/restart tests assert one durable outcome/effect, plus existing exclusivity tests. |
| Database outage | New broken physical connection/reconnection lifecycle tests; bounded export lock/statement timeout recovery. |
| HALT during work | New HALT-before-factory admission and existing active-operation control serialization tests. |
| Missing/delayed/stale/future data | Existing durable recovered-data timing, PIT, freshness and future-effective tests remain in full regression. |
| Market closures/India/USA | Existing explicit calendar/orchestration timezone tests plus new closed-session service idle test. |
| Partial/invalid schema | New exact filename/checksum tests reject absent, extra, NULL/backfill and altered migrations before work. |
| Export timeout/refresh overlap | Real PostgreSQL timeout tests plus Node harness executing shipped browser JavaScript. |
| Corrupt/stale/partial snapshots | Existing server/export atomicity tests and new independently aging browser/health observations. |
| Unauthorized access | Loopback/Host/read-only server tests, strict whitelists, optional denied SELECT tests and new role capability preflight. |
| Accounting after restart | Existing genuine PostgreSQL portfolio/accounting/reconciliation suite retained; no accounting formula altered. |
| Failed deployment/rollback | New prepared-release identity and failed-acceptance rollback tests; Linux CI executes real symlink switch/restore; deployment scripts syntax checked. |

Service integration tests inject a synthetic *catalog fixture only* to isolate
the lifecycle without manufacturing scientific promotion history. Database
claim/control/effect/recovery guards are real. Existing authoritative candidate
tests remain in full regression. These are local test results, never actual
market results or evidence of a running AWS service.

Windows does not permit local symlink creation and lacks Bash. The new rollback
symlink tests and existing three Bash-dependent contract files therefore need
Linux CI. CI installs Node 22 explicitly for deterministic browser behavior
tests. No coverage minimum or existing assertion is lowered.

## Operator-dependent completion

Follow `deploy/operations/README.md` for host inspection, secret handling,
least-privilege database acceptance, isolated backup restore, factory review,
disabled installation, private tunnel, staged activation and guarded rollback.
No privileged credential fallback is acceptable when a projection fails.

Before November 15 operational acceptance, an authorized operator must supply
the real EC2 environment, compatible schema, current validated candidates,
reviewed factory, actual data/holiday calendars and protected role credentials.
Then verify real NO_TRADE/trading outcomes, restart/HALT/outage behavior,
independent accounting and an approved reboot. The code package alone cannot
establish those facts or guarantee operational readiness by that date.

Use **M1 CODE COMPLETE — AWS DEPLOYMENT ACCEPTANCE PENDING** only after complete
local regression and exact-head GitHub CI/marker verification. Until the host
checklist is executed, do not claim HOPE is running on EC2.
