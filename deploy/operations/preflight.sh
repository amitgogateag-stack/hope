#!/usr/bin/env bash
# Read-only host preflight. Never installs, migrates, grants or starts services.
set -Eeuo pipefail
if [[ ${HOPE_OPERATIONS_SANITIZED:-} != 1 ]]; then
    exec /usr/bin/env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C \
        HOPE_OPERATIONS_SANITIZED=1 /usr/bin/bash "$0" "$@"
fi
[[ $PATH == /usr/sbin:/usr/bin:/sbin:/bin && $LANG == C && $LC_ALL == C ]] || {
    echo 'OPERATIONS_ENVIRONMENT_ISOLATION_FAILED' >&2; exit 2;
}
while IFS='=' read -r name _; do
    case "$name" in PWD|SHLVL|_|PATH|LANG|LC_ALL|HOPE_OPERATIONS_SANITIZED) ;; *)
        echo 'OPERATIONS_ENVIRONMENT_ISOLATION_FAILED' >&2; exit 2 ;; esac
done < <(/usr/bin/env)
[[ $# == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'OPERATIONS_EXPECTED_SHA_REQUIRED' >&2; exit 2; }
ROOT=/opt/hope-operations
[[ $(/usr/bin/uname -s) == Linux && -d /run/systemd/system ]] || { echo 'OPERATIONS_SYSTEMD_HOST_REQUIRED' >&2; exit 2; }
for command in /usr/bin/git /usr/bin/systemctl /usr/bin/systemd-analyze /usr/bin/ss \
    /usr/bin/stat /usr/sbin/runuser /usr/bin/find /usr/bin/readlink /usr/bin/id; do
    [[ -x $command ]] || { echo 'OPERATIONS_REQUIRED_SYSTEM_BINARY_MISSING' >&2; exit 2; }
done
[[ -x "$ROOT/venv/bin/python" && -L "$ROOT/current" ]] || { echo 'OPERATIONS_PREPARED_INSTALL_REQUIRED' >&2; exit 2; }
secure_path() {
    [[ $(/usr/bin/stat -Lc '%u' "$1") == 0 && $((8#$(/usr/bin/stat -Lc '%a' "$1") & 0022)) == 0 ]] || { echo 'OPERATIONS_UNTRUSTED_EXECUTION_PATH' >&2; exit 2; }
}
for path in /opt "$ROOT" "$ROOT/releases" "$ROOT/config" "$ROOT/venv" "$ROOT/venv/bin"; do secure_path "$path"; done
secure_path "$(/usr/bin/readlink -f "$ROOT/venv/bin/python")"
secure_path "$(/usr/bin/dirname "$(/usr/bin/readlink -f "$ROOT/venv/bin/python")")"
[[ -z $(/usr/bin/find -L "$ROOT/venv" "$ROOT/current" "$ROOT/config" -xdev \( ! -user root -o -perm /022 \) -print -quit) ]] || { echo 'OPERATIONS_UNTRUSTED_RELEASE_OR_RUNTIME' >&2; exit 2; }
[[ $("$ROOT/venv/bin/python" -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")') == 3.13 ]] || { echo 'OPERATIONS_PYTHON_313_REQUIRED' >&2; exit 2; }
for name in hope-paper hope-dashboard; do
    /usr/bin/id "$name" >/dev/null
    [[ $(/usr/bin/id -u "$name") != 0 ]] || exit 2
done
for file in /etc/hope-paper.env /etc/hope-dashboard-ops.env "$ROOT/deployed.env"; do
    [[ -f "$file" && ! -L "$file" && $(/usr/bin/stat -c '%u:%a' "$file") == 0:600 ]] || { echo 'OPERATIONS_PRIVATE_ROOT_CONFIG_REQUIRED' >&2; exit 2; }
done
[[ $(/usr/bin/stat -c '%U:%G:%a' /var/lib/hope-paper) == hope-paper:hope-operations:750 ]] || { echo 'OPERATIONS_PAPER_STATE_PERMISSIONS_INVALID' >&2; exit 2; }
[[ $(/usr/bin/stat -c '%U:%G:%a' /var/lib/hope-dashboard-ops) == hope-dashboard:hope-operations:700 ]] || { echo 'OPERATIONS_DASHBOARD_STATE_PERMISSIONS_INVALID' >&2; exit 2; }
[[ -z $(/usr/bin/find -L "$ROOT/current" "$ROOT/config" -xdev \( ! -user root -o -perm /022 \) -print -quit) ]] || { echo 'OPERATIONS_CODE_OWNERSHIP_OR_MODE_INVALID' >&2; exit 2; }
for name in hope-paper hope-dashboard; do
    /usr/sbin/runuser -u "$name" -- /usr/bin/test ! -w "$ROOT/current"
    /usr/sbin/runuser -u "$name" -- /usr/bin/test ! -w "$ROOT/config"
    /usr/sbin/runuser -u "$name" -- /usr/bin/test ! -w "$ROOT/deployed.env"
done
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C \
    PYTHONPATH="$ROOT/current/src" "$ROOT/venv/bin/python" -B -s -P \
    -m hope.api.operations_deployment --release "$ROOT/current" --expected-sha "$1"
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C \
    /usr/bin/systemd-analyze verify "$ROOT/current/deploy/operations/hope-paper.service" \
    "$ROOT/current/deploy/operations/hope-ops-dashboard-export.service" \
    "$ROOT/current/deploy/operations/hope-ops-dashboard-export.timer" \
    "$ROOT/current/deploy/operations/hope-ops-dashboard-web.service"
echo 'HOST_PREFLIGHT_PASSED_DATABASE_ACCEPTANCE_STILL_REQUIRED'
