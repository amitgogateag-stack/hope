"""Behavioral checks execute the exact shipped script, not a mirrored model."""
import os
from pathlib import Path
import shutil
import subprocess


def test_dashboard_javascript_timing_and_read_only_observations():
    node = os.environ.get("HOPE_NODE") or shutil.which("node")
    assert node, "Node.js is required for dashboard behavioral checks; CI installs Node 22"
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs/dashboard/preview.html").stat().st_size <= 65536
    result = subprocess.run(
        [node, str(root / "tests/browser/dashboard_m1_behavior.cjs"), str(root / "docs/dashboard/preview.html")],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "generation fencing passed" in result.stdout
