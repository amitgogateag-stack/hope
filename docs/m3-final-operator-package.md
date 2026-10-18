# M3 final dashboard-only operator package

Engineering preparation is distinct from AWS activation. No command in this
document has been executed on AWS by preparing or testing it. Use AWS Console,
ap-south-1, EC2 Instance Connect for the independently confirmed HOPE instance
i-019d427e5a9b6fa87. No SSH alias or autonomous terminal access is assumed.

## Prerequisites and approval boundaries

Use the final completion-report SHA, successful HOPE CI run and attempt. Both
Python 3.13 test jobs (PostgreSQL 16 and 17) must pass. The issue #5 Bot marker must
match that exact SHA/run/attempt and live branch HEAD. A branch advance requires
fresh review. Do not rerun an old pinned deployment just because its CI was green.

Historical shared SHA f17a54929a8ecb7745a62c118893b55494aeb2f1 and staged seed
1c0bd9a0e28c78c2996ebd0f658a7dc24b385484 are operator-reported facts, not current
AWS observations. Independently verify the shared pointer before staging or
activation. Preserve both releases, the shared pointer and deployed.env.

The existing root-owned venv must be Python 3.13 with the reviewed dependencies.
Existing configuration, releases and venv must satisfy the trust checks. Database
must be the isolated hope_m3_restore_test, PostgreSQL 16 or 17, with an already
provisioned scoped read-only reader and schema checksums compatible with target
AND rollback code. No installer, grants, migrations or database repair are part
of this package. Preflight reports the actual server_version_num as metadata.
It does not print a database URL, role name, credential file or exception text.

Require PAPER inactive AND disabled/masked, the original dashboard active/enabled,
no competing legacy dashboard deployment, no unknown drop-ins, a current valid
snapshot, trusted private listener owned by the correct unit/Python, and sufficient
resources. The detailed fail-closed checks and recovery limitations remain in
[dashboard activation design](m3-dashboard-only-activation.md).

### Absent PAPER unit: activation remains blocked

The operator reports no installed hope-paper unit (not-found/inactive, empty
unit-file/fragment/drop-in fields), dashboard active/enabled, shared f17a549 and
69a3034 staged. These facts do not establish absence of independent execution.
Do not create a dummy unit, add not-found to an allowlist, stop unknown workers,
delete deployment locks or retry activation with an older tool.

After separately approving staging of the newly reviewed exact-head release,
use the helper below as `hope_dashboard paper-audit`. It performs read-only
diagnostics and intentionally returns exit code 2 with status BLOCKED. Capture
only its redacted JSON, never raw process arguments, unit commands, scheduler
contents, credentials or connection strings. Use `hope_dashboard status` for
release/journal identity. No CI credentials are needed for either diagnostic.
The audit inspects all installed/loaded service names, command metadata, timers,
path/socket triggers, process identities/cgroups, cron/at and user-unit locations,
and known deployment conflicts. Limits, permissions, races, unsupported JSON and
ambiguous metadata yield UNVERIFIED. Counts include this audit and dashboard;
candidate counts are triage signals, never proof of a worker or of its absence.

Diagnostic file reads validate the opened descriptor against the checked file,
reject replacements and changes during reads, and use no-follow/nonblocking
opens on Linux so a replacement FIFO cannot hang the type check. Directory
names are streamed and capped before queueing. Systemd audit queries use the
fixed /usr/bin/systemctl binary and a read-only argument allowlist, discard
stderr, bound stdout while reading it, and share one ten-second inventory
deadline. A canonical-unit query has its own ten-second cap. Timeouts/output
overflow kill and reap the command session; inherited stdout cannot keep the
collector waiting after its leader exits. No shell or ambient credential
environment is inherited. OS filesystem calls can still stall in the kernel;
these checked census deadlines are not an absolute host-wide wall-clock SLA.
An incomplete or interrupted collection never authorizes activation.

Even zero candidates require review: a renamed executable, a detached container,
another process namespace, an external scheduler or a user manager can evade a
name/content search or start work after it. The restricted dashboard account
cannot repeat a root census at every startup. A reviewed inventory of execution
boundaries and a persistent restart-safe enforcement design are prerequisites
for accepting absence. Neither has been established for this EC2 host. This
release therefore keeps absent-unit activation blocked; it does not claim to
finish AWS activation. PAPER factory review remains a separate requirement.

At this blocker, leave the existing active dashboard and shared pointer intact.
Do not run activate or rollback solely to bypass PAPER_INACTIVE. Existing
interrupted dashboard journal state requires its separately reviewed recovery;
the audit cannot clear it or authorize recovery.

The following block defines operator commands and performs only inspection.
Set the four non-secret HOPE_DASHBOARD_* identity variables from the completion
report before pasting. Existing GH_TOKEN, if needed, must be injected through the
approved private mechanism; never paste its value. Keep sudo's normal env_reset
policy; never use sudo -E or preserve Python/loader variables. No environment file
is sourced. Staging, snapshot refresh, activation and rollback each require their
own explicit command/approval; none runs automatically after inspection.

