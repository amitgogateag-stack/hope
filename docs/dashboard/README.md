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
