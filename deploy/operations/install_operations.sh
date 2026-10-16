#!/usr/bin/env bash
# Operator-invoked installation only; never invoked by engineering or CI.
set -Eeuo pipefail
[[ ${EUID} == 0 && $# == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'Usage: sudo bash install_operations.sh APPROVED_SHA' >&2; exit 2; }
ROOT=/opt/hope-operations
bash "$ROOT/current/deploy/operations/preflight.sh" "$1"
[[ -z $(ss -H -ltn 'sport = :8788') ]] || { echo 'OPERATIONS_PORT_IN_USE' >&2; exit 2; }
units=(hope-paper.service hope-ops-dashboard-export.service hope-ops-dashboard-export.timer hope-ops-dashboard-web.service)
for unit in "${units[@]}"; do
    [[ ! -e /etc/systemd/system/$unit ]] || { echo 'OPERATIONS_EXISTING_UNIT_REQUIRES_REVIEW' >&2; exit 2; }
done
# No grant, migration, user creation, network fetch or automatic PAPER enablement.
installed=()
rollback_install() {
    local result=$?
    trap - ERR
    for unit in "${installed[@]}"; do rm -f -- "/etc/systemd/system/$unit"; done
    systemctl daemon-reload || true
    echo 'OPERATIONS_INSTALL_ROLLED_BACK_NEW_UNITS_ONLY' >&2
    exit "$result"
}
trap rollback_install ERR
for unit in "${units[@]}"; do
    install -m 0644 "$ROOT/current/deploy/operations/$unit" "/etc/systemd/system/$unit"
    installed+=("$unit")
done
systemctl daemon-reload
trap - ERR
echo 'UNITS_INSTALLED_DISABLED_COMPLETE_DATABASE_AND_OPERATOR_ACCEPTANCE_BEFORE_ENABLEMENT'
