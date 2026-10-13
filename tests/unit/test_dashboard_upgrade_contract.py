"""Safety contracts for pinned, rollback-capable EC2 dashboard upgrades."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
UPGRADE = ROOT / "deploy/dashboard/upgrade_dashboard.sh"


def test_upgrade_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", str(UPGRADE)], check=True)


def test_upgrade_refuses_missing_or_invalid_commit_without_side_effects():
    for args in ([], ["latest"], ["a" * 39], ["a" * 41], ["a" * 39 + "Z"]):
        result = subprocess.run(["bash", str(UPGRADE), *args], capture_output=True, text=True)
        assert result.returncode == 2
        assert "Usage:" in result.stderr


def test_upgrade_requires_pinned_github_release_and_clean_checkout():
    script = UPGRADE.read_text()
    assert "EXPECTED_REMOTE=https://github.com/amitgogateag-stack/hope.git" in script
    assert 'rev-parse FETCH_HEAD' in script
    assert '== "$TARGET"' in script
    assert 'status --porcelain' in script
    assert 'merge-base --is-ancestor "$PREVIOUS" "$TARGET"' in script
    assert "git -C \"$ROOT\" archive \"$TARGET\"" in script
    assert "PYTHONPATH=\"$STAGE/src\"" in script
    assert "test_dashboard_server.py" in script
    assert "test_dashboard_upgrade_contract.py" in script


def test_upgrade_rolls_back_and_checks_private_health():
    script = UPGRADE.read_text()
    assert "trap rollback_on_error ERR" in script
    assert "trap cleanup EXIT" in script
    assert 'checkout --detach --force "$PREVIOUS"' in script
    assert 'systemctl restart "$SERVICE"' in script
    assert 'systemctl is-active --quiet "$SERVICE"' in script
    assert "http://127.0.0.1:8787/status.json" in script
    assert 'data.get("service") == "RUNNING"' in script
    assert "HOPE_DATABASE_URL" not in script
    assert "systemctl enable" not in script
    assert "systemctl disable" not in script
    assert "psql" not in script
    assert "migrate" not in script.lower()
    assert "0.0.0.0" not in script
    assert "reset --hard" not in script
