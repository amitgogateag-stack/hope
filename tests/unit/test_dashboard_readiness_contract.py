"""EC2 diagnostic is safe to paste and cannot mutate database or services."""
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/dashboard/inspect_ec2_readiness.sh"


def test_diagnostic_shell_syntax_and_help():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "without changes" in result.stdout


def test_diagnostic_rejects_unexpected_arguments_without_server_access():
    for args in (["--apply"], ["--db", "hope"], ["--help", "--apply"]):
        result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
        assert result.returncode == 2


def test_diagnostic_has_no_mutating_operations_or_secret_output():
    script = SCRIPT.read_text()
    for forbidden in (
        "git checkout", "git pull", "git fetch", "systemctl start",
        "systemctl restart", "systemctl enable", "systemctl stop",
        "CREATE ROLE", "GRANT ", "ALTER ROLE", "DROP ", "INSERT INTO ",
        "UPDATE public.", "DELETE FROM ", "cat /etc/hope-dashboard.env",
        "printenv", "env |", "HOPE_DATABASE_URL", "pg_dump", "psql -f",
    ):
        assert forbidden not in script
    assert "to_regclass" in script
    assert "has_table_privilege" in script
    assert "http://127.0.0.1:8787/status.json" in script
    assert "sudo -n -u postgres psql" in script
    assert "snapshot.json" in script


def test_diagnostic_uses_only_metadata_queries():
    script = SCRIPT.read_text()
    assert "SELECT 1" in script
    assert "pg_roles" in script
    assert "research_runs" in script
    assert "job_runs" in script
    assert "paper_environment_control_events" in script
