# Non-trading staging startup and capability audit

Audit scope: all Python top-level __main__ guards in tracked-source src/ and
deploy/, every shipped systemd ExecStart and the release-critical execution
call graph. This is a static entry-point inventory plus targeted code review,
not a claim that every arbitrary Python callable or third-party import was
formally proven safe. The staging ZIP avoids that whole application graph.

## Included path
Only deploy/staging/server.py is started, directly by Python -I -S -B. It
imports stdlib and reads five exact sibling payloads plus its manifest. It
provides a static preview and bounded status, no DB or operational data. No
environment-derived factory, Python startup hook, application import, dynamic
configuration or arbitrary file/URL serving is accepted. Manifest/hash checks
and no-extra-file inventory run before the listener. Audit guards reject
outbound socket/DB connections, subprocess/exec/system, ctypes and writes.
The root-owned installer is a separate explicit operator action and is never
imported by the preview. Its fixed transient unit is new staging scope only.

## Excluded paths and consequences
- hope.api.paper_service.main loads the reviewed factory and database engine;
  infrastructure.paper_service.run_service/_run_service/run_service_cycle
  dispatch certified PAPER cycles. Entire package absent from preview.
- paper_release/runtime_bootstrap/paper_controller/paper_stage/paper_bootstrap/
  handoff/recovery are existing guarded execution routes, not preview startup.
  --stage-only is not used as a substitute for an absent execution capability.
- deploy/operations/activate_release.py and operations_deployment full activation
  can start PAPER. install_operations.sh and legacy dashboard upgrade/operator
  workflows act on existing service paths. None is in the artifact or invoked.
- dashboard_snapshot/legacy_research_inventory are database readers/exporters;
  full existing dashboard_server can read local projections/heartbeats. The
  preview has none of these modules, snapshots, credentials or read paths.
- domain.trading.kernel creates/materializes order intents; infrastructure
  repositories.paper_orders/paper_fills/paper_terminals persist financial state;
  execution.replay and certified scheduler/reconciliation participate in the
  existing PAPER path. No such code or broker adapter ships in staging.
- migrations/ and database engine libraries are absent, so startup cannot
  automatically migrate or write financial tables. There is intentionally no
  DB connectivity feature; it must be reported NOT CONNECTED/NOT TESTED.
- release_builder/release_image/activation/recovery and all other entry points
  listed below are source-only excluded functions, not runnable payloads.

No validated strategy, risk threshold, PIT behavior or existing startup path
was changed. No LIVE broker path is added. Preview status is about its own
capability surface and does not certify the effective host trading state.

## Python CLI entry-point inventory

| Source path | __main__ line | Staging disposition |
|---|---:|---|
| src/hope/api/activation_recovery.py | 22 | EXCLUDED |
| src/hope/api/aws_readiness.py | 1186 | EXCLUDED |
| src/hope/api/dashboard_activation.py | 840 | EXCLUDED |
| src/hope/api/dashboard_server.py | 515 | EXCLUDED |
| src/hope/api/dashboard_snapshot.py | 210 | EXCLUDED |
| src/hope/api/dashboard_upgrade_probe.py | 260 | EXCLUDED |
| src/hope/api/legacy_research_inventory.py | 130 | EXCLUDED |
| src/hope/api/m3_audit.py | 192 | EXCLUDED |
| src/hope/api/operations_deployment.py | 1049 | EXCLUDED |
| src/hope/api/paper_acceptance.py | 53 | EXCLUDED |
| src/hope/api/paper_controller.py | 322 | EXCLUDED |
| src/hope/api/paper_preflight.py | 245 | EXCLUDED |
| src/hope/api/paper_recovery.py | 53 | EXCLUDED |
| src/hope/api/paper_recovery_inspect.py | 40 | EXCLUDED |
| src/hope/api/paper_release_plan.py | 69 | EXCLUDED |
| src/hope/api/paper_service.py | 129 | EXCLUDED |
| src/hope/api/release_builder.py | 225 | EXCLUDED |
| src/hope/api/release_image.py | 369 | EXCLUDED |
| src/hope/api/runtime_integrity.py | 387 | EXCLUDED |
| src/hope/api/staging_release.py | 61 | local builder only |
| src/hope/security/admission_verifier.py | 42 | EXCLUDED |
| deploy/operations/activate_dashboard_release.py | 83 | EXCLUDED |
| deploy/operations/activate_release.py | 243 | EXCLUDED |
| deploy/operations/dashboard_release.py | 8 | EXCLUDED |
| deploy/operations/finalize_exporter_recovery.py | 95 | EXCLUDED |
| deploy/operations/inspect_aws_readiness.py | 7 | EXCLUDED |
| deploy/operations/inspect_exporter_install.py | 411 | EXCLUDED |
| deploy/operations/inspect_exporter_install_journal.py | 29 | EXCLUDED |
| deploy/operations/inspect_paper_preflight.py | 5 | EXCLUDED |
| deploy/operations/install_exporter_units.py | 457 | EXCLUDED |
| deploy/operations/paper_release.py | 8 | EXCLUDED |
| deploy/operations/recover_activation.py | 48 | EXCLUDED |
| deploy/operations/recover_dashboard_activation.py | 46 | EXCLUDED |
| deploy/operations/recover_exporter_install.py | 162 | EXCLUDED |
| deploy/staging/manage.py | 187 | explicit operator tool |
| deploy/staging/server.py | 103 | preview runtime |

