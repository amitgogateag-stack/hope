# M1 EC2 operations installation and rollback

This is an operator-reviewed package, not a deployment that has been performed.
Use a separate `/opt/hope-operations` installation; never repurpose `/opt/hope`,
its existing dashboard services, the frozen baseline checkout or the live database.
The new private dashboard uses loopback port **8788**. No inbound security-group
rule is required. Do not expose it through a public proxy or port forwarding.

For an existing M3 installation requiring only dashboard code activation, use
[the dedicated dashboard-only procedure](../../docs/m3-dashboard-only-activation.md).
It preserves shared `current`, keeps PAPER inactive/disabled and uses a separate
guarded dashboard pointer, durable recovery journal and boot/request fence.
Do not use the full `activate_release.py` workflow for that purpose: it starts
PAPER. Reviewed full activation is blocked while dashboard overrides remain;
restore them to the shared release before any full activation/schema change.

## Acceptance prerequisites and authorization

Obtain explicit authorization before host changes, service-account or database
role creation, installation, enablement, backup, migrations or AWS cost changes.
Record the exact green commit, issue #5 marker, EC2 identity, existing processes,
database version, owner and backup policy. Never paste a connection string,
password or private key into a ticket, shell command, log or this repository.

The service requires a reviewed **real-data plan factory**. There is no bundled
synthetic production factory or automatically selected strategy. Review the
factory against `docs/paper-service.md`: genuine certified market data,
PIT universe, current operational candidates, India/USA session calendars and
holidays, unchanged risk settings and explicit freshness/replay limits. If these
prerequisites are unavailable, leave PAPER disabled and report UNAVAILABLE.

## Prepare an immutable release (authorized operator only)

1. Inspect the EC2 Linux/systemd host and available CPU, memory and disk. Install
   Python 3.13, Git and a PostgreSQL client using the host's approved mechanism.
   Do not change packages blindly on an existing trading host.
2. Create a root-owned `/opt/hope-operations` with `releases`, `config` and a
   Python 3.13 `venv`. Prepare a clean detached Git checkout of the approved SHA
   at `releases/SHA` with origin exactly
   `https://github.com/amitgogateag-stack/hope.git`. Install dependencies using
   `constraints/ci-test.txt`; install the approved package into the dedicated
   venv. Do not edit release files or install an editable wheel over a mutable
   checkout. Test imports of `psycopg`, `hope.api.paper_service` and the reviewed
   factory. Point `current` to this release with a root-owned symlink.
3. Provision locked, non-login `hope-paper` and `hope-dashboard` accounts and
   shared `hope-operations` group after reviewing existing users. Only these
   service accounts join the group. Code, venv, release parents and factory
   configuration remain root-owned and not writable by either account.
4. Provision `/var/lib/hope-paper` as `hope-paper:hope-operations` mode 0750
   and `/var/lib/hope-dashboard-ops` as `hope-dashboard:hope-operations` mode
   0700. The sanitized service heartbeat is 0640; exported snapshots are 0600.
   Keep application credentials out of both directories and out of web content.
5. Through a privileged editor, create root-owned mode-0600 environment files:
   `/etc/hope-paper.env` contains `HOPE_DATABASE_URL` for the existing reviewed
   least-privilege PAPER writer and `HOPE_PAPER_FACTORY=reviewed_module:factory`;
   `/etc/hope-dashboard-ops.env` contains a different `HOPE_DATABASE_URL` for a
   dedicated SELECT-only reader. `/opt/hope-operations/deployed.env` contains
   only `HOPE_DEPLOYED_COMMIT=APPROVED_40_HEX_SHA`. systemd reads these files
   before dropping privileges. The web service receives no database credential.
   Python and dynamic-loader control variables are not configuration: the reviewed
   units remove them after system-manager and environment-file variables are
   assembled, then supply the exact release `PYTHONPATH` directly to the
   interpreter. Do not add `PYTHONPATH`,
   `PYTHONHOME`, `PYTHONWARNINGS`, `LD_PRELOAD`, `LD_LIBRARY_PATH` or `LD_AUDIT`
   to an environment file; they are deliberately ignored.
