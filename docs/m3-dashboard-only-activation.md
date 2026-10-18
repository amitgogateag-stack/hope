# M3 dashboard-only activation and recovery

Engineering tooling; AWS execution requires separate approval. No AWS activation
has been performed by preparing or testing this package. Use AWS Console, region
ap-south-1, EC2 Instance Connect for the reviewed HOPE instance
i-019d427e5a9b6fa87. Reconfirm account/instance identity before any host action.
No direct SSH alias, local software installation, public listener or credentials
pasted into chat are required.

## Historical facts and release selection

The operator reported shared active release
f17a54929a8ecb7745a62c118893b55494aeb2f1 and staged M3 release
1c0bd9a0e28c78c2996ebd0f658a7dc24b385484. PostgreSQL and the private web unit
were active, web enabled, port 127.0.0.1:8788, isolated hope_m3_restore_test
accessible through the scoped reader; export timer/PAPER inactive. Root/status/
snapshot were 200, operations 503, readiness 404. Python 3.13.15/dependencies
were available. These remain historical assertions until rechecked. A 503 does
not establish its own cause. The staged 1c0bd9a release lacks this dedicated
workflow and release-identity endpoint; it cannot be substituted for the new
reviewed target. Preserve it and use its Git objects as a local staging seed.

TARGET must be the final reviewed commit containing this workflow, with successful
exact-head HOPE CI and matching issue #5 Bot marker/run/attempt. The attestor also
requires live p0-pit-context HEAD and the unchanged frozen baseline
7e9d341742818a97accce3e2301b1c7f96596795. A branch advance requires a fresh review;
do not reset or overwrite newer work. The completion report supplies the target
SHA/run/attempt and a console-ready copy of this procedure with those values set.
The CI run must come from `.github/workflows/ci.yml`; its run number and attempt
must exactly match the strict `HOPE_CI_STATUS_V1` body published by
`github-actions[bot]`. Markers from other bots, duplicate/conflicting identity
lines, extra fields or merely similar workflow names fail closed.

## Architecture and trust boundary

Shared /opt/hope-operations/current and deployed.env are never changed. PAPER
continues to reference that shared release and must be inactive AND disabled or
masked. This workflow never stops/starts/restarts/enables PAPER or executes its
factory. Its service-control allowlist contains only the three dashboard units.
No migration, database write, grant, strategy/risk change or network exposure
occurs. The reader connects only to hope_m3_restore_test in a READ ONLY
transaction; exact migration checksums must match BOTH target and rollback code.

The root-owned dashboard-current pointer is used by two managed drop-ins,
90-hope-dashboard-only.conf under the existing web and export service .d
directories. The base unit files, private port, service account, hardening,
credential EnvironmentFile and enablement bits are preserved. Unknown drop-ins,
changed base units or incompatible config fail preflight rather than being
overwritten. Originals must exactly match the installed shared release templates.
Full release activation using the new reviewed operations_deployment guard is
blocked while this override/journal exists. Roll back dashboard overrides to the
shared release before a full activation or schema change. Never invoke an older
unguarded full activator while overrides exist. The dashboard boot gate also
refuses startup if the shared pointer changes unexpectedly. Separate reported
shared/dashboard SHAs describe intentionally scoped code, not a full HOPE upgrade.

The concurrent green a9169a3 implementation is preserved and reconciled into this
single supported workflow. Its activate_dashboard_release.py entry delegates here
and now requires CI run/attempt. Historical low-level recovery shares the same
kernel guard and refuses canonical journal state. The earlier dedicated
hope-m3-dashboard-web.service must be absent or inactive/disabled; canonical
activation does not install or enable it. Legacy .dashboard-activation.lock,
.dashboard-current.next or dashboard-deployed.env state blocks adoption for
explicit engineering review. Do not erase that evidence or run older copies of
the activation script. A previously executed legacy deployment needs a reviewed
migration/recovery plan; this workflow does not guess its previous service state.

Trust requires root-owned, non-group/world-writable deployment directories,
release files, Git state and venv. Symlink Git worktrees/object alternates are
refused; prepare a standalone release clone with --no-hardlinks. Venv internal
links must be root-owned and resolve to trusted files. Release/runtime trust scans
are bounded (50,000 entries and 30 seconds each). The reader EnvironmentFile is
bounded to 64 KiB and parsed as data, never sourced/executed; the single-line HOPE_DATABASE_URL must
be securely provisioned already (plain or simple outer quotes), without duplicate
keys or LIVE=true.
Only HOPE_DATABASE_URL and optional PAPER-valued HOPE_EXECUTION_MODE / false-valued
HOPE_LIVE_ENABLED keys are accepted. PYTHONPATH, loader settings and other keys
are compatibility blockers; do not bypass them. All managed Python commands
disable bytecode/user-site/current-directory imports with -B -s -P.
Privileged host probes and service controls use a fixed system executable path and
minimal C-locale environment; operator PATH/PYTHONPATH, loader variables and other
ambient process settings are not inherited. The one reviewed child-Python launch
accepts only its fixed PATH and explicit import/bytecode isolation variables.
Its credentials are never returned. It need not be readable by
hope-dashboard: systemd injects it for the exporter. Python imports, preview and
snapshot readability are separately tested as hope-dashboard using Python 3.13.
Do not chmod credentials to fix an unrelated preflight failure.

