# HOPE private dashboard — EC2 deployment runbook

Status: **deployment-ready source and tests only**. These steps have NOT been executed on EC2. They require a trusted operator with access to the existing instance. No broker or LIVE-trading changes are permitted.

## Design and trust boundary

- HTTP server binds **127.0.0.1:8787 only**, and checks the Host header. There is no internet-facing dashboard port, reverse proxy, or public URL.
- Access uses the existing EC2 SSH authentication, forwarded over an SSH tunnel. Do **not** add inbound TCP 8787 to the AWS security group.
- Dedicated OS account `hope-dashboard`, no login shell. Separate PostgreSQL `hope_dashboard` role with **SELECT only** on five aggregate source tables. No database writes, no trading commands.
- Exporter produces a crash-durable atomic JSON snapshot every five minutes: the
  completed temporary file and its replacement directory entry are both synced
  before success is reported. Publication rejects symlinked or group/world-
  writable parent directories and unsafe, substituted, or multiply linked
  temporary files. Browser refreshes every minute. Missing/invalid data
  fails closed; data older than an hour is marked stale. Snapshot contains no
  identifiers, credentials, fills or P&L.
- Engineering CI is not fetched by the HTML; GitHub Actions is authoritative. Deployment health requires separate monitoring.

## Preflight — stop on any failed condition

1. Verify OS is compatible with systemd and Python 3.13 virtual environment exists at `/opt/hope/.venv`. Do not overwrite an existing HOPE service or change its Python environment. Adjust unit paths after inspecting the real installation.
2. Verify `/opt/hope` contains the approved commit and all CI checks are green. Use a separately staged checkout if the current production path differs.
3. Verify the PostgreSQL database and required tables exist: `research_runs`, `job_runs`, `paper_environment_control_events`, `paper_effects`, `audit_events`.
4. Confirm AWS security group has **no inbound 8787** rule, and that EC2 SSH access is restricted appropriately.
5. Never place passwords or private dashboard snapshots in GitHub, public S3, or a publicly served path.

## Database least privilege (run as database administrator)

Create a dedicated login role without write privileges. Use `\\password hope_dashboard` inside interactive `psql` rather than putting a password in shell history.

```sql
CREATE ROLE hope_dashboard LOGIN;
GRANT CONNECT ON DATABASE hope TO hope_dashboard;
GRANT USAGE ON SCHEMA public TO hope_dashboard;
GRANT SELECT ON TABLE public.research_runs,
  public.job_runs, public.paper_environment_control_events,
  public.paper_effects, public.audit_events TO hope_dashboard;
ALTER ROLE hope_dashboard SET default_transaction_read_only = on;
```

Do not grant membership in the existing application role, superuser, table ownership, CREATE, or INSERT/UPDATE/DELETE. Check the effective permissions before enabling the timer.

## Installation (after confirming the actual checkout path)

```bash
sudo useradd --system --home-dir /var/lib/hope-dashboard --shell /sbin/nologin hope-dashboard
sudo install -d -m 0700 -o hope-dashboard -g hope-dashboard /var/lib/hope-dashboard
sudo install -m 0600 -o root -g root /dev/null /etc/hope-dashboard.env
# Edit /etc/hope-dashboard.env using a privileged editor:
# HOPE_DATABASE_URL=postgresql+psycopg://hope_dashboard:URL_ENCODED_PASSWORD@127.0.0.1/hope
# Never paste credentials into chat, shell history or repository.
sudo install -m 0644 deploy/dashboard/hope-dashboard-export.service /etc/systemd/system/
sudo install -m 0644 deploy/dashboard/hope-dashboard-export.timer /etc/systemd/system/
sudo install -m 0644 deploy/dashboard/hope-dashboard-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start hope-dashboard-export.service
sudo systemctl status hope-dashboard-export.service --no-pager
# Confirm /var/lib/hope-dashboard/snapshot.json is 0600, owned by hope-dashboard.
sudo systemctl enable --now hope-dashboard-export.timer
sudo systemctl enable --now hope-dashboard-web.service
sudo ss -ltnp | grep ':8787'
# Must show 127.0.0.1:8787, never 0.0.0.0:8787.
```

