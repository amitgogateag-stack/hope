# HOPE dashboard — read-only visual prototype

Open `preview.html` locally in a browser. This is an **interactive navigation prototype**, not a deployed dashboard, database connection, or real-time monitor.

The four screens are Overview, Research Lab, PAPER Operations, and Safety & Engineering. No broker actions or market-data network calls exist in this preview. Runtime quantities are explicitly **Not connected**, never fabricated or silently displayed as zero. CI #1162 and commit 6f25849f are a fixed, dated engineering snapshot.

## Integration acceptance criteria

1. Implement a separately authenticated, read-only projection boundary for database-backed research, PAPER operations and control-plane state. Do not allow direct UI database writes.
2. Represent **unknown**, **unavailable**, **stale**, and **known zero** distinctly. Never infer a zero trade count or positive edge from missing records.
3. Show dataset provenance, strategy version, OOS evidence and cost assumptions beside any research verdict.
4. Show PAPER job status, lifecycle identity, reconciliation/audit state and portfolio accounting with explicit as-of timestamps.
5. Keep LIVE submission unavailable and risk limits unchanged. Require independent security and operational validation before any deployment.
6. Add tests for read-only behavior, missing/stale data, provenance mismatches, and unauthorized access before integrating production data.

This visual slice does not certify end-to-end trading readiness.

## Verified local data bridge (v0.2)

The Python module `hope.api.dashboard_snapshot` exports a **read-only, repeatable-read** PostgreSQL snapshot with only aggregate research-run counts, PAPER job-run counts, and PAPER environment state. It does not expose orders, account IDs, credentials, positions, strategy decisions, or P&L.

On a trusted HOPE host, with a **dedicated database role granted SELECT only**, run:

```bash
HOPE_DATABASE_URL='postgresql+psycopg://READ_ONLY_ROLE:...@HOST/hope' \\
  python -m hope.api.dashboard_snapshot --output /private/hope/dashboard.json
```

Then open `preview.html` and select the exported JSON file. It is read in the browser; no upload or API call occurs. If the snapshot is older than one hour, the dashboard explicitly marks it **STALE**. If the snapshot is invalid or incomplete, it is rejected. Missing data stays **Not connected**.

**Not yet automatic:** Serving this through a private authenticated dashboard, scheduling snapshot exports, and integrating detailed PAPER / research evidence require infrastructure deployment and authorization. Do not publish private snapshots on GitHub Pages or in this public repository.

## Private EC2 dashboard (v0.3)

The deployment bundle under deploy/dashboard/ provides a loopback-only HTTP server and systemd units. The exporter runs every five minutes using a dedicated PostgreSQL SELECT-only role. The browser refreshes the snapshot every minute and shows STALE when the database snapshot is over one hour old. Missing or invalid data resets the metrics to Not connected.

Access the UI through an authenticated SSH tunnel, never an open security-group port. Example: ssh -N -L 8787:127.0.0.1:8787 EC2_USER@EC2_HOST. Then visit http://127.0.0.1:8787/ on your computer. See deploy/dashboard/README.md for installation and rollback instructions. The dashboard does not submit broker orders, operate the scheduler or modify risk policy.

The repository contains deployment instructions and service definitions, not a deployed instance or database credentials. An operator must install these on EC2 after verifying the environment.

## Runtime visibility (v0.4)

The private web service now serves `GET /status.json` with separate `service: RUNNING` and `data_feed: CURRENT|STALE|UNAVAILABLE` indicators. This endpoint reports **only** the web service liveness and the availability/freshness of a structurally verified local snapshot; it is not proof that the trading engine is running. The UI checks it every minute through the existing loopback-only connection. It never reports missing tables as zero activity. If the database schema is older than required, `UNAVAILABLE` is expected until a separately approved migration or compatible read model exists.

The dashboard no longer displays a hard-coded old CI success as if it were current. GitHub Actions remains authoritative for release CI.

## Recovery observations (v0.6, development only)

