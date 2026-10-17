#!/usr/bin/env bash
# Read-only readiness inspection for the existing HOPE EC2 dashboard and M1 stack.
# Never prints environment files, passwords, row contents or connection strings.
set -Eeuo pipefail

if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != "--help") ]]; then
  echo "Usage: bash deploy/dashboard/inspect_ec2_readiness.sh [--help]" >&2
  exit 2
fi
if [[ ${1:-} == "--help" ]]; then
  echo "Inspect HOPE web, snapshots, services and PostgreSQL metadata without changes."
  exit 0
fi

# Overrides exist only for hermetic contract tests. Defaults are fixed host paths.
LEGACY_ROOT=${HOPE_INSPECT_LEGACY_ROOT:-/opt/hope}
LEGACY_STATE=${HOPE_INSPECT_LEGACY_STATE:-/var/lib/hope-dashboard}
OPS_ROOT=${HOPE_INSPECT_OPS_ROOT:-/opt/hope-operations}
OPS_STATE=${HOPE_INSPECT_OPS_STATE:-/var/lib/hope-dashboard-ops}

legacy_web=0
legacy_feed=0
ops_installed=0
ops_prepared=0
ops_active=0
ops_feed=0
ops_snapshot=0
database_ready=0
private_bindings=1
resources_ready=0

echo "=== HOPE AWS READINESS (READ-ONLY) ==="
if [[ -d "$LEGACY_ROOT/.git" ]]; then
  printf 'Legacy source commit: '
  git -C "$LEGACY_ROOT" -c "safe.directory=$LEGACY_ROOT" rev-parse HEAD 2>/dev/null || echo "UNAVAILABLE"
  if [[ -n $(git -C "$LEGACY_ROOT" -c "safe.directory=$LEGACY_ROOT" status --porcelain 2>/dev/null || true) ]]; then
    echo "Legacy worktree: NOT CLEAN"
  else
    echo "Legacy worktree: CLEAN OR UNAVAILABLE"
  fi
else
  echo "Legacy source: NOT INSTALLED"
fi
if [[ -L "$OPS_ROOT/current" ]]; then
  release_id=$(readlink -f "$OPS_ROOT/current" 2>/dev/null | sed 's#.*/##' || true)
  if [[ $release_id =~ ^[0-9a-f]{40}$ && -f "$OPS_ROOT/deployed.env" && ! -L "$OPS_ROOT/deployed.env" ]] && \
      [[ $(<"$OPS_ROOT/deployed.env") == "HOPE_DEPLOYED_COMMIT=$release_id" ]]; then
    echo "M1 prepared release: VERIFIED $release_id"
    ops_prepared=1
  else
    echo "M1 prepared release: INCOMPLETE_OR_MISMATCHED"
  fi
else
  echo "M1 prepared release: NOT INSTALLED"
fi

unit_state() {
  local unit=$1 label=$2
  if ! systemctl cat "$unit" >/dev/null 2>&1; then
    echo "$label: NOT INSTALLED"
    return 1
  fi
  local active=INACTIVE enabled=DISABLED
  systemctl is-active --quiet "$unit" && active=ACTIVE
  systemctl is-enabled --quiet "$unit" && enabled=ENABLED
  echo "$label: $active/$enabled"
  [[ $active == ACTIVE && $enabled == ENABLED ]]
}

echo "=== SERVICES ==="
unit_state hope-dashboard-web.service "Legacy dashboard web" && legacy_web=1 || true
complete_ops=1
for spec in \
  "hope-paper.service|M1 PAPER worker" \
  "hope-ops-dashboard-export.timer|M1 dashboard export timer" \
  "hope-ops-dashboard-web.service|M1 dashboard web"; do
  IFS='|' read -r unit label <<<"$spec"
  unit_state "$unit" "$label" || complete_ops=0
done
[[ $complete_ops == 1 ]] && ops_active=1
systemctl cat hope-paper.service >/dev/null 2>&1 && \
  systemctl cat hope-ops-dashboard-export.timer >/dev/null 2>&1 && \
  systemctl cat hope-ops-dashboard-web.service >/dev/null 2>&1 && ops_installed=1 || true

python_command=''
for candidate in python3 python3.13; do
  if command -v "$candidate" >/dev/null 2>&1; then python_command=$candidate; break; fi
done
probe_feed() {
  local port=$1 label=$2 payload feed
  if [[ -z $python_command ]] || ! command -v curl >/dev/null 2>&1; then
    echo "$label HTTP: NOT CHECKED"
    return 1
  fi
  payload=$(curl --noproxy '*' --silent --fail --max-time 5 "http://127.0.0.1:$port/status.json" 2>/dev/null) || {
    echo "$label HTTP: NOT VERIFIED"
    return 1
  }
  feed=$(printf '%s' "$payload" | "$python_command" -c '
import json, sys
try:
    d = json.load(sys.stdin)
    assert set(d) == {"schema_version", "service", "data_feed", "snapshot_generated_at"}
    assert type(d["schema_version"]) is int and d["schema_version"] == 1
    assert d["service"] == "RUNNING"
    assert d["data_feed"] in ("CURRENT", "STALE", "UNAVAILABLE")
    print(d["data_feed"])
except (ValueError, AssertionError, TypeError, KeyError):
    raise SystemExit(1)
' 2>/dev/null) || {
    echo "$label HTTP: INVALID OR OLDER RELEASE"
    return 1
  }
  echo "$label HTTP: RUNNING; feed=$feed"
  [[ $feed == CURRENT ]]
}

