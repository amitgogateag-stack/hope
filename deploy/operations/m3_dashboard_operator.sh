#!/bin/bash
# Operator-side dispatcher only. Never stages, migrates, grants, or starts PAPER.
set -euo pipefail
action=${1:-preflight}
case "$action" in
  preflight|status|paper-audit) [[ $# -le 1 ]] || exit 2 ;;
  activate|rollback) [[ $# == 2 && $2 == --approve-dashboard-change ]] || {
    printf '%s\n' 'DASHBOARD_EXPLICIT_OPERATOR_APPROVAL_REQUIRED' >&2; exit 2;
  } ;;
  *) printf '%s\n' 'DASHBOARD_OPERATOR_ACTION_INVALID' >&2; exit 2 ;;
esac

# Identity only; never source an environment/credential file or print its values.
: "${HOPE_DASHBOARD_TARGET:?Reviewed target SHA required}"
[[ $HOPE_DASHBOARD_TARGET =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ $HOPE_DASHBOARD_TARGET != 7e9d341742818a97accce3e2301b1c7f96596795 ]] || exit 2
ROOT=/opt/hope-operations
RELEASE="$ROOT/releases/$HOPE_DASHBOARD_TARGET"
BOOTSTRAP="$RELEASE/deploy/operations/dashboard_release.py"
[[ -f $BOOTSTRAP ]] || { printf '%s\n' 'DASHBOARD_REVIEWED_RELEASE_NOT_STAGED' >&2; exit 2; }
arguments=("$action")
if [[ $action != status && $action != paper-audit ]]; then
  : "${HOPE_DASHBOARD_SHARED:?Independently verified shared SHA required}"
  [[ $HOPE_DASHBOARD_SHARED =~ ^[0-9a-f]{40}$ ]] || exit 2
  arguments+=(--target "$HOPE_DASHBOARD_TARGET" --expected-current "$HOPE_DASHBOARD_SHARED")
fi
if [[ $action == preflight || $action == activate ]]; then
  : "${HOPE_DASHBOARD_CI_RUN_ID:?Exact-head successful CI run required}"
  : "${HOPE_DASHBOARD_CI_ATTEMPT:?Exact-head CI attempt required}"
  [[ $HOPE_DASHBOARD_CI_RUN_ID =~ ^[1-9][0-9]*$ && $HOPE_DASHBOARD_CI_ATTEMPT =~ ^[1-9][0-9]*$ ]] || exit 2
  arguments+=(--ci-run-id "$HOPE_DASHBOARD_CI_RUN_ID" --ci-run-attempt "$HOPE_DASHBOARD_CI_ATTEMPT")
fi
if [[ $action == activate || $action == rollback ]]; then arguments+=(--approve-dashboard-change); fi

# Isolated Python ignores ambient Python import/startup variables. The reviewed
# bootstrap explicitly loads this release. sudo's normal env_reset policy must
# remain enabled; never use sudo -E or preserve loader/Python settings.
exec /usr/bin/sudo --preserve-env=GH_TOKEN "$ROOT/venv/bin/python" -I -B \
  "$BOOTSTRAP" "${arguments[@]}"