If `hope-dashboard` already exists, inspect it rather than recreating it. If a read-only PostgreSQL connection fails, **stop**; do not use the privileged HOPE application credentials as a workaround.

## Access from your computer

```bash
ssh -N -L 8787:127.0.0.1:8787 EC2_USER@EC2_HOST
```

Open `http://127.0.0.1:8787/` in Chrome/Edge. SSH tunnel authentication is the access boundary. No ChatGPT session is needed to view or refresh the dashboard.

## Health and rollback

- Inspect: `systemctl status hope-dashboard-web hope-dashboard-export.timer` and `journalctl -u hope-dashboard-export.service -n 50 --no-pager`. Do not include credentials in logs.
- If the export fails, the previous snapshot remains intact and will age into STALE; a missing snapshot is shown as Not connected.
- Stop only the dashboard components: `sudo systemctl disable --now hope-dashboard-web.service hope-dashboard-export.timer`. Do not stop HOPE's PAPER jobs, database or existing trading-related services.
- Preserve snapshot and logs for incident investigation; do not silently fabricate new data or loosen role privileges.
- Deployment is not proof of LIVE readiness, a validated strategy edge, or an autonomous PAPER operation.

## Runtime verification after v0.4 upgrade

With the service running, `curl --noproxy '*' -fsS http://127.0.0.1:8787/status.json` should return `service: RUNNING`. `data_feed: UNAVAILABLE` is expected on EC2 until all five required source tables exist and the read-only exporter is configured; do **not** manufacture database records or apply migrations just to turn this indicator green. The health endpoint does not report PAPER engine or broker readiness.

## Read-only EC2 readiness diagnostic

To inspect the installed web service and database compatibility before attempting any database exporter setup, run the **reviewed** `deploy/dashboard/inspect_ec2_readiness.sh` from a verified GitHub release on EC2. The diagnostic requires no credentials to be pasted into ChatGPT. It checks the local dashboard service, `/status.json`, presence of the snapshot file, the five required PostgreSQL tables and (if accessible) the separate `hope_dashboard` database role and effective table privileges. It displays **metadata only**, not table records or passwords.

The diagnostic now inventories both the separately installed legacy dashboard and
the M1 operations candidate. It reports exact unit installation/active/enabled
state for the PAPER worker, exporter timer and private web service; loopback versus
public listeners on 8787/8788; snapshot file metadata without content; migration
ledger/required-table/role metadata; and memory, swap and root-disk capacity. Its
final verdict intentionally classifies an HTTP-200 dashboard with no verified
snapshot as `WEB_ONLY`, and the host remains `BLOCKED` when the autonomous PAPER
worker or exporter pipeline is absent. It also requires an exact prepared-release
symlink/metadata match and a conservative metadata resource floor: at least 2 GiB
free root disk plus either 1 GiB reported physical memory or 1 GiB swap. Passing
that floor is not a load test; failing it requires explicit capacity review.
HTTP reachability is never operational acceptance.

Missing any of `research_runs`, `job_runs`, `paper_environment_control_events`, `paper_effects` or `audit_events` means the current exporter is **not schema-compatible** with the EC2 database. The correct action is to leave the exporter disabled and investigate schema/migration state independently, not to invent metrics, grant elevated access or run a bulk migration without a reviewed plan.

The script does not install anything, change service state, write to the database or modify the frozen Day 29 checkout. If noninteractive `sudo -u postgres` is unavailable, the script reports `NOT CHECKED` rather than attempting an alternate privileged connection.

## Recovery projection compatibility

The recovery exporter requires SELECT on both `paper_effects` and `audit_events`
in addition to the original three source tables. An older installation can serve
its last complete snapshot, but export remains unavailable until an operator
reviews schema and grants. The metadata-only diagnostic checks all five sources;
it does not grant permissions or read application rows.

Upgrade acceptance supports original aggregate snapshots and snapshots with the
complete recovery extension. It rejects malformed, contradictory or extra fields
in HTTP responses. A successful loopback probe proves response compatibility,
not database connectivity, least privilege or current data: inspect the feed
status and as-of timestamp separately. Development validation does not establish
that this release is installed on AWS. No deployment, role change or stored-data
inspection has been performed by this compatibility batch.