The same read-only PostgreSQL transaction now exports aggregate claims with and
without durable effects, claims created more than one hour before the database
snapshot time, and stored reconciliation receipts for successful PAPER jobs.
The recovery panel includes the snapshot timestamp and stale indicator. Older
snapshots without this extension show unavailable recovery observations rather
than inferred zeros. These are observations, never authorization to replay or
reconcile a job: ledger presence alone does not verify topology or lineage.

The HTTP server validates cross-count bounds and whitelists each recovery field.
Extra file fields, identifiers, payloads and credentials cannot pass through the
snapshot endpoint. The existing dedicated SELECT-only role needs SELECT on
`paper_effects` and `audit_events` as well as the previous dashboard tables.
No deployment, role grant, migration or AWS configuration change is performed by
this development batch; use the existing authenticated SSH tunnel only after
operator approval. Authenticated stored AWS data has not been exercised unless
an explicitly configured database connection is available.

## Bounded database reads

The exporter uses a libpq connection timeout of 10 seconds per connection target,
a PostgreSQL statement timeout of 15 seconds and a lock timeout of 3 seconds.
Statement and lock settings are transaction-local; they do not change the
authenticated database role, role grants or subsequent pooled transactions.
An interrupted or timed-out read leaves the last complete snapshot byte-for-byte
intact, and creates no partial publication. A later successful export can recover
without replaying PAPER work.

For focused tests or trusted application callers, the Python export function
accepts `connect_timeout_seconds` (integer 1 through 60), `statement_timeout_ms`
(integer 1 through 120000), and `lock_timeout_ms` (integer 1 through the statement
timeout). Snapshot construction accepts the two transaction settings. Zero,
booleans, strings and non-integers are rejected before connecting or issuing SQL.
The CLI uses the documented defaults and accepts no arbitrary database settings.

These are database-operation budgets, not an end-to-end freshness guarantee or a
wall-clock deadline for all network failures. Multiple statements can each use
their own budget, and multi-host connection strings can try multiple targets.
The existing one-hour stale threshold still applies to the retained snapshot.
Local PostgreSQL contention and slow-read tests validate rollback, preservation
and recovery; they do not certify AWS connectivity or deployment. Independent browser freshness and overlapping reads are handled by the M1
watchdog and serialized refresh boundary below.

## Integrated operational read model (M1, source only)

Snapshot schema v1 remains compatible with old files. Its optional `projections`
object provides separately verified AVAILABLE or UNAVAILABLE sections for:

- datasets: registered count and stored PIT-certified metadata count;
- strategies: registered strategy-version count;
- experiments: fixed status buckets, including OTHER for unknown stored labels;
- decisions: the six stored research-decision verdict counts;
- PAPER activity: latest successful job completion's logical time and successful
  stored NO_TRADE decisions, distinct from the service's last successful poll;
- market data: stored bar count and latest event/available timestamps, with
  freshness policy explicitly UNKNOWN;
- portfolio: UNAVAILABLE until an independent accounting projection is verified.

Optional table, column or SELECT-permission gaps roll back a read-only savepoint
and leave that section UNAVAILABLE. They never manufacture zero or invalidate
other available sections. Permission to the five required base tables remains
necessary for the original snapshot. Optional SELECT sources are `datasets`,
`strategy_versions`, `experiments`, `research_decisions`,
`paper_no_trade_decisions` and `market_bars`; operators must review grants
separately. Neither this code nor its tests grant access on AWS. Timeout errors
continue to abort export and preserve the previous complete file. Future source
market timestamps make the market-data section UNAVAILABLE while retaining
other verified sections. Latest timestamps do not prove active-instrument
coverage, calendar/session eligibility or a configured freshness policy.

The optional server argument `--operations-status-file PATH` projects the local
service health file through `GET /operations.json`; its default is `operations.json`
beside the database snapshot. Missing, partial or invalid health returns 503 with
a generic body, independently of web liveness and database feed health. The
projection whitelists timestamps, process counters, PAPER-only/LIVE-disabled
flags, fixed error codes, an optional exact deployed commit and schema
compatibility. A deployed SHA and successful service poll are not CI success or
proof of a completed PAPER job. Process counters reset on restart.

