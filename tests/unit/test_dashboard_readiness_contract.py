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
    assert 'http://127.0.0.1:$port/status.json' in script
    assert 'probe_feed 8787 "Legacy dashboard"' in script
    assert 'probe_feed 8788 "M1 dashboard"' in script
    assert "sudo -n -u postgres psql" in script
    assert "snapshot.json" in script
    assert "M1 PAPER worker" in script
    assert "M1 dashboard export timer" in script
    assert "Overall readiness: BLOCKED" in script
    assert "PUBLIC BINDING BLOCKED" in script
    assert "MemAvailable:" in script and "SwapTotal:" in script
    assert "CONSTRAINED_REVIEW_REQUIRED" in script
    assert "HOPE_DEPLOYED_COMMIT" in script


def test_diagnostic_uses_only_metadata_queries():
    script = SCRIPT.read_text()
    assert "SELECT 1" in script
    assert "pg_roles" in script
    assert "research_runs" in script
    assert "job_runs" in script
    assert "paper_environment_control_events" in script
    # Every source remains in the schema-compatibility inventory.
    for table in ("research_runs", "job_runs", "paper_environment_control_events", "paper_effects", "audit_events"):
        assert script.count(f"('{table}')") >= 1


def test_deployment_grants_cover_every_exporter_source_without_write_privileges():
    root = Path(__file__).resolve().parents[2]
    runbook = (root / "deploy/dashboard/README.md").read_text(encoding="utf-8")
    grant = runbook.split("GRANT SELECT ON TABLE ", 1)[1].split(";", 1)[0]
    assert {name.strip() for name in grant.split(" TO hope_dashboard")[0].split(",")} == {
        "public.research_runs", "public.job_runs", "public.paper_environment_control_events",
        "public.paper_effects", "public.audit_events",
    }
    assert "ALTER ROLE hope_dashboard SET default_transaction_read_only = on" in runbook