## Existing systemd entry points (all excluded)

- `deploy/dashboard/hope-dashboard-export.service`: `ExecStart=/usr/bin/env PYTHONPATH=/opt/hope/src /opt/hope/.venv/bin/python -m hope.api.dashboard_snapshot --output /var/lib/hope-dashboard/snapshot.json`
- `deploy/dashboard/hope-dashboard-web.service`: `ExecStart=/usr/bin/env PYTHONPATH=/opt/hope/src /opt/hope/.venv/bin/python -m hope.api.dashboard_server --port 8787 --preview /opt/hope/docs/dashboard/preview.html --snapshot /var/lib/hope-dashboard/snapshot.json`
- `deploy/operations/hope-m3-dashboard-web.service`: `ExecStart=/usr/bin/env PYTHONPATH=/opt/hope-operations/dashboard-current/src /opt/hope-operations/venv/bin/python -m hope.api.dashboard_server --port 8788 --preview /opt/hope-operations/dashboard-current/docs/dashboard/preview.html --snapshot /var/lib/hope-dashboard-ops/snapshot.json --operations-status-file /var/lib/hope-paper/health.json`
- `deploy/operations/hope-ops-dashboard-export.service`: `ExecStart=/usr/bin/env PYTHONPATH=/opt/hope-operations/current/src /opt/hope-operations/venv/bin/python -m hope.api.dashboard_snapshot --output /var/lib/hope-dashboard-ops/snapshot.json`
- `deploy/operations/hope-ops-dashboard-web.service`: `ExecStart=/usr/bin/env PYTHONPATH=/opt/hope-operations/current/src /opt/hope-operations/venv/bin/python -m hope.api.dashboard_server --port 8788 --preview /opt/hope-operations/current/docs/dashboard/preview.html --snapshot /var/lib/hope-dashboard-ops/snapshot.json --operations-status-file /var/lib/hope-paper/health.json`
- `deploy/operations/hope-paper.service`: `ExecStart=/usr/bin/python3 -I -B -S -X pycache_prefix=/nonexistent/hope-runtime-bootstrap /opt/hope-operations/current/deploy/operations/paper_release.py --factory ${HOPE_PAPER_FACTORY} --migrations-dir /opt/hope-operations/current/migrations --status-file /var/lib/hope-paper/health.json --poll-seconds 30 --deployment-receipt ${HOPE_DEPLOYMENT_RECEIPT} --controller-directory /var/lib/hope-controller`

The new staging unit is transient, defined only by manage.install, never enabled
on boot and never installed over an existing unit. Admission requires the unit
name to be absent. Rollback verifies exact commit-specific directory/description
and Transient=yes before stopping that unit alone. Files/evidence are retained;
there is no production pointer swap, database rollback or recursive deletion.

Tests cover denied extra executable/config/SQL artifacts, tampering, duplicate
members, hostile environment, execution flags, outbound DB/broker socket attempts,
worker launch, file writes, evidence freshness/hash/commit and rollback identity.
Native HTTP startup/GET/POST/traversal/stop runs in disposable Linux CI; no
actual EC2/systemd lifecycle or backup restore is claimed by those tests.