## One EC2 Instance Connect paste block

```bash
set -euo pipefail
: "${HOPE_DASHBOARD_TARGET:?Set final reviewed commit from completion report}"
: "${HOPE_DASHBOARD_SHARED:?Set independently confirmed shared commit}"
: "${HOPE_DASHBOARD_CI_RUN_ID:?Set successful exact-head CI run}"
: "${HOPE_DASHBOARD_CI_ATTEMPT:?Set exact CI attempt}"
[[ $HOPE_DASHBOARD_TARGET =~ ^[0-9a-f]{40}$ && $HOPE_DASHBOARD_SHARED =~ ^[0-9a-f]{40}$ ]]
[[ $HOPE_DASHBOARD_TARGET != 7e9d341742818a97accce3e2301b1c7f96596795 ]]
ROOT=/opt/hope-operations
RELEASE="$ROOT/releases/$HOPE_DASHBOARD_TARGET"
SEED="$ROOT/releases/1c0bd9a0e28c78c2996ebd0f658a7dc24b385484"
SAFE_GIT=(/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C LC_ALL=C
  GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 GIT_PAGER=cat
  /usr/bin/git --no-optional-locks -c core.hooksPath=/dev/null -c core.fsmonitor=false)

hope_inspect() {
  test "$(readlink -f "$ROOT/current")" = "$ROOT/releases/$HOPE_DASHBOARD_SHARED"
  readlink -f "$ROOT/current"
  /usr/bin/systemctl show hope-paper.service hope-ops-dashboard-web.service \
    hope-ops-dashboard-export.service hope-ops-dashboard-export.timer \
    --property=ActiveState,SubState,UnitFileState,MainPID,FragmentPath,DropInPaths
  /usr/bin/sudo /usr/bin/ss -H -ltnp 'sport = :8788'
  df -h "$ROOT"
  grep '^MemAvailable:' /proc/meminfo
}
hope_stage() {
  [[ ${1:-} == --approve-staging && $# == 1 ]] || return 2
  test "$(readlink -f "$ROOT/current")" = "$ROOT/releases/$HOPE_DASHBOARD_SHARED"
  test -d "$SEED/.git"
  if test ! -e "$RELEASE"; then
    /usr/bin/sudo "${SAFE_GIT[@]}" -c "safe.directory=$SEED" clone --no-hardlinks --no-checkout "$SEED" "$RELEASE"
    /usr/bin/sudo "${SAFE_GIT[@]}" -C "$RELEASE" remote set-url origin https://github.com/amitgogateag-stack/hope.git
    /usr/bin/sudo "${SAFE_GIT[@]}" -C "$RELEASE" fetch --no-tags origin p0-pit-context
    /usr/bin/sudo "${SAFE_GIT[@]}" -C "$RELEASE" checkout --detach "$HOPE_DASHBOARD_TARGET"
  fi
  test "$(/usr/bin/sudo "${SAFE_GIT[@]}" -C "$RELEASE" rev-parse HEAD)" = "$HOPE_DASHBOARD_TARGET"
  test -z "$(/usr/bin/sudo "${SAFE_GIT[@]}" -C "$RELEASE" status --porcelain --untracked-files=all)"
}
hope_dashboard() {
  /usr/bin/env -u BASH_ENV -u ENV /bin/bash \
    "$RELEASE/deploy/operations/m3_dashboard_operator.sh" "$@"
}
hope_refresh_snapshot() {
  [[ ${1:-} == --approve-snapshot-refresh && $# == 1 ]] || return 2
  /usr/bin/sudo /usr/bin/systemctl start hope-ops-dashboard-export.service
}
hope_verify() {
  hope_dashboard status
  curl --fail --silent --show-error --noproxy '*' http://127.0.0.1:8788/release.json
  curl --fail --silent --show-error --noproxy '*' http://127.0.0.1:8788/readiness.json
  curl --fail --silent --show-error --noproxy '*' http://127.0.0.1:8788/status.json
  curl --silent --show-error --noproxy '*' -o /dev/null -w 'snapshot=%{http_code}\n' http://127.0.0.1:8788/snapshot.json
  curl --silent --show-error --noproxy '*' -o /dev/null -w 'operations=%{http_code}\n' http://127.0.0.1:8788/operations.json
  /usr/bin/systemctl show hope-paper.service --property=ActiveState,UnitFileState
  test "$(readlink -f "$ROOT/current")" = "$ROOT/releases/$HOPE_DASHBOARD_SHARED"
}
hope_inspect
printf '%s\n' 'Inspection only. Review results before separately approved host commands.'
```

Use `hope_stage --approve-staging` only after approving host staging. It creates
only a new standalone target clone using the preserved seed's objects; existing
targets must match and remain clean. It never resets/cleans a mismatched target.
If fetch is unavailable, stop for an approved staging channel; do not add secret
URLs, credential helpers or bypass the attestor. No local software is required.

