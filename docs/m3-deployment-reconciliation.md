# M3 deployment reconciliation record

This report separates repository evidence from historical AWS observations. No
AWS host change, PAPER execution, production DB change or deployment was performed
by this engineering batch. AWS acceptance is not established.

## Current verified repository baseline

Work began after fetching live p0-pit-context. GitHub had advanced beyond the
requested historical f17a549 release to c6aeb8a358f6ee450df2f6d93260e9b0a55c9e4c.
An earlier 32fea974 commit failed CI; its correction was already at HEAD. No new
engineering was stacked while CI was pending. Run 39061927000 completed success
and the issue #5 Bot marker matched SHA/run/attempt 1 before modification. The
clean local checkout was fast-forwarded to that exact verified parent.

The parent already publishes truthful STOPPED health after successful one-shot
acceptance. M3 reuses that service, scheduler, certified binding, risk, fills,
accounting, authenticated historical recovery and release inspection; it does not
create a parallel execution engine. There are 233 repository migrations; the
existing compatibility verifier checks the exact filenames and checksums rather
than accepting a count. No migration is added or applied to AWS by this batch.
Frozen day29 SHA remains 7e9d341742818a97accce3e2301b1c7f96596795.

## Current AWS evidence supplied by the operator (October 24, 2026)

| Item | Historical observation | Current AWS status in this batch |
| --- | --- | --- |
| Release | f17a54929a8ecb7745a62c118893b55494aeb2f1 | Rechecked; behind reviewed GitHub HEAD b9eeeb67b0b70fc71f4d782f046a3706b59a76bd |
| PostgreSQL | Active and enabled | Rechecked through read-only systemd metadata; database contents were not read |
| Schema | 233 applied migrations | Counts/checksums not rechecked on host |
| Tests | 62 PostgreSQL integration passes | Not rerun on AWS |
| Dashboard | Both legacy and operations web units active, zero reported restarts | Rechecked; ports 8787 and 8788 answer 200 at `/` |
| HTTP | Operations `/status.json` on 8788 returns 200 | Rechecked; other tested paths correctly remain unproven/404 |
| Operations | schema 1, service RUNNING, data_feed STALE | Snapshot timestamp is 2026-10-17T18:35:14.543100+00:00 |
| PAPER | `hope-paper.service` not found | Not active; no service activation attempted |
| Export scheduler | operations exporter service/timer not found | No timer installation or activation attempted |
| Authentication | Scoped PostgreSQL authentication/export verified | Current grants/connectivity not rechecked |

The operator used AWS Console → EC2 Instance Connect for the approved read-only
checks. No autonomous terminal access was granted. No private key,
credential file, environment contents, database dump or operational secret was
read or committed. The actual PAPER factory has not been identified or approved;
reviewed_module:factory is only a placeholder and must not be substituted.

`python -m hope.api.aws_readiness APPROVED_SHA` is the bounded reconciliation
probe for this pre-deployment state. It authenticates `current` as one direct
40-hex child of `releases`, then authenticates that release's Git root, origin,
HEAD, cleanliness and required artifacts rather than trusting its directory name.
The `releases` store and selected release must be direct directories, never
symlinks, and must not be group- or world-writable. Their device/inode identities,
permissions and the `current` selection are checked again after Git
authentication and after all unit/HTTP observations, so replacement during a
readiness run fails closed without exposing host paths.
The resolved deployment-root device/inode identity and non-writable mode are
captured before acquiring the shared fence, checked immediately after fence
acquisition, and checked again before reporting. The locked directory and the
pathname used for every observation therefore cannot silently diverge.
The complete release, systemd and dashboard observation holds the deployment
activation fence in shared mode. A reviewed activation holds the exclusive side,
so the probe fails closed instead of combining a pre-switch release with
post-switch service or dashboard state. It reads only fixed systemd properties
and the private loopback `8788/release.json` and `8788/status.json` documents,
emits only allow-listed fields, and returns 3 while readiness blockers remain.
The running dashboard must attest the authenticated deployed commit and the
exact PAPER-only safety contract (`paper_only=true`, `live_enabled=false`, and
`execution_authorized=false`); a healthy process from an older release cannot
claim readiness after the `current` pointer advances. It does not read configuration values, query
PostgreSQL, install units, restart services, deploy a release, or execute PAPER.
Operational readiness requires PostgreSQL, both web services, the export timer,
and PAPER to be enabled for reboot persistence. Long-running services must be
both active and `running`; an `active/exited` unit is not a worker. The oneshot
export service is intentionally static and is activated only by its enabled timer.
For the managed long-running units, readiness also verifies the effective
systemd recovery contract after all drop-ins: both web services must be
`Type=simple`, `Restart=on-failure`, with their reviewed 5s/10s delays; PAPER
must be `Type=notify`, `Restart=always`, with a 15s delay and 15-minute watchdog.
Merely observing a currently running process cannot substitute for crash recovery.
Systemd collection has one five-second deadline shared across every required
unit, rather than a fresh wait for each command while the deployment fence is
held. Each call receives only the remaining budget and uses HOPE's fixed-binary,
no-shell diagnostic collector with a 4 KiB stdout ceiling. Stderr is discarded,
ambient credentials are not inherited, and timeout, output flooding, nonzero
exit, spawn and decoding failures become fixed sanitized readiness errors.
Both loopback requests use the same hardened collector around fixed
`/usr/bin/curl` commands and share one five-second aggregate deadline. Curl
configuration and proxies are disabled, only HTTP is permitted, and output is
limited to 1 KiB for release identity and 16 KiB for status. The HTTP status
trailer is parsed separately from the JSON body; timeout, connection, overflow,
non-UTF-8 and malformed-framing failures expose only fixed readiness codes.