6. Database role changes require separate review. Neither role may be superuser,
   schema/table owner, CREATEROLE, CREATEDB, replication, BYPASSRLS or able to
   CREATE in public. Do not give the reader membership in the writer role.
   Preserve existing writer grants required by the proven PAPER repositories.
   Reader SELECT needs: research_runs, job_runs, paper_effects, audit_events,
   paper_environment_control_events and hope_schema_migrations. Optional
   dashboard sections additionally read datasets, strategy_versions, experiments,
   research_decisions, paper_no_trade_decisions and market_bars. Missing optional
   privileges show UNAVAILABLE; they must not trigger privileged fallback.
   Configure the reader's default transaction as read-only. Review CONNECT and
   USAGE separately. No executable GRANT script is supplied for the existing DB.

## Inspect, install disabled, then accept

Run `sudo bash /opt/hope-operations/current/deploy/operations/preflight.sh SHA`.
It is read-only and rejects mismatched code, private-config permissions, service
accounts, unsupported Python/systemd and invalid unit definitions. It does not
certify database grants or a plan factory. Review its failures; never bypass them.

Perform **both** database checks with the correct environment injected securely
by the operator's approved secret-handling mechanism:

```
/opt/hope-operations/venv/bin/python -m hope.api.operations_deployment \
  --release /opt/hope-operations/current --expected-sha SHA --database-component paper
/opt/hope-operations/venv/bin/python -m hope.api.operations_deployment \
  --release /opt/hope-operations/current --expected-sha SHA --database-component dashboard
```

Run as the service's effective account, with `PYTHONPATH` pointing to the prepared
release. These checks issue only bounded read-only SQL, require exact installed
migration checksums and reject privileged roles. They never apply/backfill SQL.
The PAPER service repeats compatibility/authorization checks on startup and each
poll. An incompatible/partial schema requires a separately reviewed database
upgrade and backup, not an automatic repair.

After approval, `sudo bash .../deploy/operations/install_operations.sh SHA`
installs **disabled** units and refuses existing units or an occupied 8788 port.
Failure rolls back only newly installed unit files. It does not create users,
change the database, enable PAPER or touch existing dashboard units.

Before enabling, run an authorized PAPER `--once` with the reviewed factory,
inspect the sanitized health and durable database outcome, and perform the
operator acceptance checklist below. A successful one-shot must finish with
`state=STOPPED`, a non-null `last_success_at`, compatible schema and the expected
counters; `CURRENT` after the process exits is not acceptable worker health.
Use the explicit read-only recovery inspection for every reviewed run identity,
then invoke certified recovery only for an independently approved incomplete
selection. Neither observation nor recovery substitutes for autonomous service
acceptance. Only then enable the approved components:

```
sudo systemctl enable --now hope-ops-dashboard-export.timer hope-ops-dashboard-web.service
sudo systemctl enable --now hope-paper.service
```

Do not enable PAPER while HALTED. HALT is durable; stopping or rebooting the host
does not resume it. A service can be active while application health is HALTED or
UNAVAILABLE. Check the heartbeat and actual database outcomes, not just systemd.

## Private access, health and bounded operation

Use `ssh -N -L 8788:127.0.0.1:8788 EC2_USER@EC2_HOST`, then open
`http://127.0.0.1:8788/`. SSH authentication and host access are the security
boundary; there is no unauthenticated public deployment. Verify `ss -ltnp`
shows only 127.0.0.1:8788. Requests cannot mutate HOPE. Credential/raw-record
fields are whitelisted out. Portfolio metrics remain unavailable until a
separately verified accounting projection is configured.

Inspect `/status.json`, `/operations.json` and `/snapshot.json` over the tunnel.
The browser independently ages snapshots and worker heartbeats, cancels bounded
refreshes and prevents overlapping/older responses from replacing newer data.
Zero is meaningful only in an available authenticated projection. Last successful
service poll and last completed PAPER job are different metrics.

Services have bounded restart frequency, stop time, memory, tasks and descriptors.
The exporter is a serialized oneshot with a 120-second systemd ceiling and
database-level budgets. Timer reboot ordering and SIGTERM shutdown are explicit.
SIGKILL or host loss uses the existing database ownership/recovery contracts;
partial contradictory evidence blocks new work and requires reviewed reconciliation.
No startup process repairs accounting or fabricates market inputs.

Use `journalctl -u hope-paper -u hope-ops-dashboard-export -u hope-ops-dashboard-web
--since today --no-pager` privately; structured worker logs contain only sanitized
health fields. Review host journald retention rather than globally changing it:
allocate an approved quota (for example 200 MB/7 days). `logrotate.conf` is an
optional policy for explicitly configured file logs; units log to the journal by
default. Monitor state volume usage and preserve incident evidence.