Then run the read-only command `hope_dashboard preflight`. PREFLIGHT_PASSED must
be observed before a host-change request; it grants no authority itself. If the
snapshot alone needs refresh, separately review the existing exporter scope,
approve `hope_refresh_snapshot --approve-snapshot-refresh`, then rerun preflight.
This writes a local snapshot through read-only database access, not a database
change. Never synthesize health/snapshot evidence or modify historical records.

## Explicit activation, verification and rollback commands

After separate operator approval and successful preflight:

```bash
hope_dashboard activate --approve-dashboard-change
hope_verify
```

The core repeats all preflight checks under the shared kernel lock, starts only
the existing dashboard web (and timer only if previously active), and commits its
journal only after private listener, exact release/preview/snapshot, readiness and
PAPER-disabled checks succeed. It never starts/enables/restarts PAPER. Neither
shared current nor deployed.env changes. Initially inactive timer remains inactive
and snapshots eventually become stale; continuous refresh needs separate review.

The exact rollback command, after explicit rollback approval, is:

```bash
hope_dashboard rollback --approve-dashboard-change
hope_verify
```

Rollback does not need current-head CI or the CI variables, so loss of CI access
does not prevent restoring prior reviewed code. Preserve TARGET and SHARED for
the deployment being reversed. First activation rollback removes the dedicated
dashboard pointer/drop-ins and restores the shared dashboard. Nested rollback
restores the exact previous reviewed pair of drop-ins and committed journal.
After rollback, /release.json or /readiness.json may return 404 if the preserved
older dashboard predates them: in that case `hope_verify` deliberately stops.
Use the core's completed rollback result plus status.json, actual unit/listener,
shared-pointer and prior-code observations; do not call an old dashboard M3-ready.

Ordinary activation failures trigger automatic rollback. SIGKILL/power loss cannot
run cleanup; pending journal and startup/request fences require explicit recovery
after confirming the recorded owner is dead. Unknown owner state is not death.
Failed rollback persists ROLLBACK_FAILED and stops dashboard units where possible.
Missing/incompatible prior code/runtime/schema, corrupt backups, unexpected shared
pointer or conflicting legacy state block recovery for engineering review. Do not
delete evidence or use an old unguarded activator. No rollback rewinds database
history. A real reboot/service acceptance has not been performed by local tests.

## Readiness and factory review

Readiness HTTP 200 means a diagnostic endpoint answered. Missing, unreadable,
invalid, future or stale PAPER health; STOPPED/UNAVAILABLE worker; old successful
poll; inconsistent CURRENT state; stale/future snapshot; or claims needing review
cannot establish READY. A fresh durable HALT remains visible despite missing or
stale health. A stale snapshot's HALT is historical, not a current observation.
READY means current file observations only: execution_authorized remains false,
LIVE false, database is only OBSERVED_AT_SNAPSHOT, reconciliation UNVERIFIED. It
is not process liveness, continuous operation or trading permission. Compare
actual systemd state independently; never fabricate a heartbeat for a stopped worker.

Repository inspection found no production `module:function` returning a real
PaperServicePlan under src. No factory is selected or approved by this report.

| Candidate/evidence | Finding | Review still required |
|---|---|---|
| hope.infrastructure.certified_paper.CertifiedResearchPaperWork / CertifiedPaperDefinition | Production certified work/binding and strategy/parameter/risk factory interfaces; not a plan factory | Real persisted classification/decision/dataset IDs, attested implementation identity and approved risk context |
| hope.infrastructure.paper_service.PaperServicePlan and scheduling.paper builders | Production plan contract, schedules and registry assembly; not a ready factory | Reviewed composition returning a plan with real data, calendars, timing and certified bindings |
| domain.strategy.models.Strategy / strategy_family_registry | Abstract strategy interface and RESEARCH_ONLY family declarations | Evidence-backed concrete strategy implementation; catalog names do not confer PAPER eligibility |
| tests/integration/test_paper_autonomous_service_postgres.py:factory | Synthetic NoTrade integration fixture outside release src | Ineligible for deployment; never substitute this test callable |
| test_certified_paper_bridge / test_m3_controlled_acceptance fixtures | Synthetic certified acceptance reference compositions | Tests only; not real datasets or an operator-approved implementation |

The loader requires the callable to be defined by its named module inside the
attested release src directory; reexports/outside test modules are rejected.
The next PAPER milestone requires operator review of a real factory, concrete
persisted inputs, selected fresh UUID, isolated scoped writer and separate
one-cycle authorization. This dashboard procedure contains no PAPER cycle command.

Engineering complete requires passing exact-head CI on both database majors and
matching marker. AWS DASHBOARD ACTIVATED requires actual authorized host evidence.
Controlled PAPER acceptance requires its separate reviewed factory/run/data and
execution approval. Local/CI evidence alone establishes neither AWS status.
