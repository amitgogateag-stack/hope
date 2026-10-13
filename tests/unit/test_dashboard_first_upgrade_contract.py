"""Contract checks for the first EC2 dashboard upgrade handoff."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "deploy/dashboard/FIRST_UPGRADE.md"


def _shell_block():
    doc = RUNBOOK.read_text()
    match = re.search(r"```bash\n(.*?)\n```", doc, flags=re.S)
    assert match, "first-upgrade paste block missing"
    return match.group(1)


def test_first_upgrade_paste_block_has_valid_bash_syntax():
    subprocess.run(["bash", "-n"], input=_shell_block(), text=True, check=True)


def test_first_upgrade_refuses_placeholder_without_touching_git():
    block = _shell_block()
    inner = block.split("HOPE_FIRST_UPGRADE\n", 1)[1].rsplit("\nHOPE_FIRST_UPGRADE", 1)[0]
    result = subprocess.run(["bash", "-c", inner], capture_output=True, text=True)
    assert result.returncode == 2
    assert "exact approved 40-character SHA required" in result.stderr
    assert "Fetching approved source" not in result.stdout


def test_first_upgrade_pins_verified_source_and_preserves_service():
    block = _shell_block()
    assert "sudo bash <<'HOPE_FIRST_UPGRADE'" in block
    assert "REPLACE_WITH_APPROVED_40_CHARACTER_COMMIT_SHA" in block
    assert '[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]' in block
    assert 'EXPECTED_REMOTE=https://github.com/amitgogateag-stack/hope.git' in block
    assert 'status --porcelain' in block
    assert 'systemctl is-active --quiet "$SERVICE"' in block
    assert 'rev-parse --is-shallow-repository' in block
    assert 'fetch --no-tags --unshallow origin p0-pit-context' in block
    assert 'rev-parse FETCH_HEAD' in block
    assert '== "$TARGET"' in block
    assert 'show "$TARGET:deploy/dashboard/upgrade_dashboard.sh"' in block
    assert 'bash -n "$STAGE"' in block
    assert 'bash "$STAGE" "$TARGET"' in block
    assert "trap 'rm -f -- \"$STAGE\"' EXIT" in block
    for forbidden in (
        "git reset", "checkout --force", "psql", "systemctl restart",
        "systemctl stop", "systemctl enable", "migrate", "0.0.0.0",
        "HOPE_DATABASE_URL", "curl | bash", "wget | bash",
    ):
        assert forbidden not in block
