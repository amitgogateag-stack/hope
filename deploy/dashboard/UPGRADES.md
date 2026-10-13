# HOPE dashboard: pinned, rollback-capable upgrades

The dashboard source is separately installed under `/opt/hope`. The frozen Day 29 checkout under `/home/ssm-user/hope-day29/hope_day29` must never be changed by dashboard upgrades.

The upgrade script requires root privileges, a clean checkout, an active dashboard service, the expected GitHub origin and an exact **40-character approved commit SHA**. It fetches `p0-pit-context` and refuses to continue if the branch has moved past the approved SHA. It also rejects non-descendant commits. It stages the new source under a temporary directory, runs seven dashboard-only regression suites, then switches the dashboard checkout and restarts only `hope-dashboard-web.service`. It verifies the loopback `/status.json` endpoint. On errors after switching, it restores the previous commit and attempts to restart the old service.

The script never starts/stops PAPER jobs, runs database migrations, modifies PostgreSQL credentials, opens an AWS security-group port, or updates the frozen baseline.

## Operator checklist

1. Verify the approved exact SHA has a **successful exact-head GitHub CI**. Do not deploy a queued or failed run.
2. Verify `/opt/hope` has no local changes, and the service is already running. Do not run the upgrade if either check fails.
3. Obtain the upgrade script from the **same approved SHA**, review it, and run `sudo bash upgrade_dashboard.sh <approved-sha>`. Do not pipe an unverified remote shell script directly into root.
4. Confirm the output says the service is running, and inspect `systemctl status hope-dashboard-web.service`. The database feed may remain UNAVAILABLE on the older EC2 database schema; this is not a failed web deployment.
5. On unexpected behavior, stop and report the exact error. The rollback is best effort; verify the previous service explicitly.

**Bootstrap note:** Existing EC2 v0.3 installations do not yet contain this script. For the first upgrade, fetch the exact approved commit into the separate `/opt/hope` checkout, extract `deploy/dashboard/upgrade_dashboard.sh` from that verified commit to a temporary file, and run it. Do not replace the checkout before running the script.

The service remains accessible only through a private Session Manager or SSH port forward to `127.0.0.1:8787`. A successful upgrade does not imply trading readiness or prove a strategy edge.

## v0.5 acceptance gates

The staged regression suite now includes the legacy research inventory tests and the read-only EC2 diagnostic contract. After restart, the script checks the loopback server's health, verifies the v0.5 Research Lab markers in the actual served HTML, and checks that both data endpoints either return their own valid, segregated source or the expected HTTP 503 missing-data response. An absent legacy inventory or newer PAPER snapshot is **not** interpreted as zero activity. Any unexpected HTTP response or malformed source triggers rollback to the previous dashboard commit. These checks do not require a PostgreSQL credential and do not enable the exporter.

## Aggregate snapshot response minimization

The private HTTP server treats snapshot JSON as untrusted local input even when the exporter is trusted. After verifying the complete expected schema, it **constructs a new JSON response from an explicit allowlist**: version, source, generated timestamp, research run count, PAPER environment state and four PAPER job counts. Unexpected nested or top-level fields are never returned, including if a malformed exporter writes them into the snapshot file. Integer counts are bounded to JavaScript's safe-integer range, and Boolean schema versions are rejected. The independent legacy research endpoint retains its own allowlist. These are defensive HTTP output boundaries, not changes to the database or PAPER runtime.

## Bounded post-restart acceptance (v0.5)

The deployment now runs `hope.api.dashboard_upgrade_probe` rather than an inline one-shot HTTP check. It performs the same release-specific HTML and independent feed checks with a 32-KiB maximum response, no proxies or HTTP redirects. A refused connection or socket timeout is retried up to five attempts, one second apart, to tolerate normal systemd startup latency. An HTTP error, wrong release, malformed response, unexpected feed, or non-loopback URL fails immediately and triggers rollback. The probe is independently unit-tested and included in the staged pre-switch tests. The acceptance probe is not a database readiness check and does not enable exporters.
