import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/dashboard/inspect_ec2_readiness.sh"


def _executable(path: Path, content: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + content)
    path.chmod(0o755)


def test_http_200_without_snapshot_exporter_or_worker_is_blocked(tmp_path):
    """Reproduce the observed EC2 state without touching a real host."""
    legacy = tmp_path / "legacy"
    legacy_state = tmp_path / "legacy-state"
    ops = tmp_path / "operations"
    ops_state = tmp_path / "ops-state"
    fake_bin = tmp_path / "bin"
    for path in (legacy / ".git", legacy_state, ops, ops_state, fake_bin):
        path.mkdir(parents=True)

    _executable(fake_bin / "systemctl", '''
case "$1:$2" in
  cat:hope-dashboard-web.service) exit 0 ;;
  is-active:--quiet) [[ "$3" == hope-dashboard-web.service ]] && exit 0 || exit 3 ;;
  is-enabled:--quiet) [[ "$3" == hope-dashboard-web.service ]] && exit 0 || exit 4 ;;
  *) exit 4 ;;
esac
''')
    _executable(fake_bin / "git", '''
[[ "$*" == *"rev-parse HEAD"* ]] && { echo 1111111111111111111111111111111111111111; exit 0; }
[[ "$*" == *"status --porcelain"* ]] && exit 0
exit 1
''')
    _executable(fake_bin / "curl", '''
cat <<'JSON'
{"schema_version":1,"service":"RUNNING","data_feed":"UNAVAILABLE","snapshot_generated_at":null}
JSON
''')
    _executable(fake_bin / "ss", '''
[[ "$*" == *8787* ]] && echo 'LISTEN 0 5 127.0.0.1:8787 0.0.0.0:*'
''')
    _executable(fake_bin / "sudo", "exit 1\n")

    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOPE_INSPECT_LEGACY_ROOT": str(legacy),
        "HOPE_INSPECT_LEGACY_STATE": str(legacy_state),
        "HOPE_INSPECT_OPS_ROOT": str(ops),
        "HOPE_INSPECT_OPS_STATE": str(ops_state),
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=environment,
                            capture_output=True, text=True, check=True)
    assert "Legacy dashboard web: ACTIVE/ENABLED" in result.stdout
    assert "Legacy dashboard HTTP: RUNNING; feed=UNAVAILABLE" in result.stdout
    assert "Legacy snapshot: MISSING" in result.stdout
    assert "M1 PAPER worker: NOT INSTALLED" in result.stdout
    assert "M1 dashboard export timer: NOT INSTALLED" in result.stdout
    assert "Legacy dashboard readiness: WEB_ONLY" in result.stdout
    assert "M1 operations readiness: NOT_INSTALLED" in result.stdout
    assert "Overall readiness: BLOCKED" in result.stdout


def test_public_listener_can_never_report_operator_ready():
    script = SCRIPT.read_text()
    assert "private_bindings=0" in script
    assert "ops_prepared == 1 && $ops_active == 1 && $ops_feed == 1 && $ops_snapshot == 1" in script
    assert "database_ready == 1 && $private_bindings == 1 && $resources_ready == 1" in script