## Operations 503 diagnosis and resolution boundaries

Source and local HTTP tests establish that /operations.json returns 503 when its
configured health file is missing, unreadable or invalid. A valid STOPPED,
HALTED or UNAVAILABLE heartbeat remains truthful; 200 alone is not health. The
M1 unit reads /var/lib/hope-paper/health.json, while legacy/custom units may point
elsewhere. An inactive worker with no health file is a plausible expected cause of
the historical 503, not an observed AWS diagnosis. Permissions, malformed schema,
incorrect path and stale health require host evidence before choosing a repair.

The new read-only /readiness.json and Safety view explain missing/unreadable/
invalid/stale health, snapshot state, reported worker state, observed database
feed and unverified reconciliation separately from HTTP liveness. No synthetic
healthy file is introduced and /operations.json's fail-closed contract remains.
Last poll success differs from last persisted successful PAPER job. Failure
timestamp/code is retained within the service process and sanitized. Browser
observations age independently; READY never authorizes execution.

The new m3_audit CLI is a reviewed read-only path for exact-release/CI, isolated
DB schema/role/version/count, fixed systemd metadata and loopback HTTP observations.
It makes no service/configuration changes. Use the existing host-readiness script
to check bindings and install/export state. An actual AWS 503 resolution remains
blocked until these diagnostics can be run through approved access and reviewed.

## Pending operational gates

AWS readiness also requires the deployment-root pathname itself to be a direct,
trusted directory. It rejects symlink redirection and device/inode replacement
between pathname authentication and canonical resolution before taking the
shared activation fence or running any release, systemd, or loopback probe.
Every HOPE systemd unit must also resolve to its reviewed
`/etc/systemd/system/<unit>` fragment with no effective drop-ins; an alternate
fragment or override cannot claim readiness even if liveness and restart-policy
properties appear healthy. The installed fragment bytes must match the
corresponding template inside the authenticated deployed release, and systemd
must report `NeedDaemonReload=no`; edited-on-disk or stale loaded definitions
therefore fail closed. PostgreSQL remains distribution-managed.

1. Authenticated console/instance access and exact target confirmation.
2. Read-only AWS reconciliation under scoped database environments; resolve
   demonstrated drift through separately approved host changes only.
3. Actual factory implementation/dependency review, real research/PIT/session/
   authorization prerequisites and exact selected run ID.
4. Separate authorization for one bounded cycle on hope_m3_restore_test.
5. Actual outcome, no-replay restart and authenticated reconciliation evidence.
6. Private dashboard result review and separately approved persistent startup.

No local PostgreSQL 17 or synthetic fixture result substitutes for PostgreSQL 16
AWS execution. Preserve the production DB and frozen baseline. See
[operator runbook](m3-operator-runbook.md) for exact gated command, evidence,
failure preservation, HALT and rollback procedures. Deadline remains November 15,
2026. Until operational prerequisites are resolved, M3 CONTROLLED PAPER ACCEPTED
must not be claimed.