## Read-only inspection and prerequisites

In the authenticated console, first inspect metadata; do not print env files,
systemctl Environment values, DSNs or raw database rows:

```sh
readlink -f /opt/hope-operations/current
readlink -f /opt/hope-operations/dashboard-current || true
systemctl show hope-paper.service hope-ops-dashboard-web.service \
  hope-ops-dashboard-export.service hope-ops-dashboard-export.timer \
  --property=ActiveState,SubState,UnitFileState,MainPID,FragmentPath,DropInPaths
sudo ss -H -ltnp 'sport = :8788'
df -h /opt/hope-operations
grep '^MemAvailable:' /proc/meminfo
```

Require the reviewed shared SHA, inactive/disabled (or masked) PAPER, active/enabled
original web, no busy exporter, only the expected private listener belonging to
that unit's MainPID and exact venv interpreter. Available disk and memory must
each be at least 128 MiB; existing systemd resource ceilings still apply. The
snapshot must be valid/current within one hour, not future-dated, and readable
by hope-dashboard. Missing PAPER health is allowed and must remain truthful.

If the snapshot is stale/missing, preflight is BLOCKED. After separately approving
a dashboard-only snapshot refresh and confirming the existing exporter uses the
scoped reader/isolated database, an operator may run:

```sh
sudo systemctl start hope-ops-dashboard-export.service
```

This writes only the dashboard snapshot through the existing read-only exporter;
it is a host change, not a read-only inspection command. Do not fabricate a
snapshot or health file. If continuous refresh/reboot refresh is desired, separately
review enabling/starting ONLY hope-ops-dashboard-export.timer before activation;
then preflight must run again. The workflow preserves timer active/inactive state
and all enablement bits. An initially inactive timer stays inactive, so data will
eventually become stale. Engineering success does not claim continuous refresh
or AWS operational acceptance when that prerequisite remains unresolved.

## Prepare the reviewed target without replacing staged/active releases

The following is a separately approved HOST STAGING step. TARGET is the reviewed
completion-report SHA; SHARED is independently checked, not silently derived.
Use the existing staged clone as an object seed. Reuse an already matching target;
never reset, clean or overwrite it. Fetch only into the new standalone clone.

```sh
# Set TARGET, SHARED, CI_RUN and CI_ATTEMPT from the reviewed completion report.
ROOT=/opt/hope-operations
SEED="$ROOT/releases/1c0bd9a0e28c78c2996ebd0f658a7dc24b385484"
RELEASE="$ROOT/releases/$TARGET"
test -d "$SEED/.git"
if test ! -e "$RELEASE"; then
  sudo git -c "safe.directory=$SEED" clone --no-hardlinks --no-checkout "$SEED" "$RELEASE"
  sudo git -C "$RELEASE" remote set-url origin https://github.com/amitgogateag-stack/hope.git
  sudo git -C "$RELEASE" fetch --no-tags origin p0-pit-context
  sudo git -C "$RELEASE" checkout --detach "$TARGET"
fi
test "$(sudo git -C "$RELEASE" rev-parse HEAD)" = "$TARGET"
test -z "$(sudo git -C "$RELEASE" status --porcelain --untracked-files=all)"
```

Execute blocks in a shell with set -eu so any command failure stops the procedure.
Root ownership/permissions and the existing venv must already be trusted; failures
need reviewed remediation. No installer, downloads of Python dependencies, grants,
migrations or chmod/chown of existing configuration are performed automatically.
Do not stage code into the frozen baseline or modify the preserved seed.

## Read-only preflight, then explicit approved activation

If private GitHub access requires GH_TOKEN, have it injected by the approved
credential mechanism. Never put its value in commands or output. The optional
sudo preserve-env names a variable, not its value. Database credentials are read
from the existing protected /etc/hope-dashboard-ops.env.

```sh
sudo --preserve-env=GH_TOKEN "$ROOT/venv/bin/python" -B \
  "$RELEASE/deploy/operations/dashboard_release.py" preflight \
  --target "$TARGET" --expected-current "$SHARED" \
  --ci-run-id "$CI_RUN" --ci-run-attempt "$CI_ATTEMPT"
```

PREFLIGHT_PASSED changes no host/database state. BLOCKED reports a closed stage
name without exception/credential text. Do not bypass a failure. Recheck all facts
inside activation under the shared kernel guard; preflight alone grants nothing.
Only after separate explicit host-change approval:

```sh
sudo --preserve-env=GH_TOKEN "$ROOT/venv/bin/python" -B \
  "$RELEASE/deploy/operations/dashboard_release.py" activate \
  --target "$TARGET" --expected-current "$SHARED" \
  --ci-run-id "$CI_RUN" --ci-run-attempt "$CI_ATTEMPT" \
  --approve-dashboard-change
```

The workflow durably backs up its previous drop-ins/journal, records PREPARED,
stops only dashboard units, installs boot-gated drop-ins, atomically switches the
dashboard pointer, reloads systemd and starts web (and timer only if previously
active). It resets failed/start-limit state only for dashboard units. Verification
checks the private listener/PID/interpreter, exact preview/snapshot bytes, existing
endpoints, release identity and validated observational readiness. Operations 503
for absent health is permitted; HALTED/STOPPED/UNAVAILABLE remain valid truthful
observations. Only then is COMMITTED durably published. No health is synthesized.

## Independent observations and reboot behavior

```sh
sudo "$ROOT/venv/bin/python" -B "$RELEASE/deploy/operations/dashboard_release.py" status
curl --fail --silent --show-error --noproxy '*' http://127.0.0.1:8788/release.json
curl --fail --silent --show-error --noproxy '*' http://127.0.0.1:8788/readiness.json
curl --fail --silent --show-error --noproxy '*' http://127.0.0.1:8788/status.json
curl --silent --show-error --noproxy '*' -o /dev/null -w 'snapshot=%{http_code}\n' http://127.0.0.1:8788/snapshot.json
curl --silent --show-error --noproxy '*' -o /dev/null -w 'operations=%{http_code}\n' http://127.0.0.1:8788/operations.json
systemctl show hope-paper.service --property=ActiveState,UnitFileState
readlink -f "$ROOT/current"
```

/release.json is the pinned process launch identity, corroborated by the guarded
pointer, loaded module paths/PYTHONPATH, drop-ins and systemd PID/interpreter checks; it alone is not a Git or
execution authorization. Readiness READY means only current observations.
The status command reports journal/pointer metadata, not a live process-health proof.
The existing enabled web unit keeps its enablement, with ExecStartPre gating every
restart/boot. A COMMITTED journal permits the same approved dashboard SHA after
reboot; pending/failed operations or unexpected shared/config/pointer changes
block startup. PAPER must still be inactive and disabled/masked. No reboot is
performed by this tool. Recheck real AWS after any independently approved reboot.

## Automatic rollback and explicit crash recovery

Ordinary exceptions/interrupts trigger dashboard-only rollback under the same
guard. Previous release/runtime/schema are checked; previous pointer/drop-ins,
web and prior timer activity are restored. Existing enablement/config/env files
and snapshots are untouched. Rollback accepts truthful unavailable or stale snapshots rather
than inventing fresh data. A protected rollback record and backup remain for
review. Nested dashboard upgrades restore their exact prior committed journal.

SIGKILL, power loss or kernel failure cannot execute cleanup. Durable pending
journal plus boot gate prevents the uncommitted target restarting. The old
reviewed dashboard may remain active if the crash preceded its stop. After
service startup, a request-time journal/owner fence also returns HTTP 503 if the
activator dies before COMMITTED; a surviving process cannot keep serving an
uncommitted dashboard merely because its startup gate already passed. After
confirming the owner process is dead, use the following separately approved
dashboard-only recovery (also valid to revert a committed dashboard deployment):

```sh
sudo "$ROOT/venv/bin/python" -B "$RELEASE/deploy/operations/dashboard_release.py" rollback \
  --target "$TARGET" --expected-current "$SHARED" --approve-dashboard-change
```

Recovery authenticates PID/boot/start ticks; unknown owner state is not treated as
dead. It shares the persistent activation guard with activation/full deployment,
revalidates unchanged shared state and trusted backup/prior schema, and never
starts PAPER. No current-head CI requirement is imposed when restoring previously
reviewed code. If rollback itself fails, ROLLBACK_FAILED persists and dashboard
units are stopped where possible. Do not delete the journal/guard or hand-edit
pointer/config state. Diagnose metadata, restore required runtime/schema/read
permissions through a reviewed procedure, and retry explicit recovery. A corrupt
backup, changed shared release or untrusted path requires manual engineering
review; automatic rollback cannot guarantee service availability in that case.

Only the current guarded tools are supported; arbitrary root edits and old full
activators are outside the serialization contract. No rollback rewinds database
history, claims, fills or reconciliation. Controlled PAPER acceptance remains a
separate factory/data/run/authorization milestone.

## Status

M3 DASHBOARD ENGINEERING COMPLETE means code/tests/exact-head CI passed; AWS
activation pending. M3 DASHBOARD ACTIVATED requires actual approved AWS execution
and independent live verification. M3 BLOCKED applies to unresolved trust,
compatibility, freshness, environment, authorization or access prerequisites.
Local mocks, native Linux crash tests and disposable PostgreSQL evidence do not
establish AWS deployment, real reboot acceptance or production availability.