## Backup and code-only rollback

Before any approved change, take a PostgreSQL backup using the existing protected
backup account and a local mode-0600 credential source, verify restore on an
isolated database, retain current/target commit IDs, reviewed factory/config
checksums and unit files. Encrypt backups and keep them out of the release and
web directories. No migration/down-migration or historical mutation is part of
this package. Never restore an old database over active PAPER work as rollback.

Prepare a target immutable release and verify **both** target and previous code
against the unchanged installed schema and credentials. Do not change unit
contracts in the same activation: unit revisions require separate review.
After approval run the target venv's Python on
`deploy/operations/activate_release.py --target SHA --expected-current OLD_SHA
--approve-host-change` with approved PYTHONPATH. The wrapper stops only M1 units,
invokes the fixed system `systemctl` binary with a minimal locale/path environment,
and never forwards operator Python or dynamic-loader variables to service control.
It uses an expected-pointer lease, switches atomically and accepts only a matching
CURRENT service heartbeat from a successful compatible PAPER-only cycle that
started after activation. Acceptance also requires the PAPER worker, private web
service and export timer to remain active, a newly atomically published and
schema-verified PostgreSQL dashboard snapshot, and successful loopback delivery
of that snapshot through the private dashboard. A healthy worker cannot mask a
failed observability path. An exclusive `.activation.lock` spans lease checking,
service stop, switch, acceptance and rollback; competing activations fail closed.
A killed deployment process can leave the lock behind deliberately. Do not
remove it until an operator runs the read-only
`python -m hope.api.activation_recovery` diagnosis. The lease authenticates the
original Linux boot, process start identity, expected commit and target commit;
PID reuse cannot masquerade as the original deployment. `recovery_safe=true`
means only that the original pointer and metadata are intact, no pending pointer
exists and the authenticated process is gone. Every target, mixed, unreadable or
unknown state requires operator review with services stopped; the diagnostic
never removes the lease, changes pointers or starts services. For that one safe
state, an approved root operator may run `deploy/operations/recover_activation.py
--expected-current OLD_SHA --target SHA --approve-host-change`. Recovery stops
and verifies every M1 unit, rechecks the exact lease file and all pointer/metadata
facts, durably removes only that stale lease, and leaves every service stopped.
It refuses an active/unknown process, mismatched arguments, any interrupted switch,
or any state/lease change during quiescence. It never changes pointers or metadata
and never starts a service. Root/code/venv
ownership and write permissions are checked before executing the interpreter.
On failed acceptance it restores the prior pointer
and metadata and leaves services stopped for operator review. No historical
data is undone. If the stop or rollback state is uncertain, keep services stopped.
For manual rollback use the same guarded wrapper with target/expected reversed,
after schema acceptance of the old code. Preserve snapshots, logs and database
audit evidence; never delete claims to force replay.

## AWS operator acceptance checklist (pending)

- Record host/account identity and approvals; verify backups by isolated restore.
- Pass release, schema, least-privilege role and private-file checks for both users.
- Verify actual current operational candidates and real-data plan prerequisites.
- Confirm stale/missing/future-effective data fails closed without new effects.
- Observe a real scheduled PAPER cycle or a legitimate durable NO_TRADE; do not
  manufacture a trade to turn a status indicator green.
- Restart the worker twice; verify no duplicate orders/fills/effects or cash/P&L.
- Exercise approved HALT, prove no new work, resume only through existing
  separately authorized control procedure.
- Verify bounded database outage recovery and interrupted export preservation.
- Check the dashboard through SSH only, including unavailable/stale/zero states.
- Perform approved reboot; verify systemd ordering, private binding and durable
  recovery. No real EC2 reboot has been executed during local engineering.
- Exercise failed target preflight and code-only rollback on staging before EC2.
- Independently compare portfolio cash/positions/P&L to authenticated accounting
  projections before marking Portfolio available.

Code/test completion does not certify an AWS deployment. Until this checklist is
executed against the authenticated host, report **AWS DEPLOYMENT ACCEPTANCE PENDING**.

## M3 dashboard-only activation

Use the [canonical dashboard-only procedure](../../docs/m3-dashboard-only-activation.md)
and `dashboard_release.py`: exact-head CI, scoped-reader/schema/runtime preflight,
durable journal, shared kernel guard, boot/request fence and dashboard-only
rollback. Shared `current` and PAPER remain unchanged; no factory is required.
The earlier `activate_dashboard_release.py` command is now a compatibility entry
point delegating to the same canonical workflow and requiring CI run/attempt.
It cannot use its former independent unit-enablement/switch path.

