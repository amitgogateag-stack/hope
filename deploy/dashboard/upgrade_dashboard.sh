#!/usr/bin/env bash
# HOPE dashboard-only pinned-release upgrade. Never touches the Day 29 baseline.
set -Eeuo pipefail

usage() {
  echo "Usage: sudo bash deploy/dashboard/upgrade_dashboard.sh <40-character-approved-commit-sha>" >&2
  exit 2
}
[[ $# -eq 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || usage
[[ ${EUID} -eq 0 ]] || { echo "Run as root using sudo" >&2; exit 2; }

TARGET="$1"
ROOT=/opt/hope
SERVICE=hope-dashboard-web.service
EXPECTED_REMOTE=https://github.com/amitgogateag-stack/hope.git
STAGE=''
SWITCHED=0

cleanup() {
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then
    rm -rf -- "$STAGE"
  fi
}
rollback_on_error() {
  local result=$?
  trap - ERR
  if [[ "$SWITCHED" == 1 ]]; then
    echo "Upgrade failed: restoring previously running dashboard commit $PREVIOUS" >&2
    git -C "$ROOT" checkout --detach --force "$PREVIOUS" >&2 || true
    systemctl restart "$SERVICE" >&2 || true
    if systemctl is-active --quiet "$SERVICE"; then
      echo "Rollback service active; verify the old dashboard independently." >&2
    else
      echo "ALERT: rollback service did not recover; operator intervention required." >&2
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
trap rollback_on_error ERR

[[ -d "$ROOT/.git" && -x "$ROOT/.venv/bin/python" ]] || {
  echo "STOP: separate /opt/hope installation or virtual environment missing" >&2
  exit 2
}
[[ "$(git -C "$ROOT" rev-parse --show-toplevel)" == "$ROOT" ]] || {
  echo "STOP: unexpected checkout root" >&2
  exit 2
}
[[ "$(git -C "$ROOT" remote get-url origin)" == "$EXPECTED_REMOTE" ]] || {
  echo "STOP: unexpected GitHub origin" >&2
  exit 2
}
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || {
  echo "STOP: dashboard checkout has local modifications" >&2
  exit 2
}
systemctl is-active --quiet "$SERVICE" || {
  echo "STOP: dashboard service is not currently active" >&2
  exit 2
}
PREVIOUS=$(git -C "$ROOT" rev-parse HEAD)
if [[ "$PREVIOUS" == "$TARGET" ]]; then
  echo "Already on approved dashboard commit $TARGET; no changes made."
  exit 0
fi

echo "Fetching branch and verifying exact approved commit..."
git -C "$ROOT" fetch --no-tags --depth=50 origin p0-pit-context
[[ "$(git -C "$ROOT" rev-parse FETCH_HEAD)" == "$TARGET" ]] || {
  echo "STOP: branch HEAD differs from approved target; no files changed" >&2
  exit 2
}
git -C "$ROOT" merge-base --is-ancestor "$PREVIOUS" "$TARGET" || {
  echo "STOP: target is not a descendant of the installed release" >&2
  exit 2
}

STAGE=$(mktemp -d /opt/hope-dashboard-stage.XXXXXXXX)
git -C "$ROOT" archive "$TARGET" | tar -xf - -C "$STAGE"
echo "Running dashboard-only regression tests against staged source..."
(
  cd "$STAGE"
  PYTHONPATH="$STAGE/src" "$ROOT/.venv/bin/python" -m pytest -q \
    tests/unit/test_dashboard_snapshot.py \
    tests/unit/test_dashboard_server.py \
    tests/unit/test_dashboard_preview_contract.py \
    tests/unit/test_dashboard_deployment_contract.py \
    tests/unit/test_dashboard_upgrade_contract.py
)

echo "Switching dashboard source and restarting the private web service..."
SWITCHED=1
git -C "$ROOT" checkout --detach "$TARGET"
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$TARGET" ]]
systemctl restart "$SERVICE"
systemctl is-active --quiet "$SERVICE"

echo "Verifying loopback HTTP health (no database connection required)..."
curl --noproxy '*' --fail --silent --show-error --max-time 8 \
  http://127.0.0.1:8787/status.json |
  "$ROOT/.venv/bin/python" -c '
import json, sys
data = json.load(sys.stdin)
assert data.get("schema_version") == 1
assert data.get("service") == "RUNNING"
assert data.get("data_feed") in ("CURRENT", "STALE", "UNAVAILABLE")
print("Verified: web service RUNNING, database feed", data["data_feed"])
'
echo "Dashboard upgraded to verified commit $TARGET"
echo "Day 29 checkout, PostgreSQL and PAPER execution were not changed."
