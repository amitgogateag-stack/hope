# HOPE dashboard — first upgrade from an older EC2 checkout

This is the one-time bootstrap for the existing dashboard at `/opt/hope`,
which does not yet contain `deploy/dashboard/upgrade_dashboard.sh`.
Run only from an authenticated AWS Systems Manager Session Manager terminal
for the HOPE-Research EC2 instance, after verifying the exact 40-character
GitHub commit has a successful exact-head CI run.

Replace the one `REPLACE_WITH_APPROVED_40_CHARACTER_COMMIT_SHA` placeholder
with the full, approved SHA. Paste the whole block at once. It performs
read-only preflight checks, fetches the approved source, extracts the upgrade
script from that exact commit into a private temporary file, validates Bash
syntax, and delegates to the staged-test/rollback-capable upgrade. It never
runs database migrations, modifies PostgreSQL, changes PAPER execution, or
touches the frozen Day 29 checkout.

```bash
sudo bash <<'HOPE_FIRST_UPGRADE'
set -Eeuo pipefail
TARGET='REPLACE_WITH_APPROVED_40_CHARACTER_COMMIT_SHA'
ROOT=/opt/hope
SERVICE=hope-dashboard-web.service
EXPECTED_REMOTE=https://github.com/amitgogateag-stack/hope.git

[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]] || { echo "STOP: exact approved 40-character SHA required" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "STOP: root required" >&2; exit 2; }
[[ -d "$ROOT/.git" && -x "$ROOT/.venv/bin/python" ]] || {
  echo "STOP: separate dashboard checkout or virtual environment missing" >&2; exit 2;
}
[[ "$(git -C "$ROOT" rev-parse --show-toplevel)" == "$ROOT" ]] || {
  echo "STOP: unexpected checkout root" >&2; exit 2;
}
[[ "$(git -C "$ROOT" remote get-url origin)" == "$EXPECTED_REMOTE" ]] || {
  echo "STOP: unexpected GitHub origin" >&2; exit 2;
}
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || {
  echo "STOP: dashboard checkout has local changes" >&2; exit 2;
}
systemctl is-active --quiet "$SERVICE" || {
  echo "STOP: existing private dashboard service is not active" >&2; exit 2;
}

echo "Fetching approved source without modifying installed files..."
if [[ "$(git -C "$ROOT" rev-parse --is-shallow-repository)" == "true" ]]; then
  git -C "$ROOT" fetch --no-tags --unshallow origin p0-pit-context
else
  git -C "$ROOT" fetch --no-tags origin p0-pit-context
fi
[[ "$(git -C "$ROOT" rev-parse FETCH_HEAD)" == "$TARGET" ]] || {
  echo "STOP: branch HEAD is not the approved SHA; no deployment" >&2; exit 2;
}
STAGE=$(mktemp /tmp/hope-dashboard-first-upgrade.XXXXXXXX)
trap 'rm -f -- "$STAGE"' EXIT
git -C "$ROOT" show "$TARGET:deploy/dashboard/upgrade_dashboard.sh" > "$STAGE"
[[ -s "$STAGE" ]] || { echo "STOP: approved upgrade script missing" >&2; exit 2; }
chmod 0600 "$STAGE"
bash -n "$STAGE"
bash "$STAGE" "$TARGET"
HOPE_FIRST_UPGRADE
```

A successful command ends with `Dashboard upgraded to verified commit ...`.
It is normal for research and PAPER data feeds to show **UNAVAILABLE**
until separately verified read-only data access is configured. Do not
enable exporters, create dummy records, expose TCP 8787 publicly, or use
application/superuser database credentials to make the display look green.

If a check fails, stop and report the error; do not use forced checkout,
bypass CI or run migrations. The upgrade attempts rollback if the service
switch fails, but recovery must be verified independently.
