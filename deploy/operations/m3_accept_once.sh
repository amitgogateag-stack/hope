#!/usr/bin/env bash
# Operator-approved isolated acceptance only; no systemd or deployment action.
set -Eeuo pipefail
if [[ $# != 1 || $1 != --approve-one-paper-cycle ]]; then
  echo 'M3_BLOCKED: explicit one-cycle authorization required' >&2
  exit 2
fi
: "${HOPE_M3_RELEASE:?Reviewed immutable release path required}"
: "${HOPE_M3_PYTHON:?Reviewed interpreter path required}"
: "${HOPE_DEPLOYED_COMMIT:?Exact green release SHA required}"
: "${HOPE_M3_CI_RUN_ID:?Exact-head successful CI run required}"
: "${HOPE_M3_CI_ATTEMPT:?Exact CI attempt required}"
: "${HOPE_PAPER_FACTORY:?Factory not reviewed or configured}"
: "${HOPE_M3_RUN_ID:?Explicit reviewed single run UUID required}"
: "${HOPE_M3_EVIDENCE_DIR:?New private evidence directory required}"
[[ $HOPE_M3_RELEASE == /* && $HOPE_M3_PYTHON == /* && $HOPE_M3_EVIDENCE_DIR == /* ]] || exit 2
[[ ${HOPE_EXECUTION_MODE:-} == PAPER && ${HOPE_LIVE_ENABLED:-} == false ]] || exit 2
export PYTHONPATH="$HOPE_M3_RELEASE/src"
umask 0077
cd "$HOPE_M3_RELEASE"
exec timeout --signal=TERM --kill-after=10s 180s "$HOPE_M3_PYTHON" -m hope.api.paper_acceptance \
  --approve-one-paper-cycle --expected-database hope_m3_restore_test \
  --release "$HOPE_M3_RELEASE" --expected-sha "$HOPE_DEPLOYED_COMMIT" \
  --ci-run-id "$HOPE_M3_CI_RUN_ID" --ci-run-attempt "$HOPE_M3_CI_ATTEMPT" \
  --factory "$HOPE_PAPER_FACTORY" --run-id "$HOPE_M3_RUN_ID" \
  --evidence-dir "$HOPE_M3_EVIDENCE_DIR"