The former `hope-m3-dashboard-web.service`, deployment metadata and low-level
recovery helpers are retained for historical recovery only. They share the
serialization fence and refuse canonical journal state. An existing legacy unit
must be absent or inactive/disabled; legacy lease, pointer-stage or metadata
requires explicit engineering review before adopting the canonical workflow.
Do not manually remove metadata or run old unguarded scripts to bypass that gate.
The canonical procedure preserves existing web/export service identities and
never installs or enables the historical M3 unit.

## M2 upgrade prerequisites (separate approval required)

Before activating M2, stop admission, back up and restore-test the database, then
have the authorized schema owner apply migrations 231 through 233 through the normal
checksum-tracked migration runner. Review the new eligibility/invalidation
triggers and lock ordering on staging. Never disable guards or backfill invented
research evidence. M1 code expects its exact migration inventory, so an M2 schema
upgrade requires a compatible rollback release; simply pointing to unmodified M1
will fail schema preflight and is not a working rollback plan.

Review least-privilege grants separately: the worker needs SELECT on the actual
certified research/configuration/evaluation/PIT relations and INSERT/SELECT on
`paper_strategy_authorizations` and `paper_certified_outcomes`, plus existing
PAPER/accounting/audit rights and eligibility-function execution. It needs no
research promotion, schema-owner or LIVE/broker permissions. The dashboard needs
SELECT only on both new relations and their provenance joins. No new role/grant
has been applied by this engineering batch; missing access must stay UNAVAILABLE.

Replace reviewed M1 generic bindings with the certified work type and approve the
immutable `paper_execution` configuration and genuine performance-anchor resolver.
Verify both research runs, the current promotion epoch, exact strategy code, real
provider manifests, market instruments and authoritative session calendar. Missing
real-data prerequisites block startup/cycles; do not install test factories.

On isolated staging, demonstrate concurrent demotion and evidence revocation,
duplicate workers, precommit process loss, committed-effects restart, risk rejection,
NO_TRADE and one authorized cost-inclusive PAPER fill with independently checked
cash/position/P&L and reconciliation receipt. Restart after lost acknowledgement
and prove exact counts stay unchanged. Verify safe ineligible reason codes,
dashboard unavailable/stale/partial states and private authenticated access.
Only then request host activation using the existing expected-pointer procedure.
Retain November 15 operational readiness as dependent on this AWS acceptance,
real feed/candidate readiness and approved configuration, rather than local CI.

For a reviewed historical certified run whose strategy has since become
ineligible, use the bounded incident procedure in
`docs/paper-historical-recovery.md`. The recovery command authenticates explicitly
selected committed history without a strategy factory or new admission; it is
not an automatic timer or a reason to re-promote a strategy. Existing HALT,
schema, accounting and commit acknowledgement gates still apply. Approval for
this engineering batch does not activate the command on AWS or alter roles.

Post-switch observability acceptance also requires a snapshot generated after
activation starts, CURRENT loopback status, and the exact sanitized selected
export served over HTTP. A rewritten retained file or mismatched frame cannot
count as a fresh export. See `docs/dashboard/activation-acceptance.md` for bounded
rotation/retry and offline validation. Host activation still requires approval.
# Persistent activation serialization

Activation and stale-lease recovery now acquire an exclusive kernel lock on
`.activation.guard` before creating, inspecting for mutation, or removing the
authenticated `.activation.lock` lease. The guard file persists after normal
completion and process death. Do not remove, replace or rotate it: all activation
and recovery writers must use this guarded implementation and the same root.
Read-only diagnosis does not acquire or create the guard.

This closes a reproduced recovery race between the final lease fingerprint read
and unlink: another recovery could remove the stale inode, allowing a new
activation lease to be created and then incorrectly deleted by the first
recovery. Fingerprint checks remain defense in depth; they are not atomic unlink.
The guard is held across quiescence, final checks, unlink and directory fsync.
Contenders fail before stopping services or writing leases. Process death releases
the kernel guard but preserves the separate lease for explicit operator recovery.
Symlink, hard-link, foreign-owner and writable-by-others guard files are rejected.

No deployment is authorized or performed by this code change. Existing approval,
service-stop, pointer/metadata consistency and PAPER-only checks still apply.
