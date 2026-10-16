#!/usr/bin/env bash
# Read-only readiness inspection for the existing HOPE EC2 dashboard.
# Never prints environment files, passwords, row contents or connection strings.
set -Eeuo pipefail

if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != "--help") ]]; then
  echo "Usage: bash deploy/dashboard/inspect_ec2_readiness.sh [--help]" >&2
  exit 2
fi
if [[ ${1:-} == "--help" ]]; then
  echo "Inspect HOPE dashboard web, snapshot and PostgreSQL schema without changes."
  exit 0
fi

echo "=== HOPE DASHBOARD READINESS (READ-ONLY) ==="
if [[ -d /opt/hope/.git ]]; then
  printf 'Dashboard source commit: '
  git -C /opt/hope -c safe.directory=/opt/hope rev-parse HEAD 2>/dev/null || echo "UNAVAILABLE"
else
  echo "Dashboard source: NOT INSTALLED"
fi

if systemctl is-active --quiet hope-dashboard-web.service; then
  echo "Dashboard web service: ACTIVE"
else
  echo "Dashboard web service: NOT ACTIVE"
fi

if command -v curl >/dev/null 2>&1; then
  if curl --noproxy '*' --silent --fail --max-time 5 \
    http://127.0.0.1:8787/status.json |
    python3.13 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    assert d.get("schema_version") == 1
    assert d.get("service") == "RUNNING"
    assert d.get("data_feed") in ("CURRENT", "STALE", "UNAVAILABLE")
    print("Dashboard HTTP: RUNNING; database feed:", d["data_feed"])
except (ValueError, AssertionError, TypeError, KeyError):
    sys.exit(1)
' 2>/dev/null; then
    :
  else
    echo "Dashboard HTTP: NOT VERIFIED (older release or unavailable)"
  fi
else
  echo "Dashboard HTTP: curl not installed"
fi

if [[ -e /var/lib/hope-dashboard/snapshot.json ]]; then
  echo "Snapshot file: PRESENT (content not displayed)"
else
  echo "Snapshot file: MISSING"
fi

if ! command -v sudo >/dev/null 2>&1; then
  echo "Database schema: NOT CHECKED (sudo unavailable)"
  exit 0
fi
if ! sudo -n -u postgres psql -X -qAt -d hope -v ON_ERROR_STOP=1 \
  -c "SELECT 1" >/dev/null 2>&1; then
  echo "Database schema: NOT CHECKED (no noninteractive postgres access)"
  exit 0
fi

echo "=== REQUIRED DASHBOARD TABLES ==="
sudo -n -u postgres psql -X -qAt -d hope -v ON_ERROR_STOP=1 <<'SQL'
SELECT required.name || ': ' ||
  CASE WHEN to_regclass('public.' || required.name) IS NULL
       THEN 'MISSING' ELSE 'PRESENT' END
FROM (VALUES ('research_runs'), ('job_runs'),
             ('paper_environment_control_events'), ('paper_effects'),
             ('audit_events')) AS required(name)
ORDER BY required.name;
SQL

echo "=== DASHBOARD READ-ONLY ROLE ==="
sudo -n -u postgres psql -X -qAt -d hope -v ON_ERROR_STOP=1 <<'SQL'
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = 'hope_dashboard'
) THEN 'Role: PRESENT' ELSE 'Role: MISSING' END;
SELECT 'Database: ' ||
  CASE WHEN EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'hope_dashboard'
      AND rolsuper = false AND rolcreatedb = false AND rolcreaterole = false
  ) THEN 'NO ADMIN ROLE FLAGS' ELSE 'NOT VERIFIED' END;
SELECT 'Table SELECT-only: ' ||
  CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hope_dashboard')
   AND NOT EXISTS (
    SELECT 1 FROM (VALUES ('research_runs'), ('job_runs'),
       ('paper_environment_control_events'), ('paper_effects'),
       ('audit_events')) AS required(name)
    WHERE to_regclass('public.' || required.name) IS NULL
       OR NOT has_table_privilege(to_regrole('hope_dashboard'), to_regclass('public.' || required.name), 'SELECT')
       OR has_table_privilege(to_regrole('hope_dashboard'), to_regclass('public.' || required.name), 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
   ) THEN 'VERIFIED' ELSE 'NOT VERIFIED' END;
SQL
echo "=== END: no files, database records or services changed ==="