The browser ages all loaded data every second, including offline snapshots:
database/legacy aggregates become STALE after one hour; service heartbeats after
five minutes; web health becomes UNKNOWN after 90 seconds without confirmation.
Each endpoint has a 10-second AbortController deadline and one complete
read/apply cycle at a time. Late reads cannot overwrite a newly selected local
file or a newer accepted generation. A failed database-snapshot refresh retains the last verified snapshot, explicitly
labels the refresh unavailable and keeps re-aging it into STALE without a
successful network response. A later successful refresh clears the failure
notice. Without a prior verified snapshot, failure remains unavailable. Other
failed feeds clear their independent observations; none infer zero or CURRENT.

Behavior tests execute the exact shipped JavaScript in Node with deterministic
clock/network surfaces. CI explicitly installs Node 22; a local alternative can
be selected through `HOPE_NODE`. PostgreSQL tests use isolated migrated schemas
and a real restricted role to verify zero, stored counts, denied permissions,
timeouts and recovery. This validates source behavior, not an AWS deployment or
authenticated production data. No credentials, raw rows or identifiers are
projected. Broker actions and LIVE execution remain unavailable.

## M2 certification and execution observations

Optional snapshot-v1 projections now add a strategy catalog bounded to 20
strategy-version UUIDs sorted by UUID, a total count and explicit truncation.
Family codes, candidate states and India/USA markets use fixed vocabularies;
arbitrary strategy names, rationale, account/instrument identifiers and raw
trades remain excluded. Unknown family labels become OTHER. The safe bounded
strategy-version UUID is the sole intentional identifier exception to the M1
aggregate-only interface. Candidate state is stored classification metadata, not
current eligibility authorization.

Dataset version observations distinguish total, sealed, sealed PIT-certified
metadata and sealed manifest-backed counts. These metadata counts cannot prove
full certified-chain validity or policy freshness. PAPER execution observations
show fills, distinct applied fills, applications, accounting receipts and missing
application/accounting counts. No currency amounts or portfolio truth are
inferred. Failed PAPER jobs use a fixed reason vocabulary plus OTHER. Recorded
NO_TRADE decisions include the latest logical decision time and an explicit
count still owned by CLAIMED jobs. Future scheduled work remains UNKNOWN.

Authorization receipts and certified outcome receipts are historical observations.
Receipts survive candidate demotion; current OPERATIONAL classification count is
shown separately and current certified-chain eligibility remains UNKNOWN. Outcome
records are grouped into NO_TRADE, RISK_BLOCKED and FILLED, with CLAIMED ownership
reported explicitly. Risk blocks use fixed existing portfolio/intelligence reason
buckets plus OTHER; arbitrary stored reason text is never projected. Their provenance joins must be structurally supported and
consistent. Missing source columns/tables, denied SELECT or corrupt receipt
identity makes the affected section UNAVAILABLE, never a manufactured valid
zero. A known empty, fully accessible table can truthfully report zero history
while eligibility remains UNKNOWN.

Additional optional SELECT sources: strategies, strategy_versions,
current_strategy_candidate_classifications, strategy_candidate_classifications,
dataset_versions, datasets, market_data_coverage_manifests, fills, orders,
paper_portfolio_fill_applications, paper_portfolio_pnl_events, job_runs,
paper_no_trade_decisions, paper_strategy_authorizations,
paper_certified_outcomes, research_runs and experiments. Review grants and view
permissions independently; no role or database changes are performed by the
dashboard. Full migrated PostgreSQL tests use controlled fixtures, not production
research approval or performance evidence.

M2 deliberately raises only the HTML preview/acceptance budget to 64 KiB, enforced
by both server and probe. Snapshot JSON remains capped at 32 KiB, optional health
JSON at 8 KiB. The page retains M1 compatibility markers and all timeout,
serialization, retained-snapshot, generation-fencing and independent aging
behavior. New observations also age into STALE. None of these source changes
deploys services, certifies production stored data or enables LIVE trading.
