# HOPE legacy research inventory (read-only)

The existing EC2 PostgreSQL database may have only the early migrations (001–006). The newer dashboard v1 exporter requires `research_runs`, `job_runs` and `paper_environment_control_events`, so it must remain disabled on an incompatible database.

`src/hope/api/legacy_research_inventory.py` is a **separate diagnostic**, not a substitute for the dashboard snapshot. It reads metadata first, then (only if the early schema matches) counts registered experiments, strategy versions and datasets. These counts **do not** mean completed research runs, profitable strategies, proven edge, validated data, successful PAPER jobs or real P&L. The result explicitly has `paper: null` and source `hope_legacy_research_inventory`, which the dashboard v1 refuses to treat as a live snapshot.

To use it later, provision a dedicated PostgreSQL login role with `SELECT` only on `public.experiments`, `public.strategy_versions` and `public.datasets`; enforce default read-only transactions. Pass its connection URL only via the local environment variable `HOPE_LEGACY_READONLY_DATABASE_URL` (never paste passwords in ChatGPT, shell history or GitHub). The tool runs `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` and does not write files or change tables.

Example after the role is securely provisioned: `/opt/hope/.venv/bin/python -m hope.api.legacy_research_inventory`. Run this only on the authorized EC2 environment with a securely configured read-only credential.

If a required table or column is absent, it returns `schema_compatible: false` and does not query any count. If access is denied or another database error occurs, it fails rather than falling back to elevated credentials. No schema migrations are performed.


## Optional dashboard legacy inventory feed (v0.5)

The dashboard web server can now serve a **separate** read-only endpoint
`GET /legacy-research.json` from the local file
`/var/lib/hope-dashboard/legacy-research.json`. This is distinct from
`/snapshot.json`: legacy registrations are never interpreted as PAPER jobs,
completed backtests, proven strategies, risk limits or financial results.
If the file is missing, corrupt, incompatible or future-dated, the endpoint
returns HTTP 503 and the Research Lab remains **Not connected**.

The legacy inventory exporter accepts
`--output /var/lib/hope-dashboard/legacy-research.json` and writes
atomically only when the early-schema inventory is compatible. The dedicated
database login must have SELECT privileges on exactly the three legacy
research tables and no write permissions. Run it with a securely supplied
`HOPE_LEGACY_READONLY_DATABASE_URL`, as the dedicated `hope-dashboard`
service user so the private JSON file is readable by the web service. Never
store credentials in GitHub, a shell command or the exported JSON.

This does **not** install or enable a timer. First obtain a read-only
schema/permission audit and explicit approval for any automated export.
An absent feed is expected on EC2 until that operator setup is complete.