echo "=== PRIVATE HTTP AND SNAPSHOTS ==="
probe_feed 8787 "Legacy dashboard" && legacy_feed=1 || true
probe_feed 8788 "M1 dashboard" && ops_feed=1 || true
for spec in \
  "$LEGACY_STATE/snapshot.json|Legacy snapshot" \
  "$OPS_STATE/snapshot.json|M1 snapshot"; do
  IFS='|' read -r file label <<<"$spec"
  if [[ -f "$file" && ! -L "$file" ]]; then
    stat -Lc "$label: PRESENT bytes=%s modified_epoch=%Y owner=%U mode=%a" "$file" 2>/dev/null || \
      echo "$label: PRESENT; METADATA UNAVAILABLE"
    [[ $label == "M1 snapshot" ]] && ops_snapshot=1
  else
    echo "$label: MISSING"
  fi
done
for port in 8787 8788; do
  listeners=$(ss -H -ltn "sport = :$port" 2>/dev/null || true)
  local_addresses=$(awk '{print $4}' <<<"$listeners")
  if [[ -z $listeners ]]; then
    echo "Port $port: NOT LISTENING"
  elif grep -Eq '^(0\.0\.0\.0|\[::\]|\*):' <<<"$local_addresses"; then
    echo "Port $port: PUBLIC BINDING BLOCKED"
    private_bindings=0
  elif grep -q '^127.0.0.1:' <<<"$local_addresses"; then
    echo "Port $port: LOOPBACK ONLY"
  else
    echo "Port $port: BINDING UNKNOWN"
    private_bindings=0
  fi
done

echo "=== POSTGRESQL METADATA ==="
if command -v sudo >/dev/null 2>&1 && sudo -n -u postgres psql -X -qAt -d hope \
    -v ON_ERROR_STOP=1 -c "SELECT 1" >/dev/null 2>&1; then
  mapfile -t database_checks < <(sudo -n -u postgres psql -X -qAt -d hope -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'required_tables=' || count(*) FROM (VALUES ('research_runs'), ('job_runs'),
 ('paper_environment_control_events'), ('paper_effects'), ('audit_events')) required(name)
WHERE to_regclass('public.' || required.name) IS NOT NULL;
SELECT 'migration_ledger=' || CASE WHEN to_regclass('public.hope_schema_migrations') IS NULL
 THEN 'MISSING' ELSE 'PRESENT' END;
SELECT 'dashboard_role=' || CASE WHEN EXISTS (SELECT 1 FROM pg_roles
 WHERE rolname='hope_dashboard' AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls) THEN 'LEAST_PRIVILEGE_FLAGS' ELSE 'NOT_VERIFIED' END;
SELECT 'dashboard_table_access=' || CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hope_dashboard')
 AND NOT EXISTS (SELECT 1 FROM (VALUES ('research_runs'), ('job_runs'),
 ('paper_environment_control_events'), ('paper_effects'), ('audit_events')) required(name)
 WHERE to_regclass('public.' || required.name) IS NULL
 OR NOT has_table_privilege(to_regrole('hope_dashboard'), to_regclass('public.' || required.name), 'SELECT')
 OR has_table_privilege(to_regrole('hope_dashboard'), to_regclass('public.' || required.name),
   'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'))
 THEN 'SELECT_ONLY' ELSE 'NOT_VERIFIED' END;
SQL
  )
  printf '%s\n' "${database_checks[@]}"
  [[ ${database_checks[0]:-} == required_tables=5 && \
     ${database_checks[1]:-} == migration_ledger=PRESENT && \
     ${database_checks[2]:-} == dashboard_role=LEAST_PRIVILEGE_FLAGS && \
     ${database_checks[3]:-} == dashboard_table_access=SELECT_ONLY ]] && database_ready=1
else
  echo "Database metadata: NOT CHECKED"
fi

echo "=== RESOURCE REVIEW ==="
memory_total=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
memory_available=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
swap_total=$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)
root_available=$(df -Pk / | awk 'NR==2 {print $4}')
echo "MemTotal=${memory_total}_kB MemAvailable=${memory_available}_kB SwapTotal=${swap_total}_kB"
echo "root_available_kB=$root_available"
if ((root_available >= 2097152)) && \
    ((memory_total >= 1048576 || swap_total >= 1048576)); then
  echo "Resource floor: METADATA_THRESHOLD_MET"
  resources_ready=1
else
  echo "Resource floor: CONSTRAINED_REVIEW_REQUIRED"
fi

echo "=== READINESS VERDICT ==="
if [[ $legacy_web == 1 && $legacy_feed == 1 ]]; then
  echo "Legacy dashboard readiness: OPERATIONAL FEED"
elif [[ $legacy_web == 1 ]]; then
  echo "Legacy dashboard readiness: WEB_ONLY"
else
  echo "Legacy dashboard readiness: UNAVAILABLE"
fi
if [[ $ops_installed == 0 ]]; then
  echo "M1 operations readiness: NOT_INSTALLED"
elif [[ $ops_prepared == 1 && $ops_active == 1 && $ops_feed == 1 && $ops_snapshot == 1 && \
        $database_ready == 1 && $private_bindings == 1 && $resources_ready == 1 ]]; then
  echo "M1 operations readiness: READY_FOR_OPERATOR_ACCEPTANCE"
else
  echo "M1 operations readiness: INCOMPLETE_OR_UNVERIFIED"
fi
if [[ $ops_prepared == 1 && $ops_active == 1 && $ops_feed == 1 && $ops_snapshot == 1 && \
      $database_ready == 1 && $private_bindings == 1 && $resources_ready == 1 ]]; then
  echo "Overall readiness: OPERATOR_ACCEPTANCE_REQUIRED"
else
  echo "Overall readiness: BLOCKED"
fi
echo "=== END: no files, database records or services changed ==="
